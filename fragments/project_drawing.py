from copy import deepcopy
import hashlib
from pathlib import Path

import streamlit as st

from component_matcher import match_parts_to_catalog
from feedback_store import load_match_feedback
from pdf_extractor import extract_furniture
from project_store import PROJECT_STATUSES
from ui import step_header
from utils.performance import timed


SUPPORTED_TYPES = ["pdf", "jpg", "jpeg", "png"]
IMAGE_TYPES = {"jpg", "jpeg", "png"}


def _extension(filename):
    return str(filename or "").lower().rsplit(".", 1)[-1]


def _analysis_hash(uploaded_files, primary_filename, instructions):
    """Hash the exact source set and context used for one AI extraction."""
    digest = hashlib.sha256()
    digest.update(b"chisels-multifile-analysis-v1\0")
    digest.update(str(primary_filename or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(instructions or "").strip().encode("utf-8"))

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.getvalue()
        digest.update(b"\0FILE\0")
        digest.update(str(uploaded_file.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(file_bytes)).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_bytes)

    return digest.hexdigest()


def _show_source_summary(uploaded_files, primary_index):
    rows = []
    for index, uploaded_file in enumerate(uploaded_files):
        extension = _extension(uploaded_file.name)
        rows.append({
            "Role": "Primary technical source" if index == primary_index else "Supporting reference",
            "File": uploaded_file.name,
            "Type": extension.upper(),
            "Size": f"{len(uploaded_file.getvalue()) / (1024 * 1024):.1f} MB",
        })

    st.dataframe(rows, hide_index=True, width="stretch")

    image_files = [
        uploaded_file
        for uploaded_file in uploaded_files
        if _extension(uploaded_file.name) in IMAGE_TYPES
    ]
    if not image_files:
        return

    st.caption("Supporting image preview")
    columns = st.columns(min(3, len(image_files)))
    for index, uploaded_file in enumerate(image_files[:6]):
        with columns[index % len(columns)]:
            st.image(
                uploaded_file,
                caption=uploaded_file.name,
                width=260,
            )
    if len(image_files) > 6:
        st.caption(f"{len(image_files) - 6} additional image(s) are included in the analysis.")


@st.fragment
def render_project_and_drawing(all_items, apply_ai_data_fn):
    """Project metadata, estimator context and combined drawing analysis."""
    step_header(1, "Project")

    # Apply the filename fallback before the text-input widget is instantiated.
    pending_name = st.session_state.pop("pending_project_name", None)
    if pending_name and not str(st.session_state.get("project_name") or "").strip():
        st.session_state["project_name"] = pending_name

    project_col1, project_col2 = st.columns([3, 1])

    with project_col1:
        st.text_input(
            "Project name",
            key="project_name",
            placeholder="Optional — e.g. Reception desk - Europa office",
            help="Optional while estimating. If left empty, the primary file name is used after analysis.",
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

    step_header(2, "Drawing and context")

    st.text_area(
        "Additional information for AI (optional)",
        key="drawing_instructions",
        placeholder=(
            "Example: The PDF is the technical drawing. The photos show the intended oak veneer, "
            "black lacquered fronts and brass handles. Ignore the background furniture in the photos."
        ),
        help=(
            "Use this for materials, finishes, construction notes or anything that is not clear in the drawing. "
            "The text is analyzed together with all uploaded files."
        ),
        height=110,
    )

    uploader_version = int(st.session_state.get("drawing_upload_version", 0))
    uploaded_files = st.file_uploader(
        "Upload technical drawing and supporting references",
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
        key=f"drawing_uploads_{uploader_version}",
        help=(
            "Upload one or more PDFs/images. The primary technical file supplies dimensions and quantities; "
            "supporting photos/renders help identify materials, finishes and appearance."
        ),
    )

    if not project_name:
        st.caption(
            "Project name is optional. If it remains empty, the primary file name is used after analysis."
        )

    if not uploaded_files:
        saved_names = st.session_state.get("drawing_file_names") or []
        if saved_names:
            st.info(
                "Saved project references: " + ", ".join(saved_names)
                + ". Upload the source files again only if you want to re-analyze them."
            )
        return

    uploaded_files = list(uploaded_files)
    file_names = [uploaded_file.name for uploaded_file in uploaded_files]

    # Reset the primary-file selector when the uploaded set changes.
    signature = tuple((uploaded_file.name, len(uploaded_file.getvalue())) for uploaded_file in uploaded_files)
    if st.session_state.get("drawing_upload_signature") != signature:
        st.session_state["drawing_upload_signature"] = signature
        st.session_state.pop("drawing_primary_picker", None)

    pdf_indexes = [
        index
        for index, uploaded_file in enumerate(uploaded_files)
        if _extension(uploaded_file.name) == "pdf"
    ]
    primary_options = pdf_indexes or list(range(len(uploaded_files)))

    previous_primary = str(st.session_state.get("drawing_primary_file_name") or "")
    default_option_position = 0
    for option_position, file_index in enumerate(primary_options):
        if uploaded_files[file_index].name == previous_primary:
            default_option_position = option_position
            break

    primary_index = st.selectbox(
        "Primary technical source",
        options=primary_options,
        index=default_option_position,
        format_func=lambda file_index: uploaded_files[file_index].name,
        key="drawing_primary_picker",
        help=(
            "Dimensions, quantities and construction details are taken primarily from this file. "
            "When a PDF is uploaded, only PDFs are offered as the primary technical source."
        ),
    )
    primary_filename = uploaded_files[primary_index].name

    if pdf_indexes:
        st.info(
            f'Primary technical drawing: **{primary_filename}**. '
            "Photos/renders are supporting context and will not replace PDF dimensions."
        )
    else:
        st.info(
            f'Primary reference: **{primary_filename}**. '
            "Without a PDF, dimensions are used only when explicitly visible in an image."
        )

    _show_source_summary(uploaded_files, primary_index)

    total_size_mb = sum(len(uploaded_file.getvalue()) for uploaded_file in uploaded_files) / (1024 * 1024)
    st.caption(
        f"{len(uploaded_files)} file(s), {total_size_mb:.1f} MB total. "
        "They are sent in one combined AI extraction request. More files may take longer and use more tokens."
    )

    force_reanalyze = st.checkbox(
        "Force fresh AI drawing analysis",
        value=False,
        key="force_fresh_drawing_analysis",
        help=(
            "The exact same files, primary selection and instructions reuse the previous extraction in this session. "
            "Enable this only when you intentionally want OpenAI to read everything again."
        ),
    )

    if not st.button(
        "Analyze and build estimate",
        type="primary",
        key="analyze_drawing_button",
    ):
        return

    try:
        instructions = str(st.session_state.get("drawing_instructions") or "").strip()
        analysis_hash = _analysis_hash(uploaded_files, primary_filename, instructions)
        cached_analysis = st.session_state.get("drawing_analysis_cache") or {}

        with st.spinner("Reading all drawings and references with AI..."):
            if (
                not force_reanalyze
                and cached_analysis.get("sha256") == analysis_hash
                and isinstance(cached_analysis.get("ai_data"), dict)
            ):
                ai_data = deepcopy(cached_analysis["ai_data"])
                print(
                    "[CACHE] Reused combined AI extraction for",
                    ", ".join(file_names),
                    "(no new OpenAI extraction call)",
                )
            else:
                print(
                    "Starting combined drawing analysis:",
                    ", ".join(file_names),
                    "| primary:",
                    primary_filename,
                )
                ai_data = timed(
                    "AI drawing extraction",
                    extract_furniture,
                    uploaded_files,
                    user_instructions=instructions,
                    primary_filename=primary_filename,
                )
                st.session_state["drawing_analysis_cache"] = {
                    "sha256": analysis_hash,
                    "ai_data": deepcopy(ai_data),
                }

            apply_ai_data_fn(ai_data)

        st.session_state["drawing_file_names"] = file_names
        st.session_state["drawing_primary_file_name"] = primary_filename

        if not project_name:
            st.session_state["pending_project_name"] = Path(primary_filename).stem

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
