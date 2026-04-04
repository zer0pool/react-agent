"""Analyze tab — Streamlit UI only."""

import streamlit as st

from ui.db import save_ui_history
from ui.runner import parse_result_json, run_analysis


def render(selected_model: str) -> None:
    log_text = st.text_area(
        "Airflow Log",
        value=st.session_state.get("log_input", ""),
        height=220,
        placeholder="Paste Airflow error log here...",
    )

    col1, col2 = st.columns([1, 6])
    with col1:
        run_btn = st.button("Analyze", type="primary", use_container_width=True)
    with col2:
        if st.button("Clear"):
            st.session_state["log_input"] = ""
            st.rerun()

    if not run_btn:
        return

    if not log_text.strip():
        st.warning("Please enter a log.")
        return

    steps_placeholder = st.empty()

    with st.spinner(f"Running agent with `{selected_model}`..."):
        steps_html, final_result = run_analysis(log_text, selected_model)

    steps_placeholder.markdown("<br>".join(steps_html), unsafe_allow_html=True)

    if not final_result:
        return

    st.divider()
    st.subheader("Analysis Result")

    data = parse_result_json(final_result)
    if data is None:
        st.markdown(final_result)
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Error ID", data.get("error_id", "—"))
    c2.metric("Category", data.get("category", "—"))
    c3.metric("Severity", data.get("severity", "—"), delta=None, delta_color="off")

    st.markdown(f"**Root Cause:** {data.get('root_cause', '—')}")

    steps = data.get("resolution_steps", [])
    if steps:
        st.markdown("**Resolution Steps:**")
        for i, step in enumerate(steps, 1):
            st.markdown(f"{i}. {step}")

    confidence = data.get("confidence") or data.get("confidence_score")
    if confidence is not None:
        st.progress(min(float(confidence) / 10, 1.0), text=f"Confidence: {confidence}/10")

    if data.get("review_result"):
        st.info(f"Review: {data['review_result']}")

    with st.expander("Full JSON Response"):
        st.json(data)

    save_ui_history(st.session_state["db"], selected_model, log_text, data)
