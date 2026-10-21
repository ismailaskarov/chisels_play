import json
from pathlib import Path
from tempfile import NamedTemporaryFile


DEFAULT_CATALOG_ADDITIONS = Path("data") / "catalog_additions.json"


def _clean_text(value):
    return " ".join(str(value or "").strip().split())


def _identity(item):
    return (
        _clean_text(item.get("name")).casefold(),
        _clean_text(item.get("category")).casefold(),
        _clean_text(item.get("unit")).casefold(),
    )


def load_catalog_additions(path=DEFAULT_CATALOG_ADDITIONS):
    path = Path(path)
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    items = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        try:
            price = float(raw.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        name = _clean_text(raw.get("name"))
        category = _clean_text(raw.get("category"))
        unit = _clean_text(raw.get("unit"))
        if not name or not category or not unit:
            continue

        item = dict(raw)
        item.update({
            "name": name,
            "category": category,
            "unit": unit,
            "price": price,
            "catalog_addition": True,
        })
        items.append(item)

    return items


def catalog_item_exists(existing_items, candidate):
    candidate_identity = _identity(candidate)
    return any(_identity(item) == candidate_identity for item in existing_items)


def add_catalog_item(item, path=DEFAULT_CATALOG_ADDITIONS):
    """Persist one estimator-approved catalog item.

    This intentionally uses a small dedicated additions file rather than rewriting
    prices.xlsx. The app merges these additions into the live catalog on every rerun.
    It keeps the original pricing workbook safe and makes a later move to Supabase
    straightforward.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    name = _clean_text(item.get("name"))
    category = _clean_text(item.get("category"))
    unit = _clean_text(item.get("unit"))

    try:
        price = float(item.get("price"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Catalog price must be a number.") from exc

    if not name:
        raise ValueError("Catalog item name is required.")
    if not category:
        raise ValueError("Catalog category is required.")
    if not unit:
        raise ValueError("Catalog unit is required.")
    if price <= 0:
        raise ValueError("Catalog price must be greater than zero.")

    clean_item = {
        "name": name,
        "category": category,
        "unit": unit,
        "price": price,
        "catalog_addition": True,
    }

    for optional_key in (
        "source",
        "source_url",
        "source_type",
        "thickness_mm",
        "market_match",
    ):
        value = item.get(optional_key)
        if value not in (None, ""):
            clean_item[optional_key] = value

    items = load_catalog_additions(path)
    identity = _identity(clean_item)

    for existing in items:
        if _identity(existing) == identity:
            return False, existing

    items.append(clean_item)

    payload = json.dumps(items, ensure_ascii=False, indent=2)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)

    temp_path.replace(path)
    return True, clean_item
