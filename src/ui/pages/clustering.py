"""Cluster Analysis tab — Streamlit UI only.

Two modes:
  Run    — configure parameters, run DBSCAN, save session to DB
  Review — load a saved session, review clusters one by one
"""

import json
import os
import sys

import streamlit as st

from clustering.engine import run_clustering
from clustering.pattern_extractor import (
    apply_new_definition,
    apply_to_existing,
    suggest_for_existing,
    suggest_new_definition,
)
from clustering.preprocessor import load_logs
from ui.db import (
    create_cluster_session,
    delete_cluster_session,
    load_cluster_reviews,
    load_cluster_sessions,
    save_cluster_reviews,
    save_review_decision,
    update_session_status,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _rebuild_index(def_path: str) -> tuple[bool, str]:
    """Call prepare_hybrid_indices() from vector_store.py. Returns (ok, message)."""
    # vector_store.py lives at the project root; ensure root is in sys.path
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import importlib
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "vector_store", os.path.join(root, "vector_store.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.prepare_hybrid_indices(def_path)
        return True, "Search index rebuilt successfully."
    except Exception as exc:
        return False, f"Index rebuild failed: {exc}"


def _load_definitions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _def_options(definitions: list[dict]) -> list[str]:
    return ["— New error type —"] + [
        f"{d['error_id']} — {d['pattern_name']}" for d in definitions
    ]


def _progress_counts(reviews: list[dict]) -> tuple[int, int]:
    """Return (reviewed, total) counts."""
    reviewed = sum(1 for r in reviews if r["confirmed_as"] is not None)
    return reviewed, len(reviews)


# ── Main entry point ──────────────────────────────────────────────────────────

def render(log_root: str) -> None:
    st.subheader("Cluster Analysis")

    db = st.session_state["db"]

    # ── Global summary dashboard ──────────────────────────────────────────────
    sessions = load_cluster_sessions(db)
    if sessions:
        total_sessions = len(sessions)
        completed = sum(1 for s in sessions if s["status"] == "completed")
        in_progress = total_sessions - completed

        # Aggregate review progress across all sessions
        total_clusters_all = 0
        reviewed_all = 0
        new_types_all = 0
        confirmed_existing_all = 0
        for s in sessions:
            reviews = load_cluster_reviews(db, s["id"])
            total_clusters_all += len(reviews)
            reviewed_all += sum(1 for r in reviews if r["confirmed_as"] is not None)
            new_types_all += sum(1 for r in reviews if r["confirmed_as"] == "NEW")
            confirmed_existing_all += sum(
                1 for r in reviews
                if r["confirmed_as"] and r["confirmed_as"] != "NEW"
            )

        overall_pct = reviewed_all / total_clusters_all if total_clusters_all else 0

        with st.container(border=True):
            st.caption("Overall Progress (all sessions)")
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("Sessions", total_sessions,
                      delta=f"{completed} done / {in_progress} active", delta_color="off")
            d2.metric("Total Clusters", total_clusters_all)
            d3.metric("Reviewed", reviewed_all,
                      delta=f"{overall_pct:.0%}", delta_color="off")
            d4.metric("Confirmed New", new_types_all)
            d5.metric("Confirmed Existing", confirmed_existing_all)
            st.progress(overall_pct, text=f"{reviewed_all} / {total_clusters_all} clusters reviewed")

    st.divider()

    # Top-level mode selector
    # Use a separate state key so _render_run can switch mode programmatically
    if "cluster_mode_idx" not in st.session_state:
        st.session_state["cluster_mode_idx"] = 0

    mode = st.radio(
        "Mode",
        ["Run New Clustering", "Resume / Review Session"],
        index=st.session_state["cluster_mode_idx"],
        horizontal=True,
        key="cluster_mode",
    )

    st.divider()

    if mode == "Run New Clustering":
        _render_run(log_root, db)
    else:
        _render_session_list(db)


# ── Run mode ──────────────────────────────────────────────────────────────────

def _render_run(log_root: str, db) -> None:
    with st.expander("Data Source", expanded=True):
        log_dir = st.text_input("Log root directory", value=log_root)
        if not os.path.isdir(log_dir):
            st.error(f"Directory not found: {log_dir}")
            return
        log_files = list(__import__("pathlib").Path(log_dir).rglob("*.log"))
        st.caption(f"{len(log_files)} .log files found")
        if not log_files:
            st.warning("No .log files found.")
            return

    with st.expander("DBSCAN Parameters", expanded=True):
        c1, c2, c3 = st.columns(3)
        eps = c1.slider("eps (cosine distance)", 0.05, 0.95, 0.30, 0.05,
                        help="Max neighbor distance. Lower = more clusters.")
        min_samples = c2.slider("min_samples", 1, 20, 2, 1,
                                help="Min points to form a cluster core.")
        max_features = c3.select_slider("TF-IDF max_features",
                                        options=[200, 500, 1000, 2000, 5000], value=1000)

    if not st.button("Run Clustering", type="primary"):
        return

    with st.spinner("Loading and preprocessing logs..."):
        records = load_logs(log_dir)
    if not records:
        st.error("No usable log content after preprocessing.")
        return

    st.caption(f"Preprocessed {len(records)} logs.")

    with st.spinner(f"Running DBSCAN (eps={eps}, min_samples={min_samples})..."):
        result = run_clustering(records, eps=eps, min_samples=min_samples,
                                max_features=max_features)

    # ── 0-cluster guard: do not save, guide user to adjust params ────────────
    if result.n_clusters == 0:
        st.error(
            f"**No clusters formed.** "
            f"All {len(records)} logs were classified as noise (cluster = -1)."
        )
        st.markdown(
            "**Why this happens:**\n"
            "- `eps` is too small → logs are too far apart to be neighbors\n"
            "- `min_samples` is too large → no point has enough neighbors\n"
            "- Dataset is too small or too diverse for these parameters\n\n"
            "**Recommended adjustments:**"
        )
        n = len(records)
        if n < 20:
            rec_eps = 0.6
            rec_min = 1
        elif n < 100:
            rec_eps = 0.45
            rec_min = 2
        else:
            rec_eps = 0.35
            rec_min = 3
        st.info(
            f"For **{n} logs**: try `eps = {rec_eps}` and `min_samples = {rec_min}`"
        )
        st.caption("Adjust the parameters above and click Run Clustering again. Nothing was saved.")
        return

    # Save session + per-cluster rows to DB
    session_id = create_cluster_session(
        db, log_dir, eps, min_samples, max_features,
        len(records), result.n_clusters, result.n_noise, result.coverage_rate,
    )
    save_cluster_reviews(db, session_id, result.cluster_summaries)

    st.success(
        f"Session #{session_id} saved — "
        f"{result.n_clusters} clusters, coverage {result.coverage_rate:.0%}. "
        f"Switching to Review mode..."
    )
    st.session_state["cluster_mode_idx"] = 1
    st.session_state["active_session_id"] = session_id
    st.rerun()


# ── Session list mode ─────────────────────────────────────────────────────────

def _render_session_list(db) -> None:
    sessions = load_cluster_sessions(db)

    if not sessions:
        st.info("No sessions yet. Run clustering first.")
        return

    st.markdown("### Saved Sessions")

    # If a session is already active (just created or previously selected), jump to it
    active_id = st.session_state.get("active_session_id")

    for sess in sessions:
        sid = sess["id"]
        reviews = load_cluster_reviews(db, sid)
        reviewed, total = _progress_counts(reviews)
        pct = reviewed / total if total else 0
        status_icon = "✅" if sess["status"] == "completed" else (
            "🔄" if reviewed > 0 else "🆕"
        )

        col_info, col_open, col_del = st.columns([6, 1, 1])
        col_info.markdown(
            f"{status_icon} **Session #{sid}** — {sess['created_at']}  \n"
            f"eps={sess['eps']} / min_samples={sess['min_samples']} / "
            f"{sess['n_clusters']} clusters / coverage {sess['coverage_rate']:.0%}  \n"
            f"Progress: {reviewed}/{total} reviewed"
        )
        col_info.progress(pct)

        if col_open.button("Open", key=f"open_sess_{sid}"):
            st.session_state["active_session_id"] = sid
            st.rerun()

        if col_del.button("Delete", key=f"del_sess_{sid}", type="secondary"):
            delete_cluster_session(db, sid)
            if st.session_state.get("active_session_id") == sid:
                st.session_state.pop("active_session_id", None)
            st.rerun()

    st.divider()

    if active_id:
        # Verify session still exists
        if not any(s["id"] == active_id for s in sessions):
            st.session_state.pop("active_session_id", None)
            st.rerun()
        _render_review(db, active_id)


# ── Review mode ───────────────────────────────────────────────────────────────

def _render_review(db, session_id: int) -> None:
    sessions = load_cluster_sessions(db)
    sess = next((s for s in sessions if s["id"] == session_id), None)
    if not sess:
        st.error("Session not found.")
        return

    reviews = load_cluster_reviews(db, session_id)
    if not reviews:
        st.error("No clusters in this session.")
        st.markdown(
            f"This session recorded **{sess['n_logs']} logs** but **0 clusters** — "
            f"all logs were classified as noise.\n\n"
            f"**Parameters used:** eps=`{sess['eps']}`, min_samples=`{sess['min_samples']}`\n\n"
            f"Go to **Run New Clustering** and increase `eps` or decrease `min_samples`."
        )
        return

    def_path = os.path.join(os.path.dirname(__file__), "../../../data/error_definitions.json")
    definitions = _load_definitions(def_path)
    options = _def_options(definitions)

    reviewed, total = _progress_counts(reviews)
    pct = reviewed / total if total else 0

    st.markdown(f"### Reviewing Session #{session_id} — {sess['created_at']}")
    st.progress(pct, text=f"Progress: {reviewed} / {total} reviewed  ({pct:.0%})")

    # Mark complete button
    if reviewed == total and sess["status"] != "completed":
        if st.button("Mark Session as Completed ✅", type="primary"):
            update_session_status(db, session_id, "completed")
            st.success("Session marked as completed.")
            st.rerun()

    st.divider()

    # ── Filter controls ───────────────────────────────────────────────────────
    fc1, fc2 = st.columns(2)
    filter_status = fc1.selectbox(
        "Show",
        ["All", "Not reviewed", "Confirmed existing", "Confirmed new"],
        key=f"filter_status_{session_id}",
    )
    filter_matched = fc2.selectbox(
        "Auto-match filter",
        ["All", "Auto-matched (covered)", "No auto-match (uncovered)"],
        key=f"filter_matched_{session_id}",
    )

    filtered = reviews
    if filter_status == "Not reviewed":
        filtered = [r for r in filtered if r["confirmed_as"] is None]
    elif filter_status == "Confirmed existing":
        filtered = [r for r in filtered
                    if r["confirmed_as"] and r["confirmed_as"] != "NEW"]
    elif filter_status == "Confirmed new":
        filtered = [r for r in filtered if r["confirmed_as"] == "NEW"]

    if filter_matched == "Auto-matched (covered)":
        filtered = [r for r in filtered if r["matched_definition"]]
    elif filter_matched == "No auto-match (uncovered)":
        filtered = [r for r in filtered if not r["matched_definition"]]

    if not filtered:
        st.info("No clusters match the current filter.")
        return

    st.caption(f"Showing {len(filtered)} / {total} clusters")

    # ── Pagination: one cluster per page ──────────────────────────────────────
    page_key = f"cluster_page_{session_id}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0

    # Clamp index when filter changes
    max_page = len(filtered) - 1
    if st.session_state[page_key] > max_page:
        st.session_state[page_page] = 0

    idx = st.session_state[page_key]
    row = filtered[idx]

    # Navigation bar
    nav1, nav2, nav3, nav4, nav5 = st.columns([1, 1, 4, 1, 1])
    if nav1.button("⏮ First", disabled=(idx == 0), key=f"first_{session_id}"):
        st.session_state[page_key] = 0
        st.rerun()
    if nav2.button("◀ Prev", disabled=(idx == 0), key=f"prev_{session_id}"):
        st.session_state[page_key] -= 1
        st.rerun()
    nav3.markdown(
        f"<div style='text-align:center; padding-top:6px'>"
        f"Cluster <b>{idx + 1}</b> of <b>{len(filtered)}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if nav4.button("Next ▶", disabled=(idx >= max_page), key=f"next_{session_id}"):
        st.session_state[page_key] += 1
        st.rerun()
    if nav5.button("Last ⏭", disabled=(idx >= max_page), key=f"last_{session_id}"):
        st.session_state[page_key] = max_page
        st.rerun()

    st.divider()

    # ── Single cluster card ───────────────────────────────────────────────────
    _render_cluster_card(db, session_id, row, options, definitions)


def _render_cluster_card(db, session_id: int, row: dict, options: list[str],
                         definitions: list[dict]) -> None:
    cid = row["cluster_id"]
    confirmed = row["confirmed_as"]
    notes = row["notes"] or ""

    # Status badge
    if confirmed is None:
        badge = "🔲 Not reviewed"
    elif confirmed == "NEW":
        badge = "🆕 New error type"
    else:
        badge = f"✅ Confirmed: `{confirmed}`"

    st.markdown(f"## Cluster #{cid}  —  {badge}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Log count", row["count"])
    m2.metric("Auto-match", row["matched_definition"] or "—")
    m3.metric("Match ratio", f"{row['match_ratio']:.0%}" if row["match_ratio"] else "—")
    m4.metric("Closest (sim)", (
        f"{row['closest_definition']} ({row['closest_similarity']:.0%})"
        if row["closest_definition"] else "—"
    ))

    # Similarity hint
    if not row["matched_definition"] and row["closest_definition"]:
        sim = row["closest_similarity"] or 0
        if sim >= 0.3:
            st.warning(
                f"**Possible match:** `{row['closest_definition']}` "
                f"(similarity {sim:.0%}) — verify the log below before confirming."
            )
        else:
            st.info(
                f"Nearest definition: `{row['closest_definition']}` "
                f"(similarity {sim:.0%}) — low similarity, likely a new error type."
            )

    # Representative log
    st.markdown("**Representative log:**")
    st.code(row["representative"], language="text")
    st.caption(f"Source: `{row['representative_path']}`")

    # All file paths in this cluster
    paths = json.loads(row["all_paths"]) if row["all_paths"] else []
    if len(paths) > 1:
        with st.expander(f"All {len(paths)} files in this cluster"):
            for p in paths:
                st.caption(p)

    st.divider()

    # ── Decision form ─────────────────────────────────────────────────────────
    st.markdown("**Your decision**")

    # Pre-select dropdown
    default_idx = 0
    if confirmed and confirmed != "NEW":
        match = next(
            (i + 1 for i, d in enumerate(definitions) if d["error_id"] == confirmed), 0
        )
        default_idx = match
    elif not confirmed and row["closest_definition"] and (row["closest_similarity"] or 0) >= 0.3:
        match = next(
            (i + 1 for i, d in enumerate(definitions)
             if d["error_id"] == row["closest_definition"]), 0
        )
        default_idx = match

    sel_col, note_col = st.columns([3, 3])
    selected = sel_col.selectbox(
        "Assign to definition",
        options,
        index=default_idx,
        key=f"sel_{session_id}_{cid}",
    )
    note_text = note_col.text_input(
        "Notes (optional)",
        value=notes,
        key=f"notes_{session_id}_{cid}",
    )

    btn1, btn2, btn3 = st.columns([2, 2, 6])

    if btn1.button("Save & Next ▶", type="primary", key=f"save_next_{session_id}_{cid}"):
        confirmed_as = None if selected == "— New error type —" else selected.split(" — ")[0]
        if selected == "— New error type —":
            confirmed_as = "NEW"
        save_review_decision(db, session_id, cid, confirmed_as, note_text)
        page_key = f"cluster_page_{session_id}"
        reviews = load_cluster_reviews(db, session_id)
        # Find index of current cluster in filtered list and advance
        st.session_state[page_key] = min(
            st.session_state.get(page_key, 0) + 1,
            len(reviews) - 1,
        )
        st.rerun()

    if btn2.button("Send to Analyze →", type="secondary", key=f"agent_{session_id}_{cid}"):
        st.session_state["log_input"] = row["representative"]
        st.rerun()

    # Reviewed-at timestamp
    if row["reviewed_at"]:
        st.caption(f"Last reviewed: {row['reviewed_at']}")

    # ── Pattern suggestion (only shown after a decision is saved) ─────────────
    if not confirmed:
        return

    def_path = os.path.join(
        os.path.dirname(__file__), "../../../data/error_definitions.json"
    )
    paths = json.loads(row["all_paths"]) if row["all_paths"] else [row["representative_path"]]

    st.divider()
    st.markdown("### Pattern Suggestion")

    if confirmed == "NEW":
        # ── New definition suggestion ─────────────────────────────────────────
        with st.spinner("Extracting patterns from cluster logs..."):
            skeleton = suggest_new_definition(
                paths,
                existing_ids=[d["error_id"] for d in definitions],
                notes=row["notes"] or "",
            )

        st.info(
            f"Suggested new error ID: **{skeleton['error_id']}**  \n"
            f"Pattern name: **{skeleton['pattern_name']}**"
        )

        edit_key = f"new_def_{session_id}_{cid}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = json.dumps(skeleton, indent=2, ensure_ascii=False)

        st.markdown("**Edit the definition before applying:**")
        edited_json = st.text_area(
            "Definition JSON",
            value=st.session_state[edit_key],
            height=400,
            key=f"edit_new_{session_id}_{cid}",
        )

        rebuild_new = st.checkbox(
            "Also rebuild search index (BM25S + ChromaDB) after applying",
            value=True,
            key=f"rebuild_new_{session_id}_{cid}",
        )

        col_apply, col_reset = st.columns([2, 2])
        if col_apply.button("Apply — Add to error_definitions.json",
                            type="primary", key=f"apply_new_{session_id}_{cid}"):
            try:
                new_def = json.loads(edited_json)
                apply_new_definition(def_path, new_def)
                if rebuild_new:
                    with st.spinner("Rebuilding search index..."):
                        ok, msg = _rebuild_index(def_path)
                    if ok:
                        st.success(f"✅ **{new_def['error_id']}** added.  \n{msg}")
                    else:
                        st.warning(
                            f"✅ **{new_def['error_id']}** added to error_definitions.json.  \n"
                            f"⚠️ {msg}  \nRun `PYTHONPATH=src python vector_store.py` manually."
                        )
                else:
                    st.success(
                        f"✅ **{new_def['error_id']}** added to error_definitions.json.  \n"
                        f"Run `PYTHONPATH=src python vector_store.py` to rebuild the search index."
                    )
            except ValueError as e:
                st.error(str(e))
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

        if col_reset.button("Reset to suggestion", key=f"reset_new_{session_id}_{cid}"):
            st.session_state[edit_key] = json.dumps(skeleton, indent=2, ensure_ascii=False)
            st.rerun()

    else:
        # ── Existing definition suggestion ────────────────────────────────────
        target_def = next((d for d in definitions if d["error_id"] == confirmed), None)
        if not target_def:
            return

        with st.spinner(f"Comparing cluster logs against {confirmed}..."):
            suggestion = suggest_for_existing(paths, target_def)

        new_kw = suggestion["new_keywords"]
        new_ex = suggestion["new_examples"]

        if not new_kw and not new_ex:
            st.success(
                f"No gaps found — all cluster keywords and examples are already "
                f"covered by **{confirmed}**."
            )
            return

        if new_kw:
            st.warning(
                f"**{len(new_kw)} new keyword(s)** found in this cluster not in `{confirmed}`:"
            )
            st.code(", ".join(new_kw), language="text")

        if new_ex:
            st.warning(
                f"**{len(new_ex)} new example(s)** found in this cluster not in `{confirmed}`:"
            )
            for ex in new_ex:
                st.code(ex, language="text")

        st.markdown(f"**Updated `{confirmed}` definition preview:**")

        edit_key = f"upd_def_{session_id}_{cid}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = json.dumps(
                suggestion["updated_definition"], indent=2, ensure_ascii=False
            )

        edited_json = st.text_area(
            "Definition JSON (editable before apply)",
            value=st.session_state[edit_key],
            height=350,
            key=f"edit_upd_{session_id}_{cid}",
        )

        rebuild_upd = st.checkbox(
            "Also rebuild search index (BM25S + ChromaDB) after applying",
            value=True,
            key=f"rebuild_upd_{session_id}_{cid}",
        )

        col_apply, col_reset = st.columns([2, 2])
        if col_apply.button(
            f"Apply — Update {confirmed} in error_definitions.json",
            type="primary", key=f"apply_upd_{session_id}_{cid}",
        ):
            try:
                upd_def = json.loads(edited_json)
                apply_to_existing(def_path, upd_def)
                if rebuild_upd:
                    with st.spinner("Rebuilding search index..."):
                        ok, msg = _rebuild_index(def_path)
                    if ok:
                        st.success(f"✅ **{confirmed}** updated.  \n{msg}")
                    else:
                        st.warning(
                            f"✅ **{confirmed}** updated in error_definitions.json.  \n"
                            f"⚠️ {msg}  \nRun `PYTHONPATH=src python vector_store.py` manually."
                        )
                else:
                    st.success(
                        f"✅ **{confirmed}** updated in error_definitions.json.  \n"
                        f"Run `PYTHONPATH=src python vector_store.py` to rebuild the search index."
                    )
                # Clear cached edit so next open shows fresh suggestion
                st.session_state.pop(edit_key, None)
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

        if col_reset.button("Reset to suggestion", key=f"reset_upd_{session_id}_{cid}"):
            st.session_state[edit_key] = json.dumps(
                suggestion["updated_definition"], indent=2, ensure_ascii=False
            )
            st.rerun()
