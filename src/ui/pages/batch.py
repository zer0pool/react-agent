"""Batch Results tab — Streamlit UI only."""

import json

import streamlit as st

from ui.db import delete_batch_result, load_batch_results, update_batch_result


def render() -> None:
    db = st.session_state["db"]
    batch_rows = load_batch_results(db)

    if not batch_rows:
        st.info("batch_results.db 에 결과가 없습니다. run_batch.py 를 먼저 실행하세요.")
        return

    # ── 필터 ─────────────────────────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns(3)
    months = sorted({r["month"] for r in batch_rows if r.get("month")})
    statuses = sorted({r["status"] for r in batch_rows if r.get("status")})
    sel_month = fc1.selectbox("Month", ["전체"] + months, key="b_month")
    sel_status = fc2.selectbox("Status", ["전체"] + statuses, key="b_status")
    sel_search = fc3.text_input("Error ID / Category 검색", key="b_search")

    filtered = batch_rows
    if sel_month != "전체":
        filtered = [r for r in filtered if r.get("month") == sel_month]
    if sel_status != "전체":
        filtered = [r for r in filtered if r.get("status") == sel_status]
    if sel_search:
        q = sel_search.lower()
        filtered = [
            r for r in filtered
            if q in (r.get("error_id") or "").lower()
            or q in (r.get("category") or "").lower()
        ]

    st.caption(f"{len(filtered)}건")

    for row in filtered:
        result = json.loads(row["result_json"]) if row.get("result_json") else {}
        fp = row["file_path"]
        status_icon = "✅" if row["status"] == "success" else "❌"
        label = (
            f"{status_icon} {row.get('processed_at', '')[:16]} | "
            f"{fp.split('/')[-1]} | "
            f"{row.get('error_id') or '?'} — {row.get('category') or '?'}"
        )

        with st.expander(label):
            if row["status"] == "success":
                # ── 조회 ──────────────────────────────────────────────────
                c1, c2, c3 = st.columns(3)
                c1.metric("Error ID", row.get("error_id") or "—")
                c2.metric("Category", row.get("category") or "—")
                c3.metric("Confidence", f"{row.get('confidence')}/10" if row.get("confidence") else "—")
                st.markdown(f"**Root Cause:** {result.get('root_cause', '—')}")
                steps = result.get("resolution_steps", [])
                if steps:
                    st.markdown("**Resolution Steps:**")
                    for i, s in enumerate(steps, 1):
                        st.markdown(f"{i}. {s}")
                with st.expander("Full JSON"):
                    st.json(result)

                st.divider()

                # ── 편집 ──────────────────────────────────────────────────
                st.markdown("**편집**")
                e1, e2, e3 = st.columns(3)
                new_error_id = e1.text_input("Error ID", value=row.get("error_id") or "", key=f"eid_{fp}")
                new_category = e2.text_input("Category", value=row.get("category") or "", key=f"cat_{fp}")
                new_confidence = e3.number_input(
                    "Confidence", min_value=0.0, max_value=10.0, step=0.1,
                    value=float(row.get("confidence") or 0), key=f"conf_{fp}",
                )

                btn_c1, btn_c2, _ = st.columns([2, 2, 6])
                if btn_c1.button("저장", key=f"save_{fp}", type="primary"):
                    updated = {**result, "error_id": new_error_id, "category": new_category, "confidence": new_confidence}
                    update_batch_result(db, fp, new_error_id, new_category, new_confidence,
                                        json.dumps(updated, ensure_ascii=False))
                    st.success("저장됐습니다.")
                    st.rerun()
                if btn_c2.button("삭제", key=f"del_{fp}", type="secondary"):
                    delete_batch_result(db, fp)
                    st.rerun()

                if st.button("이 로그 분석 탭으로", key=f"batch_rerun_{fp}"):
                    st.session_state["log_input"] = result.get("raw_log", fp)
                    st.rerun()
            else:
                st.error(f"Error: {row.get('error_msg')}")
                if st.button("삭제", key=f"del_err_{fp}", type="secondary"):
                    delete_batch_result(db, fp)
                    st.rerun()

            st.caption(f"File: {fp} | Processed: {row.get('processed_at')}")
