from pathlib import Path
import hashlib

import streamlit as st

from component_matcher import match_parts_to_catalog
from feedback_store import load_match_feedback
from pdf_extractor import extract_furniture
from project_store import PROJECT_STATUSES
from ui import step_header
from utils.performance import timed


@st.fragment
def render_project_and_drawing(all_items, apply_ai_data_fn):
    """Project metadata + drawing analysis.

    Ordinary edits rerun only this fragment. The expensive AI pipeline runs only
    when Analyze is clicked. After analysis we trigger one full rerun so the
    component-review/estimate sections receive the new results.
    """
    step_header(1, "Project")

    # Apply an automatically generated filename-based project name before the
    # text_input widget exists. This avoids Streamlit's widget/session-state
    # mutation error and does not use AI or extra tokens.
    pending_name = st.session_state.pop("pending_project_name", None)
    if pending_name and not str(st.session_state.get("project_name") or "").strip():
        st.session_state["project_name"] = pending_name

    project_col1, project_col2 = st.columns([3, 1])

    with project_col1:
        st.text_input(
            "Project name",
            key="project_name",
            placeholder="Optional — e.g. Reception desk - Europa office",
            help="Optional while estimating. If left empty, the drawing filename is used after analysis.",
        )

    with project_col2:
        st.selectbox(
            "Status",
            PROJECT_STATUSES,
            key="project_status",
        )

    project_name = str(st.session_state.get("project_name") or "").strip()

    if st.session_state.get("current_project_id"):
        st.caption(
            "Opened from Project History. Saving will update this project rather than create a duplicate."
        )

    step_header(2, "Drawing")

    uploaded_file = st.file_uploader(
        "Upload PDF or image",
        type=["pdf", "jpg", "jpeg", "png"],
        key="drawing_upload",
    )

    if not project_name:
        st.caption(
            "Project name is optional. Upload the drawing first; if the name stays empty, "
            "the file name will be used after analysis."
        )

    if uploaded_file is None:
        return

    st.success(f"Uploaded: {uploaded_file.name}")
    extension = uploaded_file.name.lower().split(".")[-1]
    force_reanalyze = st.checkbox(
        "Force fresh AI drawing analysis",
        value=False,
        key="force_fresh_drawing_analysis",
        help=(
            "Normally the exact same uploaded file reuses its last AI extraction in this session, "
            "saving time and API tokens. Enable this only when you intentionally want OpenAI to read it again."
        ),
    )

    if extension in ["jpg", "jpeg", "png"]:
        st.image(uploaded_file, width=600)

    if not st.button(
        "Analyze and build estimate",
        type="primary",
        key="analyze_drawing_button",
    ):
        return

    try:
        file_bytes = uploaded_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        cached_analysis = st.session_state.get("drawing_analysis_cache") or {}

        with st.spinner("Reading drawing with AI..."):
            if (
                not force_reanalyze
                and cached_analysis.get("sha256") == file_hash
                and isinstance(cached_analysis.get("ai_data"), dict)
            ):
                ai_data = cached_analysis["ai_data"]
                print(
                    "[CACHE] Reused AI drawing extraction for",
                    uploaded_file.name,
                    "(no new OpenAI extraction call)",
                )
            else:
                print("Starting drawing analysis:", uploaded_file.name)
                ai_data = timed(
                    "AI drawing extraction",
                    extract_furniture,
                    uploaded_file,
                )
                st.session_state["drawing_analysis_cache"] = {
                    "sha256": file_hash,
                    "ai_data": ai_data,
                }

            apply_ai_data_fn(ai_data)

        if not project_name:
            st.session_state["pending_project_name"] = Path(uploaded_file.name).stem

        with st.spinner("Loading learned corrections..."):
            feedback = timed(
                "load_match_feedback",
                load_match_feedback,
            )

        with st.spinner("Matching drawing parts to the real price catalog..."):
            matches = timed(
                "AI catalog matching",
                match_parts_to_catalog,
                ai_data,
                all_items,
                feedback=feedback,
            )
            st.session_state["catalog_matches"] = matches

        print("Drawing analysis and catalog matching complete.")
        st.rerun()

    except Exception as error:
        print("AI PROCESSING ERROR:", repr(error))

        if st.session_state.get("catalog_matches") is None:
            st.session_state["ai_result"] = None
            st.session_state["ai_parts"] = []

        st.error(f"AI processing failed: {error}")
