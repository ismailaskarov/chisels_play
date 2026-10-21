import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests

from feedback_store import get_supabase_config, normalize_text, supabase_headers


DEFAULT_CATALOG_ADDITIONS = Path(__file__).resolve().parent / "data" / "catalog_additions.json"

SUPABASE_TABLE = "catalog_items"

OPTIONAL_KEYS = (
    "source",
    "source_url",
    "source_type",
    "thickness_mm",
    "market_match",
)

_file_lock = threading.Lock()


def _clean_text(value):
    return " ".join(str(value or "").strip().split())


def _identity(item):
    return (
        normalize_text(item.get("name")),
        normalize_text(item.get("category")),
        normalize_text(item.get("unit")),
    )


def _now():
    return datetime.now(timezone.utc).isoformat()


def _set_aside_unreadable(path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.stem}.unreadable-{stamp}.json")
    try:
        os.replace(path, backup)
        print("Unreadable catalog additions file kept as:", backup)
    except Exception as error:
        print("Could not set aside unreadable catalog additions file:", error)


def _read_raw_items(path):
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print("Could not open catalog additions file:", error)
        return None
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except ValueError as error:
        print("Catalog additions file is damaged:", error)
        _set_aside_unreadable(path)
        return []
    if not isinstance(payload, list):
        print("Catalog additions file does not contain a list.")
        _set_aside_unreadable(path)
        return []
    return payload


def _clean_item(raw):
    if not isinstance(raw, dict):
        return None
    try:
        price = float(raw.get("price"))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    name = _clean_text(raw.get("name"))
    category = _clean_text(raw.get("category"))
    unit = _clean_text(raw.get("unit"))
    if not name or not category or not unit:
        return None

    item = dict(raw)
    item.update({
        "name": name,
        "category": category,
        "unit": unit,
        "price": price,
        "catalog_addition": True,
    })
    return item


def _load_supabase_items(url, key):
    response = requests.get(
        f"{url}/rest/v1/{SUPABASE_TABLE}",
        headers=supabase_headers(key),
        params={"select": "*"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _add_supabase_item(item, url, key):
    row = {k: v for k, v in item.items() if k != "catalog_addition"}
    response = requests.post(
        f"{url}/rest/v1/{SUPABASE_TABLE}",
        headers=supabase_headers(key),
        json=row,
        timeout=15,
    )
    response.raise_for_status()


def _write_items(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def load_catalog_additions(path=DEFAULT_CATALOG_ADDITIONS):
    items = {}

    for raw in _read_raw_items(Path(path)) or []:
        item = _clean_item(raw)
        if item is not None:
            items[_identity(item)] = item

    url, key = get_supabase_config()
    if url and key:
        try:
            for raw in _load_supabase_items(url, key):
                if isinstance(raw, dict) and raw.get("active") is False:
                    continue
                item = _clean_item(raw)
                if item is not None:
                    items[_identity(item)] = item
        except Exception as error:
            print("Supabase catalog load failed:", error)

    return list(items.values())


def catalog_storage_name():
    url, key = get_supabase_config()
    if url and key:
        return "Supabase"
    return "local file"


def catalog_item_exists(existing_items, candidate):
    candidate_identity = _identity(candidate)
    return any(_identity(item) == candidate_identity for item in existing_items)


def add_catalog_item(item, path=DEFAULT_CATALOG_ADDITIONS):
    path = Path(path)

    name = _clean_text(item.get("name"))
    category = _clean_text(item.get("category"))
    unit = _clean_text(item.get("unit"))

    try:
        price = float(item.get("price"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Catalog price must be a number.") from exc

    if not name:
        raise ValueError("Catalog item name is required.")
    if not category:
        raise ValueError("Catalog category is required.")
    if not unit:
        raise ValueError("Catalog unit is required.")
    if price <= 0:
        raise ValueError("Catalog price must be greater than zero.")

    created_at = _now()
    clean_item = {
        "id": uuid.uuid4().hex,
        "name": name,
        "category": category,
        "unit": unit,
        "price": price,
        "active": True,
        "created_at": created_at,
        "updated_at": created_at,
        "catalog_addition": True,
    }

    for optional_key in OPTIONAL_KEYS:
        value = item.get(optional_key)
        if value not in (None, ""):
            clean_item[optional_key] = value

    identity = _identity(clean_item)

    for existing in load_catalog_additions(path):
        if _identity(existing) == identity:
            return False, existing

    url, key = get_supabase_config()
    if url and key:
        try:
            _add_supabase_item(clean_item, url, key)
            print("Added catalog item to Supabase:", name)
            return True, clean_item
        except Exception as error:
            print("Supabase catalog save failed, saving locally:", error)

    with _file_lock:
        items = _read_raw_items(path)
        if items is None:
            raise RuntimeError("Catalog additions file could not be opened, so it was not overwritten.")
        for existing in items:
            if isinstance(existing, dict) and _identity(existing) == identity:
                return False, existing
        items.append(clean_item)
        _write_items(path, items)

    print("Added catalog item locally:", name)
    return True, clean_item
