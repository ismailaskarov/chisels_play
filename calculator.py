CATEGORY_ALIASES = {
    "metalas": "metal",
    "metal": "metal",
    "lmdp": "lmdp",
    "paslauga": "paslauga",
    "masyvas": "masyvas",
    "furnitura": "furnitura",
    "stiklas": "stiklas",
    "be kofo": "be kofo",
}


def normalize_category(category):
    category_key = category.strip().lower()
    return CATEGORY_ALIASES.get(category_key, category_key)


def calculate_line(name, category, quantity, unit_price, markups):
    category_key = normalize_category(category)

    if category_key not in markups:
        raise ValueError(f"Unknown category: {category}")

    raw_cost = quantity * unit_price
    markup = markups[category_key]
    sale_cost = raw_cost * markup

    return {
        "name": name,
        "category": category,
        "quantity": quantity,
        "unit_price": unit_price,
        "raw_cost": raw_cost,
        "markup": markup,
        "sale_cost": sale_cost,
    }


def calculate_project(items, markups, project_multiplier=1.0):
    calculated_items = []
    subtotal = 0.0

    for item in items:
        result = calculate_line(
            item["name"],
            item["category"],
            item["quantity"],
            item["unit_price"],
            markups,
        )

        calculated_items.append(result)
        subtotal += result["sale_cost"]

    total = subtotal * project_multiplier

    return {
        "items": calculated_items,
        "subtotal": subtotal,
        "project_multiplier": project_multiplier,
        "total": total,
    }