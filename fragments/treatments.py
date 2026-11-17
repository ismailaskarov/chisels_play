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

SCOPE_LABELS = {
    "one_main_face": "One main face",
    "both_main_faces": "Both main faces",
    "one_main_face_and_edges": "One main face + edges",
    "both_main_faces_and_edges": "Both main faces + edges",
    "all_faces": "All faces",
    "edges_only": "Edges only",
    "custom": "Custom / unclear",
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


def _mm2_to_m2(value):
    return float(value) / 1_000_000


def _panel_geometry(ai_part):
    length = _positive(ai_part.get("length_mm"))
    width = _positive(ai_part.get("width_mm"))
    thickness = _positive(ai_part.get("thickness_mm"))

    if not length or not width:
        return None

    face_m2 = _mm2_to_m2(length * width)
    edge_m2 = None
    if thickness:
        edge_m2 = _mm2_to_m2(2 * (length + width) * thickness)

    return {
        "length_mm": length,
        "width_mm": width,
        "thickness_mm": thickness,
        "face_m2": face_m2,
        "edge_m2": edge_m2,
    }


def _box_geometry(ai_part):
    length = _positive(ai_part.get("length_mm"))
    width = _positive(ai_part.get("width_mm"))
    height = _positive(ai_part.get("height_mm"))

    if not length or not width or not height:
        return None

    return {
        "length_mm": length,
        "width_mm": width,
        "height_mm": height,
        "main_face_m2": _mm2_to_m2(length * width),
        "all_faces_m2": _mm2_to_m2(
            2 * (length * width + length * height + width * height)
        ),
    }


def calculate_treatment_area(ai_part, scope, quantity=None):
    """Return deterministic treatment geometry for one extracted physical part.

    The function never invents a missing piece quantity. If quantity is unavailable,
    the caller must ask the estimator for it or use a manual total area.
    """
    quantity = _positive(quantity if quantity is not None else ai_part.get("quantity"))
    if quantity is None:
        return {
            "area_m2": None,
            "per_piece_m2": None,
            "quantity": None,
            "breakdown": "Piece quantity is missing.",
            "warning": "Piece quantity is missing, so treatment area cannot be calculated automatically.",
        }

    component_type = _norm(ai_part.get("component_type"))
    panel = _panel_geometry(ai_part)

    if panel:
        face = panel["face_m2"]
        edge = panel["edge_m2"]

        if scope == "one_main_face":
            per_piece = face
            detail = f"1 face × {face:.3f} m²"
        elif scope == "both_main_faces":
            per_piece = 2 * face
            detail = f"2 faces × {face:.3f} m²"
        elif scope == "edges_only":
            if edge is None:
                return {
                    "area_m2": None,
                    "per_piece_m2": None,
                    "quantity": quantity,
                    "breakdown": "Panel thickness is missing.",
                    "warning": "Thickness is required to calculate painted/lacquered edges.",
                }
            per_piece = edge
            detail = f"edges {edge:.3f} m²"
        elif scope == "one_main_face_and_edges":
            if edge is None:
                return {
                    "area_m2": None,
                    "per_piece_m2": None,
                    "quantity": quantity,
                    "breakdown": "Panel thickness is missing.",
                    "warning": "Thickness is required to include the edges automatically.",
                }
            per_piece = face + edge
            detail = f"1 face {face:.3f} + edges {edge:.3f} m²"
        elif scope in {"both_main_faces_and_edges", "all_faces"}:
            if edge is None:
                return {
                    "area_m2": None,
                    "per_piece_m2": None,
                    "quantity": quantity,
                    "breakdown": "Panel thickness is missing.",
                    "warning": "Thickness is required to calculate all panel faces and edges.",
                }
            per_piece = 2 * face + edge
            detail = f"2 faces {2 * face:.3f} + edges {edge:.3f} m²"
        else:
            return {
                "area_m2": None,
                "per_piece_m2": None,
                "quantity": quantity,
                "breakdown": "Custom treatment scope selected.",
                "warning": "Choose a calculable surface scope or enter the total treatment area manually.",
            }

        total = per_piece * quantity
        return {
            "area_m2": total,
            "per_piece_m2": per_piece,
            "quantity": quantity,
            "breakdown": f"{detail} × {quantity:g} pcs = {total:.3f} m²",
            "warning": None,
        }

    box = _box_geometry(ai_part)
    if box:
        if scope == "one_main_face":
            per_piece = box["main_face_m2"]
            detail = f"1 main face {per_piece:.3f} m²"
        elif scope == "both_main_faces":
            per_piece = 2 * box["main_face_m2"]
            detail = f"2 main faces {per_piece:.3f} m²"
        elif scope == "all_faces":
            per_piece = box["all_faces_m2"]
            detail = f"all rectangular faces {per_piece:.3f} m²"
        else:
            return {
                "area_m2": None,
                "per_piece_m2": None,
                "quantity": quantity,
                "breakdown": "This geometry does not support the selected automatic scope.",
                "warning": "Automatic edge treatment needs panel thickness/geometry; use manual total area for this part.",
            }

        total = per_piece * quantity
        return {
            "area_m2": total,
            "per_piece_m2": per_piece,
            "quantity": quantity,
            "breakdown": f"{detail} × {quantity:g} pcs = {total:.3f} m²",
            "warning": None,
        }

    return {
        "area_m2": None,
        "per_piece_m2": None,
        "quantity": quantity,
        "breakdown": "Not enough dimensions for automatic surface calculation.",
        "warning": "Length and width are required for automatic treatment-area calculation.",
    }


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


def _default_scope(ai_part):
    scope = str(ai_part.get("treatment_scope") or "").strip()
    if scope in SCOPE_LABELS:
        return scope

    component_type = _norm(ai_part.get("component_type"))
    if component_type == "panel":
        return "one_main_face"
    return "custom"


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
        "Paint / lacquer" + (" · AI suggestion" if suggestions else ""),
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
            help="Select every finish process that applies to this physical part.",
        )

        if not choices:
            return [], []

        scope_options = list(SCOPE_LABELS)
        suggested_scope = _default_scope(ai_part)
        scope = st.selectbox(
            "Painted / lacquered surfaces",
            options=scope_options,
            index=scope_options.index(suggested_scope),
            format_func=lambda value: SCOPE_LABELS[value],
            key=f"treatment_scope_{part_index}",
            help=(
                "The calculation uses the part dimensions. For panels, edges use the detected thickness. "
                "Change this only when the drawing/finish specification requires a different surface set."
            ),
        )

        scope_reason = str(ai_part.get("treatment_scope_reason") or "").strip()
        if scope_reason:
            st.caption(f"AI surface interpretation: {scope_reason}")

        detected_quantity = _positive(ai_part.get("quantity"))
        quantity = detected_quantity
        if detected_quantity is None:
            st.warning("The drawing did not provide a reliable piece quantity for this part.")
            quantity = st.number_input(
                "Piece quantity for treatment",
                min_value=0.0,
                value=1.0,
                step=1.0,
                key=f"treatment_quantity_{part_index}",
            )

        auto = calculate_treatment_area(ai_part, scope, quantity=quantity)
        can_auto = auto["area_m2"] is not None and auto["area_m2"] > 0

        mode_options = ["Automatic from dimensions", "Manual total area"]
        default_mode = "Automatic from dimensions" if can_auto else "Manual total area"
        area_mode = st.radio(
            "Area calculation",
            mode_options,
            index=mode_options.index(default_mode),
            horizontal=True,
            key=f"treatment_area_mode_{part_index}",
        )

        review_messages = []

        if area_mode == "Automatic from dimensions":
            if can_auto:
                area = float(auto["area_m2"])
                st.success(f"Calculated treatment area: **{area:.3f} m²**")
                st.caption(auto["breakdown"])
            else:
                area = 0.0
                warning = auto.get("warning") or "Treatment area could not be calculated automatically."
                st.warning(warning)
                review_messages.append(warning)
        else:
            suggested_manual = float(auto["area_m2"] or 0.0)
            area = st.number_input(
                "Total treatment area (m²)",
                min_value=0.0,
                value=float(round(suggested_manual, 3)),
                step=0.1,
                format="%.3f",
                key=f"treatment_manual_area_{part_index}",
                help="Enter the total area across all pieces of this component.",
            )
            if can_auto:
                st.caption(
                    f"Automatic geometry would give {auto['area_m2']:.3f} m² ({auto['breakdown']})."
                )

        if scope == "custom" and area_mode == "Automatic from dimensions":
            message = "Treatment surface scope is custom/unclear; use manual total area or choose a specific surface scope."
            if message not in review_messages:
                review_messages.append(message)

        if area <= 0:
            review_messages.append("Surface treatment is selected but its total area is zero.")
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
                "treatment_scope": scope,
                "piece_quantity": float(quantity or 0),
                "area_source": "automatic" if area_mode == "Automatic from dimensions" else "manual",
                "area_breakdown": auto.get("breakdown") or "",
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
            "treatment_details": [],
        })
        group["quantity"] += float(item.get("quantity") or 0)
        base_part = str(item.get("base_part") or "").strip()
        if base_part and base_part not in group["base_parts"]:
            group["base_parts"].append(base_part)
        detail = {
            "part": base_part,
            "area_m2": float(item.get("quantity") or 0),
            "scope": item.get("treatment_scope") or "",
            "area_source": item.get("area_source") or "",
            "breakdown": item.get("area_breakdown") or "",
        }
        group["treatment_details"].append(detail)

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
