from pathlib import Path

import streamlit as st

from calculator import calculate_project
from price_reader import read_price_data
from pdf_extractor import extract_furniture

from component_matcher import (
    match_parts_to_catalog,
    calculate_quantity,
    is_valid_price_item,
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


price_data = load_data()

all_items = price_data[
    "items"
]

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
                ),
            )

            st.write(
                "Dimensions:",
                (
                    f'{part.get("length_mm")} × '
                    f'{part.get("width_mm")} × '
                    f'{part.get("height_mm")} mm'
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


if matches:

    st.divider()

    st.subheader(
        "AI price-list matching"
    )

    st.caption(
        "AI chooses an existing price-list "
        "row. You can override any choice."
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
                or "-"
            )


        with i2:

            st.caption(
                "Drawing quantity"
            )

            st.write(
                ai_part.get(
                    "quantity"
                )
                or "-"
            )


        with i3:

            st.caption(
                "Dimensions"
            )

            length = (
                ai_part.get(
                    "length_mm"
                )
                or "-"
            )

            width = (
                ai_part.get(
                    "width_mm"
                )
                or "-"
            )

            st.write(
                f"{length} × "
                f"{width} mm"
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
            )
        )


        if reason:

            st.caption(
                f"AI reasoning: "
                f"{reason}"
            )


        # ------------------------------------------
        # NO MATCH
        # ------------------------------------------

        if selected_item is None:

            st.warning(
                "This drawing part is not "
                "included in the estimate yet."
            )

            continue


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


    if st.button(
        "Use this estimate",
        type="primary",
    ):

        st.session_state[
            "items"
        ] = [
            dict(item)
            for item
            in confirmed_items
        ]

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
# CLEAR
# --------------------------------------------------

st.divider()


st.button(
    "Clear product",
    on_click=clear_product,
)