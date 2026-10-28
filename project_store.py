import json
import os
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests

from feedback_store import get_supabase_config, supabase_get_all, supabase_headers


LOCAL_PROJECTS_FILE = Path(__file__).resolve().parent / "data" / "projects.json"
SUPABASE_TABLE = "projects"
PROJECT_STATUSES = ("Draft", "In review", "Quoted", "Approved", "Lost")
_file_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # NumPy/pandas scalar values sometimes appear in Streamlit editor data.
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return str(value)


def _read_local():
    if not LOCAL_PROJECTS_FILE.exists():
        return []
    try:
        text = LOCAL_PROJECTS_FILE.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("Could not open local projects file.") from error
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise RuntimeError("Local projects file is damaged.") from error
    return payload if isinstance(payload, list) else []


def _write_local(rows):
    LOCAL_PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, indent=2)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(LOCAL_PROJECTS_FILE.parent),
        delete=False,
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    os.replace(temp_path, LOCAL_PROJECTS_FILE)


def _local_upsert(row):
    with _file_lock:
        rows = _read_local()
        rows = [item for item in rows if item.get("id") != row["id"]]
        rows.append(row)
        _write_local(rows)


def _supabase_upsert(row, url, key):
    headers = supabase_headers(key)
    headers["Prefer"] = "resolution=merge-duplicates"
    response = requests.post(
        f"{url}/rest/v1/{SUPABASE_TABLE}?on_conflict=id",
        headers=headers,
        json=row,
        timeout=30,
    )
    response.raise_for_status()


def project_storage_name():
    url, key = get_supabase_config()
    return "Supabase" if url and key else "local file"


def list_projects(include_archived=False):
    url, key = get_supabase_config()
    if url and key:
        rows = supabase_get_all(url, key, SUPABASE_TABLE, "updated_at.desc,id.asc")
    else:
        rows = sorted(
            _read_local(),
            key=lambda row: str(row.get("updated_at") or ""),
            reverse=True,
        )
    if include_archived:
        return rows
    return [row for row in rows if not row.get("archived", False)]


def get_project(project_id):
    if not project_id:
        return None
    url, key = get_supabase_config()
    if url and key:
        response = requests.get(
            f"{url}/rest/v1/{SUPABASE_TABLE}",
            headers=supabase_headers(key),
            params={"id": f"eq.{project_id}", "select": "*", "limit": 1},
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json()
        return rows[0] if rows else None
    return next((row for row in _read_local() if row.get("id") == project_id), None)


def save_project(
    snapshot,
    project_id=None,
    project_name="",
    client_name="",
    status="Draft",
    furniture_name="",
    quote_number="",
    calculated_price=0.0,
    final_client_price=0.0,
):
    project_name = str(project_name or "").strip()
    if not project_name:
        raise ValueError("Project name is required before saving.")
    if status not in PROJECT_STATUSES:
        status = "Draft"

    existing = get_project(project_id) if project_id else None
    created_at = (existing or {}).get("created_at") or _now()
    row = {
        "id": project_id or uuid.uuid4().hex,
        "project_name": project_name,
        "client_name": str(client_name or "").strip(),
        "status": status,
        "furniture_name": str(furniture_name or "").strip(),
        "quote_number": str(quote_number or "").strip(),
        "calculated_price": float(calculated_price or 0),
        "final_client_price": float(final_client_price or 0),
        "snapshot": _json_safe(snapshot or {}),
        "archived": False,
        "created_at": created_at,
        "updated_at": _now(),
    }

    url, key = get_supabase_config()
    if url and key:
        _supabase_upsert(row, url, key)
    else:
        _local_upsert(row)
    return row


def archive_project(project_id, archived=True):
    current = get_project(project_id)
    if current is None:
        raise ValueError("Project no longer exists.")
    changes = {"archived": bool(archived), "updated_at": _now()}
    url, key = get_supabase_config()
    if url and key:
        response = requests.patch(
            f"{url}/rest/v1/{SUPABASE_TABLE}",
            headers=supabase_headers(key),
            params={"id": f"eq.{project_id}"},
            json=changes,
            timeout=15,
        )
        response.raise_for_status()
        return
    current.update(changes)
    _local_upsert(current)
