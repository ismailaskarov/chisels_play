from html import escape
from pathlib import Path

import streamlit as st


LOGO_FILE = Path(__file__).resolve().parent / "data" / "logo.png"


def current_theme():
    return st.session_state.get("cp_theme", "Light")


def apply_style():
    dark = current_theme() == "Dark"

    if dark:
        background = "#161616"
        secondary = "#202020"
        card = "#242424"
        text = "#F3F0EC"
        muted = "#AAA49E"
        border = "#3A3734"
        input_bg = "#252321"
        accent = "#B86E3A"
    else:
        background = "#FFFFFF"
        secondary = "#FAF8F4"
        card = "#FAF8F4"
        text = "#23242A"
        muted = "#6B6A70"
        border = "#E4DDD2"
        input_bg = "#F8F5F0"
        accent = "#9C5A2E"

    style = f"""
    <style>
    .stApp {{ background-color: {background}; color: {text}; }}
    [data-testid="stSidebar"] {{ background-color: {secondary}; }}
    [data-testid="stSidebar"] * {{ color: {text}; }}
    h1, h2, h3, h4, h5, h6, p, label {{ color: {text}; }}

    .cp-step {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        margin: 2.25rem 0 0.35rem 0;
        padding-bottom: 0.55rem;
        border-bottom: 1px solid {border};
    }}
    .cp-step-number {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 2rem;
        width: 2rem;
        height: 2rem;
        border-radius: 50%;
        background: {accent};
        color: #FFFFFF;
        font-weight: 700;
        font-size: 1rem;
    }}
    .cp-step-title {{
        font-size: 1.45rem;
        font-weight: 700;
        color: {text};
    }}
    .cp-project {{
        display: inline-block;
        margin-top: 0.15rem;
        padding: 0.3rem 0.8rem;
        border-radius: 999px;
        background: {card};
        border: 1px solid {border};
        color: {text};
        font-weight: 600;
        word-break: break-word;
    }}
    .cp-muted {{ color: {muted}; font-size: 0.85rem; }}

    [data-testid="stMetric"] {{
        background: {card};
        border: 1px solid {border};
        border-radius: 0.75rem;
        padding: 0.8rem 1rem;
    }}
    [data-testid="stMetric"] * {{ color: {text}; }}
    [data-testid="stExpander"] details {{
        border: 1px solid {border};
        border-radius: 0.75rem;
        background: {secondary};
    }}

    div[data-baseweb="input"] > div,
    div[data-baseweb="textarea"] > div,
    div[data-baseweb="select"] > div {{
        background-color: {input_bg};
        border-color: {border};
        color: {text};
    }}
    input, textarea {{ color: {text} !important; }}

    .cp-review-link {{
        display: inline-block;
        text-decoration: none !important;
        background: {accent};
        color: #FFFFFF !important;
        padding: 0.48rem 0.75rem;
        border-radius: 0.45rem;
        font-size: 0.88rem;
        font-weight: 600;
        white-space: nowrap;
    }}
    .cp-review-link:hover {{ opacity: 0.88; color: #FFFFFF !important; }}
    </style>
    """
    st.markdown(style, unsafe_allow_html=True)


def show_sidebar(project_name=""):
    st.logo(str(LOGO_FILE), size="large")

    with st.sidebar:
        st.page_link("app.py", label="Calculator", icon=":material/calculate:")

        if (Path(__file__).resolve().parent / "pages" / "Projects.py").exists():
            st.page_link("pages/Projects.py", label="Projects", icon=":material/folder:")

        st.page_link("pages/Admin.py", label="Admin", icon=":material/admin_panel_settings:")

        st.divider()
        st.radio(
            "Appearance",
            ["Light", "Dark"],
            horizontal=True,
            key="cp_theme",
        )

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
