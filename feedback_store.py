import hashlib
import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import requests

LOCAL_FEEDBACK_FILE = Path(__file__).resolve().parent / "data" / "match_feedback.json"

_local_file_lock = threading.Lock()

STOPWORDS = {
    "a", "an", "and", "the", "for", "of", "with", "to", "in", "on", "or",
    "ir", "su", "be", "is", "per",
}


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
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def tokens(value):
    return {word for word in normalize_text(value).split() if word not in STOPWORDS}


def similarity(a, b):
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    sequence_score = SequenceMatcher(None, a_norm, b_norm).ratio()

    a_tokens = tokens(a)
    b_tokens = tokens(b)
    token_score = 0.0
    if a_tokens and b_tokens:
        token_score = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
        sorted_score = SequenceMatcher(
            None, " ".join(sorted(a_tokens)), " ".join(sorted(b_tokens))
        ).ratio()
        token_score = max(token_score, sorted_score)

    return max(sequence_score, token_score)


def part_similarity(ai_part, record):
    name_score = similarity(ai_part.get("name"), record.get("source_name"))
    material_score = similarity(ai_part.get("material"), record.get("source_material"))

    part_type = normalize_text(ai_part.get("component_type"))
    record_type = normalize_text(record.get("component_type"))
    if part_type and record_type:
        type_score = 1.0 if part_type == record_type else 0.0
    else:
        type_score = 0.5

    has_material = bool(normalize_text(ai_part.get("material"))) and bool(
        normalize_text(record.get("source_material"))
    )
    if has_material:
        return name_score * 0.45 + material_score * 0.45 + type_score * 0.10
    return name_score * 0.85 + type_score * 0.15


def make_source_key(ai_part):
    raw = "|".join([
        normalize_text(ai_part.get("name")),
        normalize_text(ai_part.get("material")),
        normalize_text(ai_part.get("component_type")),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_record(ai_part, selected_item):
    return {
        "source_key": make_source_key(ai_part),
        "source_name": str(ai_part.get("name") or ""),
        "source_material": str(ai_part.get("material") or ""),
        "component_type": str(ai_part.get("component_type") or ""),
        "catalog_name": str(selected_item.get("name") or ""),
        "catalog_category": str(selected_item.get("category") or ""),
        "catalog_unit": str(selected_item.get("unit") or ""),
        "catalog_price": float(selected_item.get("price") or 0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def find_price_item(record, price_items):
    for item in price_items:
        if (
            normalize_text(item.get("name")) == normalize_text(record.get("catalog_name"))
            and normalize_text(item.get("category")) == normalize_text(record.get("catalog_category"))
            and normalize_text(item.get("unit")) == normalize_text(record.get("catalog_unit"))
        ):
            return item
    return None


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
    with _local_file_lock:
        records = [
            existing for existing in load_local_feedback()
            if existing.get("source_key") != record["source_key"]
        ]
        records.append(record)

        LOCAL_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = LOCAL_FEEDBACK_FILE.with_suffix(".json.tmp")
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)
        os.replace(temp_file, LOCAL_FEEDBACK_FILE)

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
    print("Saved Supabase match feedback:", record["source_name"], "->", record["catalog_name"])


def load_supabase_feedback(url, key):
    endpoint = f"{url}/rest/v1/match_feedback?select=*"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    response = requests.get(endpoint, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def save_match_feedback(ai_part, selected_item):
    if not ai_part or not selected_item:
        return False
    record = build_record(ai_part, selected_item)

    url, key = get_supabase_config()
    if url and key:
        try:
            save_supabase_feedback(record, url, key)
            return True
        except Exception as error:
            print("Supabase feedback save failed, saving locally:", error)

    try:
        save_local_feedback(record)
        return True
    except Exception as error:
        print("Local feedback save failed:", error)
        return False


def load_match_feedback():
    url, key = get_supabase_config()
    if url and key:
        try:
            return load_supabase_feedback(url, key)
        except Exception as error:
            print("Supabase feedback load failed:", error)
    return load_local_feedback()


def find_learned_catalog_item(ai_part, price_items, feedback=None, threshold=0.8):
    if feedback is None:
        feedback = load_match_feedback()
    if not feedback:
        return None, 0.0, ""

    best_record = None
    best_score = 0.0
    for record in feedback:
        score = part_similarity(ai_part, record)
        if score > best_score + 0.01 or (
            best_record is not None
            and abs(score - best_score) <= 0.01
            and str(record.get("updated_at") or "") > str(best_record.get("updated_at") or "")
        ):
            best_score = max(score, best_score)
            best_record = record

    if best_record is None or best_score < threshold:
        return None, best_score, ""

    item = find_price_item(best_record, price_items)
    if item is None:
        return None, best_score, ""

    reason = (
        "Learned from a previous estimator's choice for "
        f"\"{best_record.get('source_name')}\" "
        f"({best_record.get('source_material') or 'no material'}). "
        f"Similarity: {best_score:.0%}."
    )
    return item, best_score, reason


def relevant_feedback_examples(parts, feedback, price_items, limit=30, min_score=0.35):
    scored = []
    for record in feedback or []:
        if find_price_item(record, price_items) is None:
            continue
        score = max((part_similarity(part, record) for part in parts), default=0.0)
        if score >= min_score:
            scored.append((score, record))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [record for _, record in scored[:limit]]
