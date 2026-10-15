import json
import re
from statistics import mean, median
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI

from component_matcher import EXPECTED_UNITS, normalize_unit, safe_float


load_dotenv()

client = OpenAI()

LT_VAT_RATE = 0.21

THICKNESS_TOLERANCE_MM = 0.5

UNUSUAL_PRICE_FACTOR = 2.5

MIN_OFFERS_FOR_UNUSUAL_CHECK = 3

MARKET_SEARCH_INSTRUCTIONS = """
You research current market prices in Lithuania for a furniture manufacturer.

Use web search to find the given furniture component (board, glass, hardware,
LED, etc.) in shops and suppliers that sell in Lithuania. Prefer Lithuanian
companies; their websites often end in .lt but may also use .com, .eu etc.

Rules:
1. Only report prices you actually saw on a product page during this search.
   Never estimate, round or invent a price.
2. url must be the exact product page where the price is shown.
3. Only include the same product or a closely similar one (same material,
   same type). Set match to "exact" or "similar".
4. If the part has a thickness, only include products with that thickness.
   If it has no thickness, include the common thicknesses you find.
5. thickness_mm is the product's thickness in mm if stated, otherwise null.
6. price_unit is the unit the price is for: "m2", "m", "pcs", "set",
   "sheet" (a whole board/glass sheet) or "other".
7. For "sheet" prices, and for "pcs" prices of a fixed-size panel or glass
   piece, give item_length_mm and item_width_mm if shown, otherwise null.
8. includes_vat: shops for private customers usually show prices with 21%
   PVM. Set it to true unless the page says the price is without PVM.
9. Return at most 8 offers, preferring different shops.
10. Write notes in English.
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
                    "thickness_mm": {"type": ["number", "null"]},
                    "item_length_mm": {"type": ["number", "null"]},
                    "item_width_mm": {"type": ["number", "null"]},
                    "includes_vat": {"type": "boolean"},
                    "match": {"type": "string", "enum": ["exact", "similar"]},
                },
                "required": [
                    "shop",
                    "url",
                    "product_name",
                    "price",
                    "price_unit",
                    "thickness_mm",
                    "item_length_mm",
                    "item_width_mm",
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


NUMBER = r"(\d+(?:[.,]\d+)?)"

SIZE_PATTERN = re.compile(
    NUMBER + r"\s*[x×*]\s*" + NUMBER + r"(?:\s*[x×*]\s*" + NUMBER + r")?(?:\s*mm\b)?"
)

MAX_THICKNESS_MM = 100


def plausible_thickness(value):
    thickness = safe_float(value)
    if thickness is None or thickness <= 0 or thickness > MAX_THICKNESS_MM:
        return None
    return thickness


def thickness_from_text(text):
    text = str(text or "").lower()
    for size in SIZE_PATTERN.finditer(text):
        if size.group(3):
            return plausible_thickness(size.group(3).replace(",", "."))
    found = re.search(NUMBER + r"\s*mm\b", SIZE_PATTERN.sub(" ", text))
    if not found:
        return None
    return plausible_thickness(found.group(1).replace(",", "."))


def target_thickness_for(ai_part, price_item=None):
    thickness = plausible_thickness(ai_part.get("thickness_mm"))
    if thickness:
        return thickness, "drawing"
    if price_item is not None:
        thickness = thickness_from_text(price_item.get("name"))
        if thickness:
            return thickness, "price list"
    return None, ""


def offer_thickness(offer):
    thickness = plausible_thickness(offer.get("thickness_mm"))
    if thickness:
        return thickness
    return thickness_from_text(offer.get("product_name"))


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

    if offer_unit == target_unit or {offer_unit, target_unit} <= {"pcs", "set"}:
        return price, ""

    if target_unit == "m2" and offer_unit in ("sheet", "pcs"):
        length = safe_float(offer.get("item_length_mm"))
        width = safe_float(offer.get("item_width_mm"))
        if not length or not width:
            return None, f"priced per {offer.get('price_unit')}, size unknown"
        return price / ((length / 1000) * (width / 1000)), ""

    return None, f"priced per {offer.get('price_unit')}"


def split_unusual_prices(offers):
    if len(offers) < MIN_OFFERS_FOR_UNUSUAL_CHECK:
        return offers, []
    typical = median(offer["price_excl_vat_per_unit"] for offer in offers)
    normal = []
    unusual = []
    for offer in offers:
        price = offer["price_excl_vat_per_unit"]
        if price < typical / UNUSUAL_PRICE_FACTOR or price > typical * UNUSUAL_PRICE_FACTOR:
            unusual.append((offer, f"unusual price, €{price:.2f} vs typical €{typical:.2f}"))
        else:
            normal.append(offer)
    return normal, unusual


def summarize_group(offers, thickness):
    exact = [offer for offer in offers if offer.get("match") == "exact"]
    averaged = exact or offers
    for offer in offers:
        offer["in_average"] = offer in averaged
    prices = [offer["price_excl_vat_per_unit"] for offer in averaged]
    return {
        "thickness": thickness,
        "label": f"{thickness:g} mm" if thickness else "thickness not stated",
        "average": round(mean(prices), 2),
        "min": min(prices),
        "max": max(prices),
        "offers": len(averaged),
        "similar_left_out": len(offers) - len(averaged),
        "exact_only": bool(exact) and len(exact) < len(offers),
        "shops": len({domain_of(offer.get("url")) for offer in averaged}),
        "shop_names": sorted({offer.get("shop") or domain_of(offer.get("url")) for offer in averaged}),
    }


def summarize_offers(offers, target_unit, allowed_domains=None, target_thickness=None):
    used = []
    skipped = []

    for offer in offers:
        url = str(offer.get("url") or "")
        domain = domain_of(url)
        if not url.startswith(("http://", "https://")):
            skipped.append((offer, "no link"))
            continue
        if allowed_domains:
            if domain not in allowed_domains:
                skipped.append((offer, "not found in the web search"))
                continue
        elif not domain.endswith(".lt"):
            skipped.append((offer, "not a Lithuanian site"))
            continue

        thickness = offer_thickness(offer) if target_unit == "m2" else None
        if (
            target_thickness
            and thickness
            and abs(thickness - target_thickness) > THICKNESS_TOLERANCE_MM
        ):
            skipped.append((offer, f"different thickness ({thickness:g} mm)"))
            continue

        unit_price, problem = offer_price_per_unit(offer, target_unit)
        if unit_price is None:
            skipped.append((offer, problem))
            continue

        used.append({
            **offer,
            "thickness": thickness,
            "price_excl_vat_per_unit": round(unit_price, 2),
        })

    grouped = {}
    for offer in used:
        key = None if target_thickness else offer["thickness"]
        grouped.setdefault(key, []).append(offer)

    for key, group_offers in list(grouped.items()):
        normal, unusual = split_unusual_prices(group_offers)
        grouped[key] = normal
        skipped.extend(unusual)

    kept = {id(offer) for group_offers in grouped.values() for offer in group_offers}
    used = [offer for offer in used if id(offer) in kept]

    groups = sorted(
        (summarize_group(group_offers, target_thickness or key) for key, group_offers in grouped.items()),
        key=lambda group: (group["thickness"] is None, group["thickness"] or 0),
    )

    for group in groups:
        notes = []
        if group["offers"] == 1:
            notes.append("only 1 offer")
        thinner = [
            other for other in groups
            if other["thickness"] and group["thickness"] and other["thickness"] < group["thickness"]
        ]
        if any(group["average"] < other["average"] / UNUSUAL_PRICE_FACTOR for other in thinner):
            notes.append("much cheaper than thinner boards, check this price")
        group["note"] = "; ".join(notes)

    single = groups[0] if len(groups) == 1 else None

    return {
        "unit": target_unit,
        "target_thickness": target_thickness,
        "offers": used,
        "skipped": [{**offer, "skip_reason": reason} for offer, reason in skipped],
        "groups": groups,
        "average": single["average"] if single else None,
        "min": single["min"] if single else None,
        "max": single["max"] if single else None,
        "count": len(used),
        "shops": len({domain_of(offer.get("url")) for offer in used}),
        "shop_names": sorted({offer.get("shop") or domain_of(offer.get("url")) for offer in used}),
    }


def search_market_prices(ai_part, price_item=None):
    target_unit = target_unit_for(ai_part, price_item)
    target_thickness, thickness_source = (
        target_thickness_for(ai_part, price_item) if target_unit == "m2" else (None, "")
    )

    request = {
        "part": {
            "name": ai_part.get("name"),
            "component_type": ai_part.get("component_type"),
            "material": ai_part.get("material"),
            "thickness_mm": target_thickness,
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
        target_thickness=target_thickness,
    )
    summary["thickness_source"] = thickness_source
    summary["notes"] = result.get("notes", "")

    print(
        "Market prices:", summary["count"], "usable offers from", summary["shops"], "shops,",
        len(summary["skipped"]), "skipped,", len(summary["groups"]), "thickness groups",
    )
    return summary
