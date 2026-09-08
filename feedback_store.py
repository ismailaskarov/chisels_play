import hashlib
import json
import os
from difflib import SequenceMatcher
from pathlib import Path

import requests

LOCAL_FEEDBACK_FILE = Path("data") / "match_feedback.json"

def get_supabase_config():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if url and key:
        return url.rstrip("/"), key
    try:
        import streamlit as st
        url = st.secrets.get("SUPABASE_URL")
        key = st.secrets.get("SUPABASE_KEY")
        if url and key:
            return str(url).rstrip("/"), str(key)
    except Exception:
        pass
    return None, None

def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip().lower().replace("\n", " ")

def make_source_key(ai_part):
    raw = "|".join([
        normalize_text(ai_part.get("name")),
        normalize_text(ai_part.get("material")),
        normalize_text(ai_part.get("component_type")),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def load_local_feedback():
    if not LOCAL_FEEDBACK_FILE.exists():
        return []
    try:
        with open(LOCAL_FEEDBACK_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, list):
            return data
    except Exception as error:
        print("Could not read feedback:", error)
    return []

def save_local_feedback(record):
    records = load_local_feedback()
    existing_index = None
    for index, existing in enumerate(records):
        if existing.get("source_key") == record["source_key"]:
            existing_index = index
            break
    if existing_index is None:
        records.append(record)
    else:
        records[existing_index] = record
    LOCAL_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_FEEDBACK_FILE, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)
    print("Saved local match feedback:", record["source_name"], "->", record["catalog_name"])

def save_supabase_feedback(record, url, key):
    endpoint = f"{url}/rest/v1/match_feedback?on_conflict=source_key"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    response = requests.post(endpoint, headers=headers, json=record, timeout=15)
    response.raise_for_status()

def load_supabase_feedback(url, key):
    endpoint = f"{url}/rest/v1/match_feedback?select=*"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    response = requests.get(endpoint, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()

def save_match_feedback(ai_part, selected_item):
    if not ai_part or not selected_item:
        return
    record = {
        "source_key": make_source_key(ai_part),
        "source_name": str(ai_part.get("name") or ""),
        "source_material": str(ai_part.get("material") or ""),
        "component_type": str(ai_part.get("component_type") or ""),
        "catalog_name": str(selected_item.get("name") or ""),
        "catalog_category": str(selected_item.get("category") or ""),
        "catalog_unit": str(selected_item.get("unit") or ""),
        "catalog_price": float(selected_item.get("price") or 0),
    }
    url, key = get_supabase_config()
    if url and key:
        try:
            save_supabase_feedback(record, url, key)
            return
        except Exception as error:
            print("Supabase feedback save failed:", error)
    save_local_feedback(record)

def load_match_feedback():
    url, key = get_supabase_config()
    if url and key:
        try:
            return load_supabase_feedback(url, key)
        except Exception as error:
            print("Supabase feedback load failed:", error)
    return load_local_feedback()

def similarity(a, b):
    a = normalize_text(a)
    b = normalize_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()

def find_learned_catalog_item(ai_part, price_items, threshold=0.84):
    feedback = load_match_feedback()
    if not feedback:
        return None, 0.0, ""

    part_name = normalize_text(ai_part.get("name"))
    part_material = normalize_text(ai_part.get("material"))
    part_type = normalize_text(ai_part.get("component_type"))

    best_record = None
    best_score = 0.0

    for record in feedback:
        record_type = normalize_text(record.get("component_type"))
        if part_type and record_type and part_type != record_type:
            continue

        name_score = similarity(part_name, record.get("source_name"))
        material_score = similarity(part_material, record.get("source_material"))
        type_score = similarity(part_type, record_type)

        score = name_score * 0.75 + material_score * 0.15 + type_score * 0.10
        if score > best_score:
            best_score = score
            best_record = record

    if best_record is None or best_score < threshold:
        return None, best_score, ""

    for item in price_items:
        same_name = normalize_text(item.get("name")) == normalize_text(best_record.get("catalog_name"))
        same_category = normalize_text(item.get("category")) == normalize_text(best_record.get("catalog_category"))
        same_unit = normalize_text(item.get("unit")) == normalize_text(best_record.get("catalog_unit"))

        if same_name and same_category and same_unit:
            reason = (
                "Learned from a previous manual catalog correction. "
                f"Similarity: {best_score:.0%}."
            )
            return item, best_score, reason

    return None, best_score, ""
