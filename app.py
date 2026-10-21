import csv
import io
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from calculator import calculate_project
from price_reader import read_price_data
from pdf_extractor import extract_furniture
from feedback_store import (
    delete_match_feedback,
    feedback_storage_name,
    load_match_feedback,
    record_votes,
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
from market_prices import search_market_prices, target_unit_for
from catalog_store import add_catalog_item, catalog_item_key, load_catalog_additions
from quote_generator import (
    build_quote_pdf,
    calculate_quote_totals,
    quote_filename,
)


PRICE_FILE = Path("data") / "prices.xlsx"
LOGO_PATH = Path("data") / "logo.jpg"



# --------------------------------------------------
# CONFIG
# --------------------------------------------------

st.set_page_config(
    page_title="Chisels Play Cost Calculator",
    layout="wide",
)


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


@st.cache_data(ttl=300)
def load_added_items():
    return load_catalog_additions()


price_data = load_data()

added_items = load_added_items()

all_items = (
    price_data[
        "items"
    ]
    + added_items
)

markups = price_data[
    "markups"
]


# --------------------------------------------------
# SESSION STATE
# --------------------------------------------------

DEFAULT_SESSION_STATE = {
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
                "additem_"
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


def show_market_check(
    match,
    ai_part,
    selected_item,
):
    part_index = match["part_index"]

    result = match.get(
        "market_result"
    )

    with st.expander(
        "Market price check",
        expanded=(
            result is not None
            or selected_item is None
        ),
    ):

        if st.button(
            (
                "Check market prices"
                if result is None
                else "Search again"
            ),
            key=(
                f"market_check_"
                f"{part_index}"
            ),
        ):

            with st.spinner(
                "Searching shops..."
            ):

                try:

                    result = search_market_prices(
                        ai_part,
                        selected_item,
                    )

                    result["searched_for"] = (
                        selected_item["name"]
                        if selected_item is not None
                        else ai_part.get("name")
                    )

                    match[
                        "market_result"
                    ] = result

                    match.pop(
                        "market_error",
                        None,
                    )

                except Exception as error:

                    print(
                        "MARKET PRICE ERROR:",
                        repr(error),
                    )

                    match[
                        "market_error"
                    ] = str(error)

            st.rerun()

        if match.get(
            "market_error"
        ):

            st.error(
                f'Market price search failed: '
                f'{match["market_error"]}'
            )

        if result is None:

            st.caption(
                "Searches online shops for this "
                "part. Prices are shown "
                "without 21% VAT. The price-list "
                "price always comes first."
            )

            return None

        unit = result["unit"]

        thickness_text = ""

        if result.get(
            "target_thickness"
        ):

            thickness_text = (
                f' Thickness: '
                f'{result["target_thickness"]:g} mm '
                f'(from the {result["thickness_source"]}).'
            )

        st.caption(
            f'Searched for: '
            f'{result.get("searched_for")}. '
            f'Prices per {unit}, without VAT.'
            f'{thickness_text}'
        )

        groups = result["groups"]

        if result.get("shops") == 1:

            st.warning(
                f'All prices come from one shop '
                f'({", ".join(result.get("shop_names") or [])}), '
                f"so this is that shop's price, "
                f'not a market average.'
            )

        list_price = None

        if (
            selected_item is not None
            and normalize_unit(
                selected_item["unit"]
            )
            == unit
        ):

            list_price = float(
                selected_item["price"]
            )

        if not groups:

            st.warning(
                "No usable prices were found."
            )

        elif len(groups) == 1:

            group = groups[0]

            c1, c2, c3, c4 = (
                st.columns(4)
            )

            with c1:

                st.metric(
                    "Market average",
                    f'€{group["average"]:.2f}',
                )

            with c2:

                st.metric(
                    "Lowest",
                    f'€{group["min"]:.2f}',
                )

            with c3:

                st.metric(
                    "Highest",
                    f'€{group["max"]:.2f}',
                )

            with c4:

                st.metric(
                    "Shops / offers",
                    f'{group["shops"]} / {group["offers"]}',
                )

            if group.get("note"):

                st.caption(
                    f'Note: {group["note"]}.'
                )

            if group["similar_left_out"]:

                st.caption(
                    f'The average uses only exact '
                    f'matches. {group["similar_left_out"]} '
                    f'similar product(s) are listed '
                    f'below but not averaged.'
                )

            if list_price is not None:

                difference = (
                    (list_price - group["average"])
                    / group["average"]
                    * 100
                )

                st.info(
                    f'Price list: €{list_price:.2f}/{unit}, '
                    f'{abs(difference):.0f}% '
                    f'{"above" if difference > 0 else "below"} '
                    f'the market average. The price-list '
                    f'price is used in the estimate.'
                )

        else:

            st.warning(
                "Different thicknesses were found, so "
                "prices are shown separately for each "
                "thickness."
            )

            group_rows = []

            for group in groups:

                row = {
                    "Thickness": group["label"],
                    f"Average € / {unit}": group["average"],
                    "Lowest": group["min"],
                    "Highest": group["max"],
                    "Shops / offers": (
                        f'{group["shops"]} / {group["offers"]}'
                    ),
                    "Similar, not averaged": (
                        group["similar_left_out"]
                    ),
                    "Note": group.get("note") or "",
                }

                if list_price is not None:

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

            st.dataframe(
                group_rows,
                hide_index=True,
            )

            if list_price is not None:

                st.caption(
                    f'Price list: €{list_price:.2f}/{unit}. '
                    f'The price-list price is used in '
                    f'the estimate.'
                )

        if result["offers"]:

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
                            f'€{offer["price"]:.2f} / '
                            f'{offer["price_unit"]}'
                            + (
                                " incl. VAT"
                                if offer["includes_vat"]
                                else " excl. VAT"
                            )
                        ),
                        f"€ per {unit} excl. VAT": (
                            offer[
                                "price_excl_vat_per_unit"
                            ]
                        ),
                        "Match": offer["match"],
                        "In average": (
                            "✓"
                            if offer.get("in_average")
                            else "–"
                        ),
                        "Link": offer["url"],
                    }
                    for offer in result["offers"]
                ],
                column_config={
                    "Link": st.column_config.LinkColumn(
                        "Link"
                    ),
                },
                hide_index=True,
            )

        if result["skipped"]:

            st.caption(
                "Not used: "
                + "; ".join(
                    f'{offer.get("shop")} '
                    f'({offer["skip_reason"]})'
                    for offer in result["skipped"]
                )
            )

        if result.get("notes"):

            st.caption(
                f'Search notes: '
                f'{result["notes"]}'
            )

        if (
            selected_item is not None
            or not groups
        ):
            return None

        group = groups[0]

        if len(groups) > 1:

            group_labels = [
                candidate["label"]
                for candidate in groups
            ]

            chosen_label = st.selectbox(
                "Thickness to use",
                group_labels,
                key=(
                    f"market_thickness_"
                    f"{part_index}"
                ),
            )

            group = groups[
                group_labels.index(
                    chosen_label
                )
            ]

        category_options = sorted(
            set(
                item["category"]
                for item in valid_catalog_items
            )
        )

        default_category = (
            MARKET_CATEGORY_BY_TYPE.get(
                str(
                    ai_part.get("component_type")
                    or ""
                ).lower()
            )
        )

        category = st.selectbox(
            "Category for the coefficient",
            category_options,
            index=(
                category_options.index(
                    default_category
                )
                if default_category
                in category_options
                else 0
            ),
            key=(
                f"market_category_"
                f"{part_index}"
            ),
        )

        if not st.toggle(
            (
                f'Use market price '
                f'€{group["average"]:.2f}/{unit} '
                f'in the estimate'
            ),
            key=(
                f"market_use_"
                f"{part_index}"
            ),
        ):
            return None

        thickness_name = (
            f', {group["label"]}'
            if group["thickness"]
            else ""
        )

        return {
            "name": (
                f'{ai_part.get("name")} '
                f'(market price{thickness_name})'
            ),
            "category": category,
            "unit": unit,
            "price": group["average"],
            "market": True,
        }


def market_choices_for_new_item(
    match,
    ai_part,
):
    result = match.get(
        "market_result"
    )

    if not result:
        return []

    unit = result["unit"]

    base_name = str(
        ai_part.get("material")
        or ai_part.get("name")
        or ""
    ).strip()

    choices = []

    for group in result["groups"]:

        thickness_name = (
            f' {group["label"]}'
            if group["thickness"]
            else ""
        )

        group_offers = [
            offer
            for offer in result["offers"]
            if offer.get("in_average")
            and offer.get("thickness") == group["thickness"]
        ]

        choices.append({
            "label": (
                f'Market average{thickness_name}: '
                f'€{group["average"]:.2f}/{unit} '
                f'({group["shops"]} shop(s), '
                f'{group["offers"]} offer(s))'
            ),
            "name": f"{base_name}{thickness_name}".strip(),
            "price": group["average"],
            "unit": unit,
            "url": (
                group_offers[0]["url"]
                if group_offers
                else ""
            ),
            "thickness": group["thickness"],
        })

    for offer in result["offers"]:

        choices.append({
            "label": (
                f'{offer["shop"]} — '
                f'{offer["product_name"]} — '
                f'€{offer["price_excl_vat_per_unit"]:.2f}/{unit}'
            ),
            "name": offer["product_name"],
            "price": offer["price_excl_vat_per_unit"],
            "unit": unit,
            "url": offer["url"],
            "thickness": offer.get("thickness"),
        })

    return choices


def show_add_item_form(
    match,
    ai_part,
):
    part_index = match["part_index"]

    with st.expander(
        "＋ Add price-list item",
        expanded=False,
    ):

        choices = market_choices_for_new_item(
            match,
            ai_part,
        )

        sources = ["My own price"]

        if choices:
            sources.append(
                "Market price"
            )

        source = st.radio(
            "Price source",
            sources,
            horizontal=True,
            key=(
                f"additem_source_"
                f"{part_index}"
            ),
        )

        if not choices:

            st.caption(
                "To use a market price, run "
                "\"Check market prices\" for this "
                "part first."
            )

        chosen = None

        choice_id = "own"

        if source == "Market price":

            labels = [
                choice["label"]
                for choice in choices
            ]

            picked_label = st.selectbox(
                "Market result",
                labels,
                key=(
                    f"additem_market_"
                    f"{part_index}"
                ),
            )

            chosen = choices[
                labels.index(
                    picked_label
                )
            ]

            choice_id = (
                f"market{labels.index(picked_label)}"
            )

        category_options = sorted(
            set(
                item["category"]
                for item in valid_catalog_items
                if get_markup(
                    item["category"]
                )
                is not None
            )
        )

        default_category = (
            MARKET_CATEGORY_BY_TYPE.get(
                str(
                    ai_part.get("component_type")
                    or ""
                ).lower()
            )
        )

        unit_options = sorted(
            set(
                normalize_unit(
                    item["unit"]
                )
                for item in valid_catalog_items
            )
        )

        default_unit = (
            chosen["unit"]
            if chosen
            else target_unit_for(
                ai_part
            )
        )

        if default_unit not in unit_options:
            unit_options.append(
                default_unit
            )

        default_name = (
            chosen["name"]
            if chosen
            else str(
                ai_part.get("material")
                or ai_part.get("name")
                or ""
            ).strip()
        )

        default_thickness = (
            chosen["thickness"]
            if chosen
            else ai_part.get("thickness_mm")
        )

        widget_suffix = (
            f"{part_index}_"
            f"{choice_id}"
        )

        with st.form(
            key=(
                f"additem_form_"
                f"{part_index}"
            ),
        ):

            name = st.text_input(
                "Name",
                value=default_name,
                key=(
                    f"additem_name_"
                    f"{widget_suffix}"
                ),
            )

            f1, f2, f3 = st.columns(3)

            with f1:

                category = st.selectbox(
                    "Category (sets the coefficient)",
                    category_options,
                    index=(
                        category_options.index(
                            default_category
                        )
                        if default_category
                        in category_options
                        else 0
                    ),
                    key=(
                        f"additem_category_"
                        f"{widget_suffix}"
                    ),
                )

            with f2:

                unit = st.selectbox(
                    "Unit",
                    unit_options,
                    index=unit_options.index(
                        default_unit
                    ),
                    key=(
                        f"additem_unit_"
                        f"{widget_suffix}"
                    ),
                )

            with f3:

                price = st.number_input(
                    "Price excl. VAT (€)",
                    min_value=0.0,
                    value=float(
                        chosen["price"]
                        if chosen
                        else 0.0
                    ),
                    step=1.0,
                    format="%.2f",
                    key=(
                        f"additem_price_"
                        f"{widget_suffix}"
                    ),
                )

            source_url = st.text_input(
                "Supplier link (optional)",
                value=(
                    chosen["url"]
                    if chosen
                    else ""
                ),
                key=(
                    f"additem_url_"
                    f"{widget_suffix}"
                ),
            )

            f4, f5 = st.columns(2)

            with f4:

                thickness = st.number_input(
                    "Thickness mm (optional, 0 = not set)",
                    min_value=0.0,
                    value=float(
                        default_thickness
                        or 0.0
                    ),
                    step=1.0,
                    key=(
                        f"additem_thickness_"
                        f"{widget_suffix}"
                    ),
                )

            with f5:

                brand_model = st.text_input(
                    "Brand / model (optional)",
                    key=(
                        f"additem_brand_"
                        f"{widget_suffix}"
                    ),
                )

            if chosen:

                st.caption(
                    f'Market prices are per '
                    f'{chosen["unit"]}, without VAT.'
                )

            submitted = st.form_submit_button(
                "Save and use",
                type="primary",
            )

        if not submitted:
            return

        new_key = catalog_item_key({
            "name": name,
            "category": category,
            "unit": unit,
        })

        if any(
            catalog_item_key(item) == new_key
            for item in all_items
        ):

            st.error(
                "An item with this name, category "
                "and unit already exists. Choose it "
                "from the Price-list item list."
            )

            return

        try:

            new_item = add_catalog_item(
                name=name,
                category=category,
                unit=unit,
                price=price,
                source_type=(
                    "market"
                    if chosen
                    else "manual"
                ),
                source_url=source_url,
                material_type=ai_part.get(
                    "material"
                ),
                component_type=ai_part.get(
                    "component_type"
                ),
                thickness_mm=thickness,
                brand_model=brand_model,
                added_for_part=ai_part.get(
                    "name"
                ),
            )

        except Exception as error:

            st.error(
                f"Could not add the item: "
                f"{error}"
            )

            return

        load_added_items.clear()

        st.session_state[
            f"additem_pick_"
            f"{part_index}"
        ] = catalog_item_key(
            new_item
        )

        st.session_state[
            "feedback_notice"
        ] = (
            f'Added "{new_item["name"]}" '
            f'(€{new_item["price"]:.2f}/{new_item["unit"]}) '
            f'to the price list and selected it.'
        )

        st.rerun()


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
            return

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


# --------------------------------------------------
# CLEAR
# --------------------------------------------------

def clear_product():
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
                "additem_"
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

st.title(
    "Chisels Play Cost Calculator"
)


# --------------------------------------------------
# FILE UPLOAD
# --------------------------------------------------

st.subheader(
    "Upload drawing / model"
)


uploaded_file = st.file_uploader(
    "Upload PDF or image",
    type=[
        "pdf",
        "jpg",
        "jpeg",
        "png",
    ],
)


if uploaded_file is not None:

    st.success(
        f"Uploaded: "
        f"{uploaded_file.name}"
    )


    extension = (
        uploaded_file.name
        .lower()
        .split(".")[-1]
    )


    if extension in [
        "jpg",
        "jpeg",
        "png",
    ]:

        st.image(
            uploaded_file,
            width=600,
        )


    if st.button(
        "Analyze and build estimate",
        type="primary",
    ):

        try:

            with st.spinner(
                "Reading drawing with AI..."
            ):

                print(
                    "Starting drawing analysis:",
                    uploaded_file.name,
                )

                ai_data = (
                    extract_furniture(
                        uploaded_file
                    )
                )

                apply_ai_data(
                    ai_data
                )


            with st.spinner(
                "Matching drawing parts "
                "to the real price catalog..."
            ):

                matches = (
                    match_parts_to_catalog(
                        ai_data,
                        all_items,
                        feedback=(
                            load_match_feedback()
                        ),
                    )
                )

                st.session_state[
                    "catalog_matches"
                ] = matches


            print(
                "Drawing analysis "
                "and catalog matching complete."
            )


            st.rerun()


        except Exception as error:

            print(
                "AI PROCESSING ERROR:",
                repr(error),
            )

            if (
                st.session_state[
                    "catalog_matches"
                ]
                is None
            ):

                st.session_state[
                    "ai_result"
                ] = None

                st.session_state[
                    "ai_parts"
                ] = []

            st.error(
                f"AI processing failed: "
                f"{error}"
            )


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


if matches:

    st.divider()

    st.subheader(
        "AI price-list matching"
    )

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


        picked_key = st.session_state.pop(
            f"additem_pick_"
            f"{part_index}",
            None,
        )

        if picked_key is not None:

            picked_item = next(
                (
                    option
                    for option in options
                    if option is not None
                    and catalog_item_key(option)
                    == picked_key
                ),
                None,
            )

            if picked_item is not None:

                st.session_state[
                    f"catalog_select_"
                    f"{part_index}"
                ] = picked_item

                default_index = 0

                market_result = match.get(
                    "market_result"
                )

                remember_catalog_choice(
                    part_index
                )

                st.session_state.pop(
                    "feedback_notice",
                    None,
                )

                if market_result is not None:

                    match[
                        "market_result"
                    ] = market_result


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
                            + (
                                " · added"
                                if item.get("source_type")
                                else ""
                            )
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


        show_add_item_form(
            match,
            ai_part,
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


        # ------------------------------------------
        # NO MATCH
        # ------------------------------------------

        if selected_item is None:

            market_item = show_market_check(
                match,
                ai_part,
                None,
            )

            if market_item is None:

                st.warning(
                    "This drawing part is not "
                    "included in the estimate yet."
                )

                continue

            selected_item = market_item


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
        })

        if not selected_item.get(
            "market"
        ):

            confirmed_choices.append(
                (
                    match,
                    selected_item,
                )
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
                price_data[
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

            if match.get(
                "saved_record_key"
            ):
                continue

            match[
                "saved_record_key"
            ] = save_match_feedback(
                match["ai_part"],
                selected_item,
                ai_item=match.get(
                    "ai_price_item"
                ),
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
# DRAWING INFORMATION
# --------------------------------------------------

st.divider()

st.subheader(
    "Drawing details"
)


st.text_input(
    "Furniture name",
    key="furniture_name",
)


st.selectbox(
    "Furniture type",
    [
        "Wardrobe",
        "Kitchen",
        "Table",
        "Desk",
        "Shelf",
        "Cabinet",
        "Bed",
        "Other",
    ],
    key="furniture_type",
)


d1, d2, d3 = (
    st.columns(3)
)


with d1:

    st.number_input(
        "Overall width (mm)",
        min_value=0.0,
        step=10.0,
        key="overall_width",
    )


with d2:

    st.number_input(
        "Overall height (mm)",
        min_value=0.0,
        step=10.0,
        key="overall_height",
    )


with d3:

    st.number_input(
        "Overall depth (mm)",
        min_value=0.0,
        step=10.0,
        key="overall_depth",
    )


d1, d2 = (
    st.columns(2)
)


with d1:

    st.number_input(
        "Doors",
        min_value=0,
        step=1,
        key="door_count",
    )


with d2:

    st.number_input(
        "Shelves",
        min_value=0,
        step=1,
        key="shelf_count",
    )


st.text_area(
    "Notes",
    key="drawing_notes",
)


# --------------------------------------------------
# MANUAL COMPONENT
# --------------------------------------------------

st.divider()

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

            st.caption(
                item[
                    "category"
                ]
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
                price_data[
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
    # CLIENT QUOTATION
    # --------------------------------------------------

    st.divider()

    st.subheader(
        "Client quotation"
    )

    st.caption(
        "The calculated price stays unchanged internally. "
        "Set the final client price separately if you want "
        "to adjust it based on the market or commercial decision."
    )

    q1, q2, q3 = st.columns(3)

    with q1:
        quote_number = st.text_input(
            "Quote number",
            value=(
                st.session_state.get(
                    "quote_number_value"
                )
                or f"CP-{date.today():%Y%m%d}"
            ),
            key="quote_number_value",
        )

    with q2:
        quote_date = st.date_input(
            "Quote date",
            value=date.today(),
            key="quote_date_value",
        )

    with q3:
        validity_date = st.date_input(
            "Validity date",
            value=date.today() + timedelta(days=30),
            key="quote_validity_value",
        )

    q4, q5 = st.columns(2)

    with q4:
        client_name = st.text_input(
            "Client name",
            key="quote_client_name",
        )

    with q5:
        quote_product_name = st.text_input(
            "Product / furniture name",
            value=(
                st.session_state.get(
                    "furniture_name"
                )
                or "Furniture"
            ),
            key="quote_product_name",
        )

    price_col1, price_col2, price_col3 = st.columns(3)

    with price_col1:
        st.metric(
            "Calculated price",
            f'€{result["total"]:.2f}',
        )

    with price_col2:
        final_client_price = st.number_input(
            "Final client price (excl. VAT)",
            min_value=0.0,
            value=float(
                result["total"]
            ),
            step=50.0,
            key="quote_final_client_price",
        )

    with price_col3:
        price_adjustment = (
            final_client_price
            - float(result["total"])
        )

        st.metric(
            "Price adjustment",
            f'€{price_adjustment:+.2f}',
        )

    st.markdown(
        "**Additional services**"
    )

    default_services = [
        {
            "Service": "Matavimas",
            "Quantity": 1.0,
            "Price excl. VAT": 0.0,
        },
        {
            "Service": "Pristatymas",
            "Quantity": 1.0,
            "Price excl. VAT": 0.0,
        },
        {
            "Service": "Montavimas",
            "Quantity": 1.0,
            "Price excl. VAT": 0.0,
        },
        {
            "Service": "Kita paslauga",
            "Quantity": 1.0,
            "Price excl. VAT": 0.0,
        },
    ]

    edited_services = st.data_editor(
        default_services,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Service": st.column_config.TextColumn(
                "Service",
                required=True,
            ),
            "Quantity": st.column_config.NumberColumn(
                "Quantity",
                min_value=0.0,
                step=1.0,
            ),
            "Price excl. VAT": st.column_config.NumberColumn(
                "Price excl. VAT",
                min_value=0.0,
                step=10.0,
                format="€%.2f",
            ),
        },
        key="quote_services_editor",
    )

    service_rows = []

    if hasattr(edited_services, "to_dict"):
        edited_service_rows = edited_services.to_dict(
            orient="records"
        )
    else:
        edited_service_rows = list(
            edited_services
        )

    for row in edited_service_rows:
        service_rows.append({
            "name": row.get(
                "Service"
            ),
            "quantity": row.get(
                "Quantity",
                1.0,
            ),
            "price": row.get(
                "Price excl. VAT",
                0.0,
            ),
        })

    settings1, settings2, settings3 = st.columns(3)

    with settings1:
        vat_percent = st.number_input(
            "VAT %",
            min_value=0.0,
            value=21.0,
            step=1.0,
            key="quote_vat_percent",
        )

    with settings2:
        discount_type_label = st.selectbox(
            "Discount type",
            [
                "Amount (€)",
                "Percent (%)",
            ],
            key="quote_discount_type",
        )

    with settings3:
        discount_value = st.number_input(
            "Discount",
            min_value=0.0,
            value=0.0,
            step=10.0,
            key="quote_discount_value",
        )

    discount_mode = (
        "percent"
        if discount_type_label == "Percent (%)"
        else "amount"
    )

    quote_totals = calculate_quote_totals(
        furniture_price=final_client_price,
        services=service_rows,
        vat_percent=vat_percent,
        discount_value=discount_value,
        discount_mode=discount_mode,
    )

    preview1, preview2, preview3, preview4 = st.columns(4)

    with preview1:
        st.metric(
            "Before discount",
            f'€{quote_totals["subtotal_before_discount"]:.2f}',
        )

    with preview2:
        st.metric(
            "Discount",
            f'€{quote_totals["discount_amount"]:.2f}',
        )

    with preview3:
        st.metric(
            f'VAT {vat_percent:g}%',
            f'€{quote_totals["vat_amount"]:.2f}',
        )

    with preview4:
        st.metric(
            "CLIENT TOTAL",
            f'€{quote_totals["total_with_vat"]:.2f}',
        )

    if st.button(
        "Generate quotation",
        type="primary",
        key="generate_quote_button",
    ):

        if not str(client_name).strip():
            st.error(
                "Enter the client name before generating the quotation."
            )

        elif not str(quote_number).strip():
            st.error(
                "Enter the quote number before generating the quotation."
            )

        else:
            st.session_state[
                "generated_quote_bytes"
            ] = build_quote_pdf(
                quote_number=quote_number,
                quote_date=quote_date,
                validity_date=validity_date,
                client_name=client_name,
                furniture_name=quote_product_name,
                furniture_price=final_client_price,
                services=service_rows,
                vat_percent=vat_percent,
                discount_value=discount_value,
                discount_mode=discount_mode,
                logo_path=LOGO_PATH,
            )

            st.session_state[
                "generated_quote_filename"
            ] = quote_filename(
                quote_number,
                client_name,
            )

            st.success(
                "Quotation PDF generated. Download it below."
            )

    if st.session_state.get(
        "generated_quote_bytes"
    ):

        st.download_button(
            "Download quotation (.pdf)",
            data=st.session_state[
                "generated_quote_bytes"
            ],
            file_name=st.session_state.get(
                "generated_quote_filename",
                "quotation.pdf",
            ),
            mime="application/pdf",
            key="download_quote_button",
        )


def delete_selected_corrections():
    for source_key in (
        st.session_state.get(
            "feedback_delete_select"
        )
        or []
    ):

        delete_match_feedback(
            source_key
        )

    st.session_state[
        "feedback_delete_select"
    ] = []


st.divider()

st.subheader(
    "Learned corrections"
)


if st.toggle(
    "Show learned corrections",
    key="show_learned_corrections",
):

    if (
        feedback_storage_name()
        == "Supabase"
    ):

        st.caption(
            "Stored in Supabase: shared by "
            "all users and kept permanently."
        )

    else:

        st.warning(
            "Stored in a local file on the "
            "server. On Streamlit Cloud it is "
            "lost when the app restarts. Set "
            "up Supabase to keep it."
        )


    corrections = sorted(
        [
            record
            for record in (
                load_match_feedback()
            )
            if record_votes(record) > 0
        ],
        key=lambda record: str(
            record.get("updated_at")
            or ""
        ),
        reverse=True,
    )


    if not corrections:

        st.info(
            "No corrections saved yet."
        )

    else:

        correction_rows = [
            {
                "Part": record.get(
                    "source_name"
                ),
                "Material": record.get(
                    "source_material"
                ),
                "Type": record.get(
                    "component_type"
                ),
                "Chosen item": record.get(
                    "catalog_name"
                ),
                "Category": record.get(
                    "catalog_category"
                ),
                "Unit": record.get(
                    "catalog_unit"
                ),
                "AI suggested": (
                    record.get(
                        "ai_catalog_name"
                    )
                    or "-"
                ),
                "Times chosen": record_votes(
                    record
                ),
                "Last chosen": str(
                    record.get("updated_at")
                    or ""
                )[:10],
            }
            for record in corrections
        ]


        st.dataframe(
            correction_rows,
            hide_index=True,
        )


        csv_buffer = io.StringIO()

        writer = csv.DictWriter(
            csv_buffer,
            fieldnames=list(
                correction_rows[0].keys()
            ),
        )

        writer.writeheader()

        writer.writerows(
            correction_rows
        )

        st.download_button(
            "Download CSV",
            data=(
                csv_buffer
                .getvalue()
                .encode("utf-8-sig")
            ),
            file_name=(
                "learned_corrections.csv"
            ),
            mime="text/csv",
        )


        corrections_by_key = {
            record["source_key"]: record
            for record in corrections
        }

        selected_for_delete = (
            st.multiselect(
                "Wrong corrections to delete",
                list(
                    corrections_by_key.keys()
                ),
                format_func=(
                    lambda source_key:
                    (
                        f'{corrections_by_key[source_key].get("source_name")} '
                        f'→ {corrections_by_key[source_key].get("catalog_name")} '
                        f'({record_votes(corrections_by_key[source_key])}×)'
                    )
                ),
                key="feedback_delete_select",
            )
        )

        st.button(
            "Delete selected",
            disabled=not selected_for_delete,
            on_click=delete_selected_corrections,
        )


# --------------------------------------------------
# CLEAR
# --------------------------------------------------

st.divider()


st.button(
    "Clear product",
    on_click=clear_product,
)