import re
import unicodedata

import streamlit as st


TREATMENT_LABELS = {
    "painting": "Painting",
    "lacquering": "Lacquering",
}

TREATMENT_TERMS = {
    "painting": ("painting", "paint", "dazymas", "dažymas", "dazyti", "dažyti"),
    "lacquering": (
        "lacquering", "lacquer", "varnish", "lakavimas", "lakas", "lakuoti"
    ),
}

EXCLUDED_COMPONENT_TYPES = {"hardware", "service", "led", "glass", "mirror"}


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _auto_area(ai_part, treated_faces=1):
    quantity = _positive(ai_part.get("quantity")) or 1.0
    length = _positive(ai_part.get("length_mm"))
    width = _positive(ai_part.get("width_mm"))
    height = _positive(ai_part.get("height_mm"))
    thickness = _positive(ai_part.get("thickness_mm"))
    component_type = _norm(ai_part.get("component_type"))

    if length and width and component_type in {"panel", "edge"}:
        return length * width * quantity * max(1, treated_faces) / 1_000_000

    if length and (width or thickness) and component_type in {"profile", "other"}:
        side_a = width or thickness
        side_b = height or thickness
        if side_a and side_b:
            return 2 * (side_a + side_b) * length * quantity / 1_000_000

    if length and width and height:
        return 2 * (length * width + length * height + width * height) * quantity / 1_000_000

    if length and width:
        return length * width * quantity * max(1, treated_faces) / 1_000_000

    return 0.0


def _matching_catalog_items(catalog_items, treatment):
    terms = tuple(_norm(term) for term in TREATMENT_TERMS[treatment])
    matches = []
    for item in catalog_items:
        text = _norm(" ".join(str(item.get(key) or "") for key in (
            "name", "category", "keywords", "synonyms", "description"
        )))
        unit = _norm(item.get("unit"))
        if not any(term in text for term in terms):
            continue
        if unit not in {"m2", "m 2", "sqm"}:
            continue
        score = 2
        if _norm(item.get("category")) == "paslauga":
            score += 1
        matches.append((score, item))
    matches.sort(key=lambda entry: (-entry[0], str(entry[1].get("name") or "")))
    return [item for _, item in matches]


def render_surface_treatments(ai_part, part_index, catalog_items):
    """Return (price_items, review_messages) for treatments selected under one part."""
    component_type = _norm(ai_part.get("component_type"))
    suggestions = [
        value for value in (ai_part.get("recommended_treatments") or [])
        if value in TREATMENT_LABELS
    ]

    if component_type in EXCLUDED_COMPONENT_TYPES and not suggestions:
        return [], []

    with st.expander(
        "Surface treatment"
        + (" · AI suggestion" if suggestions else ""),
        expanded=bool(suggestions),
    ):
        if suggestions:
            reason = str(ai_part.get("treatment_reason") or "").strip()
            st.info(
                "AI suggested: "
                + ", ".join(TREATMENT_LABELS[value] for value in suggestions)
                + (f". {reason}" if reason else ".")
            )

        choices = st.multiselect(
            "Processes",
            options=list(TREATMENT_LABELS),
            default=suggestions,
            format_func=lambda value: TREATMENT_LABELS[value],
            key=f"treatment_processes_{part_index}",
            help="Add or remove processes before using the estimate.",
        )

        if not choices:
            return [], []

        treated_faces = 1
        if _norm(ai_part.get("component_type")) in {"panel", "edge"}:
            treated_faces = st.number_input(
                "Treated faces",
                min_value=1,
                max_value=2,
                value=1,
                step=1,
                key=f"treatment_faces_{part_index}",
            )

        calculated_area = _auto_area(ai_part, treated_faces)
        area = st.number_input(
            "Treatment area (m²)",
            min_value=0.0,
            value=float(round(calculated_area, 3)),
            step=0.1,
            format="%.3f",
            key=f"treatment_area_{part_index}",
            help=(
                "Calculated from the detected dimensions and quantity. "
                "Adjust it when only selected surfaces are treated."
            ),
        )

        if calculated_area > 0:
            st.caption(f"Automatic area before adjustment: {calculated_area:.3f} m²")

        review_messages = []
        if area <= 0:
            review_messages.append("Surface treatment is selected but its area is zero.")
            return [], review_messages

        rows = []
        for treatment in choices:
            matches = _matching_catalog_items(catalog_items, treatment)
            chosen = None
            if matches:
                chosen = st.selectbox(
                    f"{TREATMENT_LABELS[treatment]} price item",
                    options=matches,
                    format_func=lambda item: (
                        f'{item.get("name")} — {item.get("unit")} — '
                        f'€{float(item.get("price") or 0):.2f}'
                    ),
                    key=f"treatment_catalog_{part_index}_{treatment}",
                )
                unit_price = float(chosen.get("price") or 0)
                category = str(chosen.get("category") or "PASLAUGA")
            else:
                st.warning(
                    f'No {TREATMENT_LABELS[treatment].lower()} price was found in the catalog. '
                    "Enter a temporary price for this estimate."
                )
                unit_price = st.number_input(
                    f"{TREATMENT_LABELS[treatment]} price per m²",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    format="%.2f",
                    key=f"treatment_price_{part_index}_{treatment}",
                )
                category = "PASLAUGA"

            if unit_price <= 0:
                review_messages.append(
                    f'{TREATMENT_LABELS[treatment]} is selected but has no unit price.'
                )
                continue

            rows.append({
                "name": f'{TREATMENT_LABELS[treatment]} — {ai_part.get("name") or "part"}',
                "category": category,
                "quantity": float(area),
                "unit_price": float(unit_price),
                "unit": "m2",
                "source": "treatment",
                "treatment": treatment,
                "base_part": ai_part.get("name") or "",
            })

        return rows, review_messages


def aggregate_treatment_items(items):
    """Combine treatment rows so large projects show one total m² line per process/price."""
    normal = []
    grouped = {}
    for item in items or []:
        if item.get("source") != "treatment":
            normal.append(item)
            continue
        key = (
            item.get("treatment"),
            item.get("category"),
            item.get("unit"),
            float(item.get("unit_price") or 0),
        )
        group = grouped.setdefault(key, {
            **item,
            "quantity": 0.0,
            "base_parts": [],
        })
        group["quantity"] += float(item.get("quantity") or 0)
        base_part = str(item.get("base_part") or "").strip()
        if base_part and base_part not in group["base_parts"]:
            group["base_parts"].append(base_part)

    for group in grouped.values():
        treatment = group.get("treatment")
        label = TREATMENT_LABELS.get(treatment, str(treatment or "Treatment"))
        part_count = len(group.get("base_parts") or [])
        group["name"] = (
            f"{label} — {part_count} parts"
            if part_count > 1
            else f"{label} — {(group.get('base_parts') or ['part'])[0]}"
        )
        normal.append(group)
    return normal
