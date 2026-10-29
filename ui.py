from html import escape
from pathlib import Path

import streamlit as st


LOGO_FILE = Path(__file__).resolve().parent / "data" / "logo.png"
STORAGE_TABLES = ("catalog_items", "match_feedback", "pricing_settings", "projects")


@st.cache_data(ttl=60, show_spinner=False)
def storage_status():
    from feedback_store import supabase_health

    return supabase_health(STORAGE_TABLES)


STYLE = """
<style>
img.stLogo {
    background: #FFFFFF;
    border-radius: 0.6rem;
    padding: 0.35rem 0.6rem;
    box-sizing: content-box;
}
.cp-step {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 2.25rem 0 0.35rem 0;
    padding-bottom: 0.55rem;
    border-bottom: 1px solid rgba(128, 118, 108, 0.30);
}
.cp-step-number {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 2rem;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    background: #A8622F;
    color: #FFFFFF;
    font-weight: 700;
    font-size: 1rem;
}
.cp-step-title {
    font-size: 1.45rem;
    font-weight: 700;
    color: inherit;
}
.cp-project {
    display: inline-block;
    margin-top: 0.15rem;
    padding: 0.3rem 0.8rem;
    border-radius: 999px;
    background: rgba(156, 90, 46, 0.10);
    border: 1px solid rgba(156, 90, 46, 0.35);
    color: inherit;
    font-weight: 600;
    word-break: break-word;
}
.cp-muted {
    opacity: 0.7;
    font-size: 0.85rem;
}
[data-testid="stMetric"] {
    background: rgba(156, 90, 46, 0.06);
    border: 1px solid rgba(128, 118, 108, 0.28);
    border-radius: 0.75rem;
    padding: 0.8rem 1rem;
}
[data-testid="stExpander"] details {
    border-radius: 0.75rem;
}
.cp-review-link {
    display: inline-block;
    text-decoration: none !important;
    background: #A8622F;
    color: #FFFFFF !important;
    padding: 0.48rem 0.75rem;
    border-radius: 0.45rem;
    font-size: 0.88rem;
    font-weight: 600;
    white-space: nowrap;
}
.cp-review-link:hover { opacity: 0.88; color: #FFFFFF !important; }
</style>
"""


def apply_style():
    st.markdown(STYLE, unsafe_allow_html=True)


def show_sidebar(project_name=""):
    st.logo(str(LOGO_FILE), size="large")

    with st.sidebar:
        st.page_link("app.py", label="Calculator", icon=":material/calculate:")

        if (Path(__file__).resolve().parent / "pages" / "Projects.py").exists():
            st.page_link("pages/Projects.py", label="Projects", icon=":material/folder:")

        st.page_link("pages/Admin.py", label="Admin", icon=":material/admin_panel_settings:")

        st.divider()
        st.caption("Light / Dark mode: ⋮ menu (top right). Your choice is remembered in this browser.")

        if project_name:
            st.markdown(
                '<div class="cp-muted">Current project</div>'
                f'<div class="cp-project">{escape(project_name)}</div>',
                unsafe_allow_html=True,
            )

        show_storage_status()


def show_storage_status():
    status = storage_status()
    if status["backend"] == "local":
        st.caption(":orange[●] Storage: local file — not kept when the app restarts")
    elif status["ok"]:
        st.caption(":green[●] Storage: Supabase · connected")
    else:
        st.caption(":red[●] Storage: Supabase · problem: " + "; ".join(status["problems"]))


def show_header(title, caption):
    st.title(title)
    st.caption(caption)


def step_header(number, title):
    st.markdown(
        '<div class="cp-step">'
        f'<span class="cp-step-number">{number}</span>'
        f'<span class="cp-step-title">{escape(title)}</span>'
        "</div>",
        unsafe_allow_html=True,
    )
