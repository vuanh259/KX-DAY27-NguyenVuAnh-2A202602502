"""Offline integration tests: actual graphs and SQLite, mocked external services."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langgraph.types import Command
from streamlit.testing.v1 import AppTest

import app
from common.db import replay_events
from common.schemas import PRAnalysis
from exercises import exercise_1_confidence as ex1
from exercises import exercise_2_hitl as ex2
from exercises import exercise_3_escalation as ex3
from exercises import exercise_4_audit as ex4

URL = 'https://github.com/example/demo/pull/1'
PR = SimpleNamespace(title='Add field', diff='+priority = 1',
                     files_changed=['task.py'], head_sha='abcdef123')


def analysis(confidence):
    return PRAnalysis(summary='Review summary', confidence=confidence,
        confidence_reasoning='Migration needs clarification',
        risk_factors=['Migration'], escalation_questions=['How is task.py migrated?'])


class FakeLLM:
    def __init__(self, *values):
        self.values = iter(values)
        self.messages = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        return next(self.values)

    async def ainvoke(self, messages):
        return self.invoke(messages)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'scratch'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'HITL_DB_PATH': str(Path(self.temp.name) / 'audit.db'),
                                      'GITHUB_USER': 'test-reviewer'})
        env.start()
        self.addCleanup(env.stop)

    def test_thresholds_and_exercise_one_graph(self):
        for confidence, decision in [(0, 'escalate'), (.5799, 'escalate'),
                (.58, 'human_approval'), (.7299, 'human_approval'),
                (.73, 'auto_approve'), (1, 'auto_approve')]:
            with self.subTest(confidence=confidence), patch.object(ex1, 'fetch_pr', return_value=PR), \
                    patch.object(ex1, 'get_llm', return_value=FakeLLM(analysis(confidence))):
                result = ex1.build_graph().invoke({'pr_url': URL})
                self.assertEqual(result['decision'], decision)

    def test_exercise_two_pause_and_choices(self):
        for choice in ('approve', 'reject', 'edit'):
            with self.subTest(choice=choice), patch.object(ex2, 'fetch_pr', return_value=PR), \
                    patch.object(ex2, 'get_llm', return_value=FakeLLM(analysis(.65))), \
                    patch.object(ex2, 'post_review_comment') as post:
                graph = ex2.build_graph()
                cfg = {'configurable': {'thread_id': choice}}
                result = graph.invoke({'pr_url': URL}, cfg)
                self.assertEqual(result['__interrupt__'][0].value['kind'], 'approval_request')
                post.assert_not_called()
                result = graph.invoke(Command(resume={'choice': choice, 'feedback': 'note'}), cfg)
                self.assertEqual(result['final_action'], 'committed' if choice == 'approve' else 'rejected')
                self.assertEqual(post.call_count, int(choice == 'approve'))

    def test_exercise_three_refinement_uses_context(self):
        llm = FakeLLM(analysis(.4), analysis(.7))
        with patch.object(ex3, 'fetch_pr', return_value=PR), \
                patch.object(ex3, 'get_llm', return_value=llm), \
                patch.object(ex3, 'post_review_comment') as post:
            graph = ex3.build_graph()
            cfg = {'configurable': {'thread_id': 'escalation'}}
            result = graph.invoke({'pr_url': URL}, cfg)
            self.assertEqual(result['__interrupt__'][0].value['kind'], 'escalation')
            post.assert_not_called()
            result = graph.invoke(Command(resume={'How is task.py migrated?': 'Backfill first'}), cfg)
            self.assertEqual(result['final_action'], 'committed_after_escalation')
            self.assertIn('Backfill first', llm.messages[-1][-1]['content'])
            self.assertIn('Initial analysis', llm.messages[-1][-1]['content'])
            post.assert_called_once()

    def test_durable_resume_and_audit(self):
        llm = FakeLLM(analysis(.65))
        with patch.object(ex4, 'fetch_pr', return_value=PR) as fetch, \
                patch.object(ex4, 'get_llm', return_value=llm), \
                patch.object(ex4, 'post_review_comment') as post:
            result = asyncio.run(app.run_graph(URL, 'durable'))
            self.assertTrue(result.get('__interrupt__'))
            # Each call opens and closes a new SQLite checkpointer and event loop.
            result = asyncio.run(app.run_graph(URL, 'durable'))
            self.assertTrue(result.get('__interrupt__'))
            result = asyncio.run(app.run_graph(URL, 'durable', {'choice': 'approve', 'feedback': 'Checked'}))
            self.assertEqual(result['final_action'], 'committed')
            result = asyncio.run(app.run_graph(URL, 'durable'))
            self.assertEqual(result['final_action'], 'committed')
            post.assert_called_once()
            fetch.assert_called_once()
            events = asyncio.run(replay_events('durable'))
            self.assertTrue({'fetch_pr', 'analyze', 'route', 'human_approval', 'commit'} <= {e['action'] for e in events})
            approvals = [e for e in events if e['action'] == 'human_approval' and e['decision'] == 'approve']
            self.assertEqual(approvals[0]['reviewer_id'], 'test-reviewer')

    def test_auto_failure_and_escalation_audit(self):
        for thread, confidence, fails in [('auto', .9, False), ('failure', .9, True), ('low', .4, False)]:
            llm = FakeLLM(analysis(confidence), analysis(.68))
            with self.subTest(thread=thread), patch.object(ex4, 'fetch_pr', return_value=PR), \
                    patch.object(ex4, 'get_llm', return_value=llm), \
                    patch.object(ex4, 'post_review_comment', side_effect=RuntimeError('denied') if fails else None) as post:
                result = asyncio.run(app.run_graph(URL, thread))
                if confidence < .58:
                    post.assert_not_called()
                    result = asyncio.run(app.run_graph(URL, thread, {'How is task.py migrated?': 'Backfill'}))
                self.assertEqual(result['final_action'], 'committed' if confidence < .58 else
                                 'auto_commit_failed' if fails else 'auto_committed')
                events = asyncio.run(replay_events(thread))
                self.assertFalse(any(e['risk_level'] == 'high' and e['decision'] == 'auto' for e in events))
                if confidence < .58:
                    self.assertTrue(any(e['action'] == 'synthesize' and e['confidence'] == .68 for e in events))

    def test_streamlit_approval_and_rejection(self):
        with patch.object(ex4, 'fetch_pr', return_value=PR), \
                patch.object(ex4, 'get_llm', return_value=FakeLLM(analysis(.65))), \
                patch.object(ex4, 'post_review_comment') as post:
            ui = AppTest.from_file(str(Path(app.__file__).resolve()), default_timeout=15).run()
            self.assertFalse(ui.exception)
            ui.text_input[0].set_value(URL)
            next(b for b in ui.button if b.label == 'Run review').click().run()
            self.assertFalse(ui.exception)
            self.assertTrue(any('Approval requested' in h.value for h in ui.subheader))
            next(b for b in ui.button if b.label == 'Reject').click().run()
            self.assertFalse(ui.exception)
            self.assertTrue(any('skipped' in w.value for w in ui.warning))
            post.assert_not_called()

    def test_streamlit_escalation(self):
        with patch.object(ex4, 'fetch_pr', return_value=PR), \
                patch.object(ex4, 'get_llm', return_value=FakeLLM(analysis(.4), analysis(.7))), \
                patch.object(ex4, 'post_review_comment') as post:
            ui = AppTest.from_file(str(Path(app.__file__).resolve()), default_timeout=15).run()
            ui.text_input[0].set_value(URL)
            next(b for b in ui.button if b.label == 'Run review').click().run()
            self.assertFalse(ui.exception)
            self.assertTrue(any('Strong escalation' in h.value for h in ui.subheader))
            next(t for t in ui.text_input if t.label == 'How is task.py migrated?').set_value('Backfill first')
            next(b for b in ui.button if b.label == 'Submit answers').click().run()
            self.assertFalse(ui.exception)
            self.assertTrue(ui.success)
            post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
