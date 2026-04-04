"""Streamlit entry point — layout and routing only."""

import os
import sys

import streamlit as st
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

load_dotenv()
if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

# ── UI modules (imported after sys.path setup) ────────────────────────────────
from ui.db import get_db
from ui.pages import analyze, batch, history

# ── Constants ─────────────────────────────────────────────────────────────────

AVAILABLE_MODELS = [
    "ollama/qwen2.5-coder:1.5b",
    "ollama/qwen2.5-coder:7b",
    "ollama/llama3.1:latest",
    "google_genai/gemini-2.5-flash",
    "google_genai/gemini-2.0-flash",
]

ERROR_LOGS_ROOT = os.path.join(os.path.dirname(__file__), "error_logs")

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Airflow Error Analyzer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stTextArea textarea { font-family: monospace; font-size: 13px; }
.step-box { padding: 8px 12px; border-radius: 6px; margin: 4px 0; font-size: 13px; }
.step-tool  { background: #1e3a5f; border-left: 3px solid #4a9eff; }
.step-ai    { background: #1a3a1a; border-left: 3px solid #4caf50; }
.step-human { background: #3a2a1a; border-left: 3px solid #ff9800; }
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────

if "log_input" not in st.session_state:
    st.session_state["log_input"] = ""
if "db" not in st.session_state:
    st.session_state["db"] = get_db()
if "browser_path" not in st.session_state:
    st.session_state["browser_path"] = ERROR_LOGS_ROOT

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Settings")
    selected_model = st.selectbox(
        "Model", AVAILABLE_MODELS, index=0,
        help="provider/model-name 형식. ollama 모델은 로컬에서 실행 중이어야 합니다.",
    )

    st.divider()
    st.subheader("Sample Logs")

    current_path = st.session_state["browser_path"]
    rel_path = os.path.relpath(current_path, ERROR_LOGS_ROOT)
    st.caption(f"📁 /{'' if rel_path == '.' else rel_path}")

    if current_path != ERROR_LOGS_ROOT:
        if st.button("⬆ ..", use_container_width=True):
            st.session_state["browser_path"] = os.path.dirname(current_path)
            st.rerun()

    try:
        entries = sorted(os.scandir(current_path), key=lambda e: (e.is_file(), e.name))
        for entry in entries:
            if entry.is_dir():
                if st.button(f"📂 {entry.name}", use_container_width=True):
                    st.session_state["browser_path"] = entry.path
                    st.rerun()
            elif entry.is_file() and entry.name.endswith(".log"):
                if st.button(f"📄 {entry.name}", use_container_width=True):
                    with open(entry.path, "r", encoding="utf-8") as f:
                        st.session_state["log_input"] = f.read()
                    st.rerun()
    except PermissionError:
        st.warning("접근 권한이 없습니다.")

    st.divider()
    st.caption("Airflow Error Analyzer v0.1")

# ── Main tabs ─────────────────────────────────────────────────────────────────

st.title("Airflow Error Analyzer")
tab_analyze, tab_history, tab_batch = st.tabs(["Analyze", "History", "Batch Results"])

with tab_analyze:
    analyze.render(selected_model)

with tab_history:
    history.render()

with tab_batch:
    batch.render()
