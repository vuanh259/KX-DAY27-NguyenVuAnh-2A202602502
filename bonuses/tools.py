"""Persistent checkpoint history/forks and session-level confidence calibration."""
from __future__ import annotations

from common.db import db_conn
from exercises.checkpoints import open_checkpointer
from common.schemas import AuditEntry, risk_level_for
from bonuses.workflow import build_graph
from exercises import exercise_4_audit as core


async def history(thread_id):
    async with open_checkpointer() as cp:
        graph = build_graph(cp)
        result = []
        async for snapshot in graph.aget_state_history({'configurable': {'thread_id': thread_id}}):
            analysis = snapshot.values.get('analysis')
            result.append({
                'checkpoint_id': snapshot.config['configurable']['checkpoint_id'],
                'created_at': snapshot.created_at, 'next': list(snapshot.next),
                'confidence': analysis.confidence if analysis else None,
                'final_action': snapshot.values.get('final_action'),
                'can_fork': snapshot.next == ('human_approval',),
            })
        return result


async def fork_checkpoint(source_thread, checkpoint_id, new_thread):
    """Fork an approval checkpoint into a new thread without replaying side effects.

    The source thread is never rewound. Only approval checkpoints are eligible;
    auto-posting and in-flight fan-out checkpoints cannot be forked here.
    """
    if not new_thread or source_thread == new_thread:
        raise ValueError('Time travel requires a distinct new thread ID')
    async with open_checkpointer() as cp:
        graph = build_graph(cp)
        cfg = {'configurable': {'thread_id': new_thread}}
        existing = await graph.aget_state(cfg)
        if existing.values:
            raise ValueError('The destination thread already exists')
        source = await graph.aget_state({'configurable': {
            'thread_id': source_thread, 'checkpoint_id': checkpoint_id}})
        if source.next != ('human_approval',) or not source.values.get('analysis'):
            raise ValueError('Select a checkpoint waiting for human approval')
        state = {**source.values, 'thread_id': new_thread,
                 'forked_from': f'{source_thread}:{checkpoint_id}',
                 'decision': 'human_approval', 'human_choice': None,
                 'human_feedback': None, 'final_action': None,
                 'posted_comment_body': None, 'reviewer_results': [],
                 'escalation_answers': None}
        await graph.aupdate_state(cfg, state, as_node='route')
        analysis = state['analysis']
        await core.audit(state, AuditEntry(
            agent_id=core.AGENT_ID, action='time_travel', confidence=analysis.confidence,
            risk_level=risk_level_for(analysis.confidence), decision='pending',
            reason=f'Forked approval checkpoint from {state["forked_from"]}',
            execution_time_ms=0,
        ))
        return {'thread_id': new_thread, 'pr_url': state['pr_url']}


async def calibration():
    """Use the final explicit approve/reject per session, never duplicate retries.

    Edits awaiting a decision and automatic decisions are excluded. Human approval
    is only a proxy for review correctness, so the result is descriptive.
    """
    async with db_conn() as conn:
        async with conn.execute('''
            WITH ranked AS (
                SELECT thread_id, confidence, decision,
                    ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY id DESC) AS rn
                FROM audit_events
                WHERE action = 'human_approval'
                  AND decision IN ('approve', 'reject', 'edit')
            )
            SELECT CASE WHEN confidence < 0.58 THEN '<0.58'
                        WHEN confidence < 0.73 THEN '0.58-<0.73' ELSE '>=0.73' END AS bucket,
                COUNT(*) AS sessions, AVG(confidence) AS mean_confidence,
                AVG(CASE decision WHEN 'approve' THEN 1.0 ELSE 0.0 END) AS approval_rate
            FROM ranked WHERE rn = 1 AND decision IN ('approve', 'reject')
            GROUP BY bucket ORDER BY MIN(confidence)
        ''') as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        row['confidence_minus_approval'] = row['mean_confidence'] - row['approval_rate']
    return rows
