import re
import unicodedata


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9xø]+", " ", text.lower())
    return " ".join(text.split())


FAMILY_TERMS = {
    "solid wood": ("masyvas", "masyv", "solid wood", "solid timber"),
    "plywood": ("fanera", "plywood"),
    "particle board": (
        "lmdp", "mfc", "chipboard", "particle board", "particleboard",
        "drožliu", "drozliu",
    ),
    "mdf": ("mdf",),
    "hdf": ("hdf", "hdp"),
    "veneer": ("lukstas", "lukštas", "veneer"),
    "metal": (
        "metal", "steel", "stainless", "nerud", "plien", "aluminium",
        "aluminum", "alium",
    ),
    "glass": ("stiklas", "glass"),
    "mirror": ("veidrodis", "mirror"),
}

COMPATIBLE_FAMILIES = {frozenset(("glass", "mirror"))}
GENERIC_COMPONENT_TYPES = {"", "other", "unknown", "generic"}
INCOMPATIBILITY_PHRASES = (
    "incompatible",
    "not suitable",
    "not an appropriate substitute",
    "not appropriate substitute",
    "no catalog row represents",
    "no catalog item matches",
    "does not match",
    "cannot replace",
)


def material_families(*values):
    text = " ".join(_norm(value) for value in values if value not in (None, ""))
    families = set()
    for family, terms in FAMILY_TERMS.items():
        if any(_norm(term) in text for term in terms):
            families.add(family)
    return families


def material_conflict(ai_part, selected_item):
    source = material_families(ai_part.get("material"), ai_part.get("name"))
    target = material_families(
        selected_item.get("material_type"),
        selected_item.get("name"),
        selected_item.get("category"),
        selected_item.get("keywords"),
        selected_item.get("synonyms"),
    )
    if not source or not target or source & target:
        return None
    if any(
        frozenset((left, right)) in COMPATIBLE_FAMILIES
        for left in source for right in target
    ):
        return None
    return (
        f"Detected material looks like {', '.join(sorted(source))}, while the "
        f"selected catalog item looks like {', '.join(sorted(target))}."
    )


def _positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text_dimension_pairs(*values):
    pairs = []
    for value in values:
        text = _norm(value).replace("×", "x")
        for left, right in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)(?!\d)", text):
            pair = tuple(sorted((round(float(left), 2), round(float(right), 2))))
            pairs.append(pair)
    return pairs


def _part_profile_pair(part):
    # Prefer explicit thickness + width/height for a linear/profile-like part.
    thickness = _positive_number(part.get("thickness_mm"))
    width = _positive_number(part.get("width_mm"))
    height = _positive_number(part.get("height_mm"))
    if thickness is not None:
        companion = width if width is not None else height
        if companion is not None:
            return tuple(sorted((round(thickness, 2), round(companion, 2))))

    # Some extractors put profile cross-section in width + height.
    if width is not None and height is not None:
        length = _positive_number(part.get("length_mm"))
        if length is None or max(width, height) < length * 0.75:
            return tuple(sorted((round(width, 2), round(height, 2))))

    text_pairs = _text_dimension_pairs(part.get("name"))
    return text_pairs[0] if text_pairs else None


def _item_profile_pair(item):
    pairs = _text_dimension_pairs(
        item.get("name"), item.get("keywords"), item.get("synonyms"), item.get("brand_model")
    )
    return pairs[0] if pairs else None


def dimension_conflict(ai_part, selected_item, tolerance_mm=1.0):
    part_pair = _part_profile_pair(ai_part)
    item_pair = _item_profile_pair(selected_item)
    if part_pair and item_pair:
        if any(abs(a - b) > tolerance_mm for a, b in zip(part_pair, item_pair)):
            return (
                f"Detected profile/cross-section is {part_pair[0]:g}×{part_pair[1]:g} mm, "
                f"while the selected catalog item is {item_pair[0]:g}×{item_pair[1]:g} mm."
            )

    part_thickness = _positive_number(ai_part.get("thickness_mm"))
    item_thickness = _positive_number(selected_item.get("thickness_mm"))
    if part_thickness is not None and item_thickness is not None:
        if abs(part_thickness - item_thickness) > tolerance_mm:
            return (
                f"Detected thickness is {part_thickness:g} mm, while the selected "
                f"catalog item is {item_thickness:g} mm."
            )
    return None


def component_type_conflict(ai_part, selected_item):
    source = _norm(ai_part.get("component_type"))
    target = _norm(selected_item.get("component_type"))
    if source in GENERIC_COMPONENT_TYPES or target in GENERIC_COMPONENT_TYPES:
        return None
    if source == target or source in target or target in source:
        return None
    return f"Detected component type is {source}, while the catalog item type is {target}."


def selection_conflict(ai_part, selected_item):
    for check in (material_conflict, dimension_conflict, component_type_conflict):
        issue = check(ai_part, selected_item)
        if issue:
            return issue
    return None


def ai_match_conflict(ai_part, selected_item, checks=None, reason=""):
    """Return a deterministic reason to reject/flag an AI match, or None."""
    issue = selection_conflict(ai_part, selected_item)
    if issue:
        return issue

    for check in checks or []:
        if check.get("aspect") in {"material", "component_type", "thickness"} and check.get("result") == "mismatch":
            detail = str(check.get("detail") or "").strip()
            return f"AI reported a {check.get('aspect')} mismatch. {detail}".strip()

    reason_norm = _norm(reason)
    for phrase in INCOMPATIBILITY_PHRASES:
        if _norm(phrase) in reason_norm:
            return f"AI explanation indicates the selected catalog item is incompatible: {reason}"
    return None


def feedback_record_conflict(record, catalog_item):
    source_part = {
        "name": record.get("source_name"),
        "material": record.get("source_material"),
        "component_type": record.get("component_type"),
        "thickness_mm": record.get("thickness_mm"),
        "width_mm": record.get("width_mm"),
        "height_mm": record.get("height_mm"),
    }
    return selection_conflict(source_part, catalog_item)


def should_auto_learn_match(match, selected_item, min_confidence=0.90):
    """Only learn clean, high-confidence matches automatically."""
    if selected_item is None:
        return False, "No catalog item selected."
    if match.get("learned"):
        return False, "This choice already came from learned feedback."

    issue = ai_match_conflict(
        match.get("ai_part") or {},
        selected_item,
        checks=match.get("checks") or [],
        reason=match.get("reason") or "",
    )
    if issue:
        return False, issue

    confidence = float(match.get("confidence") or 0)
    # Explicit human selections can still be learned elsewhere after validation.
    if not match.get("manual_choice") and confidence < min_confidence:
        return False, f"AI confidence {confidence:.2f} is below {min_confidence:.2f}."
    return True, None
