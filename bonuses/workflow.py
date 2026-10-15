"""The integrated graph extends the four original exercises with two bonuses."""
from __future__ import annotations

import operator
import os
import time
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from common.schemas import AuditEntry, PRAnalysis, ReviewState, risk_level_for
from exercises import exercise_4_audit as core
from exercises.review_prompts import REVIEW_SYSTEM


class BonusState(ReviewState, total=False):
    reviewer_ids: list[str]
    assigned_reviewer: str
    reviewer_results: Annotated[list[dict], operator.add]
    forked_from: str


async def rewrite_review(state):
    """Edit never posts directly: the rewritten analysis needs another approval."""
    feedback = (state.get('human_feedback') or '').strip()
    if not feedback:
        raise ValueError('Edit requires feedback describing the requested changes')
    started = time.monotonic()
    analysis = await core.get_llm().with_structured_output(PRAnalysis).ainvoke([
        {'role': 'system', 'content': REVIEW_SYSTEM +
         ' Rewrite the review using reviewer feedback. Correct unsupported claims, '
         'retain evidence-backed findings, and return the complete revised analysis. '
         'The result will be shown for another explicit approve/reject decision.'},
        {'role': 'user', 'content':
         f"Title: {state['pr_title']}\nDiff:\n{state['pr_diff']}\n"
         f"Original review:\n{state['analysis'].model_dump_json()}\nFeedback:\n{feedback}"},
    ])
    await core.audit(state, AuditEntry(
        agent_id=core.AGENT_ID, action='rewrite', confidence=analysis.confidence,
        risk_level=risk_level_for(analysis.confidence), reviewer_id=os.getenv('GITHUB_USER'),
        decision='edit', reason=feedback,
        execution_time_ms=int((time.monotonic() - started) * 1000),
    ))
    return {'analysis': analysis, 'human_choice': None}


def dispatch(state):
    reviewers = state.get('reviewer_ids') or []
    if state['decision'] == 'escalate' and reviewers:
        if len(reviewers) != 2 or len(set(reviewers)) != 2 or any(not r.strip() for r in reviewers):
            raise ValueError('Fan-out needs exactly two distinct reviewer IDs')
        return [Send('reviewer_questions', {**state, 'assigned_reviewer': r}) for r in reviewers]
    return state['decision']


async def reviewer_questions(state):
    reviewer = state['assigned_reviewer']
    analysis = state['analysis']
    started = time.monotonic()
    questions = analysis.escalation_questions or ['What context is missing to assess this PR?']
    await core.audit(state, AuditEntry(
        agent_id=core.AGENT_ID, action='reviewer_request', confidence=analysis.confidence,
        risk_level=risk_level_for(analysis.confidence), reviewer_id=reviewer,
        decision='pending', reason='Waiting for this fan-out reviewer', execution_time_ms=0,
    ))
    answers = interrupt({
        'kind': 'escalation', 'reviewer_id': reviewer, 'pr_url': state['pr_url'],
        'confidence': analysis.confidence, 'confidence_reasoning': analysis.confidence_reasoning,
        'summary': analysis.summary, 'risk_factors': analysis.risk_factors, 'questions': questions,
    })
    if not isinstance(answers, dict) or set(answers) != set(questions) or any(
        not isinstance(a, str) or not a.strip() for a in answers.values()
    ):
        raise ValueError('Each reviewer must answer every question')
    await core.audit(state, AuditEntry(
        agent_id=core.AGENT_ID, action='reviewer_response', confidence=analysis.confidence,
        risk_level=risk_level_for(analysis.confidence), reviewer_id=reviewer,
        decision='escalate', reason=str(answers),
        execution_time_ms=int((time.monotonic() - started) * 1000),
    ))
    return {'reviewer_results': [{'reviewer_id': reviewer, 'answers': answers}]}


async def merge_reviewers(state):
    # Parallel Send tasks must all finish before this aggregation node runs.
    results = {r['reviewer_id']: r['answers'] for r in state['reviewer_results']}
    if set(results) != set(state['reviewer_ids']):
        raise ValueError('Both reviewer responses are required before synthesis')
    answers = {f'{reviewer}: {q}': answer for reviewer in sorted(results)
               for q, answer in results[reviewer].items()}
    analysis = state['analysis']
    await core.audit(state, AuditEntry(
        agent_id=core.AGENT_ID, action='merge_reviewers', confidence=analysis.confidence,
        risk_level=risk_level_for(analysis.confidence), decision='escalate',
        reason='Collected both reviewer responses; preserve disagreements in synthesis',
        execution_time_ms=0,
    ))
    return {'escalation_answers': answers}


def build_graph(checkpointer):
    graph = StateGraph(BonusState)
    for name in ('fetch_pr', 'analyze', 'route', 'auto_approve', 'human_approval',
                 'commit', 'escalate', 'synthesize'):
        graph.add_node(name, getattr(core, 'node_' + name))
    graph.add_node('rewrite', rewrite_review)
    graph.add_node('reviewer_questions', reviewer_questions)
    graph.add_node('merge_reviewers', merge_reviewers)
    graph.add_edge(START, 'fetch_pr')
    graph.add_edge('fetch_pr', 'analyze')
    graph.add_edge('analyze', 'route')
    graph.add_conditional_edges('route', dispatch,
        ['auto_approve', 'human_approval', 'escalate', 'reviewer_questions'])
    graph.add_edge('auto_approve', END)
    graph.add_conditional_edges('human_approval',
        lambda state: 'rewrite' if state.get('human_choice') == 'edit' else 'commit',
        ['rewrite', 'commit'])
    graph.add_edge('rewrite', 'human_approval')
    graph.add_edge('escalate', 'synthesize')
    graph.add_edge('reviewer_questions', 'merge_reviewers')
    graph.add_edge('merge_reviewers', 'synthesize')
    graph.add_edge('synthesize', 'commit')
    graph.add_edge('commit', END)
    return graph.compile(checkpointer=checkpointer)
