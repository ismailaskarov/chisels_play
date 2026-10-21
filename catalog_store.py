import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from feedback_store import get_supabase_config, normalize_text, supabase_headers

LOCAL_CATALOG_FILE = Path(__file__).resolve().parent / "data" / "catalog_items.json"

SUPABASE_TABLE = "catalog_items"

_local_file_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def catalog_item_key(item):
    return (
        normalize_text(item.get("name")),
        normalize_text(item.get("category")),
        normalize_text(item.get("unit")),
    )


def set_aside_unreadable_catalog():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = LOCAL_CATALOG_FILE.with_name(f"catalog_items.unreadable-{stamp}.json")
    try:
        os.replace(LOCAL_CATALOG_FILE, backup)
        print("Unreadable catalog file kept as:", backup)
    except Exception as error:
        print("Could not set aside unreadable catalog file:", error)


def read_local_items():
    if not LOCAL_CATALOG_FILE.exists():
        return []
    try:
        with open(LOCAL_CATALOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except ValueError as error:
        print("Catalog file is damaged:", error)
        set_aside_unreadable_catalog()
        return []
    except OSError as error:
        print("Could not open catalog file:", error)
        return None
    if isinstance(data, list):
        return data
    print("Catalog file does not contain a list.")
    set_aside_unreadable_catalog()
    return []


def write_local_items(items):
    LOCAL_CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = LOCAL_CATALOG_FILE.with_suffix(".json.tmp")
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)
    os.replace(temp_file, LOCAL_CATALOG_FILE)


def add_local_item(item):
    with _local_file_lock:
        items = read_local_items()
        if items is None:
            raise RuntimeError("catalog file could not be opened, not overwriting it")
        items.append(item)
        write_local_items(items)


def load_supabase_items(url, key):
    response = requests.get(
        f"{url}/rest/v1/{SUPABASE_TABLE}",
        headers=supabase_headers(key),
        params={"select": "*"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def add_supabase_item(item, url, key):
    response = requests.post(
        f"{url}/rest/v1/{SUPABASE_TABLE}",
        headers=supabase_headers(key),
        json=item,
        timeout=15,
    )
    response.raise_for_status()


def clean_item(item):
    try:
        price = float(item.get("price"))
    except (TypeError, ValueError):
        return None
    if not str(item.get("name") or "").strip() or price <= 0:
        return None
    return {**item, "price": price}


def load_catalog_additions():
    items = {}
    for item in read_local_items() or []:
        items[item.get("id")] = item
    url, key = get_supabase_config()
    if url and key:
        try:
            for item in load_supabase_items(url, key):
                items[item.get("id")] = item
        except Exception as error:
            print("Supabase catalog load failed:", error)
    cleaned = []
    for item in items.values():
        if item.get("active") is False:
            continue
        item = clean_item(item)
        if item is not None:
            cleaned.append(item)
    return cleaned


def catalog_storage_name():
    url, key = get_supabase_config()
    if url and key:
        return "Supabase"
    return "local file"


def add_catalog_item(
    name,
    category,
    unit,
    price,
    source_type,
    source_url="",
    material_type="",
    component_type="",
    thickness_mm=None,
    brand_model="",
    added_for_part="",
):
    name = str(name or "").strip()
    category = str(category or "").strip()
    unit = str(unit or "").strip()
    if not name or not category or not unit:
        raise ValueError("Name, category and unit are required.")
    try:
        price = float(price)
    except (TypeError, ValueError):
        raise ValueError("Price must be a number.")
    if price <= 0:
        raise ValueError("Price must be greater than 0.")

    created_at = now_iso()
    item = {
        "id": uuid.uuid4().hex,
        "name": name,
        "category": category,
        "unit": unit,
        "price": round(price, 2),
        "source_type": source_type,
        "source_url": str(source_url or "").strip(),
        "material_type": str(material_type or "").strip(),
        "component_type": str(component_type or "").strip(),
        "thickness_mm": thickness_mm if thickness_mm else None,
        "brand_model": str(brand_model or "").strip(),
        "added_for_part": str(added_for_part or "").strip(),
        "active": True,
        "created_at": created_at,
        "updated_at": created_at,
    }

    url, key = get_supabase_config()
    if url and key:
        try:
            add_supabase_item(item, url, key)
            print("Added catalog item to Supabase:", name)
            return item
        except Exception as error:
            print("Supabase catalog save failed, saving locally:", error)

    add_local_item(item)
    print("Added catalog item locally:", name)
    return item
