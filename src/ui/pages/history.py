"""History tab — Streamlit UI only."""

import json

import streamlit as st

from ui.db import delete_ui_history, load_ui_history


def render() -> None:
    db = st.session_state["db"]
    history = load_ui_history(db)

    if not history:
        st.info("아직 분석 기록이 없습니다. Analyze 탭에서 로그를 분석하세요.")
        return

    col1, col2 = st.columns([6, 1])
    col1.caption(f"총 {len(history)}건 (최근 100건)")
    if col2.button("전체 삭제", type="secondary"):
        delete_ui_history(db)
        st.rerun()

    for row in history:
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        label = (
            f"{row['analyzed_at']} | "
            f"{row['error_id'] or '?'} — {row['category'] or '?'} | "
            f"{row['model']}"
        )
        with st.expander(label):
            st.caption(f"Log: `{row['log_snippet']}`")
            c1, c2, c3 = st.columns(3)
            c1.metric("Error ID", row["error_id"] or "—")
            c2.metric("Severity", row["severity"] or "—")
            c3.metric("Confidence", f"{row['confidence']}/10" if row["confidence"] else "—")
            st.markdown(f"**Root Cause:** {result.get('root_cause', '—')}")
            steps = result.get("resolution_steps", [])
            if steps:
                st.markdown("**Resolution Steps:**")
                for i, step in enumerate(steps, 1):
                    st.markdown(f"{i}. {step}")
            with st.expander("Full JSON"):
                st.json(result)
            if st.button("이 로그 다시 분석", key=f"rerun_{row['id']}"):
                st.session_state["log_input"] = row["raw_log"]
                st.rerun()
