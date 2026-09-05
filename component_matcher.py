import json
from dotenv import load_dotenv
from openai import OpenAI


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

def build_catalog(price_items):
    catalog = []

    for original_index, item in enumerate(
        price_items
    ):
        if not is_valid_price_item(item):
            continue

        catalog.append({
            "catalog_index": len(catalog),
            "original_index": original_index,
            "name": str(item["name"]),
            "category": str(item["category"]),
            "unit": str(item["unit"]),
            "price": float(item["price"]),
        })

    return catalog


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

The human will be able to override the selected catalog item afterwards.
"""


# --------------------------------------------------
# MATCH USING OPENAI
# --------------------------------------------------

def match_parts_to_catalog(
    ai_result,
    price_items,
):
    parts = ai_result.get(
        "parts",
        [],
    )

    catalog = build_catalog(
        price_items
    )

    if not parts:
        return []

    if not catalog:
        raise ValueError(
            "No valid items were found in the price catalog."
        )

    # Small, explicit representation for OpenAI.
    part_payload = []

    for index, part in enumerate(parts):
        part_payload.append({
            "part_index": index,
            "name": part.get("name"),
            "material": part.get("material"),
            "quantity": part.get("quantity"),
            "length_mm": part.get("length_mm"),
            "width_mm": part.get("width_mm"),
            "height_mm": part.get("height_mm"),
            "thickness_mm": part.get("thickness_mm"),
        })

    catalog_payload = []

    for item in catalog:
        catalog_payload.append({
            "catalog_index": item["catalog_index"],
            "name": item["name"],
            "category": item["category"],
            "unit": item["unit"],
            "price": item["price"],
        })

    prompt_data = {
        "furniture_name": ai_result.get(
            "furniture_name"
        ),
        "furniture_type": ai_result.get(
            "furniture_type"
        ),
        "drawing_notes": ai_result.get(
            "notes"
        ),
        "parts": part_payload,
        "catalog": catalog_payload,
    }

    print(
        "Sending",
        len(parts),
        "parts and",
        len(catalog),
        "catalog items to OpenAI matcher.",
    )

    response = client.responses.create(
        model="gpt-5.6-terra",
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": MATCHING_INSTRUCTIONS,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            prompt_data,
                            ensure_ascii=False,
                        ),
                    }
                ],
            },
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
                                    "part_index": {
                                        "type": "integer"
                                    },
                                    "catalog_index": {
                                        "type": "integer"
                                    },
                                    "confidence": {
                                        "type": "number"
                                    },
                                    "reason": {
                                        "type": "string"
                                    },
                                },
                                "required": [
                                    "part_index",
                                    "catalog_index",
                                    "confidence",
                                    "reason",
                                ],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": [
                        "matches"
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    result = json.loads(
        response.output_text
    )

    raw_matches = result.get(
        "matches",
        [],
    )

    # Index responses by part number.
    returned = {}

    for match in raw_matches:
        part_index = match.get(
            "part_index"
        )

        if isinstance(
            part_index,
            int,
        ):
            returned[
                part_index
            ] = match

    final_matches = []

    for part_index, part in enumerate(parts):
        raw_match = returned.get(
            part_index,
            {},
        )

        catalog_index = raw_match.get(
            "catalog_index",
            -1,
        )

        confidence = raw_match.get(
            "confidence",
            0,
        )

        reason = raw_match.get(
            "reason",
            "No matcher result.",
        )

        matched_catalog_item = None
        matched_price_item = None

        if (
            isinstance(catalog_index, int)
            and 0 <= catalog_index < len(catalog)
        ):
            matched_catalog_item = (
                catalog[catalog_index]
            )

            original_index = (
                matched_catalog_item[
                    "original_index"
                ]
            )

            matched_price_item = (
                price_items[
                    original_index
                ]
            )

        final_match = {
            "part_index": part_index,
            "ai_part": part,
            "catalog_index": (
                catalog_index
                if matched_price_item
                is not None
                else -1
            ),
            "price_item": matched_price_item,
            "confidence": float(
                confidence or 0
            ),
            "reason": reason,
        }

        final_matches.append(
            final_match
        )

        print()
        print(
            "AI PART:",
            part.get("name"),
        )

        print(
            "MATERIAL:",
            part.get("material"),
        )

        if matched_price_item:
            print(
                "MATCH:",
                matched_price_item[
                    "name"
                ],
            )

            print(
                "CATEGORY:",
                matched_price_item[
                    "category"
                ],
            )

            print(
                "UNIT:",
                matched_price_item[
                    "unit"
                ],
            )

            print(
                "CONFIDENCE:",
                round(
                    float(
                        confidence or 0
                    ),
                    3,
                ),
            )

            print(
                "REASON:",
                reason,
            )

        else:
            print(
                "MATCH: NO MATCH"
            )

            print(
                "REASON:",
                reason,
            )

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

    if pieces is None or pieces <= 0:
        pieces = 1.0

    length_mm = safe_float(
        ai_part.get("length_mm")
    )

    width_mm = safe_float(
        ai_part.get("width_mm")
    )

    height_mm = safe_float(
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

        if dimension_2 is None:
            if height_mm != dimension_1:
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
        return pieces


    # ----------------------------------------------
    # SET
    # ----------------------------------------------

    if unit == "set":
        return pieces


    # ----------------------------------------------
    # FIXED
    # ----------------------------------------------

    if unit == "fixed":
        return pieces


    # ----------------------------------------------
    # HOURS
    # ----------------------------------------------

    # We deliberately do NOT let AI invent labor time.
    if unit == "hour":
        return None


    return None