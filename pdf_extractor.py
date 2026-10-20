import base64
import json
from io import BytesIO

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

client = OpenAI()


# --------------------------------------------------
# STRUCTURED OUTPUT SCHEMA
# --------------------------------------------------

FURNITURE_SCHEMA = {
    "type": "object",
    "properties": {
        "furniture_name": {
            "type": "string"
        },

        "furniture_type": {
            "type": "string",
            "enum": [
                "Wardrobe",
                "Kitchen",
                "Table",
                "Desk",
                "Shelf",
                "Cabinet",
                "Bed",
                "Other",
            ],
        },

        "width_mm": {
            "type": ["number", "null"]
        },

        "height_mm": {
            "type": ["number", "null"]
        },

        "depth_mm": {
            "type": ["number", "null"]
        },

        "doors": {
            "type": ["integer", "null"]
        },

        "hinged_doors": {
            "type": ["integer", "null"]
        },

        "sliding_doors": {
            "type": ["integer", "null"]
        },

        "door_height_mm": {
            "type": ["number", "null"]
        },

        "shelves": {
            "type": ["integer", "null"]
        },

        "notes": {
            "type": "string"
        },

        "materials": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string"
                    },
                    "part": {
                        "type": "string"
                    },
                },
                "required": [
                    "name",
                    "part",
                ],
                "additionalProperties": False,
            },
        },

        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string"
                    },

                    "component_type": {
                        "type": "string",
                        "enum": [
                            "panel",
                            "glass",
                            "mirror",
                            "hardware",
                            "profile",
                            "edge",
                            "led",
                            "service",
                            "other",
                        ],
                    },

                    "material": {
                        "type": "string"
                    },

                    "quantity": {
                        "type": ["number", "null"]
                    },

                    "length_mm": {
                        "type": ["number", "null"]
                    },

                    "width_mm": {
                        "type": ["number", "null"]
                    },

                    "height_mm": {
                        "type": ["number", "null"]
                    },

                    "thickness_mm": {
                        "type": ["number", "null"]
                    },

                    "source_context": {
                        "type": "string"
                    },

                    "confidence": {
                        "type": "number"
                    },
                },

                "required": [
                    "name",
                    "component_type",
                    "material",
                    "quantity",
                    "length_mm",
                    "width_mm",
                    "height_mm",
                    "thickness_mm",
                    "source_context",
                    "confidence",
                ],

                "additionalProperties": False,
            },
        },

        "uncertain": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
    },

    "required": [
        "furniture_name",
        "furniture_type",
        "width_mm",
        "height_mm",
        "depth_mm",
        "doors",
        "hinged_doors",
        "sliding_doors",
        "door_height_mm",
        "shelves",
        "notes",
        "materials",
        "parts",
        "uncertain",
    ],

    "additionalProperties": False,
}


# --------------------------------------------------
# EXTRACTION PROMPT
# --------------------------------------------------

PROMPT = """
You are reading a technical furniture manufacturing drawing.

Your job is NOT to list whole furniture assemblies.

Your job is to extract ONLY PRICEABLE / BOM-LEVEL COMPONENTS that can
later be matched against a price catalog.

CRITICAL RULES:

1. DO NOT output whole furniture assemblies as parts.
   BAD examples:
   - Room 2 wardrobe
   - Desk
   - Shoe cabinet
   - Refrigerator cabinet
   - Wardrobe assembly

2. Instead, decompose furniture into priceable atomic components when the
   drawing provides enough information.

   GOOD examples:
   - LMDP panel
   - plywood panel
   - HDP panel
   - mirror
   - glass panel
   - drawer front
   - drawer base
   - drawer side
   - drawer back
   - hinge
   - drawer runner
   - BLUM TANDEM runner set
   - LED strip
   - metal profile
   - edge banding
   - installation/service item

3. Preserve material names exactly where possible.

   Examples:
   - LMDP
   - HDP
   - FANERA
   - MDF
   - mirror
   - glass
   - TANDEM 19 BLUMOTION
   - metal
   - veneer
   - solid wood

4. If a component is explicitly described with dimensions and quantity,
   extract it as a separate part.

5. If multiple identical components are given, combine them into one row
   with the correct quantity.

6. Do not invent dimensions.

7. Do not invent quantities.

8. Use millimeters for dimensions.

9. Use null when a value cannot be determined reliably.

10. For board/sheet pieces:
    - name should describe the physical part
    - component_type = "panel"
    - material should be the actual board/material type

11. For mirrors:
    - component_type = "mirror"
    - material = "Mirror" or exact stated mirror description

12. For glass:
    - component_type = "glass"

13. For drawer runners, hinges, handles, brackets and similar:
    - component_type = "hardware"
    - preserve brand/model text if visible

14. For LED:
    - component_type = "led"

15. Ignore drawing labels that merely identify an assembled furniture unit,
    unless that unit contains separately identifiable priceable components.

16. Ignore items explicitly marked:
    - not used
    - do not manufacture
    - do not install
    - nenaudojamos
    - negaminti
    - nemontuoti

17. If the drawing gives a bill-of-materials/table, use that information
    preferentially.

18. If the same component is described visually and also in text/table,
    avoid double-counting it.

19. source_context should briefly say where the information came from,
    for example:
    - "page 11 drawer table"
    - "page 4 mirror dimensions"
    - "drawer detail section"

20. confidence must be from 0 to 1:
    - 0.95+ = explicit text/table
    - 0.80-0.94 = very clear drawing annotation
    - below 0.80 = ambiguous

21. If a whole furniture object contains LMDP / FANERA / HDP but the drawing
    does NOT provide enough individual panel dimensions or quantities,
    DO NOT create a fake board-area estimate for the whole furniture unit.
    Put that limitation in "uncertain".

22. The output will later be matched to a real price catalog and priced
    deterministically by Python.

23. Detect the door opening system whenever the drawing provides enough
    information.

    - "doors" = total number of furniture doors that can be reliably identified.
    - "hinged_doors" = number of doors that clearly use normal furniture hinges.
    - "sliding_doors" = number of doors that clearly use a sliding system.
    - Sliding doors must NEVER be counted as hinged doors.
    - If the drawing shows doors but does not make the opening system clear,
      set hinged_doors and sliding_doors to null.
    - Never assume that every wardrobe door is hinged.
    - Do not count drawer fronts as doors.

24. "door_height_mm" is the actual height of the hinged door leaf when this
    can be reliably determined.

    - Do NOT automatically copy the overall furniture height.
    - Only use the furniture height when the drawing clearly shows that it is
      also the actual door height.
    - If several hinged doors have significantly different heights and one
      representative height cannot be used safely, return null and describe
      the different heights in "uncertain".

25. If hinges are explicitly listed in the BOM or drawing, extract them as a
    normal hardware part.

    - Preserve an explicit hinge brand/model when visible.
    - Preserve the explicit hinge quantity.
    - Do NOT create an additional generic hinge row when the drawing already
      explicitly specifies the hinges.

26. Hinges that are not explicitly listed may later be derived automatically
    by Python from confirmed hinged doors. Therefore do not invent a hinge
    quantity yourself.
"""


# --------------------------------------------------
# DERIVED HARDWARE
# --------------------------------------------------

def hinges_per_door(height_mm):
    """Return the hinge quantity for one hinged door."""

    if height_mm is None:
        return None

    try:
        height_mm = float(height_mm)
    except (TypeError, ValueError):
        return None

    if height_mm <= 0:
        return None

    if height_mm <= 900:
        return 2

    if height_mm <= 1600:
        return 3

    if height_mm <= 2100:
        return 4

    return 5


def explicit_hinges_exist(parts):
    hinge_words = (
        "hinge",
        "hinges",
        "vyris",
        "vyriai",
    )

    for part in parts:
        component_type = str(
            part.get("component_type") or ""
        ).strip().lower()

        combined = (
            str(part.get("name") or "")
            + " "
            + str(part.get("material") or "")
        ).lower()

        if (
            component_type == "hardware"
            and any(word in combined for word in hinge_words)
        ):
            return True

    return False


def add_uncertain_message(ai_result, message):
    uncertain = ai_result.get("uncertain")

    if not isinstance(uncertain, list):
        uncertain = []

    if message not in uncertain:
        uncertain.append(message)

    ai_result["uncertain"] = uncertain


def add_derived_hinges(ai_result):
    """Add a deterministic hinge component for confirmed hinged doors."""

    parts = ai_result.get("parts")

    if not isinstance(parts, list):
        parts = []

    if explicit_hinges_exist(parts):
        print(
            "Explicit hinges found in drawing/BOM. "
            "No derived hinges added."
        )
        return ai_result

    hinged_doors = ai_result.get("hinged_doors")

    try:
        hinged_doors = int(hinged_doors)
    except (TypeError, ValueError):
        hinged_doors = 0

    if hinged_doors <= 0:
        print(
            "No confirmed hinged doors. "
            "No derived hinges added."
        )
        return ai_result

    door_height_mm = ai_result.get("door_height_mm")
    per_door = hinges_per_door(door_height_mm)

    if per_door is None:
        message = (
            f"{hinged_doors} hinged door(s) were detected, "
            "but the door height could not be determined reliably, "
            "so hinge quantity was not calculated."
        )
        add_uncertain_message(ai_result, message)
        print("HINGES NOT DERIVED:", message)
        return ai_result

    total_hinges = hinged_doors * per_door

    parts.append({
        "name": "Furniture hinge",
        "component_type": "hardware",
        "material": "Hinge",
        "quantity": total_hinges,
        "length_mm": None,
        "width_mm": None,
        "height_mm": None,
        "thickness_mm": None,
        "source_context": (
            f"Derived automatically from {hinged_doors} hinged door(s), "
            f"door height {float(door_height_mm):g} mm, "
            f"{per_door} hinges per door"
        ),
        "confidence": 1.0,
    })

    ai_result["parts"] = parts

    print(
        "DERIVED HINGES:",
        hinged_doors,
        "doors x",
        per_door,
        "hinges =",
        total_hinges,
    )

    return ai_result


def post_process_result(result):
    return add_derived_hinges(result)


# --------------------------------------------------
# PDF ANALYSIS
# --------------------------------------------------

def analyze_pdf(
    file_bytes,
    filename,
):
    print(
        "Uploading PDF to OpenAI:",
        filename,
    )

    file_object = BytesIO(
        file_bytes
    )

    file_object.name = filename

    uploaded_file = (
        client.files.create(
            file=file_object,
            purpose="user_data",
        )
    )

    print(
        "OpenAI file ID:",
        uploaded_file.id,
    )

    try:
        response = client.responses.create(
            model="gpt-5.6-terra",

            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_id": uploaded_file.id,
                        },
                        {
                            "type": "input_text",
                            "text": PROMPT,
                        },
                    ],
                }
            ],

            text={
                "format": {
                    "type": "json_schema",
                    "name": "furniture_bom_extraction",
                    "strict": True,
                    "schema": FURNITURE_SCHEMA,
                }
            },
        )

    finally:
        try:
            client.files.delete(
                uploaded_file.id
            )

            print(
                "Deleted OpenAI file:",
                uploaded_file.id,
            )

        except Exception as error:
            print(
                "Could not delete OpenAI file:",
                uploaded_file.id,
                repr(error),
            )

    print(
        "PDF BOM analysis complete."
    )

    result = json.loads(
        response.output_text
    )

    result = post_process_result(
        result
    )

    print(
        "Detected BOM components:",
        len(
            result.get(
                "parts",
                [],
            )
        ),
    )

    for part in result.get(
        "parts",
        [],
    ):
        print(
            "PART:",
            part.get("name"),
            "| TYPE:",
            part.get(
                "component_type"
            ),
            "| MATERIAL:",
            part.get(
                "material"
            ),
            "| QTY:",
            part.get(
                "quantity"
            ),
        )

    return result


# --------------------------------------------------
# IMAGE ANALYSIS
# --------------------------------------------------

def analyze_image(
    file_bytes,
    filename,
):
    extension = (
        filename
        .lower()
        .split(".")[-1]
    )

    mime_types = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }

    mime_type = mime_types.get(
        extension,
        "image/jpeg",
    )

    encoded = base64.b64encode(
        file_bytes
    ).decode(
        "utf-8"
    )

    data_url = (
        f"data:{mime_type};"
        f"base64,{encoded}"
    )

    print(
        "Sending image to OpenAI:",
        filename,
    )

    response = client.responses.create(
        model="gpt-5.6-terra",

        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": data_url,
                    },
                    {
                        "type": "input_text",
                        "text": PROMPT,
                    },
                ],
            }
        ],

        text={
            "format": {
                "type": "json_schema",
                "name": "furniture_bom_extraction",
                "strict": True,
                "schema": FURNITURE_SCHEMA,
            }
        },
    )

    print(
        "Image BOM analysis complete."
    )

    result = json.loads(
        response.output_text
    )

    result = post_process_result(
        result
    )

    print(
        "Detected BOM components:",
        len(
            result.get(
                "parts",
                [],
            )
        ),
    )

    return result


# --------------------------------------------------
# MAIN ENTRY POINT
# --------------------------------------------------

def extract_furniture(
    uploaded_file,
):
    filename = (
        uploaded_file.name
    )

    extension = (
        filename
        .lower()
        .split(".")[-1]
    )

    file_bytes = (
        uploaded_file.getvalue()
    )

    print(
        "Analyzing BOM:",
        filename,
    )

    if extension == "pdf":
        return analyze_pdf(
            file_bytes,
            filename,
        )

    if extension in [
        "jpg",
        "jpeg",
        "png",
    ]:
        return analyze_image(
            file_bytes,
            filename,
        )

    raise ValueError(
        f"AI analysis is not supported "
        f"for .{extension} files."
    )