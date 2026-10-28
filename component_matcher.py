import json
import re
import unicodedata
from difflib import SequenceMatcher
from dotenv import load_dotenv
from openai import OpenAI

from feedback_store import (
    find_learned_catalog_item,
    record_votes,
    relevant_feedback_examples,
    tokens,
)
from utils.match_safety import (
    ai_match_conflict,
    feedback_record_conflict,
    material_families,
    selection_conflict,
)


load_dotenv()

client = OpenAI()


# --------------------------------------------------
# UNIT NORMALIZATION
# --------------------------------------------------

def normalize_unit(unit):
    if not unit:
        return ""

    unit = str(unit).strip().lower()

    aliases = {
        "m²": "m2",
        "m^2": "m2",
        "sqm": "m2",

        "vnt": "pcs",
        "vnt.": "pcs",
        "pc": "pcs",
        "piece": "pcs",
        "pieces": "pcs",

        "val": "hour",
        "val.": "hour",
        "hours": "hour",
        "hrs": "hour",

        "fixed price": "fixed",

        "kompl": "set",
        "kompl.": "set",
        "komplektas": "set",
        "komplektai": "set",
    }

    return aliases.get(
        unit,
        unit,
    )


# --------------------------------------------------
# VALID PRICE ITEM
# --------------------------------------------------

def is_valid_price_item(item):
    category = str(
        item.get("category", "")
    ).strip()

    unit = str(
        item.get("unit", "")
    ).strip()

    price = item.get(
        "price",
        0,
    )

    if not item.get("name"):
        return False

    if not category:
        return False

    if not unit:
        return False

    if category.upper() == "REVIEW":
        return False

    if unit.upper() == "REVIEW":
        return False

    try:
        price = float(price)

    except (TypeError, ValueError):
        return False

    if price <= 0:
        return False

    return True


# --------------------------------------------------
# BUILD CLEAN CATALOG
# --------------------------------------------------

CATALOG_DETAIL_KEYS = (
    "material_type",
    "component_type",
    "brand_model",
    "thickness_mm",
    "keywords",
    "synonyms",
)


def build_catalog(price_items):
    catalog = []

    for original_index, item in enumerate(
        price_items
    ):
        if not is_valid_price_item(item):
            continue

        entry = {
            "catalog_index": len(catalog),
            "original_index": original_index,
            "name": str(item["name"]),
            "category": str(item["category"]),
            "unit": str(item["unit"]),
            "price": float(item["price"]),
        }
        for key in CATALOG_DETAIL_KEYS:
            if item.get(key) not in (None, ""):
                entry[key] = item[key]
        catalog.append(entry)

    return catalog



# --------------------------------------------------
# LOCAL CATALOG PREFILTER
# --------------------------------------------------

# The catalog is small enough for local Python ranking, but large enough that
# sending every row to OpenAI adds avoidable latency and tokens. These limits
# are intentionally conservative: each drawing part keeps a focused shortlist,
# while learned corrections and strong material/type matches are pinned.
PREFILTER_PER_PART = 12
PREFILTER_MAX_CATALOG = 60
PREFILTER_SAFETY_PER_SIGNAL = 3


def _search_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _search_tokens(*values):
    result = set()
    for value in values:
        result.update(_search_text(value).split())
    return {token for token in result if len(token) > 1}


def _item_search_values(item):
    return [
        item.get("name"),
        item.get("category"),
        item.get("unit"),
        item.get("material_type"),
        item.get("component_type"),
        item.get("brand_model"),
        item.get("keywords"),
        item.get("synonyms"),
        item.get("description"),
        item.get("allowed_use"),
    ]


def _candidate_score(part, item):
    part_name = _search_text(part.get("name"))
    item_name = _search_text(item.get("name"))
    part_tokens = _search_tokens(
        part.get("name"),
        part.get("material"),
        part.get("component_type"),
    )
    item_tokens = _search_tokens(*_item_search_values(item))

    overlap = len(part_tokens & item_tokens)
    score = overlap * 3.0

    if part_name and item_name:
        score += SequenceMatcher(None, part_name, item_name).ratio() * 3.0

    part_type = _search_text(part.get("component_type"))
    item_type = _search_text(item.get("component_type"))
    if part_type and item_type:
        if part_type == item_type:
            score += 8.0
        elif part_type in item_type or item_type in part_type:
            score += 4.0

    part_material = _search_tokens(part.get("material"))
    item_material = _search_tokens(
        item.get("material_type"),
        item.get("name"),
        item.get("keywords"),
        item.get("synonyms"),
    )
    if part_material and item_material:
        score += len(part_material & item_material) * 5.0

    expected_units = {
        "panel": {"m2"},
        "glass": {"m2"},
        "mirror": {"m2"},
        "profile": {"m"},
        "edge": {"m"},
        "led": {"m"},
        "hardware": {"pcs", "set"},
        "service": {"hour", "pcs", "set", "fixed"},
    }.get(part_type)
    if expected_units and normalize_unit(item.get("unit")) in expected_units:
        score += 2.5

    part_thickness = part.get("thickness_mm")
    item_thickness = item.get("thickness_mm")
    try:
        if part_thickness not in (None, "") and item_thickness not in (None, ""):
            difference = abs(float(part_thickness) - float(item_thickness))
            if difference <= 0.5:
                score += 5.0
            elif difference <= 2.0:
                score += 2.0
    except (TypeError, ValueError):
        pass

    return score


def _strong_material_match(part, item):
    part_material = _search_tokens(part.get("material"))
    if not part_material:
        return False
    item_material = _search_tokens(
        item.get("material_type"),
        item.get("name"),
        item.get("keywords"),
        item.get("synonyms"),
    )
    return bool(part_material & item_material)


def _strong_type_match(part, item):
    part_type = _search_text(part.get("component_type"))
    item_type = _search_text(item.get("component_type"))
    return bool(part_type and item_type and part_type == item_type)


def prefilter_catalog_for_parts(parts, price_items, feedback=None):
    """Return a compact catalog for the AI matcher without hiding UI choices.

    The full catalog still goes to the Streamlit selector. This function only
    reduces the payload sent to OpenAI. It always keeps relevant learned
    corrections, plus a few strong material/type safety candidates per part.
    """
    valid = [item for item in price_items if is_valid_price_item(item)]
    if not valid or not parts:
        return valid

    chosen = {}
    best_score = {}
    pinned = set()
    safety = set()

    for part in parts:
        ranked = sorted(
            ((_candidate_score(part, item), index, item) for index, item in enumerate(valid)),
            key=lambda row: row[0],
            reverse=True,
        )

        # Main shortlist for this component.
        for score, index, item in ranked[:PREFILTER_PER_PART]:
            chosen[index] = item
            best_score[index] = max(best_score.get(index, float("-inf")), score)

        # Safety net: keep a few of the strongest exact material/type candidates
        # even if name similarity is weak.
        material_rows = [row for row in ranked if _strong_material_match(part, row[2])]
        type_rows = [row for row in ranked if _strong_type_match(part, row[2])]
        for score, index, item in (
            material_rows[:PREFILTER_SAFETY_PER_SIGNAL]
            + type_rows[:PREFILTER_SAFETY_PER_SIGNAL]
        ):
            chosen[index] = item
            best_score[index] = max(best_score.get(index, float("-inf")), score + 100.0)
            safety.add(index)

    # Never filter out rows explicitly chosen by estimators for relevant
    # historical corrections. These are pinned above the global cap.
    if feedback:
        learned = relevant_feedback_examples(parts, feedback, price_items)
        learned_by_key = {
            (
                _search_text(record.get("catalog_name")),
                _search_text(record.get("catalog_category")),
                normalize_unit(record.get("catalog_unit")),
            ): record
            for record in learned
        }
        for index, item in enumerate(valid):
            key = (
                _search_text(item.get("name")),
                _search_text(item.get("category")),
                normalize_unit(item.get("unit")),
            )
            record = learned_by_key.get(key)
            if record is None:
                continue
            conflict = feedback_record_conflict(record, item)
            if conflict:
                print(
                    "[LEARNING SAFETY] Ignoring conflicting learned correction:",
                    record.get("source_name"), "->", item.get("name"),
                )
                continue
            chosen[index] = item
            best_score[index] = max(best_score.get(index, 0.0), 1000.0)
            pinned.add(index)

    ranked_union = sorted(
        chosen.items(),
        key=lambda pair: best_score.get(pair[0], 0.0),
        reverse=True,
    )

    # Learned rows are unconditionally preserved. Strong safety rows are next,
    # then the best remaining local candidates until the cap is reached.
    pinned_rows = [pair for pair in ranked_union if pair[0] in pinned]
    safety_rows = [
        pair for pair in ranked_union
        if pair[0] in safety and pair[0] not in pinned
    ]
    other_rows = [
        pair for pair in ranked_union
        if pair[0] not in pinned and pair[0] not in safety
    ]

    if len(ranked_union) > PREFILTER_MAX_CATALOG:
        keep = pinned_rows + safety_rows
        remaining = max(0, PREFILTER_MAX_CATALOG - len(keep))
        ranked_union = keep + other_rows[:remaining]

    filtered = [item for _, item in ranked_union]

    # Rough but useful payload comparison. Character count tracks prompt size
    # closely enough to verify whether this optimization is actually helping.
    full_chars = len(json.dumps(valid, ensure_ascii=False, default=str))
    filtered_chars = len(json.dumps(filtered, ensure_ascii=False, default=str))
    reduction = (1.0 - filtered_chars / full_chars) * 100 if full_chars else 0.0

    print(
        f"[MATCH PREFILTER] {len(valid)} -> {len(filtered)} catalog rows "
        f"for {len(parts)} drawing parts "
        f"({PREFILTER_PER_PART}/part, {len(pinned)} learned pinned, "
        f"{len(safety)} material/type safety rows)"
    )
    print(
        f"[MATCH PREFILTER] catalog payload chars: "
        f"{full_chars:,} -> {filtered_chars:,} ({reduction:.1f}% smaller)"
    )
    return filtered


# --------------------------------------------------
# DETERMINISTIC LOCAL MATCHING
# --------------------------------------------------

LOCAL_LEARNED_MIN_SCORE = 0.93


def _same_material_family(part, item):
    source = material_families(part.get("material"), part.get("name"))
    target = material_families(
        item.get("material_type"), item.get("name"), item.get("category"),
        item.get("keywords"), item.get("synonyms"),
    )
    return bool(source and target and source & target)


def _dimension_signature_from_text(value):
    text = _search_text(value).replace(" ", "")
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?!\d)", text)
    if not match:
        return None
    return tuple(sorted((round(float(match.group(1)), 2), round(float(match.group(2)), 2))))


def _part_dimension_signature(part):
    try:
        thickness = float(part.get("thickness_mm")) if part.get("thickness_mm") not in (None, "") else None
    except (TypeError, ValueError):
        thickness = None
    try:
        width = float(part.get("width_mm")) if part.get("width_mm") not in (None, "") else None
    except (TypeError, ValueError):
        width = None
    try:
        height = float(part.get("height_mm")) if part.get("height_mm") not in (None, "") else None
    except (TypeError, ValueError):
        height = None
    if thickness and width:
        return tuple(sorted((round(thickness, 2), round(width, 2))))
    if width and height:
        try:
            length = float(part.get("length_mm")) if part.get("length_mm") not in (None, "") else None
        except (TypeError, ValueError):
            length = None
        if length is None or max(width, height) < length * 0.75:
            return tuple(sorted((round(width, 2), round(height, 2))))
    return _dimension_signature_from_text(part.get("name"))


def _item_dimension_signature(item):
    for value in (item.get("name"), item.get("keywords"), item.get("synonyms"), item.get("brand_model")):
        sig = _dimension_signature_from_text(value)
        if sig:
            return sig
    return None


def _local_deterministic_match(part, price_items, feedback):
    """Resolve only obvious matches locally; ambiguous parts still go to AI."""
    learned_item, learned_score, learned_reason, learned_record = find_learned_catalog_item(
        part, price_items, feedback=feedback or [],
    )
    if learned_item is not None and float(learned_score or 0) >= LOCAL_LEARNED_MIN_SCORE:
        if not selection_conflict(part, learned_item):
            return learned_item, float(learned_score), learned_reason, "learned"

    valid = [item for item in price_items if is_valid_price_item(item)]
    part_sig = _part_dimension_signature(part)
    if part_sig:
        exact = []
        for item in valid:
            if not _same_material_family(part, item):
                continue
            if selection_conflict(part, item):
                continue
            if _item_dimension_signature(item) == part_sig:
                exact.append(item)
        if len(exact) == 1:
            return (
                exact[0], 1.0,
                f"Local deterministic match: material and {part_sig[0]:g}×{part_sig[1]:g} mm profile match exactly.",
                "local_exact_dimensions",
            )

    # Unique exact material/type/thickness rows are safe to resolve without AI.
    candidates = []
    for item in valid:
        if not _same_material_family(part, item):
            continue
        if selection_conflict(part, item):
            continue
        score = _candidate_score(part, item)
        if score >= 15:
            candidates.append((score, item))
    candidates.sort(key=lambda row: row[0], reverse=True)
    if candidates:
        top_score, top_item = candidates[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else -999
        if top_score >= 18 and top_score - runner_up >= 5:
            return (
                top_item, 0.97,
                "Local deterministic match: one catalog row is clearly stronger on material/type/specification.",
                "local_unique",
            )
    return None, 0.0, "", None

# --------------------------------------------------
# PROMPT
# --------------------------------------------------

MATCHING_INSTRUCTIONS = """
You are matching furniture drawing parts to an existing furniture
manufacturer price catalog.

CRITICAL RULES:

1. You may ONLY choose a catalog_index that exists in the supplied catalog.
2. If there is no sufficiently appropriate catalog item, return -1.
3. NEVER choose an unrelated item just because its wording is similar.
4. Material compatibility is extremely important.
5. Product type compatibility is extremely important.
6. Brand/model identifiers are extremely important when present.
7. Unit suitability matters:
   - board/glass/sheet material commonly uses m2
   - linear profiles/LED/edges commonly use m
   - hardware commonly uses pcs or set
   - labor commonly uses hour
8. Mirror / mirror glass must map to a mirror/glass catalog item if one exists,
   not LMDP or another board merely because both use m2.
9. Drawer runners / BLUM / TANDEM hardware must map to appropriate hardware,
   not arbitrary furniture hardware.
10. LMDP physical panels should normally map to the appropriate LMDP material.
11. HDP, plywood/fanera, MDF, veneer, solid wood and other materials are
    different materials. Do not treat them as equivalent unless the catalog
    explicitly provides a combined/generic item.
12. If the drawing describes multiple possible materials and the correct
    one cannot be determined, use -1 rather than inventing the answer.
13. A low-confidence semantic guess should be NO MATCH.
14. Do not calculate prices. Only select catalog rows.
15. previous_human_decisions lists catalog rows that human estimators chose
    for parts in earlier drawings; times_chosen is how many times estimators
    made that choice, so prefer decisions chosen more often. These are corrections of past AI mistakes
    and reflect how this company prices things. When a part is the same as,
    or clearly similar to, a previous decision's part (same material / same
    kind of component), choose that decision's catalog_index, even if the
    wording differs or you would otherwise return -1. Do not apply a decision
    to a part that is a different material or different kind of component.
16. For every part, return checks explaining the chosen catalog row, with
    exactly one entry for each aspect: material, component_type,
    brand_model and thickness. result is "match", "partial", "mismatch" or
    "not_applicable" (for example no brand/model on either side, or
    catalog_index is -1). detail is one short sentence quoting the drawing
    value and the catalog value, for example
    "Drawing: Compact juodas; catalog: LMDP negro FENIX".
    Be honest: use "partial" or "mismatch" when the chosen row differs on
    that aspect, even though you chose it.
17. Catalog rows may include material_type, component_type, brand_model,
    thickness_mm, keywords and synonyms (often Lithuanian and English names
    for the same thing). Use them to recognise what a catalog row is, but
    rules 3-13 still apply.

The human will be able to override the selected catalog item afterwards.
"""


AI_CHECK_ASPECTS = [
    "material",
    "component_type",
    "brand_model",
    "thickness",
]

CHECK_RESULTS = [
    "match",
    "partial",
    "mismatch",
    "not_applicable",
]


# --------------------------------------------------
# MATCH USING OPENAI
# --------------------------------------------------

def match_parts_to_catalog(
    ai_result,
    price_items,
    feedback=None,
):
    parts = ai_result.get("parts", [])
    if not parts:
        return []

    local_results = {}
    ambiguous_parts = []
    ambiguous_original_indexes = []

    for part_index, part in enumerate(parts):
        item, confidence, reason, source = _local_deterministic_match(
            part, price_items, feedback or [],
        )
        if item is not None:
            local_results[part_index] = {
                "part_index": part_index,
                "ai_part": part,
                "catalog_index": -1,
                "price_item": item,
                "ai_price_item": item,
                "confidence": confidence,
                "reason": reason,
                "checks": [],
                "learned": source == "learned",
                "learned_record_key": None,
                "match_source": source,
                "post_validation_rejected": False,
            }
        else:
            ambiguous_parts.append(part)
            ambiguous_original_indexes.append(part_index)

    print(
        f"[LOCAL MATCH] resolved {len(local_results)}/{len(parts)} parts locally; "
        f"sending {len(ambiguous_parts)} ambiguous parts to AI."
    )

    if not ambiguous_parts:
        return [local_results[index] for index in range(len(parts))]

    filtered_price_items = prefilter_catalog_for_parts(
        ambiguous_parts, price_items, feedback=feedback,
    )
    catalog = build_catalog(filtered_price_items)
    if not catalog:
        raise ValueError("No valid items were found in the price catalog.")

    part_payload = []
    for compact_index, part in enumerate(ambiguous_parts):
        part_payload.append({
            "part_index": compact_index,
            "name": part.get("name"),
            "component_type": part.get("component_type"),
            "material": part.get("material"),
            "quantity": part.get("quantity"),
            "length_mm": part.get("length_mm"),
            "width_mm": part.get("width_mm"),
            "height_mm": part.get("height_mm"),
            "thickness_mm": part.get("thickness_mm"),
        })

    catalog_payload = [
        {key: value for key, value in item.items() if key != "original_index"}
        for item in catalog
    ]
    catalog_index_by_key = {
        (item["name"].strip().lower(), item["category"].strip().lower(), item["unit"].strip().lower()): item["catalog_index"]
        for item in catalog
    }
    filtered_item_by_key = {
        (str(item.get("name") or "").strip().lower(), str(item.get("category") or "").strip().lower(), str(item.get("unit") or "").strip().lower()): item
        for item in filtered_price_items
    }

    human_decisions_payload = []
    for record in relevant_feedback_examples(ambiguous_parts, feedback, price_items):
        key = (
            str(record.get("catalog_name") or "").strip().lower(),
            str(record.get("catalog_category") or "").strip().lower(),
            str(record.get("catalog_unit") or "").strip().lower(),
        )
        decision_catalog_index = catalog_index_by_key.get(key)
        if decision_catalog_index is None:
            continue
        decision_item = filtered_item_by_key.get(key)
        if decision_item is not None and feedback_record_conflict(record, decision_item):
            print(
                "[LEARNING SAFETY] Excluding conflicting correction from AI prompt:",
                record.get("source_name"), "->", decision_item.get("name"),
            )
            continue
        human_decisions_payload.append({
            "part_name": record.get("source_name"),
            "material": record.get("source_material"),
            "component_type": record.get("component_type"),
            "catalog_index": decision_catalog_index,
            "catalog_name": record.get("catalog_name"),
            "times_chosen": record_votes(record),
        })

    prompt_data = {
        "furniture_name": ai_result.get("furniture_name"),
        "furniture_type": ai_result.get("furniture_type"),
        "drawing_notes": ai_result.get("notes"),
        "parts": part_payload,
        "catalog": catalog_payload,
        "previous_human_decisions": human_decisions_payload,
    }

    print(
        "Sending", len(ambiguous_parts), "ambiguous parts and", len(catalog),
        "catalog items and", len(human_decisions_payload),
        "previous human decisions to OpenAI matcher.",
    )

    response = client.responses.create(
        model="gpt-5.6-terra",
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": MATCHING_INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(prompt_data, ensure_ascii=False)}]},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "catalog_matching",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "matches": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "part_index": {"type": "integer"},
                                    "catalog_index": {"type": "integer"},
                                    "confidence": {"type": "number"},
                                    "reason": {"type": "string"},
                                    "checks": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "aspect": {"type": "string", "enum": AI_CHECK_ASPECTS},
                                                "result": {"type": "string", "enum": CHECK_RESULTS},
                                                "detail": {"type": "string"},
                                            },
                                            "required": ["aspect", "result", "detail"],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["part_index", "catalog_index", "confidence", "reason", "checks"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["matches"],
                    "additionalProperties": False,
                },
            }
        },
    )

    raw_matches = json.loads(response.output_text).get("matches", [])
    returned = {
        match.get("part_index"): match
        for match in raw_matches
        if isinstance(match.get("part_index"), int)
    }

    ai_results = {}
    for compact_index, original_index in enumerate(ambiguous_original_indexes):
        part = parts[original_index]
        raw_match = returned.get(compact_index, {})
        catalog_index = raw_match.get("catalog_index", -1)
        confidence = float(raw_match.get("confidence") or 0)
        reason = raw_match.get("reason", "No matcher result.")
        checks = [
            check for check in raw_match.get("checks", [])
            if check.get("aspect") in AI_CHECK_ASPECTS and check.get("result") in CHECK_RESULTS
        ]

        matched_price_item = None
        if isinstance(catalog_index, int) and 0 <= catalog_index < len(catalog):
            catalog_item = catalog[catalog_index]
            matched_price_item = filtered_price_items[catalog_item["original_index"]]

        rejected_reason = None
        if matched_price_item is not None:
            rejected_reason = ai_match_conflict(part, matched_price_item, checks=checks, reason=reason)
            if rejected_reason:
                print(
                    "[POST VALIDATION] Rejecting AI match:",
                    part.get("name"), "->", matched_price_item.get("name"), "|", rejected_reason,
                )
                matched_price_item = None

        final_reason = reason
        if rejected_reason:
            final_reason = f"Rejected after deterministic validation. {rejected_reason}"

        ai_results[original_index] = {
            "part_index": original_index,
            "ai_part": part,
            "catalog_index": catalog_index if matched_price_item is not None else -1,
            "price_item": matched_price_item,
            "ai_price_item": matched_price_item,
            "confidence": confidence if matched_price_item is not None else 0.0,
            "reason": final_reason,
            "checks": checks if matched_price_item is not None else [],
            "learned": False,
            "learned_record_key": None,
            "match_source": "ai" if matched_price_item is not None else "ai_no_match",
            "post_validation_rejected": bool(rejected_reason),
            "post_validation_reason": rejected_reason,
        }

    final_matches = []
    for part_index, part in enumerate(parts):
        final_match = local_results.get(part_index) or ai_results.get(part_index)
        if final_match is None:
            final_match = {
                "part_index": part_index, "ai_part": part, "catalog_index": -1,
                "price_item": None, "ai_price_item": None, "confidence": 0.0,
                "reason": "No matcher result.", "checks": [], "learned": False,
                "learned_record_key": None, "match_source": "no_match",
                "post_validation_rejected": False,
            }
        final_matches.append(final_match)

        matched = final_match.get("price_item")
        print()
        print("PART:", part.get("name"))
        print("MATERIAL:", part.get("material"))
        print("MATCH SOURCE:", final_match.get("match_source"))
        if matched:
            print("MATCH:", matched.get("name"))
            print("CATEGORY:", matched.get("category"))
            print("UNIT:", matched.get("unit"))
            print("CONFIDENCE:", round(float(final_match.get("confidence") or 0), 3))
        else:
            print("MATCH: NO MATCH")
        print("REASON:", final_match.get("reason"))

    return final_matches


# --------------------------------------------------
# SAFE FLOAT
# --------------------------------------------------

def safe_float(value):
    if value is None:
        return None

    try:
        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return None


# --------------------------------------------------
# CALCULATE QUANTITY
# --------------------------------------------------

def positive_mm(
    value,
):
    number = safe_float(
        value
    )

    if number is None or number <= 0:
        return None

    return number


def calculate_quantity(
    ai_part,
    price_item,
):
    unit = normalize_unit(
        price_item.get("unit")
    )

    pieces = safe_float(
        ai_part.get("quantity")
    )

    count_known = (
        pieces is not None
        and pieces > 0
    )

    if not count_known:
        pieces = 1.0

    length_mm = positive_mm(
        ai_part.get("length_mm")
    )

    width_mm = positive_mm(
        ai_part.get("width_mm")
    )

    height_mm = positive_mm(
        ai_part.get("height_mm")
    )


    # ----------------------------------------------
    # M2
    # ----------------------------------------------

    if unit == "m2":

        dimension_1 = length_mm
        dimension_2 = width_mm

        if dimension_1 is None:
            dimension_1 = height_mm

        elif dimension_2 is None:
            dimension_2 = height_mm

        if (
            dimension_1 is None
            or dimension_2 is None
        ):
            return None

        return (
            (dimension_1 / 1000)
            * (dimension_2 / 1000)
            * pieces
        )


    # ----------------------------------------------
    # METERS
    # ----------------------------------------------

    if unit == "m":

        dimension = length_mm

        if dimension is None:
            dimension = height_mm

        if dimension is None:
            dimension = width_mm

        if dimension is None:
            return None

        return (
            (dimension / 1000)
            * pieces
        )


    # ----------------------------------------------
    # PIECES
    # ----------------------------------------------

    if unit == "pcs":
        return pieces if count_known else None


    # ----------------------------------------------
    # SET
    # ----------------------------------------------

    if unit == "set":
        return pieces if count_known else None


    # ----------------------------------------------
    # FIXED
    # ----------------------------------------------

    if unit == "fixed":
        return pieces if count_known else None


    # ----------------------------------------------
    # HOURS
    # ----------------------------------------------

    # We deliberately do NOT let AI invent labor time.
    if unit == "hour":
        return None


    return None


EXPECTED_UNITS = {
    "panel": {"m2"},
    "glass": {"m2"},
    "mirror": {"m2"},
    "profile": {"m"},
    "edge": {"m"},
    "led": {"m", "pcs", "set"},
    "hardware": {"pcs", "set"},
}

CHECK_ORDER = [
    "previous_choice",
    "material",
    "component_type",
    "brand_model",
    "thickness",
    "unit",
    "keywords",
]


NUMBER = r"(\d+(?:[.,]\d+)?)"

SIZE_PATTERN = re.compile(
    NUMBER + r"\s*[x×*]\s*" + NUMBER + r"(?:\s*[x×*]\s*" + NUMBER + r")?(?:\s*mm\b)?"
)

MAX_THICKNESS_MM = 100


def plausible_thickness(value):
    thickness = safe_float(value)
    if thickness is None or thickness <= 0 or thickness > MAX_THICKNESS_MM:
        return None
    return thickness


def thickness_from_text(text):
    text = str(text or "").lower()
    for size in SIZE_PATTERN.finditer(text):
        if size.group(3):
            return plausible_thickness(size.group(3).replace(",", "."))
    found = re.search(NUMBER + r"\s*mm\b", SIZE_PATTERN.sub(" ", text))
    if not found:
        return None
    return plausible_thickness(found.group(1).replace(",", "."))


def catalog_thickness_mm(
    item_name,
):
    return thickness_from_text(
        item_name
    )


def check_thickness(
    ai_part,
    price_item,
):
    part_thickness = plausible_thickness(
        ai_part.get("thickness_mm")
    )

    item_thickness = plausible_thickness(
        price_item.get("thickness_mm")
    ) or catalog_thickness_mm(
        price_item.get("name")
    )

    if part_thickness is None or item_thickness is None:
        return None

    return {
        "aspect": "thickness",
        "result": (
            "match"
            if abs(part_thickness - item_thickness) <= 0.5
            else "mismatch"
        ),
        "detail": (
            f"Drawing: {part_thickness:g} mm; "
            f"catalog: {item_thickness:g} mm."
        ),
    }


def check_unit(
    ai_part,
    price_item,
):
    component_type = str(
        ai_part.get("component_type") or ""
    ).strip().lower()

    expected = EXPECTED_UNITS.get(
        component_type
    )

    unit = normalize_unit(
        price_item.get("unit")
    )

    if not expected or not unit:
        return {
            "aspect": "unit",
            "result": "not_applicable",
            "detail": (
                f"Catalog unit: {price_item.get('unit')}."
            ),
        }

    return {
        "aspect": "unit",
        "result": (
            "match"
            if unit in expected
            else "mismatch"
        ),
        "detail": (
            f"{component_type.capitalize()} parts are usually priced per "
            f"{' / '.join(sorted(expected))}; "
            f"catalog unit: {price_item.get('unit')}."
        ),
    }


def check_keywords(
    ai_part,
    price_item,
):
    part_words = {
        word
        for word in (
            tokens(ai_part.get("name"))
            | tokens(ai_part.get("material"))
        )
        if len(word) > 2 and not word.isdigit()
    }

    shared = sorted(
        part_words
        & tokens(price_item.get("name"))
    )

    if shared:
        return {
            "aspect": "keywords",
            "result": "match",
            "detail": (
                f"Shared words: {', '.join(shared)}."
            ),
        }

    return {
        "aspect": "keywords",
        "result": "not_applicable",
        "detail": (
            "No shared words (the drawing and the price list "
            "may use different languages)."
        ),
    }


def explain_match(
    ai_part,
    price_item,
    ai_checks=None,
    learned_reason=None,
):
    if price_item is None:
        return []

    checks = {}

    for check in ai_checks or []:
        checks[check["aspect"]] = check

    computed_thickness = check_thickness(
        ai_part,
        price_item,
    )

    if computed_thickness is not None:
        checks["thickness"] = computed_thickness

    checks["unit"] = check_unit(
        ai_part,
        price_item,
    )

    checks["keywords"] = check_keywords(
        ai_part,
        price_item,
    )

    if learned_reason:
        checks["previous_choice"] = {
            "aspect": "previous_choice",
            "result": "match",
            "detail": learned_reason,
        }

    return [
        checks[aspect]
        for aspect in CHECK_ORDER
        if aspect in checks
    ]
