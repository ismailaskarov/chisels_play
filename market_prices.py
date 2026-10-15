import json
from statistics import mean
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI

from component_matcher import EXPECTED_UNITS, normalize_unit, safe_float


load_dotenv()

client = OpenAI()

LT_VAT_RATE = 0.21

MARKET_SEARCH_INSTRUCTIONS = """
You research current market prices in Lithuania for a furniture manufacturer.

Use web search to find the given furniture component (board, glass, hardware,
LED, etc.) in Lithuanian online shops and suppliers (websites ending in .lt).

Rules:
1. Only report prices you actually saw on a product page during this search.
   Never estimate, round or invent a price.
2. url must be the exact product page where the price is shown.
3. Only include the same product or a closely similar one (same material,
   same type, similar thickness/size). Set match to "exact" or "similar".
4. price_unit is the unit the shop sells in: "m2", "m", "pcs", "set",
   "sheet" (a whole board/glass sheet) or "other".
5. For "sheet" prices, give the sheet length and width in mm if shown,
   otherwise null.
6. includes_vat: Lithuanian shops usually show prices with 21% PVM for
   private customers. Set it to true unless the page says the price is
   without PVM.
7. Return at most 6 offers, preferring different shops.
"""

OFFER_SCHEMA = {
    "type": "object",
    "properties": {
        "offers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shop": {"type": "string"},
                    "url": {"type": "string"},
                    "product_name": {"type": "string"},
                    "price": {"type": "number"},
                    "price_unit": {
                        "type": "string",
                        "enum": ["m2", "m", "pcs", "set", "sheet", "other"],
                    },
                    "sheet_length_mm": {"type": ["number", "null"]},
                    "sheet_width_mm": {"type": ["number", "null"]},
                    "includes_vat": {"type": "boolean"},
                    "match": {"type": "string", "enum": ["exact", "similar"]},
                },
                "required": [
                    "shop",
                    "url",
                    "product_name",
                    "price",
                    "price_unit",
                    "sheet_length_mm",
                    "sheet_width_mm",
                    "includes_vat",
                    "match",
                ],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["offers", "notes"],
    "additionalProperties": False,
}


def target_unit_for(ai_part, price_item=None):
    if price_item is not None:
        return normalize_unit(price_item.get("unit"))
    expected = EXPECTED_UNITS.get(str(ai_part.get("component_type") or "").strip().lower(), set())
    if "m2" in expected:
        return "m2"
    if "m" in expected:
        return "m"
    return "pcs"


def domain_of(url):
    try:
        host = urlparse(str(url)).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def searched_domains(response):
    domains = set()
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "web_search_call":
            continue
        action = getattr(item, "action", None)
        for source in getattr(action, "sources", None) or []:
            url = source.get("url") if isinstance(source, dict) else getattr(source, "url", None)
            if url:
                domains.add(domain_of(url))
    return domains


def offer_price_per_unit(offer, target_unit):
    price = safe_float(offer.get("price"))
    if price is None or price <= 0:
        return None, "no price"

    if offer.get("includes_vat"):
        price = price / (1 + LT_VAT_RATE)

    offer_unit = normalize_unit(offer.get("price_unit"))

    if offer_unit == "sheet":
        if target_unit != "m2":
            return None, "sold per sheet"
        length = safe_float(offer.get("sheet_length_mm"))
        width = safe_float(offer.get("sheet_width_mm"))
        if not length or not width:
            return None, "sheet size unknown"
        return price / ((length / 1000) * (width / 1000)), ""

    if offer_unit == target_unit or {offer_unit, target_unit} <= {"pcs", "set"}:
        return price, ""

    return None, f"priced per {offer.get('price_unit')}"


def summarize_offers(offers, target_unit, allowed_domains=None):
    used = []
    skipped = []

    for offer in offers:
        domain = domain_of(offer.get("url"))
        if not str(offer.get("url") or "").startswith(("http://", "https://")):
            skipped.append((offer, "no link"))
            continue
        if not domain.endswith(".lt"):
            skipped.append((offer, "not a Lithuanian site"))
            continue
        if allowed_domains and domain not in allowed_domains:
            skipped.append((offer, "not found in the web search"))
            continue

        unit_price, problem = offer_price_per_unit(offer, target_unit)
        if unit_price is None:
            skipped.append((offer, problem))
            continue

        used.append({**offer, "price_excl_vat_per_unit": round(unit_price, 2)})

    prices = [offer["price_excl_vat_per_unit"] for offer in used]

    return {
        "unit": target_unit,
        "offers": used,
        "skipped": [{**offer, "skip_reason": reason} for offer, reason in skipped],
        "average": round(mean(prices), 2) if prices else None,
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "count": len(prices),
    }


def search_market_prices(ai_part, price_item=None):
    target_unit = target_unit_for(ai_part, price_item)

    request = {
        "part": {
            "name": ai_part.get("name"),
            "component_type": ai_part.get("component_type"),
            "material": ai_part.get("material"),
            "thickness_mm": ai_part.get("thickness_mm"),
            "length_mm": ai_part.get("length_mm"),
            "width_mm": ai_part.get("width_mm"),
        },
        "price_list_item": (
            {"name": price_item.get("name"), "unit": price_item.get("unit")}
            if price_item is not None
            else None
        ),
        "compare_per_unit": target_unit,
    }

    print("Searching market prices for:", ai_part.get("name"))

    response = client.responses.create(
        model="gpt-5.6-terra",
        tools=[{
            "type": "web_search",
            "user_location": {"type": "approximate", "country": "LT"},
        }],
        include=["web_search_call.action.sources"],
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": MARKET_SEARCH_INSTRUCTIONS}],
            },
            {
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": json.dumps(request, ensure_ascii=False),
                }],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "market_prices",
                "strict": True,
                "schema": OFFER_SCHEMA,
            }
        },
    )

    result = json.loads(response.output_text)

    summary = summarize_offers(
        result.get("offers", []),
        target_unit,
        allowed_domains=searched_domains(response),
    )
    summary["notes"] = result.get("notes", "")

    print(
        "Market prices:", summary["count"], "usable offers,",
        len(summary["skipped"]), "skipped, average", summary["average"],
    )
    return summary
