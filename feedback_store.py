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


def part_key_text(ai_part):
    return "|".join([
        normalize_text(ai_part.get("name")),
        normalize_text(ai_part.get("material")),
        normalize_text(ai_part.get("component_type")),
    ])


def catalog_key_text(name, category, unit):
    return "|".join([normalize_text(name), normalize_text(category), normalize_text(unit)])


def item_catalog_key(item):
    return catalog_key_text(item.get("name"), item.get("category"), item.get("unit"))


def record_catalog_key(record):
    return catalog_key_text(
        record.get("catalog_name"), record.get("catalog_category"), record.get("catalog_unit")
    )


def make_source_key(ai_part, selected_item):
    raw = part_key_text(ai_part) + "||" + item_catalog_key(selected_item)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_votes(record):
    value = record.get("times_chosen")
    if value is None:
        return 1
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 1


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_record(ai_part, selected_item, ai_item=None):
    return {
        "source_key": make_source_key(ai_part, selected_item),
        "source_name": str(ai_part.get("name") or ""),
        "source_material": str(ai_part.get("material") or ""),
        "component_type": str(ai_part.get("component_type") or ""),
        "catalog_name": str(selected_item.get("name") or ""),
        "catalog_category": str(selected_item.get("category") or ""),
        "catalog_unit": str(selected_item.get("unit") or ""),
        "catalog_price": float(selected_item.get("price") or 0),
        "ai_catalog_name": str(ai_item.get("name") or "") if ai_item else "NO MATCH",
    }


def find_price_item(record, price_items):
    wanted = record_catalog_key(record)
    for item in price_items:
        if item_catalog_key(item) == wanted:
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


def write_local_feedback(records):
    LOCAL_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = LOCAL_FEEDBACK_FILE.with_suffix(".json.tmp")
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)
    os.replace(temp_file, LOCAL_FEEDBACK_FILE)


def apply_vote(existing, new_record, delta):
    if existing is None:
        if new_record is None or delta <= 0:
            return None
        record = dict(new_record)
        record["times_chosen"] = delta
        record["created_at"] = now_iso()
        record["updated_at"] = record["created_at"]
        return record

    record = dict(existing)
    if new_record is not None:
        record.update(new_record)
    record["times_chosen"] = max(0, record_votes(existing) + delta)
    if delta > 0:
        record["updated_at"] = now_iso()
    return record


def vote_local(source_key, new_record, delta):
    with _local_file_lock:
        records = load_local_feedback()
        existing = next((r for r in records if r.get("source_key") == source_key), None)
        record = apply_vote(existing, new_record, delta)
        if record is None:
            return None
        records = [r for r in records if r.get("source_key") != source_key]
        records.append(record)
        write_local_feedback(records)
    return record


def delete_local(source_key):
    with _local_file_lock:
        records = load_local_feedback()
        remaining = [r for r in records if r.get("source_key") != source_key]
        if len(remaining) != len(records):
            write_local_feedback(remaining)


def supabase_headers(key):
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def get_supabase_record(source_key, url, key):
    response = requests.get(
        f"{url}/rest/v1/match_feedback",
        headers=supabase_headers(key),
        params={"source_key": f"eq.{source_key}", "select": "*"},
        timeout=15,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def put_supabase_record(record, url, key):
    headers = supabase_headers(key)
    headers["Prefer"] = "resolution=merge-duplicates"
    response = requests.post(
        f"{url}/rest/v1/match_feedback?on_conflict=source_key",
        headers=headers,
        json=record,
        timeout=15,
    )
    response.raise_for_status()


def vote_supabase(source_key, new_record, delta, url, key):
    record = apply_vote(get_supabase_record(source_key, url, key), new_record, delta)
    if record is None:
        return None
    put_supabase_record(record, url, key)
    return record


def load_supabase_feedback(url, key):
    response = requests.get(
        f"{url}/rest/v1/match_feedback?select=*", headers=supabase_headers(key), timeout=15
    )
    response.raise_for_status()
    return response.json()


def vote(source_key, new_record, delta):
    url, key = get_supabase_config()
    if url and key:
        try:
            return vote_supabase(source_key, new_record, delta, url, key)
        except Exception as error:
            print("Supabase feedback save failed, saving locally:", error)
    try:
        return vote_local(source_key, new_record, delta)
    except Exception as error:
        print("Local feedback save failed:", error)
        return None


def save_match_feedback(ai_part, selected_item, ai_item=None):
    if not ai_part or not selected_item:
        return None
    new_record = build_record(ai_part, selected_item, ai_item)
    record = vote(new_record["source_key"], new_record, 1)
    if record is None:
        return None
    print(
        "Saved match feedback:", record["source_name"], "->", record["catalog_name"],
        f"({record['times_chosen']} votes)",
    )
    return record["source_key"]


def remove_match_vote(source_key):
    if not source_key:
        return False
    return vote(source_key, None, -1) is not None


def delete_match_feedback(source_key):
    url, key = get_supabase_config()
    deleted = False
    if url and key:
        try:
            response = requests.delete(
                f"{url}/rest/v1/match_feedback",
                headers=supabase_headers(key),
                params={"source_key": f"eq.{source_key}"},
                timeout=15,
            )
            response.raise_for_status()
            deleted = True
        except Exception as error:
            print("Supabase feedback delete failed:", error)
    try:
        delete_local(source_key)
        deleted = True
    except Exception as error:
        print("Local feedback delete failed:", error)
    return deleted


def load_match_feedback():
    records = {}
    for record in load_local_feedback():
        records[record.get("source_key")] = record
    url, key = get_supabase_config()
    if url and key:
        try:
            for record in load_supabase_feedback(url, key):
                records[record.get("source_key")] = record
        except Exception as error:
            print("Supabase feedback load failed:", error)
    return list(records.values())


def feedback_storage_name():
    url, key = get_supabase_config()
    if url and key:
        return "Supabase"
    return "local file"


def find_learned_catalog_item(ai_part, price_items, feedback=None, threshold=0.8):
    if feedback is None:
        feedback = load_match_feedback()

    candidates = []
    for record in feedback or []:
        if record_votes(record) <= 0:
            continue
        score = part_similarity(ai_part, record)
        if score >= threshold:
            candidates.append((score, record))

    if not candidates:
        return None, 0.0, "", None

    best_score = max(score for score, _ in candidates)

    groups = {}
    for score, record in candidates:
        group = groups.setdefault(record_catalog_key(record), {
            "votes": 0, "weight": 0.0, "score": 0.0, "updated_at": "", "record": None,
        })
        group["votes"] += record_votes(record)
        group["weight"] += record_votes(record) * score
        group["updated_at"] = max(group["updated_at"], str(record.get("updated_at") or ""))
        if score > group["score"]:
            group["score"] = score
            group["record"] = record

    ranked = sorted(
        (group for group in groups.values() if find_price_item(group["record"], price_items)),
        key=lambda group: (round(group["weight"], 6), group["updated_at"]),
        reverse=True,
    )
    if not ranked:
        return None, best_score, "", None

    winner = ranked[0]
    record = winner["record"]
    item = find_price_item(record, price_items)

    times = "time" if winner["votes"] == 1 else "times"
    reason = (
        f"Previous estimators chose this {winner['votes']} {times} for "
        f"\"{record.get('source_name')}\" "
        f"({record.get('source_material') or 'no material'}). "
        f"Similarity: {winner['score']:.0%}."
    )
    others = [
        f"{group['record'].get('catalog_name')} ({group['votes']})"
        for group in ranked[1:]
    ]
    if others:
        reason += " Other choices: " + ", ".join(others) + "."

    return item, winner["score"], reason, record


def relevant_feedback_examples(parts, feedback, price_items, limit=30, min_score=0.35):
    scored = []
    for record in feedback or []:
        if record_votes(record) <= 0:
            continue
        if find_price_item(record, price_items) is None:
            continue
        score = max((part_similarity(part, record) for part in parts), default=0.0)
        if score >= min_score:
            scored.append((score, record_votes(record), record))

    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [record for _, _, record in scored[:limit]]
