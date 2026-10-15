"""Exercise the actual bonus graphs, checkpoints, SQL aggregation and UI."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import app
from bonuses.tools import calibration, fork_checkpoint, history
from common.db import replay_events, write_audit_event
from common.schemas import AuditEntry
from exercises import exercise_4_audit as core
from test_workflow import FakeLLM, PR, URL, analysis


class BonusTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'scratch'
        scratch.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'HITL_DB_PATH': str(Path(temp.name) / 'bonus.db'),
                                      'GITHUB_USER': 'test-reviewer'})
        env.start()
        self.addCleanup(env.stop)

    def test_edit_rewrites_then_requires_a_second_approval(self):
        revised = analysis(.67).model_copy(update={'summary': 'Rewritten review'})
        llm = FakeLLM(analysis(.65), revised)
        with patch.object(core, 'fetch_pr', return_value=PR), \
                patch.object(core, 'get_llm', return_value=llm), \
                patch.object(core, 'post_review_comment') as post:
            result = asyncio.run(app.run_graph(URL, 'edit'))
            result = asyncio.run(app.run_graph(URL, 'edit',
                {'choice': 'edit', 'feedback': 'Remove unsupported migration claim'}))
            post.assert_not_called()
            self.assertEqual(result['__interrupt__'][0].value['summary'], 'Rewritten review')
            self.assertIn('Remove unsupported migration claim', llm.messages[-1][-1]['content'])
            result = asyncio.run(app.run_graph(URL, 'edit', {'choice': 'approve'}))
            self.assertEqual(result['final_action'], 'committed')
            self.assertIn('Rewritten review', post.call_args.args[1])
            post.assert_called_once()
            events = asyncio.run(replay_events('edit'))
            self.assertTrue(any(e['action'] == 'rewrite' and e['decision'] == 'edit' for e in events))

    def test_fanout_waits_for_both_reviewers_across_reopens(self):
        llm = FakeLLM(analysis(.4), analysis(.7))
        with patch.object(core, 'fetch_pr', return_value=PR), \
                patch.object(core, 'get_llm', return_value=llm), \
                patch.object(core, 'post_review_comment') as post:
            result = asyncio.run(app.run_graph(URL, 'fanout', reviewer_ids=['alice', 'bob']))
            self.assertEqual(len(result['__interrupt__']), 2)
            requests = {i.value['reviewer_id']: i for i in result['__interrupt__']}
            result = asyncio.run(app.run_graph(URL, 'fanout',
                {'How is task.py migrated?': 'Backfill first'}, interrupt_id=requests['alice'].id))
            post.assert_not_called()
            self.assertEqual(len(llm.messages), 1)
            self.assertEqual(len(result['__interrupt__']), 1)
            self.assertEqual(result['__interrupt__'][0].value['reviewer_id'], 'bob')
            result = asyncio.run(app.run_graph(URL, 'fanout',
                {'How is task.py migrated?': 'Backfill is not yet validated'}, interrupt_id=requests['bob'].id))
            self.assertEqual(result['final_action'], 'committed')
            self.assertIn('alice: How is task.py migrated?', llm.messages[-1][-1]['content'])
            self.assertIn('Backfill is not yet validated', llm.messages[-1][-1]['content'])
            asyncio.run(app.run_graph(URL, 'fanout'))
            post.assert_called_once()
            responses = [e for e in asyncio.run(replay_events('fanout')) if e['action'] == 'reviewer_response']
            self.assertEqual({e['reviewer_id'] for e in responses}, {'alice', 'bob'})

    def test_time_travel_forks_without_rewinding_source(self):
        with patch.object(core, 'fetch_pr', return_value=PR) as fetch, \
                patch.object(core, 'get_llm', return_value=FakeLLM(analysis(.65))), \
                patch.object(core, 'post_review_comment') as post:
            asyncio.run(app.run_graph(URL, 'original'))
            asyncio.run(app.run_graph(URL, 'original', {'choice': 'reject'}))
            snapshots = asyncio.run(history('original'))
            selected = next(s for s in snapshots if s['can_fork'])
            created = asyncio.run(fork_checkpoint('original', selected['checkpoint_id'], 'alternate'))
            self.assertEqual(created['pr_url'], URL)
            result = asyncio.run(app.run_graph(URL, 'alternate'))
            self.assertTrue(result.get('__interrupt__'))
            result = asyncio.run(app.run_graph(URL, 'alternate', {'choice': 'approve'}))
            self.assertEqual(result['final_action'], 'committed')
            original = asyncio.run(app.run_graph(URL, 'original'))
            self.assertEqual(original['final_action'], 'rejected')
            fetch.assert_called_once()
            post.assert_called_once()
            self.assertEqual(asyncio.run(replay_events('alternate'))[0]['action'], 'time_travel')
            with self.assertRaises(ValueError):
                asyncio.run(fork_checkpoint('original', selected['checkpoint_id'], 'alternate'))
            with self.assertRaises(ValueError):
                asyncio.run(fork_checkpoint('original', snapshots[0]['checkpoint_id'], 'not-approval'))

    def test_calibration_uses_one_final_decision_per_session(self):
        async def seed():
            for thread, choice, confidence in [('a', 'edit', .65), ('a', 'approve', .7),
                    ('b', 'reject', .6), ('c', 'edit', .65)]:
                await write_audit_event(thread_id=thread, pr_url=URL, entry=AuditEntry(
                    agent_id='test', action='human_approval', confidence=confidence,
                    risk_level='med', decision=choice, execution_time_ms=0))
        asyncio.run(seed())
        rows = asyncio.run(calibration())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sessions'], 2)
        self.assertAlmostEqual(rows[0]['mean_confidence'], .65)
        self.assertAlmostEqual(rows[0]['approval_rate'], .5)
        self.assertAlmostEqual(rows[0]['confidence_minus_approval'], .15)

    def test_ui_edit_shows_rewritten_review_before_posting(self):
        revised = analysis(.67).model_copy(update={'summary': 'Rewritten review'})
        with patch.object(core, 'fetch_pr', return_value=PR), \
                patch.object(core, 'get_llm', return_value=FakeLLM(analysis(.65), revised)), \
                patch.object(core, 'post_review_comment') as post:
            ui = AppTest.from_file(str(Path(app.__file__).resolve()), default_timeout=15).run()
            ui.text_input[0].set_value(URL)
            next(b for b in ui.button if b.label == 'Run review').click().run()
            ui.text_area[0].set_value('Remove unsupported claim')
            next(b for b in ui.button if b.label == 'Edit').click().run()
            self.assertFalse(ui.exception)
            self.assertTrue(any(m.value == 'Rewritten review' for m in ui.markdown))
            post.assert_not_called()
            next(b for b in ui.button if b.label == 'Approve').click().run()
            self.assertFalse(ui.exception)
            post.assert_called_once()

    def test_ui_fanout_collects_two_separate_forms(self):
        with patch.object(core, 'fetch_pr', return_value=PR), \
                patch.object(core, 'get_llm', return_value=FakeLLM(analysis(.4), analysis(.7))), \
                patch.object(core, 'post_review_comment') as post:
            ui = AppTest.from_file(str(Path(app.__file__).resolve()), default_timeout=15).run()
            ui.text_input[0].set_value(URL)
            ui.text_input[1].set_value('alice, bob')
            next(b for b in ui.button if b.label == 'Run review').click().run()
            for answer in ('Backfill first', 'Add rollback tests'):
                next(t for t in ui.text_input if t.label == 'How is task.py migrated?').set_value(answer)
                next(b for b in ui.button if b.label == 'Submit answers').click().run()
                self.assertFalse(ui.exception)
                if answer == 'Backfill first':
                    post.assert_not_called()
            post.assert_called_once()
            self.assertTrue(ui.success)


if __name__ == '__main__':
    unittest.main()
