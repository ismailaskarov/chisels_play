import hashlib
import json
import mimetypes
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import requests

from feedback_store import get_supabase_config, supabase_headers


BUCKET = "project-files"
TABLE = "project_files"
LOCAL_ROOT = Path(__file__).resolve().parent / "data" / "project_files"
LOCAL_INDEX = LOCAL_ROOT / "index.json"
_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value):
    name = Path(str(value or "file")).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._") or "file"
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", Path(name).suffix)[:12]
    return f"{stem}{suffix}"


def _content_type(name, supplied=""):
    return str(supplied or mimetypes.guess_type(str(name))[0] or "application/octet-stream")


def _read_local_index():
    if not LOCAL_INDEX.exists():
        return []
    try:
        payload = json.loads(LOCAL_INDEX.read_text(encoding="utf-8") or "[]")
    except (OSError, ValueError) as error:
        raise RuntimeError("Local project-file index could not be read.") from error
    return payload if isinstance(payload, list) else []


def _write_local_index(rows):
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(LOCAL_ROOT), delete=False, suffix=".tmp"
    ) as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
        temp_path = Path(handle.name)
    os.replace(temp_path, LOCAL_INDEX)


def project_file_storage_name():
    url, key = get_supabase_config()
    return "Supabase Storage" if url and key else "local file"


def list_project_files(project_id=None):
    url, key = get_supabase_config()
    if url and key:
        params = {
            "select": "*",
            "order": "project_id.asc,primary_source.desc,created_at.asc,filename.asc",
        }
        if project_id:
            params["project_id"] = f"eq.{project_id}"
        response = requests.get(
            f"{url}/rest/v1/{TABLE}",
            headers=supabase_headers(key),
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    rows = _read_local_index()
    if project_id:
        return [row for row in rows if row.get("project_id") == project_id]
    return rows


def _upload_supabase(url, key, storage_path, content_type, data):
    headers = supabase_headers(key)
    headers["Content-Type"] = content_type
    headers["x-upsert"] = "true"
    response = requests.post(
        f"{url}/storage/v1/object/{BUCKET}/{quote(storage_path, safe='/')}",
        headers=headers,
        data=data,
        timeout=60,
    )
    response.raise_for_status()


def _insert_metadata(url, key, row):
    headers = supabase_headers(key)
    headers["Prefer"] = "resolution=merge-duplicates"
    response = requests.post(
        f"{url}/rest/v1/{TABLE}?on_conflict=project_id,sha256",
        headers=headers,
        json=row,
        timeout=30,
    )
    response.raise_for_status()


def _patch_primary_flags(url, key, project_id, primary_sha):
    response = requests.patch(
        f"{url}/rest/v1/{TABLE}",
        headers=supabase_headers(key),
        params={"project_id": f"eq.{project_id}"},
        json={"primary_source": False, "updated_at": _now()},
        timeout=15,
    )
    response.raise_for_status()
    if primary_sha:
        response = requests.patch(
            f"{url}/rest/v1/{TABLE}",
            headers=supabase_headers(key),
            params={"project_id": f"eq.{project_id}", "sha256": f"eq.{primary_sha}"},
            json={"primary_source": True, "updated_at": _now()},
            timeout=15,
        )
        response.raise_for_status()


def save_project_files(project_id, files):
    """Persist uploaded source files. Existing identical files are reused."""
    if not project_id:
        raise ValueError("Project must be saved before its files can be stored.")
    files = list(files or [])
    if not files:
        return list_project_files(project_id)

    prepared = []
    for file in files:
        data = file.get("bytes")
        if not isinstance(data, (bytes, bytearray)) or not data:
            continue
        display_name = Path(str(file.get("name") or file.get("original_name") or "file")).name
        safe_name = _safe_name(display_name)
        sha256 = str(file.get("sha256") or hashlib.sha256(data).hexdigest())
        prepared.append({
            "filename": display_name,
            "safe_name": safe_name,
            "content_type": _content_type(display_name, file.get("content_type")),
            "size_bytes": len(data),
            "sha256": sha256,
            "primary_source": bool(file.get("primary_source")),
            "bytes": bytes(data),
        })

    if not prepared:
        return list_project_files(project_id)

    primary_sha = next((item["sha256"] for item in prepared if item["primary_source"]), None)
    url, key = get_supabase_config()
    if url and key:
        existing = {row.get("sha256"): row for row in list_project_files(project_id)}
        now = _now()
        for item in prepared:
            storage_path = (
                existing.get(item["sha256"], {}).get("storage_path")
                or f'{project_id}/{item["sha256"][:16]}_{item["safe_name"]}'
            )
            if item["sha256"] not in existing:
                _upload_supabase(
                    url, key, storage_path, item["content_type"], item["bytes"]
                )
            row = {
                "id": existing.get(item["sha256"], {}).get("id") or uuid.uuid4().hex,
                "project_id": project_id,
                "filename": item["filename"],
                "storage_path": storage_path,
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
                "primary_source": item["primary_source"],
                "created_at": existing.get(item["sha256"], {}).get("created_at") or now,
                "updated_at": now,
            }
            _insert_metadata(url, key, row)
        _patch_primary_flags(url, key, project_id, primary_sha)
        return list_project_files(project_id)

    with _lock:
        rows = _read_local_index()
        existing = {
            row.get("sha256"): row
            for row in rows
            if row.get("project_id") == project_id
        }
        now = _now()
        project_dir = LOCAL_ROOT / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            if row.get("project_id") == project_id:
                row["primary_source"] = False
        for item in prepared:
            storage_path = (
                existing.get(item["sha256"], {}).get("storage_path")
                or str(Path(project_id) / f'{item["sha256"][:16]}_{item["safe_name"]}')
            )
            target = LOCAL_ROOT / storage_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(item["bytes"])
            row = {
                "id": existing.get(item["sha256"], {}).get("id") or uuid.uuid4().hex,
                "project_id": project_id,
                "filename": item["filename"],
                "storage_path": storage_path,
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
                "primary_source": item["sha256"] == primary_sha,
                "created_at": existing.get(item["sha256"], {}).get("created_at") or now,
                "updated_at": now,
            }
            rows = [
                old for old in rows
                if not (
                    old.get("project_id") == project_id
                    and old.get("sha256") == item["sha256"]
                )
            ]
            rows.append(row)
        _write_local_index(rows)
    return list_project_files(project_id)


def read_project_file(file_record):
    path = str((file_record or {}).get("storage_path") or "")
    if not path:
        raise ValueError("Stored file has no storage path.")
    url, key = get_supabase_config()
    if url and key:
        response = requests.get(
            f"{url}/storage/v1/object/{BUCKET}/{quote(path, safe='/')}",
            headers=supabase_headers(key),
            timeout=60,
        )
        response.raise_for_status()
        return response.content
    target = LOCAL_ROOT / path
    if not target.exists():
        raise FileNotFoundError("Stored project file no longer exists.")
    return target.read_bytes()


def delete_project_files(project_id):
    records = list_project_files(project_id)
    if not records:
        return 0
    url, key = get_supabase_config()
    if url and key:
        prefixes = [row.get("storage_path") for row in records if row.get("storage_path")]
        if prefixes:
            response = requests.delete(
                f"{url}/storage/v1/object/{BUCKET}",
                headers=supabase_headers(key),
                json={"prefixes": prefixes},
                timeout=60,
            )
            response.raise_for_status()
        response = requests.delete(
            f"{url}/rest/v1/{TABLE}",
            headers=supabase_headers(key),
            params={"project_id": f"eq.{project_id}"},
            timeout=30,
        )
        response.raise_for_status()
        return len(records)

    with _lock:
        rows = _read_local_index()
        for row in records:
            target = LOCAL_ROOT / str(row.get("storage_path") or "")
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        rows = [row for row in rows if row.get("project_id") != project_id]
        _write_local_index(rows)
    return len(records)
