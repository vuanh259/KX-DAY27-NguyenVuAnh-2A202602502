"""Streamlit UI: uv run streamlit run app.py."""
from __future__ import annotations
import asyncio
import uuid
import streamlit as st
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from common.db import db_conn, db_path
from common.github import parse_pr_url
from exercises.exercise_4_audit import build_graph

async def recent_sessions():
    async with db_conn() as conn:
        async with conn.execute('''
            SELECT thread_id, pr_url, MAX(timestamp) AS last_event,
                CASE MAX(CASE risk_level WHEN 'high' THEN 3 WHEN 'med' THEN 2 ELSE 1 END)
                    WHEN 3 THEN 'high' WHEN 2 THEN 'med' ELSE 'low' END AS worst_risk
            FROM audit_events GROUP BY thread_id, pr_url
            ORDER BY MAX(timestamp) DESC LIMIT 25
        ''') as cur:
            return [dict(row) for row in await cur.fetchall()]

async def run_graph(pr_url: str, thread_id: str, resume_value=None):
    async with AsyncSqliteSaver.from_conn_string(db_path()) as cp:
        await cp.setup()
        graph = build_graph(cp)
        cfg = {'configurable': {'thread_id': thread_id}}
        saved = await graph.aget_state(cfg)
        if saved.values and saved.values.get('pr_url') != pr_url:
            raise ValueError('This session belongs to another PR')
        if resume_value is not None:
            if not saved.next:
                raise ValueError('This session is not waiting for a response')
            return await graph.ainvoke(Command(resume=resume_value), cfg)
        if saved.next:
            return await graph.ainvoke(None, cfg)
        if saved.values:
            return saved.values
        return await graph.ainvoke({'pr_url': pr_url, 'thread_id': thread_id}, cfg)

def remember(result):
    pending = result.get('__interrupt__')
    st.session_state.interrupt_payload = pending[0].value if pending else None
    st.session_state.final = None if pending else result

def render_approval_card(payload):
    st.subheader(f"Approval requested — confidence {payload['confidence']:.0%}")
    st.caption(payload['confidence_reasoning'])
    st.markdown(payload['summary'])
    for c in payload.get('comments', []):
        st.markdown(f"- **[{c['severity']}]** `{c['file']}:{c.get('line') or '?'}` — {c['body']}")
    with st.expander('Diff'):
        st.code(payload.get('diff_preview', ''), language='diff')
    feedback = st.text_area('Feedback (optional)', key=f'feedback_{st.session_state.thread_id}')
    st.caption('Edit records feedback and skips posting, as in Exercise 2.')
    columns = st.columns(3)
    for col, choice in zip(columns, ('approve', 'reject', 'edit')):
        if col.button(choice.title(), type='primary' if choice == 'approve' else 'secondary'):
            return {'choice': choice, 'feedback': feedback}
    return None

def render_escalation_card(payload):
    st.subheader(f"Strong escalation — confidence {payload['confidence']:.0%}")
    st.caption(payload['confidence_reasoning'])
    st.markdown(payload['summary'])
    for risk in payload.get('risk_factors', []):
        st.error(risk)
    with st.form(f'escalation_{st.session_state.thread_id}'):
        answers = {q: st.text_input(q, key=f'answer_{st.session_state.thread_id}_{i}')
                   for i, q in enumerate(payload['questions'])}
        if st.form_submit_button('Submit answers'):
            if all(a.strip() for a in answers.values()):
                return answers
            st.warning('Please answer every question.')
    return None

def drive(resume_value=None):
    try:
        with st.spinner('Reviewing PR...'):
            remember(asyncio.run(run_graph(st.session_state.pr_url, st.session_state.thread_id, resume_value)))
        return True
    except Exception as error:
        st.error(f'Review failed: {error}')
        return False

def main():
    load_dotenv()
    st.set_page_config(page_title='HITL PR Review', layout='wide')
    st.title('HITL PR Review Agent')
    for key, default in {'thread_id': None, 'pr_url': '', 'interrupt_payload': None, 'final': None}.items():
        st.session_state.setdefault(key, default)
    with st.sidebar:
        st.header('Recent sessions')
        try:
            rows = asyncio.run(recent_sessions())
            if rows:
                st.dataframe(rows, hide_index=True)
                selected = st.selectbox('Session to reopen', rows,
                    format_func=lambda r: f"{r['thread_id'][:8]} | {r['worst_risk']} | {r['pr_url']}")
                if st.button('Open session'):
                    st.session_state.thread_id = selected['thread_id']
                    st.session_state.pr_url = selected['pr_url']
                    st.session_state.interrupt_payload = None
                    st.session_state.final = None
                    st.query_params['thread'] = selected['thread_id']
                    st.query_params['pr'] = selected['pr_url']
                    if drive():
                        st.rerun()
            else:
                st.caption('No sessions yet.')
        except Exception as error:
            st.error(f'Cannot load sessions: {error}')
    # Query parameters preserve the thread across a browser refresh.
    if not st.session_state.thread_id and st.query_params.get('thread') and st.query_params.get('pr'):
        st.session_state.thread_id = st.query_params['thread']
        st.session_state.pr_url = st.query_params['pr']
        drive()
    with st.form('start'):
        pr_url = st.text_input('PR URL', value=st.session_state.pr_url,
            placeholder='https://github.com/VinUni-AI20k/PR-Demo/pull/1')
        submitted = st.form_submit_button('Run review')
    if submitted:
        try:
            parse_pr_url(pr_url.strip())
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.pr_url = pr_url.strip()
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.interrupt_payload = None
            st.session_state.final = None
            st.query_params['thread'] = st.session_state.thread_id
            st.query_params['pr'] = st.session_state.pr_url
            drive()
    payload = st.session_state.interrupt_payload
    if payload:
        if payload['kind'] == 'approval_request':
            answer = render_approval_card(payload)
        elif payload['kind'] == 'escalation':
            answer = render_escalation_card(payload)
        else:
            st.error('Unknown interrupt kind')
            answer = None
        if answer is not None and drive(answer):
            st.rerun()
    final = st.session_state.final
    if final:
        action = final.get('final_action', 'unknown')
        if action in {'committed', 'auto_committed'}:
            st.success(f'{action} — comment posted')
            st.link_button('View review on GitHub', st.session_state.pr_url)
        elif action == 'rejected':
            st.warning('Review skipped; no comment posted.')
        elif 'failed' in action:
            st.error('Posting failed. Check GitHub access and replay the audit trail.')
        else:
            st.info(action)
        if final.get('analysis'):
            st.metric('Confidence', f"{final['analysis'].confidence:.0%}")
            st.markdown(final['analysis'].summary)
        st.caption(f'thread_id = {st.session_state.thread_id}')
        st.code(f'uv run python -m audit.replay --thread {st.session_state.thread_id}')

if __name__ == '__main__':
    main()
