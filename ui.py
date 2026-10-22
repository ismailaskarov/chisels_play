from html import escape
from pathlib import Path

import streamlit as st


LOGO_FILE = Path(__file__).resolve().parent / "data" / "logo.png"

STYLE = """
<style>
.cp-step {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 2.25rem 0 0.35rem 0;
    padding-bottom: 0.55rem;
    border-bottom: 1px solid #E4DDD2;
}
.cp-step-number {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 2rem;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    background: #9C5A2E;
    color: #FFFFFF;
    font-weight: 700;
    font-size: 1rem;
}
.cp-step-title {
    font-size: 1.45rem;
    font-weight: 700;
    color: #23242A;
}
.cp-project {
    display: inline-block;
    margin-top: 0.15rem;
    padding: 0.3rem 0.8rem;
    border-radius: 999px;
    background: #FFFFFF;
    border: 1px solid #E4DDD2;
    font-weight: 600;
    word-break: break-word;
}
.cp-muted {
    color: #6B6A70;
    font-size: 0.85rem;
}
[data-testid="stMetric"] {
    background: #FAF8F4;
    border: 1px solid #E4DDD2;
    border-radius: 0.75rem;
    padding: 0.8rem 1rem;
}
[data-testid="stExpander"] details {
    border-radius: 0.75rem;
}
</style>
"""


def apply_style():
    st.markdown(STYLE, unsafe_allow_html=True)


def show_sidebar(project_name=""):
    st.logo(str(LOGO_FILE), size="large")
    with st.sidebar:
        st.page_link("app.py", label="Calculator", icon=":material/calculate:")
        st.page_link("pages/Admin.py", label="Admin", icon=":material/admin_panel_settings:")
        if project_name:
            st.markdown(
                '<div class="cp-muted">Current project</div>'
                f'<div class="cp-project">{escape(project_name)}</div>',
                unsafe_allow_html=True,
            )


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
