from openpyxl import load_workbook


ITEM_COLUMNS = {
    "name": "name",
    "category": "category",
    "unit": "unit",
    "price": "price",
    "price (eur)": "price",
    "material_type": "material_type",
    "component_type": "component_type",
    "brand": "brand",
    "model": "model",
    "brand_model": "brand_model",
    "thickness_mm": "thickness_mm",
    "keywords": "keywords",
    "synonyms": "synonyms",
    "description": "description",
    "allowed_use": "allowed_use",
    "match_priority": "match_priority",
    "needs_review": "needs_review",
    "notes": "notes",
    "supplier": "supplier",
    "source_url": "source_url",
    "active": "active",
}


def _header_key(value):
    return " ".join(str(value or "").strip().lower().replace(" ", "_").split()).replace("_(eur)", " (eur)")


def _yes(value):
    return str(value or "").strip().lower() in ("yes", "true", "1", "y", "taip")


def read_catalog_rows(source):
    workbook = load_workbook(source, data_only=True)
    sheet = workbook["All_Items"] if "All_Items" in workbook.sheetnames else workbook.worksheets[0]

    headers = [ITEM_COLUMNS.get(_header_key(cell.value)) for cell in sheet[1]]
    if "name" not in headers:
        headers = ["name", "category", "unit", "price"]

    items = []

    for row in sheet.iter_rows(min_row=2, values_only=True):
        values = {}
        for key, value in zip(headers, row):
            if key and value not in (None, ""):
                values[key] = value

        name = values.get("name")
        if not name:
            continue

        price = values.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0.0

        item = {
            "name": str(name).strip(),
            "category": str(values.get("category")).strip() if values.get("category") else "REVIEW",
            "unit": str(values.get("unit")).strip() if values.get("unit") else "REVIEW",
            "price": price,
        }

        brand_model = values.get("brand_model") or " ".join(
            str(values[key]).strip() for key in ("brand", "model") if values.get(key)
        )
        if brand_model:
            item["brand_model"] = str(brand_model).strip()

        for key in (
            "material_type",
            "component_type",
            "keywords",
            "synonyms",
            "description",
            "allowed_use",
            "match_priority",
            "notes",
            "supplier",
            "source_url",
        ):
            if values.get(key) not in (None, ""):
                item[key] = str(values[key]).strip()

        try:
            thickness = float(values.get("thickness_mm"))
            if thickness > 0:
                item["thickness_mm"] = thickness
        except (TypeError, ValueError):
            pass

        if "needs_review" in values:
            item["needs_review"] = _yes(values["needs_review"])
        if "active" in values:
            item["active"] = _yes(values["active"])

        items.append(item)

    return items


def read_price_data(excel_path):
    workbook = load_workbook(excel_path, data_only=True)

    items = read_catalog_rows(excel_path)

    categories_sheet = workbook["Categories"]

    markups = {}

    for row in range(2, categories_sheet.max_row + 1):
        category = categories_sheet[f"A{row}"].value
        coefficient = categories_sheet[f"B{row}"].value

        if not category or coefficient is None:
            continue

        category = str(category)

        if "(" in category and ")" in category:
            category = category.split("(")[-1].split(")")[0]

        markups[category.strip().lower()] = float(coefficient)

    settings = workbook["Global_Settings"]

    project_multiplier = float(settings["B2"].value or 1.0)

    return {
        "items": items,
        "markups": markups,
        "project_multiplier": project_multiplier,
    }
