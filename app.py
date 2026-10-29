from pathlib import Path
from copy import deepcopy

import streamlit as st

from calculator import calculate_project
from price_reader import read_price_data
from pdf_extractor import extract_furniture
from feedback_store import (
    load_match_feedback,
    remove_match_vote,
    save_match_feedback,
)

from component_matcher import (
    match_parts_to_catalog,
    calculate_quantity,
    is_valid_price_item,
    explain_match,
    normalize_unit,
)
from market_prices import search_market_prices
from catalog_store import (
    add_catalog_item,
    catalog_item_exists,
    catalog_item_status,
    load_live_catalog,
    load_pricing,
)
from ui import apply_style, show_header, show_sidebar, step_header
from project_store import project_storage_name, save_project
from fragments.project_drawing import render_project_and_drawing
from fragments.review_summary import render_review_summary
from fragments.quotation import render_quotation
from utils.performance import timed
from utils.match_safety import selection_conflict, should_auto_learn_match


PRICE_FILE = Path("data") / "prices.xlsx"
LOGO_PATH = Path("data") / "logo.jpg"



# --------------------------------------------------
# CONFIG
# --------------------------------------------------

st.set_page_config(
    page_title="Chisels Play Cost Calculator",
    layout="wide",
)

apply_style()


# --------------------------------------------------
# LOAD PRICING
# --------------------------------------------------

@st.cache_data
def load_data():
    print(
        "Loading pricing data..."
    )

    return read_price_data(
        PRICE_FILE
    )


price_data = timed("load_data", load_data)


@st.cache_data(ttl=300)
def load_catalog(base_items):
    return load_live_catalog(base_items, raise_errors=True)


@st.cache_data(ttl=300)
def load_company_pricing(base_markups, base_multiplier):
    return load_pricing(base_markups, base_multiplier, raise_errors=True)


try:
    all_items = timed(
        "load_catalog",
        load_catalog,
        price_data["items"],
    )
    company_pricing = timed(
        "load_company_pricing",
        load_company_pricing,
        price_data["markups"],
        price_data["project_multiplier"],
    )
except Exception as error:
    print("CATALOG LOAD ERROR:", repr(error))
    st.warning(
        "The catalog database could not be reached, so prices may be out of date: "
        "admin changes and newly approved items are missing until it is back. "
        "Reload the page in a minute before preparing a quotation."
    )
    all_items = load_live_catalog(price_data[
        "items"
    ])
    company_pricing = load_pricing(
        price_data["markups"],
        price_data["project_multiplier"],
    )

company_markups = company_pricing["markups"]

markups = {
    **company_markups,
    **{
        category: value
        for category, value in st.session_state.get("estimate_coefficients", {}).items()
        if category in company_markups
    },
}


# --------------------------------------------------
# SESSION STATE
# --------------------------------------------------

DEFAULT_SESSION_STATE = {
    "project_name": "",
    "items": [],
    "furniture_name": "",
    "furniture_type": "Other",
    "overall_width": 0.0,
    "overall_height": 0.0,
    "overall_depth": 0.0,
    "door_count": 0,
    "shelf_count": 0,
    "drawing_notes": "",
    "ai_result": None,
    "ai_parts": [],
    "catalog_matches": None,
    "current_project_id": None,
    "project_status": "Draft",
    "project_service_rows": [],
}


for key, value in (
    DEFAULT_SESSION_STATE.items()
):
    if key not in st.session_state:
        st.session_state[
            key
        ] = value


# --------------------------------------------------
# CATEGORY
# --------------------------------------------------

def normalize_category(
    category
):
    category = str(
        category
    ).strip().lower()

    aliases = {
        "metalas": "metal",
    }

    return aliases.get(
        category,
        category,
    )


def get_markup(
    category
):
    return markups.get(
        normalize_category(
            category
        )
    )


# --------------------------------------------------
# APPLY AI EXTRACTION
# --------------------------------------------------

def apply_ai_data(
    ai_data
):
    st.session_state[
        "furniture_name"
    ] = (
        ai_data.get(
            "furniture_name"
        )
        or ""
    )

    furniture_type = (
        ai_data.get(
            "furniture_type"
        )
        or "Other"
    )

    allowed_types = [
        "Wardrobe",
        "Kitchen",
        "Table",
        "Desk",
        "Shelf",
        "Cabinet",
        "Bed",
        "Other",
    ]

    if (
        furniture_type
        not in allowed_types
    ):
        furniture_type = (
            "Other"
        )

    st.session_state[
        "furniture_type"
    ] = furniture_type


    st.session_state[
        "overall_width"
    ] = float(
        ai_data.get(
            "width_mm"
        )
        or 0
    )

    st.session_state[
        "overall_height"
    ] = float(
        ai_data.get(
            "height_mm"
        )
        or 0
    )

    st.session_state[
        "overall_depth"
    ] = float(
        ai_data.get(
            "depth_mm"
        )
        or 0
    )


    st.session_state[
        "door_count"
    ] = int(
        ai_data.get(
            "doors"
        )
        or 0
    )

    st.session_state[
        "shelf_count"
    ] = int(
        ai_data.get(
            "shelves"
        )
        or 0
    )


    notes = (
        ai_data.get(
            "notes"
        )
        or ""
    )


    materials = (
        ai_data.get(
            "materials",
            [],
        )
    )

    if materials:

        notes += (
            "\n\nDetected materials:\n"
        )

        for material in materials:

            notes += (
                f'- '
                f'{material.get("part", "")}: '
                f'{material.get("name", "")}'
                f'\n'
            )


    uncertain = (
        ai_data.get(
            "uncertain",
            [],
        )
    )

    if uncertain:

        notes += (
            "\nNeeds confirmation:\n"
        )

        for item in uncertain:

            notes += (
                f"- {item}\n"
            )


    st.session_state[
        "drawing_notes"
    ] = notes.strip()


    st.session_state[
        "ai_parts"
    ] = ai_data.get(
        "parts",
        [],
    )

    st.session_state[
        "ai_result"
    ] = ai_data


    # Force a fresh catalog match.
    st.session_state[
        "catalog_matches"
    ] = None


    # Clear old AI selection widgets.
    keys_to_delete = []

    for key in (
        st.session_state.keys()
    ):

        if (
            str(key).startswith(
                "catalog_select_"
            )
            or
            str(key).startswith(
                "catalog_quantity_"
            )
            or
            str(key).startswith(
                "market_"
            )
            or
            str(key).startswith(
                "quote_"
            )
            or
            str(key).startswith(
                "custom_"
            )
            or
            str(key).startswith(
                "no_match_resolution_"
            )
            or
            str(key).startswith(
                "generated_quote_"
            )
            or
            str(key).startswith(
                "estimate_coefficient"
            )
            or
            str(key).startswith(
                "learning_conflict_"
            )
        ):
            keys_to_delete.append(
                key
            )

    for key in keys_to_delete:

        del st.session_state[
            key
        ]


    print(
        "AI extraction applied."
    )


CHECK_LABELS = {
    "previous_choice": "Previous choice",
    "material": "Material",
    "component_type": "Component type",
    "brand_model": "Brand/model",
    "thickness": "Thickness",
    "unit": "Unit",
    "keywords": "Keywords",
}

CHECK_RESULT_STYLES = {
    "match": ("green", "✓", "match"),
    "partial": ("orange", "~", "partial"),
    "mismatch": ("red", "✗", "conflict"),
    "not_applicable": ("gray", "–", "not checked"),
}


def same_catalog_item(
    first,
    second,
):
    if first is None or second is None:
        return False

    return (
        first["name"],
        first["category"],
        first["unit"],
        float(first["price"]),
    ) == (
        second["name"],
        second["category"],
        second["unit"],
        float(second["price"]),
    )


def show_match_explanation(
    checks,
    manual_choice,
):
    badges = []

    for check in checks:

        if check["result"] == "not_applicable":
            continue

        color, icon, _ = (
            CHECK_RESULT_STYLES[
                check["result"]
            ]
        )

        badges.append(
            f':{color}-badge['
            f'{icon} '
            f'{CHECK_LABELS[check["aspect"]]}'
            f']'
        )

    if badges:

        st.markdown(
            " ".join(badges)
        )

    with st.expander(
        "Why this match",
        expanded=False,
    ):

        if manual_choice:

            st.caption(
                "Your own choice: material, component "
                "type and brand/model are only checked "
                "for the AI's pick."
            )

        for check in checks:

            _, icon, result_text = (
                CHECK_RESULT_STYLES[
                    check["result"]
                ]
            )

            st.markdown(
                f'{icon} '
                f'**{CHECK_LABELS[check["aspect"]]}** '
                f'({result_text}): '
                f'{check["detail"]}'
            )


MARKET_CATEGORY_BY_TYPE = {
    "panel": "LMDP",
    "glass": "STIKLAS",
    "mirror": "STIKLAS",
    "hardware": "FURNITURA",
    "profile": "FURNITURA",
    "edge": "FURNITURA",
    "led": "FURNITURA",
    "service": "PASLAUGA",
}


def add_review_issue(
    issues,
    part_index,
    part_name,
    issue_type,
    detail,
):
    """Add one de-duplicated estimator review warning."""
    key = (
        part_index,
        str(issue_type),
        str(detail),
    )

    if any(issue.get("_key") == key for issue in issues):
        return

    issues.append({
        "_key": key,
        "part_index": part_index,
        "part_name": str(part_name or "Unknown part"),
        "issue_type": str(issue_type),
        "detail": str(detail),
    })


def custom_item_defaults(ai_part):
    component_type = str(
        ai_part.get("component_type") or ""
    ).strip().lower()

    category = MARKET_CATEGORY_BY_TYPE.get(
        component_type
    )

    unit_by_type = {
        "panel": "m2",
        "glass": "m2",
        "mirror": "m2",
        "profile": "m",
        "edge": "m",
        "led": "m",
        "hardware": "pcs",
        "service": "pcs",
    }

    return category, unit_by_type.get(
        component_type,
        "pcs",
    )


def market_price_warning(
    match,
    selected_item,
    threshold_percent=35.0,
):
    """Return a warning string when catalog and market differ materially."""
    if selected_item is None or selected_item.get("market"):
        return None

    result = match.get("market_result")
    if not result:
        return None

    groups = result.get("groups") or []
    if len(groups) != 1:
        return None

    group = groups[0]
    market_average = float(group.get("average") or 0)
    if market_average <= 0:
        return None

    try:
        list_price = float(selected_item.get("price") or 0)
    except (TypeError, ValueError):
        return None

    if list_price <= 0:
        return None

    difference = (
        (list_price - market_average)
        / market_average
        * 100
    )

    if abs(difference) < threshold_percent:
        return None

    direction = "above" if difference > 0 else "below"
    return (
        f'Catalog price is {abs(difference):.0f}% {direction} '
        f'the checked market average.'
    )


def save_catalog_candidate(candidate):
    if catalog_item_exists(all_items, candidate):
        st.info(f'Already in catalog: {candidate["name"]}')
        return
    try:
        created, existing = add_catalog_item(candidate)
    except Exception as error:
        st.error(f'Could not send {candidate["name"]} for approval: {error}')
        return
    status = catalog_item_status(existing)
    if created:
        st.success(
            f'Sent to admin for approval: {candidate["name"]}. '
            "It will be used in future estimates once approved."
        )
    elif status == "pending":
        st.info(f'Already waiting for admin approval: {candidate["name"]}')
    elif status == "disabled":
        st.warning(f'{candidate["name"]} was disabled by an admin, so it was not added again.')
    else:
        st.info(f'Already in catalog: {candidate["name"]}')


def _market_cache_key(ai_part, selected_item):
    item = selected_item or {}
    fields = (
        ai_part.get("name"), ai_part.get("component_type"), ai_part.get("material"),
        ai_part.get("thickness_mm"), ai_part.get("length_mm"), ai_part.get("width_mm"),
        item.get("name"), item.get("unit"), item.get("material_type"), item.get("thickness_mm"),
    )
    return tuple(str(value or "").strip().lower() for value in fields)


def show_market_check(
    match,
    ai_part,
    selected_item,
):
    part_index = match["part_index"]
    result = match.get("market_result")

    with st.expander(
        "Market price check",
        expanded=(result is not None or selected_item is None),
    ):
        cache = st.session_state.setdefault("market_search_cache", {})
        cache_key = _market_cache_key(ai_part, selected_item)
        cached_result = cache.get(cache_key)
        if result is None and cached_result is not None:
            button_label = "Use cached market prices"
        elif result is None:
            button_label = "Check market prices"
        else:
            button_label = "Search again"

        if st.button(button_label, key=f"market_check_{part_index}"):
            force_refresh = result is not None
            if cached_result is not None and not force_refresh:
                result = deepcopy(cached_result)
                print("[MARKET CACHE] Reused cached market search for:", ai_part.get("name"))
                match["market_result"] = result
                match.pop("market_error", None)
            else:
                with st.spinner("Searching shops..."):
                    try:
                        result = timed("market_price_search", search_market_prices, ai_part, selected_item)
                        match["market_search_count"] = match.get("market_search_count", 0) + 1
                        result["search_id"] = match["market_search_count"]
                        result["searched_for"] = (
                            selected_item["name"]
                            if selected_item is not None
                            else ai_part.get("name")
                        )
                        cache[cache_key] = deepcopy(result)
                        match["market_result"] = result
                        match.pop("market_error", None)
                    except Exception as error:
                        print("MARKET PRICE ERROR:", repr(error))
                        match["market_error"] = str(error)
            st.rerun()

        if match.get("market_error"):
            st.error(f'Market price search failed: {match["market_error"]}')

        if result is None:
            st.caption(
                "Searches online shops for this part. Prices are shown without VAT. "
                "For unmatched parts, choose a real offer below rather than saving "
                "a market average as a catalog product."
            )
            return None

        unit = result["unit"]
        thickness_text = ""
        if result.get("target_thickness"):
            thickness_text = (
                f' Thickness: {result["target_thickness"]:g} mm '
                f'(from the {result["thickness_source"]}).'
            )

        st.caption(
            f'Searched for: {result.get("searched_for")}. '
            f'Prices per {unit}, without VAT.{thickness_text}'
        )

        groups = result.get("groups") or []
        offers = result.get("offers") or []

        if result.get("shops") == 1:
            st.warning(
                f'All prices come from one shop '
                f'({", ".join(result.get("shop_names") or [])}), '
                "so this is that shop's price, not a market average."
            )

        list_price = None
        if (
            selected_item is not None
            and normalize_unit(selected_item["unit"]) == unit
        ):
            list_price = float(selected_item["price"])

        if not groups:
            st.warning("No usable prices were found.")
        elif len(groups) == 1:
            group = groups[0]
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Market average", f'€{group["average"]:.2f}')
            with c2:
                st.metric("Lowest", f'€{group["min"]:.2f}')
            with c3:
                st.metric("Highest", f'€{group["max"]:.2f}')
            with c4:
                st.metric(
                    "Shops / offers",
                    f'{group["shops"]} / {group["offers"]}',
                )

            if group.get("note"):
                st.caption(f'Note: {group["note"]}.')
            if group.get("similar_left_out"):
                st.caption(
                    "The average uses only exact matches. "
                    f'{group["similar_left_out"]} similar product(s) are listed '
                    "below but not averaged."
                )
            if list_price is not None and group.get("average"):
                difference = (
                    (list_price - group["average"])
                    / group["average"]
                    * 100
                )
                st.info(
                    f'Price list: €{list_price:.2f}/{unit}, '
                    f'{abs(difference):.0f}% '
                    f'{"above" if difference > 0 else "below"} '
                    "the market average. The price-list price is used in the estimate."
                )
        else:
            st.warning(
                "Different thicknesses were found, so prices are shown separately "
                "for each thickness."
            )
            group_rows = []
            for group in groups:
                row = {
                    "Thickness": group["label"],
                    f"Average € / {unit}": group["average"],
                    "Lowest": group["min"],
                    "Highest": group["max"],
                    "Shops / offers": f'{group["shops"]} / {group["offers"]}',
                    "Similar, not averaged": group["similar_left_out"],
                    "Note": group.get("note") or "",
                }
                if list_price is not None and group.get("average"):
                    difference = (
                        (list_price - group["average"])
                        / group["average"]
                        * 100
                    )
                    row["Price list vs market"] = (
                        f'{abs(difference):.0f}% '
                        f'{"above" if difference > 0 else "below"}'
                    )
                group_rows.append(row)
            st.dataframe(group_rows, hide_index=True, width="stretch")
            if list_price is not None:
                st.caption(
                    f'Price list: €{list_price:.2f}/{unit}. '
                    "The price-list price is used in the estimate."
                )

        if result.get("skipped"):
            st.caption(
                "Not used: "
                + "; ".join(
                    f'{offer.get("shop")} ({offer["skip_reason"]})'
                    for offer in result["skipped"]
                )
            )

        if result.get("notes"):
            st.caption(f'Search notes: {result["notes"]}')

        if not offers:
            return None

        # When a valid catalog item is already selected, market prices remain
        # reference-only. The price-list item continues to drive the estimate.
        if selected_item is not None:
            st.dataframe(
                [
                    {
                        "Shop": offer["shop"],
                        "Product": offer["product_name"],
                        "Thickness": (
                            f'{offer["thickness"]:g} mm'
                            if offer.get("thickness")
                            else "-"
                        ),
                        "Shop price": (
                            f'€{offer["price"]:.2f} / {offer["price_unit"]}'
                            + (" incl. VAT" if offer["includes_vat"] else " excl. VAT")
                        ),
                        f"€ per {unit} excl. VAT": offer["price_excl_vat_per_unit"],
                        "Match": offer["match"],
                        "In average": "✓" if offer.get("in_average") else "–",
                        "Link": offer["url"],
                    }
                    for offer in offers
                ],
                column_config={"Link": st.column_config.LinkColumn("Link")},
                hide_index=True,
                width="stretch",
            )
            return None

        st.markdown("**Choose a market offer**")
        st.caption(
            "Select one real offer to use in this estimate. You can also save "
            "that exact offer to the catalog for future estimates."
        )

        offer_rows = []
        for offer_index, offer in enumerate(offers):
            offer_rows.append({
                "Use": False,
                "Add to catalog": False,
                "Shop": offer.get("shop") or "",
                "Product": offer.get("product_name") or ai_part.get("name") or "Market item",
                "Thickness": (
                    f'{offer["thickness"]:g} mm'
                    if offer.get("thickness")
                    else "-"
                ),
                f"€ per {unit} excl. VAT": float(offer["price_excl_vat_per_unit"]),
                "Match": offer.get("match") or "",
                "Link": offer.get("url") or "",
                "_offer_index": offer_index,
            })

        edited_offers = st.data_editor(
            offer_rows,
            hide_index=True,
            width="stretch",
            disabled=[
                "Shop",
                "Product",
                "Thickness",
                f"€ per {unit} excl. VAT",
                "Match",
                "Link",
                "_offer_index",
            ],
            column_config={
                "Use": st.column_config.CheckboxColumn("Use", default=False),
                "Add to catalog": st.column_config.CheckboxColumn(
                    "Add to catalog",
                    default=False,
                ),
                f"€ per {unit} excl. VAT": st.column_config.NumberColumn(
                    f"€ per {unit} excl. VAT",
                    format="€%.2f",
                ),
                "Link": st.column_config.LinkColumn("Link"),
                "_offer_index": None,
            },
            key=f"market_offer_editor_{part_index}_{result.get('search_id', 0)}",
        )

        if hasattr(edited_offers, "to_dict"):
            edited_rows = edited_offers.to_dict(orient="records")
        else:
            edited_rows = list(edited_offers)

        category_options = sorted(
            set(item["category"] for item in valid_catalog_items)
        )
        default_category = MARKET_CATEGORY_BY_TYPE.get(
            str(ai_part.get("component_type") or "").lower()
        )
        if default_category not in category_options:
            default_category = category_options[0] if category_options else "FURNITURA"

        category = st.selectbox(
            "Category for the coefficient / catalog",
            category_options or [default_category],
            index=(
                category_options.index(default_category)
                if default_category in category_options
                else 0
            ),
            key=f"market_category_{part_index}",
        )

        selected_rows = [row for row in edited_rows if row.get("Use")]
        if len(selected_rows) > 1:
            st.warning("Choose only one market offer to use in the estimate.")
            return None

        rows_to_save = [row for row in edited_rows if row.get("Add to catalog")]
        market_notes = st.text_input(
            "Notes for the admin (optional)",
            key=f"market_notes_{part_index}",
        ) if rows_to_save else ""
        save_offers = bool(rows_to_save) and st.button(
            "Send ticked offers for catalog approval",
            key=f"market_save_catalog_{part_index}",
        )

        for row in rows_to_save if save_offers else []:
            offer = offers[int(row["_offer_index"])]
            save_catalog_candidate({
                "name": offer.get("product_name") or ai_part.get("name") or "Market item",
                "category": category,
                "unit": unit,
                "price": float(offer["price_excl_vat_per_unit"]),
                "supplier": offer.get("shop") or "Market search",
                "source_url": offer.get("url") or "",
                "source_type": "market",
                "thickness_mm": offer.get("thickness"),
                "material_type": ai_part.get("material"),
                "component_type": ai_part.get("component_type"),
                "added_for_part": ai_part.get("name"),
                "notes": " ".join(filter(None, [
                    f'Market match: {offer["match"]}.' if offer.get("match") else "",
                    market_notes.strip(),
                ])),
            })

        if not selected_rows:
            return None

        chosen_row = selected_rows[0]
        offer = offers[int(chosen_row["_offer_index"])]
        return {
            "name": offer.get("product_name") or ai_part.get("name") or "Market item",
            "category": category,
            "unit": unit,
            "price": float(offer["price_excl_vat_per_unit"]),
            "market": True,
            "market_offer": True,
            "source": offer.get("shop") or "Market search",
            "source_url": offer.get("url") or "",
            "market_match": offer.get("match") or "",
        }


def remember_catalog_choice(
    part_index
):
    selected_item = st.session_state.get(
        f"catalog_select_"
        f"{part_index}"
    )

    for match in (
        st.session_state[
            "catalog_matches"
        ]
        or []
    ):

        if (
            match["part_index"]
            != part_index
        ):
            continue

        match.pop(
            "market_result",
            None,
        )

        match.pop(
            "market_error",
            None,
        )

        if match.get(
            "saved_record_key"
        ):

            remove_match_vote(
                match[
                    "saved_record_key"
                ]
            )

            match[
                "saved_record_key"
            ] = None

        elif (
            selected_item is not None
            and match.get(
                "learned_record_key"
            )
            and not match.get(
                "learned_vote_removed"
            )
        ):

            remove_match_vote(
                match[
                    "learned_record_key"
                ]
            )

            match[
                "learned_vote_removed"
            ] = True

        if selected_item is None:
            st.session_state.pop(f"learning_conflict_{part_index}", None)
            st.session_state.pop(f"learning_conflict_confirmed_{part_index}", None)
            return

        match["manual_choice"] = not same_catalog_item(selected_item, match.get("ai_price_item"))
        conflict = selection_conflict(match["ai_part"], selected_item)
        if conflict:
            st.session_state[f"learning_conflict_{part_index}"] = conflict
            st.session_state[f"learning_conflict_confirmed_{part_index}"] = False
            st.session_state["feedback_notice"] = (
                f'Changed "{match["ai_part"].get("name")}" to {selected_item["name"]}. '
                "This specification conflicts with the detected part. It will be used in the "
                "current estimate but will not be learned unless you explicitly confirm it."
            )
            return

        st.session_state.pop(f"learning_conflict_{part_index}", None)
        st.session_state.pop(f"learning_conflict_confirmed_{part_index}", None)

        saved = save_match_feedback(
            match["ai_part"],
            selected_item,
            ai_item=match.get(
                "ai_price_item"
            ),
        )

        match[
            "saved_record_key"
        ] = saved

        if saved:

            st.session_state[
                "feedback_notice"
            ] = (
                f'Remembered: '
                f'"{match["ai_part"].get("name")}" '
                f'→ {selected_item["name"]}. '
                f'Similar parts will get this '
                f'suggestion in future drawings.'
            )

        else:

            st.session_state[
                "feedback_notice"
            ] = (
                "Could not save this choice "
                "for future suggestions."
            )

        return


def confirm_conflicting_learning(part_index, match, selected_item):
    saved = save_match_feedback(
        match["ai_part"],
        selected_item,
        ai_item=match.get("ai_price_item"),
    )
    match["saved_record_key"] = saved
    if saved:
        st.session_state[f"learning_conflict_confirmed_{part_index}"] = True
        st.session_state["feedback_notice"] = (
            f'Confirmed and remembered unusual match: '
            f'"{match["ai_part"].get("name")}" → {selected_item["name"]}.'
        )
    else:
        st.session_state["feedback_notice"] = (
            "Could not save this unusual match for future suggestions."
        )



# --------------------------------------------------
# CLEAR
# --------------------------------------------------

def clear_product():
    st.session_state[
        "project_name"
    ] = ""

    st.session_state[
        "items"
    ] = []

    st.session_state[
        "furniture_name"
    ] = ""

    st.session_state[
        "furniture_type"
    ] = "Other"

    st.session_state[
        "overall_width"
    ] = 0.0

    st.session_state[
        "overall_height"
    ] = 0.0

    st.session_state[
        "overall_depth"
    ] = 0.0

    st.session_state[
        "door_count"
    ] = 0

    st.session_state[
        "shelf_count"
    ] = 0

    st.session_state[
        "drawing_notes"
    ] = ""

    st.session_state[
        "ai_result"
    ] = None

    st.session_state[
        "ai_parts"
    ] = []

    st.session_state[
        "catalog_matches"
    ] = None

    st.session_state["current_project_id"] = None
    st.session_state["project_status"] = "Draft"
    st.session_state["project_service_rows"] = []


    keys_to_delete = []

    for key in (
        st.session_state.keys()
    ):

        if (
            str(key).startswith(
                "catalog_select_"
            )
            or
            str(key).startswith(
                "catalog_quantity_"
            )
            or
            str(key).startswith(
                "market_"
            )
            or
            str(key).startswith(
                "quote_"
            )
            or
            str(key).startswith(
                "custom_"
            )
            or
            str(key).startswith(
                "no_match_resolution_"
            )
            or
            str(key).startswith(
                "generated_quote_"
            )
            or
            str(key).startswith(
                "estimate_coefficient"
            )
            or
            str(key).startswith(
                "learning_conflict_"
            )
        ):
            keys_to_delete.append(
                key
            )

    for key in keys_to_delete:

        del st.session_state[
            key
        ]

    # File uploader/project-name fallback state belongs to the current drawing.
    st.session_state.pop("drawing_upload", None)
    st.session_state.pop("pending_project_name", None)


    print(
        "Product cleared."
    )


# --------------------------------------------------
# VALID PRICE ITEMS
# --------------------------------------------------

valid_catalog_items = [
    item
    for item in all_items
    if is_valid_price_item(
        item
    )
]


# --------------------------------------------------
# HEADER
# --------------------------------------------------

show_sidebar(st.session_state["project_name"].strip())

show_header(
    "Cost calculator",
    "Name the project, upload its drawing, check the matched components "
    "and prepare the client quotation.",
)


# --------------------------------------------------
# PROJECT + DRAWING (FRAGMENT)
# --------------------------------------------------

render_project_and_drawing(
    all_items,
    apply_ai_data,
)

project_name = str(st.session_state.get("project_name") or "").strip()


# --------------------------------------------------
# AI DRAWING RESULTS
# --------------------------------------------------

if st.session_state[
    "ai_result"
]:

    st.divider()

    st.success(
        "Drawing analyzed. "
        "Review the detected components "
        "and catalog matches below."
    )


    with st.expander(
        "Drawing extraction",
        expanded=False,
    ):

        for part in (
            st.session_state[
                "ai_parts"
            ]
        ):

            st.write(
                f'**'
                f'{part.get("name", "Unknown")}'
                f'**'
            )

            st.write(
                "Material:",
                part.get(
                    "material"
                )
                or "-",
            )

            st.write(
                "Quantity:",
                part.get(
                    "quantity"
                )
                or "–",
            )

            st.write(
                "Dimensions:",
                (
                    f'{part.get("length_mm") or "–"} × '
                    f'{part.get("width_mm") or "–"} × '
                    f'{part.get("height_mm") or "–"} mm'
                ),
            )

            st.divider()


# --------------------------------------------------
# CATALOG MATCHING
# --------------------------------------------------

matches = (
    st.session_state[
        "catalog_matches"
    ]
)


confirmed_items = []

confirmed_choices = []

review_issues = []
st.session_state["current_review_issues"] = []


if not matches:
    step_header(3, "Review components")
    st.caption(
        "The components found in the drawing appear here after it is analysed. "
        "You can also add components manually in step 4."
    )


if matches:

    step_header(3, "Review components")

    st.caption(
        "AI chooses an existing price-list "
        "row. You can override any choice — "
        "your choice is remembered and "
        "suggested for similar parts later."
    )


    feedback_notice = (
        st.session_state.pop(
            "feedback_notice",
            None,
        )
    )

    if feedback_notice:

        st.toast(
            feedback_notice
        )


    for match in matches:

        part_index = (
            match[
                "part_index"
            ]
        )

        st.markdown(
            f'<div id="component-{part_index}"></div>',
            unsafe_allow_html=True,
        )

        ai_part = (
            match[
                "ai_part"
            ]
        )

        ai_price_item = (
            match.get(
                "price_item"
            )
        )

        confidence = float(
            match.get(
                "confidence",
                0,
            )
            or 0
        )

        reason = (
            match.get(
                "reason"
            )
            or ""
        )


        st.divider()

        st.markdown(
            f'### '
            f'{ai_part.get("name", "Unknown part")}'
        )


        i1, i2, i3, i4 = (
            st.columns(4)
        )


        with i1:

            st.caption(
                "Detected material"
            )

            st.write(
                ai_part.get(
                    "material"
                )
                or "–"
            )


        with i2:

            st.caption(
                "Drawing quantity"
            )

            st.write(
                ai_part.get(
                    "quantity"
                )
                or "–"
            )


        with i3:

            st.caption(
                "Dimensions"
            )

            length = (
                ai_part.get(
                    "length_mm"
                )
                or "–"
            )

            width = (
                ai_part.get(
                    "width_mm"
                )
                or "–"
            )

            height = (
                ai_part.get(
                    "height_mm"
                )
                or "–"
            )

            st.write(
                f"{length} × "
                f"{width} × "
                f"{height} mm"
            )


        with i4:

            st.caption(
                "AI catalog confidence"
            )

            st.write(
                f"{confidence * 100:.0f}%"
            )


        # ------------------------------------------
        # OPTIONS
        # ------------------------------------------

        options = [
            None
        ]

        # Put AI's selected item first.
        if ai_price_item is not None:

            options.append(
                ai_price_item
            )


        ai_item_key = None

        if ai_price_item:

            ai_item_key = (
                ai_price_item[
                    "name"
                ],
                ai_price_item[
                    "category"
                ],
                ai_price_item[
                    "unit"
                ],
                float(
                    ai_price_item[
                        "price"
                    ]
                ),
            )


        for item in (
            valid_catalog_items
        ):

            item_key = (
                item["name"],
                item["category"],
                item["unit"],
                float(
                    item["price"]
                ),
            )

            if (
                ai_item_key
                and
                item_key
                == ai_item_key
            ):
                continue

            options.append(
                item
            )


        default_index = (
            1
            if ai_price_item
            is not None
            else 0
        )


        selected_item = (
            st.selectbox(
                "Price-list item",
                options,
                index=default_index,
                format_func=(
                    lambda item:
                    (
                        "NO MATCH / choose manually"
                        if item is None
                        else
                        (
                            f'{item["name"]} '
                            f'— {item["category"]} '
                            f'— {item["unit"]} '
                            f'— €{float(item["price"]):.2f}'
                        )
                    )
                ),
                key=(
                    f"catalog_select_"
                    f"{part_index}"
                ),
                on_change=(
                    remember_catalog_choice
                ),
                args=(
                    part_index,
                ),
            )
        )


        if match.get(
            "learned"
        ) and same_catalog_item(
            selected_item,
            ai_price_item,
        ):

            st.info(
                f"Suggested from a previous "
                f"estimator's choice. "
                f"{reason}"
            )

        elif match.get(
            "learned"
        ):

            st.caption(
                f'Previous estimators suggested '
                f'{ai_price_item["name"]}. '
                f'Your choice replaces it.'
            )

        elif reason:

            st.caption(
                f"AI reasoning: "
                f"{reason}"
            )


        if selected_item is not None:

            ai_choice = same_catalog_item(
                selected_item,
                ai_price_item,
            )

            show_match_explanation(
                explain_match(
                    ai_part,
                    selected_item,
                    ai_checks=(
                        match.get("checks")
                        if ai_choice
                        else None
                    ),
                    learned_reason=(
                        reason
                        if ai_choice
                        and match.get("learned")
                        else None
                    ),
                ),
                manual_choice=not ai_choice,
            )


        selected_match_conflict = (
            selection_conflict(ai_part, selected_item)
            if selected_item is not None
            and not selected_item.get("market")
            and not selected_item.get("custom")
            else None
        )

        if selected_match_conflict:
            add_review_issue(
                review_issues,
                part_index,
                ai_part.get("name"),
                "Specification conflict",
                selected_match_conflict,
            )
            st.warning(
                selected_match_conflict
                + " If this is intentional, confirm it below before the choice is learned."
            )
            confirm_key = f"learning_conflict_confirmed_{part_index}"
            confirmed_conflict = st.checkbox(
                "I confirm this unusual specification match should be learned",
                key=confirm_key,
            )
            if st.button(
                "Confirm and remember unusual match",
                key=f"confirm_learning_conflict_button_{part_index}",
                disabled=not confirmed_conflict or bool(match.get("saved_record_key")),
            ):
                confirm_conflicting_learning(part_index, match, selected_item)
                st.rerun()



        # ------------------------------------------
        # NO MATCH WORKFLOW
        # ------------------------------------------

        if selected_item is None:

            st.warning(
                "No catalog match is selected. Choose a market offer or create "
                "a custom item if you want to price this part. If you do nothing, "
                "the part simply remains unresolved."
            )

            resolution = st.radio(
                "Resolve this part",
                [
                    "Check market price",
                    "Create custom item",
                ],
                index=None,
                key=f"no_match_resolution_{part_index}",
                horizontal=True,
            )

            if resolution == "Check market price":
                market_item = show_market_check(match, ai_part, None)
                if market_item is None:
                    add_review_issue(
                        review_issues,
                        part_index,
                        ai_part.get("name"),
                        "No match",
                        "No market offer has been selected for this unresolved part.",
                    )
                    continue
                selected_item = market_item

            elif resolution == "Create custom item":
                default_category, default_unit = custom_item_defaults(ai_part)

                category_options = sorted(
                    set(item["category"] for item in valid_catalog_items)
                )
                if default_category not in category_options:
                    default_category = category_options[0] if category_options else "FURNITURA"

                unit_options = sorted(set(
                    ["pcs", "set", "m", "m2", "hour"]
                    + [
                        normalize_unit(item.get("unit"))
                        for item in valid_catalog_items
                        if item.get("unit")
                    ]
                ))

                c1, c2 = st.columns(2)
                with c1:
                    custom_name = st.text_input(
                        "Custom item name",
                        value=str(ai_part.get("name") or "Custom item"),
                        key=f"custom_name_{part_index}",
                    )
                    custom_category = st.selectbox(
                        "Category",
                        category_options or [default_category],
                        index=(
                            category_options.index(default_category)
                            if default_category in category_options
                            else 0
                        ),
                        key=f"custom_category_{part_index}",
                    )
                with c2:
                    custom_unit = st.selectbox(
                        "Unit",
                        unit_options,
                        index=(
                            unit_options.index(default_unit)
                            if default_unit in unit_options
                            else 0
                        ),
                        key=f"custom_unit_{part_index}",
                    )
                    custom_price = st.number_input(
                        "Unit price (excl. VAT)",
                        min_value=0.0,
                        value=0.0,
                        step=1.0,
                        format="%.2f",
                        key=f"custom_price_{part_index}",
                    )

                if custom_price <= 0:
                    add_review_issue(
                        review_issues,
                        part_index,
                        ai_part.get("name"),
                        "Custom item",
                        "Custom item still needs a unit price.",
                    )
                    continue

                selected_item = {
                    "name": custom_name.strip() or "Custom item",
                    "category": custom_category,
                    "unit": custom_unit,
                    "price": float(custom_price),
                    "custom": True,
                }

                c1, c2 = st.columns(2)
                with c1:
                    custom_brand = st.text_input(
                        "Brand / model (optional, for the catalog)",
                        key=f"custom_brand_{part_index}",
                    )
                with c2:
                    custom_notes = st.text_input(
                        "Notes for the admin (optional)",
                        key=f"custom_notes_{part_index}",
                    )

                if st.button(
                    "Send to catalog for approval",
                    key=f"custom_save_catalog_{part_index}",
                ):
                    save_catalog_candidate({
                        **selected_item,
                        "brand_model": custom_brand,
                        "notes": custom_notes,
                        "source_type": "custom",
                        "material_type": ai_part.get("material"),
                        "component_type": ai_part.get("component_type"),
                        "added_for_part": ai_part.get("name"),
                    })

                st.caption(
                    "The custom item is included in this estimate. "
                    "Saving it to the catalog is optional."
                )

            else:
                add_review_issue(
                    review_issues,
                    part_index,
                    ai_part.get("name"),
                    "No match",
                    "Part is unresolved and is not included in the estimate yet.",
                )
                continue

        else:
            match["ignored_for_estimate"] = False


        # ------------------------------------------
        # QUANTITY
        # ------------------------------------------

        calculated_quantity = (
            calculate_quantity(
                ai_part,
                selected_item,
            )
        )


        if (
            calculated_quantity
            is None
        ):

            st.warning(
                "Quantity cannot be derived "
                "from the drawing for this unit."
            )

            quantity = (
                st.number_input(
                    "Quantity",
                    min_value=0.0,
                    value=1.0,
                    step=0.1,
                    key=(
                        f"catalog_quantity_"
                        f"{part_index}"
                    ),
                )
            )

        else:

            quantity = float(
                calculated_quantity
            )

        if quantity <= 0:
            add_review_issue(
                review_issues,
                part_index,
                ai_part.get("name"),
                "Quantity",
                "Quantity is zero, so this part contributes no price.",
            )


        # ------------------------------------------
        # PRICE
        # ------------------------------------------

        markup = get_markup(
            selected_item[
                "category"
            ]
        )


        if markup is None:

            st.error(
                "Selected catalog item has "
                "no valid coefficient."
            )

            add_review_issue(
                review_issues,
                part_index,
                ai_part.get("name"),
                "Coefficient",
                "Selected category has no valid coefficient.",
            )

            continue


        unit_price = float(
            selected_item[
                "price"
            ]
        )


        raw_cost = (
            quantity
            * unit_price
        )


        sale_cost = (
            raw_cost
            * markup
        )


        p1, p2, p3, p4, p5 = (
            st.columns(5)
        )


        with p1:

            st.metric(
                "Quantity",
                (
                    f'{quantity:.3f} '
                    f'{selected_item["unit"]}'
                ),
            )


        with p2:

            st.metric(
                "Unit price",
                (
                    f'€'
                    f'{unit_price:.2f}'
                ),
            )


        with p3:

            st.metric(
                "Category",
                selected_item[
                    "category"
                ],
            )


        with p4:

            st.metric(
                "Coefficient",
                f"×{markup}",
            )


        with p5:

            st.metric(
                "Price",
                f"€{sale_cost:.2f}",
            )


        if not selected_item.get(
            "market"
        ):

            show_market_check(
                match,
                ai_part,
                selected_item,
            )


        ai_choice_for_review = same_catalog_item(
            selected_item,
            ai_price_item,
        )

        if (
            ai_choice_for_review
            and not match.get("learned")
            and confidence < 0.70
        ):
            add_review_issue(
                review_issues,
                part_index,
                ai_part.get("name"),
                "Low match confidence",
                f"AI catalog confidence is {confidence * 100:.0f}%.",
            )

        price_warning = market_price_warning(
            match,
            selected_item,
        )

        if price_warning:
            add_review_issue(
                review_issues,
                part_index,
                ai_part.get("name"),
                "Market price difference",
                price_warning,
            )

        confirmed_items.append({
            "name": selected_item[
                "name"
            ],
            "category": selected_item[
                "category"
            ],
            "quantity": quantity,
            "unit_price": unit_price,
            "unit": selected_item[
                "unit"
            ],
            "source": (
                "custom"
                if selected_item.get("custom")
                else "market"
                if selected_item.get("market")
                else "catalog"
            ),
        })

        if (
            not selected_item.get("market")
            and not selected_item.get("custom")
            and not selection_conflict(ai_part, selected_item)
        ):

            confirmed_choices.append(
                (
                    match,
                    selected_item,
                )
            )


# --------------------------------------------------
# NEEDS REVIEW SUMMARY (FRAGMENT)
# --------------------------------------------------

st.session_state["current_review_issues"] = [
    {key: value for key, value in issue.items() if key != "_key"}
    for issue in review_issues
]

render_review_summary(matches, review_issues, len(confirmed_items))


# --------------------------------------------------
# CATEGORY COEFFICIENTS FOR THIS ESTIMATE
# --------------------------------------------------

def remember_estimate_coefficient(category):
    value = float(st.session_state[f"estimate_coefficient_{category}"])
    overrides = dict(st.session_state.get("estimate_coefficients", {}))
    if value == company_markups.get(category):
        overrides.pop(category, None)
    else:
        overrides[category] = value
    st.session_state["estimate_coefficients"] = overrides


def reset_estimate_coefficients():
    for key in list(st.session_state.keys()):
        if str(key).startswith("estimate_coefficient"):
            del st.session_state[key]


step_header(4, "Estimate")

if confirmed_items or st.session_state["items"]:
    changed_coefficients = st.session_state.get("estimate_coefficients", {})
    with st.expander(
        "Category coefficients for this estimate"
        + (f" ({len(changed_coefficients)} changed)" if changed_coefficients else ""),
        expanded=bool(changed_coefficients),
    ):
        st.caption(
            "These start from the company coefficients set in Admin. Changes here apply to "
            "this estimate only and are reset when a new drawing is analysed or Clear product is clicked."
        )
        coefficient_columns = st.columns(4)
        for column_index, (category, company_value) in enumerate(sorted(company_markups.items())):
            with coefficient_columns[column_index % 4]:
                st.number_input(
                    category.upper(),
                    min_value=0.01,
                    value=float(markups[category]),
                    step=0.1,
                    format="%.2f",
                    key=f"estimate_coefficient_{category}",
                    on_change=remember_estimate_coefficient,
                    args=(category,),
                    help=f"Company coefficient: ×{company_value:g}",
                )
        if changed_coefficients:
            st.caption(
                "Changed for this estimate: "
                + ", ".join(
                    f"{category.upper()} ×{company_markups[category]:g} → ×{value:g}"
                    for category, value in sorted(changed_coefficients.items())
                    if category in company_markups
                )
            )
            st.button(
                "Reset to company coefficients",
                on_click=reset_estimate_coefficients,
                key="estimate_coefficients_reset",
            )


# --------------------------------------------------
# AI ESTIMATE
# --------------------------------------------------

if confirmed_items:

    st.divider()

    st.subheader(
        "AI estimate"
    )


    ai_multiplier = (
        st.number_input(
            "Global project multiplier",
            min_value=0.0,
            value=float(
                company_pricing[
                    "project_multiplier"
                ]
            ),
            step=0.05,
            key="ai_multiplier",
        )
    )


    ai_estimate = (
        calculate_project(
            confirmed_items,
            markups=markups,
            project_multiplier=(
                ai_multiplier
            ),
        )
    )


    e1, e2, e3 = (
        st.columns(3)
    )


    with e1:

        st.metric(
            "Subtotal",
            (
                f'€'
                f'{ai_estimate["subtotal"]:.2f}'
            ),
        )


    with e2:

        st.metric(
            "Multiplier",
            f"×{ai_multiplier}",
        )


    with e3:

        st.metric(
            "ESTIMATED PRICE",
            (
                f'€'
                f'{ai_estimate["total"]:.2f}'
            ),
        )


    use_estimate = st.button(
        "Use this estimate",
        type="primary",
    )

    st.caption(
        "Using the estimate also confirms its "
        "price-list items, so they are suggested "
        "more strongly for similar parts later."
    )

    if use_estimate:

        st.session_state[
            "items"
        ] = [
            dict(item)
            for item
            in confirmed_items
        ]

        for match, selected_item in (
            confirmed_choices
        ):

            if match.get("saved_record_key"):
                continue

            safe_to_learn, learning_reason = should_auto_learn_match(
                match, selected_item, min_confidence=0.90
            )
            if not safe_to_learn:
                print(
                    "[LEARNING SAFETY] Not auto-learning:",
                    match["ai_part"].get("name"), "->", selected_item.get("name"),
                    "|", learning_reason,
                )
                continue

            match["saved_record_key"] = save_match_feedback(
                match["ai_part"],
                selected_item,
                ai_item=match.get("ai_price_item"),
            )

        print(
            "Estimate applied:",
            len(
                confirmed_items
            ),
            "components",
        )

        st.rerun()


# --------------------------------------------------
# MANUAL COMPONENT
# --------------------------------------------------

st.subheader(
    "Add component manually"
)


categories = sorted(
    set(
        item["category"]
        for item in all_items
        if item.get(
            "category"
        )
    )
)


selected_category = (
    st.selectbox(
        "Category filter",
        [
            "All categories"
        ]
        + categories,
    )
)


if (
    selected_category
    == "All categories"
):

    filtered_items = (
        valid_catalog_items
    )

else:

    filtered_items = [
        item
        for item
        in valid_catalog_items
        if (
            item[
                "category"
            ]
            == selected_category
        )
    ]


if filtered_items:

    manual_index = (
        st.selectbox(
            "Component",
            range(
                len(
                    filtered_items
                )
            ),
            format_func=(
                lambda index:
                (
                    f'{filtered_items[index]["name"]} '
                    f'— {filtered_items[index]["unit"]} '
                    f'— €'
                    f'{float(filtered_items[index]["price"]):.2f}'
                )
            ),
            key="manual_component",
        )
    )


    manual_item = (
        filtered_items[
            manual_index
        ]
    )


    manual_markup = (
        get_markup(
            manual_item[
                "category"
            ]
        )
    )


    m1, m2, m3, m4 = (
        st.columns(4)
    )


    with m1:

        st.metric(
            "Category",
            manual_item[
                "category"
            ],
        )


    with m2:

        st.metric(
            "Unit",
            manual_item[
                "unit"
            ],
        )


    with m3:

        st.metric(
            "Unit price",
            (
                f'€'
                f'{float(manual_item["price"]):.2f}'
            ),
        )


    with m4:

        st.metric(
            "Coefficient",
            (
                f"×{manual_markup}"
                if manual_markup
                is not None
                else "ERROR"
            ),
        )


    manual_quantity = (
        st.number_input(
            "Manual quantity",
            min_value=0.0,
            value=1.0,
            step=0.1,
            key="manual_quantity",
        )
    )


    if st.button(
        "Add manual component"
    ):

        if (
            manual_markup
            is None
        ):

            st.error(
                "This item has no "
                "valid coefficient."
            )

        else:

            st.session_state[
                "items"
            ].append({
                "name": (
                    manual_item[
                        "name"
                    ]
                ),
                "category": (
                    manual_item[
                        "category"
                    ]
                ),
                "quantity": (
                    manual_quantity
                ),
                "unit_price": float(
                    manual_item[
                        "price"
                    ]
                ),
                "unit": (
                    manual_item[
                        "unit"
                    ]
                ),
            })

            print(
                "Manual component added:",
                manual_item[
                    "name"
                ],
            )

            st.rerun()


# --------------------------------------------------
# CURRENT ESTIMATE COMPONENTS
# --------------------------------------------------

st.divider()

st.subheader(
    "Product components"
)


if not st.session_state[
    "items"
]:

    st.info(
        "No components added yet."
    )


else:

    for index, item in enumerate(
        st.session_state[
            "items"
        ]
    ):

        markup = get_markup(
            item[
                "category"
            ]
        )

        if markup is None:
            markup = 1.0


        line_price = (
            item["quantity"]
            * item["unit_price"]
            * markup
        )


        c1, c2, c3, c4, c5 = (
            st.columns(
                [
                    3,
                    1.4,
                    1.4,
                    1.4,
                    0.7,
                ]
            )
        )


        with c1:

            st.write(
                f'**'
                f'{item["name"]}'
                f'**'
            )

            source_label = {
                "custom": "Custom item",
                "market": "Market price",
                "catalog": "Catalog",
            }.get(
                item.get("source"),
                "",
            )

            category_caption = item["category"]
            if source_label:
                category_caption += f" · {source_label}"

            st.caption(
                category_caption
            )


        with c2:

            st.write(
                f'{item["quantity"]:.3f} '
                f'{item["unit"]}'
            )


        with c3:

            st.write(
                f'€'
                f'{item["unit_price"]:.2f}'
            )


        with c4:

            st.write(
                f'**€'
                f'{line_price:.2f}'
                f'**'
            )


        with c5:

            if st.button(
                "✕",
                key=(
                    f"remove_"
                    f"{index}"
                ),
            ):

                st.session_state[
                    "items"
                ].pop(
                    index
                )

                st.rerun()


# --------------------------------------------------
# FINAL TOTAL
# --------------------------------------------------

if st.session_state[
    "items"
]:

    st.divider()

    final_multiplier = (
        st.number_input(
            "Final global multiplier",
            min_value=0.0,
            value=float(
                company_pricing[
                    "project_multiplier"
                ]
            ),
            step=0.05,
            key="final_multiplier",
        )
    )


    result = calculate_project(
        st.session_state[
            "items"
        ],
        markups=markups,
        project_multiplier=(
            final_multiplier
        ),
    )


    st.subheader(
        "Final estimate"
    )


    f1, f2, f3 = (
        st.columns(3)
    )


    with f1:

        st.metric(
            "Subtotal",
            (
                f'€'
                f'{result["subtotal"]:.2f}'
            ),
        )


    with f2:

        st.metric(
            "Multiplier",
            f"×{final_multiplier}",
        )


    with f3:

        st.metric(
            "TOTAL PRICE",
            (
                f'€'
                f'{result["total"]:.2f}'
            ),
        )


    # --------------------------------------------------
    # CALCULATION BREAKDOWN
    # --------------------------------------------------

    with st.expander(
        "Calculation breakdown",
        expanded=False,
    ):
        breakdown_rows = []
        calculated_subtotal = 0.0

        for item in st.session_state["items"]:
            item_markup = get_markup(
                item.get("category")
            )
            if item_markup is None:
                continue

            item_quantity = float(
                item.get("quantity") or 0
            )
            item_unit_price = float(
                item.get("unit_price") or 0
            )
            raw_cost = item_quantity * item_unit_price
            sale_cost = raw_cost * float(item_markup)
            calculated_subtotal += sale_cost

            breakdown_rows.append({
                "Item": item.get("name") or "",
                "Qty": item_quantity,
                "Unit": item.get("unit") or "",
                "Unit price": item_unit_price,
                "Raw cost": raw_cost,
                "Category": item.get("category") or "",
                "Coefficient": float(item_markup),
                "After coefficient": sale_cost,
            })

        if breakdown_rows:
            st.dataframe(
                breakdown_rows,
                hide_index=True,
                width="stretch",
                column_config={
                    "Unit price": st.column_config.NumberColumn(
                        "Unit price",
                        format="€%.2f",
                    ),
                    "Raw cost": st.column_config.NumberColumn(
                        "Raw cost",
                        format="€%.2f",
                    ),
                    "Coefficient": st.column_config.NumberColumn(
                        "Coefficient",
                        format="×%.2f",
                    ),
                    "After coefficient": st.column_config.NumberColumn(
                        "After coefficient",
                        format="€%.2f",
                    ),
                },
            )

        b1, b2, b3 = st.columns(3)
        with b1:
            st.metric(
                "After category coefficients",
                f"€{calculated_subtotal:.2f}",
            )
        with b2:
            st.metric(
                "Global multiplier",
                f"×{final_multiplier:g}",
            )
        with b3:
            st.metric(
                "Calculated selling price",
                f"€{result['total']:.2f}",
            )

        st.caption(
            "Raw cost = quantity × catalog/custom unit price. "
            "After coefficient = raw cost × category coefficient. "
            "The project multiplier is applied to the subtotal afterwards."
        )


    render_quotation(result, project_name, LOGO_PATH)

# --------------------------------------------------
# PROJECT HISTORY SAVE
# --------------------------------------------------

st.divider()
st.subheader("Save project")
st.caption(
    "Save the current estimate as a reusable project snapshot. Reopening a project keeps "
    "the prices and selections saved at that time; it does not silently replace them with newer catalog prices."
)


def _project_snapshot():
    snapshot = {}
    exact_keys = {
        "project_name",
        "project_status",
        "items",
        "furniture_name",
        "furniture_type",
        "overall_width",
        "overall_height",
        "overall_depth",
        "door_count",
        "shelf_count",
        "drawing_notes",
        "ai_result",
        "ai_parts",
        "catalog_matches",
        "current_review_issues",
        "estimate_coefficients",
        "ai_multiplier",
        "final_multiplier",
        "quote_number_value",
        "quote_date_value",
        "quote_validity_value",
        "quote_client_name",
        "quote_product_name",
        "quote_final_client_price",
        "quote_calculated_total",
        "quote_vat_percent",
        "quote_discount_type",
        "quote_discount_value",
        "project_service_rows",
    }
    prefixes = (
        "catalog_select_",
        "catalog_quantity_",
        "no_match_resolution_",
        "custom_name_",
        "custom_category_",
        "custom_unit_",
        "custom_price_",
        "custom_brand_",
        "custom_notes_",
    )
    for key, value in st.session_state.items():
        key_text = str(key)
        if key_text in exact_keys or key_text.startswith(prefixes):
            snapshot[key_text] = value
    return snapshot


calculated_price_for_history = 0.0
if "result" in locals() and isinstance(result, dict):
    calculated_price_for_history = float(result.get("total") or 0.0)
else:
    calculated_price_for_history = float(
        st.session_state.get("quote_calculated_total") or 0.0
    )

final_price_for_history = float(
    st.session_state.get("quote_final_client_price")
    or calculated_price_for_history
    or 0.0
)

save_clicked = st.button(
    "Save project",
    type="primary",
    disabled=not project_name,
    key="save_project_history_button",
)
st.caption(
    f"Storage: {project_storage_name()}. "
    + (
        "This will update the opened project."
        if st.session_state.get("current_project_id")
        else "This will create a new project history record."
    )
)

if save_clicked:
    try:
        saved = timed(
            "save_project",
            save_project,
            snapshot=_project_snapshot(),
            project_id=st.session_state.get("current_project_id"),
            project_name=project_name,
            client_name=st.session_state.get("quote_client_name", ""),
            status=st.session_state.get("project_status", "Draft"),
            furniture_name=st.session_state.get("quote_product_name")
            or st.session_state.get("furniture_name", ""),
            quote_number=st.session_state.get("quote_number_value", ""),
            calculated_price=calculated_price_for_history,
            final_client_price=final_price_for_history,
        )
        st.session_state["current_project_id"] = saved["id"]
        st.success("Project saved to history.")
    except Exception as error:
        print("PROJECT SAVE ERROR:", repr(error))
        st.error(f"Project could not be saved: {error}")


# --------------------------------------------------
# CLEAR
# --------------------------------------------------

st.divider()


st.button(
    "Clear product",
    on_click=clear_product,
)