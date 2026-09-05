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
"""


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

    print(
        "PDF BOM analysis complete."
    )

    result = json.loads(
        response.output_text
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