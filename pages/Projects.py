from datetime import date

import streamlit as st

from project_store import PROJECT_STATUSES, archive_project, list_projects, project_storage_name
from project_files import list_project_files, read_project_file, project_file_storage_name
from ui import apply_style, show_header, show_sidebar


st.set_page_config(page_title="Projects | Chisels Play", layout="wide")
apply_style()
show_sidebar(st.session_state.get("project_name", "").strip())
show_header(
    "Projects",
    "Reopen previous estimates, continue work and keep a history of quotations.",
)


def _parse_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _restore_project(project):
    snapshot = dict(project.get("snapshot") or {})

    # Remove stale estimator/quotation widget state before loading the snapshot.
    prefixes = (
        "catalog_select_",
        "catalog_quantity_",
        "market_",
        "quote_",
        "custom_",
        "no_match_resolution_",
        "generated_quote_",
        "estimate_coefficient",
        "learning_conflict_",
        "treatment_",
        "autonomous_",
    )
    for key in list(st.session_state.keys()):
        if str(key).startswith(prefixes):
            del st.session_state[key]

    # The data editor has its own widget state. Restored service rows are supplied
    # back as default data by app.py instead.
    st.session_state.pop("quote_services_editor", None)

    for key, value in snapshot.items():
        if key in ("quote_date_value", "quote_validity_value"):
            parsed = _parse_date(value)
            if parsed is not None:
                value = parsed
        st.session_state[key] = value

    st.session_state["current_project_id"] = project.get("id")
    st.session_state["project_name"] = project.get("project_name") or snapshot.get("project_name") or ""
    st.session_state["project_status"] = project.get("status") or snapshot.get("project_status") or "Draft"
    st.session_state["project_service_rows"] = snapshot.get("project_service_rows") or []
    st.session_state.pop("generated_quote_bytes", None)
    st.session_state.pop("generated_quote_filename", None)


@st.cache_data(ttl=30, show_spinner=False)
def _all_project_files():
    return list_project_files()


def _project_files(project_id):
    return [
        row for row in _all_project_files()
        if row.get("project_id") == project_id
    ]


st.caption(
    f"Project data: {project_storage_name()} · Source files: {project_file_storage_name()}"
)

try:
    projects = list_projects()
except Exception as error:
    st.error(f"Projects could not be loaded: {error}")
    st.stop()

if not projects:
    st.info("No saved projects yet. Create an estimate in Calculator and click Save project.")
    st.stop()

filter1, filter2 = st.columns([2, 1])
with filter1:
    search = st.text_input("Search", placeholder="Project, client or quote number").strip().lower()
with filter2:
    status_filter = st.selectbox("Status", ["All", *PROJECT_STATUSES])

filtered = []
for project in projects:
    if status_filter != "All" and project.get("status") != status_filter:
        continue
    haystack = " ".join(
        str(project.get(key) or "")
        for key in ("project_name", "client_name", "quote_number", "furniture_name")
    ).lower()
    if search and search not in haystack:
        continue
    filtered.append(project)

st.caption(f"{len(filtered)} project(s)")

for project in filtered:
    title = project.get("project_name") or "Unnamed project"
    status = project.get("status") or "Draft"
    updated = str(project.get("updated_at") or "").replace("T", " ")[:16]
    client = project.get("client_name") or "—"
    quote = project.get("quote_number") or "—"
    calculated = float(project.get("calculated_price") or 0)
    final_price = float(project.get("final_client_price") or 0)

    with st.container(border=True):
        top1, top2 = st.columns([4, 1])
        with top1:
            st.markdown(f"### {title}")
            st.caption(f"Client: {client} · Quote: {quote} · Updated: {updated or '—'}")
        with top2:
            st.markdown(f"**{status}**")

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Calculated", f"€{calculated:,.2f}")
        with m2:
            st.metric("Final client price", f"€{final_price:,.2f}")
        with m3:
            snapshot = project.get("snapshot") or {}
            st.metric("Components", len(snapshot.get("items") or []))

        with st.expander("Source files"):
            try:
                file_rows = _project_files(project["id"])
            except Exception as error:
                st.error(f"Source files could not be loaded: {error}")
                file_rows = []

            if not file_rows:
                st.caption("No source files are stored for this project.")
            else:
                for file_row in file_rows:
                    label = file_row.get("filename") or "file"
                    if file_row.get("primary_source"):
                        label += " · primary"
                    size_kb = float(file_row.get("size_bytes") or 0) / 1024
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.write(f"**{label}**")
                        st.caption(f"{size_kb:,.0f} KB")
                    with c2:
                        prepare_key = f'prepare_file_{project["id"]}_{file_row["id"]}'
                        data_key = f'project_file_bytes_{file_row["id"]}'
                        if st.button("Prepare", key=prepare_key):
                            try:
                                st.session_state[data_key] = read_project_file(file_row)
                            except Exception as error:
                                st.error(f"File could not be downloaded: {error}")
                        if st.session_state.get(data_key) is not None:
                            st.download_button(
                                "Download",
                                data=st.session_state[data_key],
                                file_name=file_row.get("filename") or "project-file",
                                mime=file_row.get("content_type") or "application/octet-stream",
                                key=f'download_file_{project["id"]}_{file_row["id"]}',
                            )

        a1, a2 = st.columns([1, 1])
        with a1:
            if st.button("Open project", key=f"open_project_{project['id']}", type="primary"):
                _restore_project(project)
                try:
                    st.session_state["project_file_records"] = _project_files(project["id"])
                except Exception:
                    st.session_state["project_file_records"] = []
                st.switch_page("app.py")
        with a2:
            if st.button("Archive", key=f"archive_project_{project['id']}"):
                try:
                    archive_project(project["id"], True)
                    st.success("Project archived.")
                    st.rerun()
                except Exception as error:
                    st.error(f"Project could not be archived: {error}")
