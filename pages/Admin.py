import csv
import hmac
import io
import os
import time
from pathlib import Path

import streamlit as st

from catalog_store import (
    add_catalog_item,
    approve_catalog_item,
    catalog_item_status,
    catalog_storage_name,
    catalog_to_excel,
    delete_catalog_item,
    import_catalog_items,
    list_catalog_items,
    load_pricing,
    price_list_imported,
    save_pricing_settings,
    update_catalog_item,
)
from component_matcher import normalize_unit
from feedback_store import (
    copy_local_feedback_to_supabase,
    delete_match_feedback,
    feedback_for_item,
    feedback_storage_name,
    load_match_feedback,
    local_feedback_missing_from_supabase,
    normalize_text,
    record_votes,
)
from price_reader import read_catalog_rows, read_price_data, read_pricing_sheets
from ui import apply_style, show_sidebar


PRICE_FILE = Path("data") / "prices.xlsx"
MAX_PASSWORD_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
BASE_UNITS = ["pcs", "set", "m", "m2", "hour"]
SEARCH_KEYS = ("name", "category", "supplier", "brand_model", "keywords", "synonyms", "notes", "material_type")


st.set_page_config(
    page_title="Admin · Chisels Play",
    layout="wide",
)

apply_style()
show_sidebar()


def admin_password():
    password = os.getenv("ADMIN_PASSWORD")
    if password:
        return password
    try:
        return st.secrets.get("ADMIN_PASSWORD")
    except Exception:
        return None


@st.cache_data
def load_price_data():
    return read_price_data(PRICE_FILE)


def csv_bytes(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def run_change(action, message):
    try:
        result = action()
    except Exception as error:
        st.error(f"Could not save the change: {error}")
        return
    st.cache_data.clear()
    st.session_state["admin_message"] = message(result) if callable(message) else message
    st.rerun()


def editor_rows(value):
    if hasattr(value, "to_dict"):
        return value.to_dict(orient="records")
    return list(value)


def coefficient_changes(current, new_markups, new_multiplier):
    changes = []
    for category in sorted(set(current["markups"]) | set(new_markups)):
        old_value = current["markups"].get(category)
        new_value = new_markups.get(category)
        if old_value != new_value:
            changes.append(
                f"{category.upper()}: "
                + (f"×{old_value:g}" if old_value is not None else "new")
                + " → "
                + (f"×{new_value:g}" if new_value is not None else "removed")
            )
    if new_multiplier is not None and new_multiplier != current["project_multiplier"]:
        changes.append(f'Project multiplier: ×{current["project_multiplier"]:g} → ×{new_multiplier:g}')
    return changes


def options_with(options, value):
    if value and value not in options:
        return [value] + options
    return options


def import_message(result):
    parts = [f'{len(result["added"])} added', f'{len(result["updated"])} updated', f'{result["unchanged"]} unchanged']
    if result["duplicates"]:
        parts.append(f'{len(result["duplicates"])} duplicate rows skipped')
    if result["skipped"]:
        parts.append(f'{len(result["skipped"])} invalid rows skipped')
    return "Import finished: " + ", ".join(parts) + "."


def item_fields(item, prefix, categories, units):
    c1, c2, c3 = st.columns(3)
    with c1:
        name = st.text_input("Name", item.get("name") or "", key=f"{prefix}_name")
        category_options = options_with(categories, item.get("category"))
        category = st.selectbox(
            "Category",
            category_options,
            index=category_options.index(item["category"]) if item.get("category") in category_options else 0,
            key=f"{prefix}_category",
        )
        unit_options = options_with(units, item.get("unit"))
        unit = st.selectbox(
            "Unit",
            unit_options,
            index=unit_options.index(item["unit"]) if item.get("unit") in unit_options else 0,
            key=f"{prefix}_unit",
        )
        price = st.number_input(
            "Price (excl. VAT)",
            min_value=0.0,
            value=float(item.get("price") or 0),
            step=1.0,
            format="%.2f",
            key=f"{prefix}_price",
        )
        thickness = st.number_input(
            "Thickness (mm, 0 = none)",
            min_value=0.0,
            value=float(item.get("thickness_mm") or 0),
            step=1.0,
            key=f"{prefix}_thickness",
        )
    with c2:
        material_type = st.text_input("Material type", item.get("material_type") or "", key=f"{prefix}_material")
        component_type = st.text_input("Component type", item.get("component_type") or "", key=f"{prefix}_component")
        brand_model = st.text_input("Brand / model", item.get("brand_model") or "", key=f"{prefix}_brand")
        supplier = st.text_input("Supplier", item.get("supplier") or "", key=f"{prefix}_supplier")
        source_url = st.text_input("Supplier / source URL", item.get("source_url") or "", key=f"{prefix}_url")
    with c3:
        keywords = st.text_input("Keywords", item.get("keywords") or "", key=f"{prefix}_keywords")
        synonyms = st.text_input("Synonyms", item.get("synonyms") or "", key=f"{prefix}_synonyms")
        description = st.text_input("Description", item.get("description") or "", key=f"{prefix}_description")
        allowed_use = st.text_input("Allowed use", item.get("allowed_use") or "", key=f"{prefix}_allowed_use")
        notes = st.text_area("Notes", item.get("notes") or "", key=f"{prefix}_notes", height=68)
        needs_review = st.checkbox("Needs review", bool(item.get("needs_review")), key=f"{prefix}_needs_review")

    return {
        "name": name,
        "category": category,
        "unit": unit,
        "price": price,
        "thickness_mm": thickness,
        "material_type": material_type,
        "component_type": component_type,
        "brand_model": brand_model,
        "supplier": supplier,
        "source_url": source_url,
        "keywords": keywords,
        "synonyms": synonyms,
        "description": description,
        "allowed_use": allowed_use,
        "notes": notes,
        "needs_review": needs_review,
    }


def item_details(item):
    details = [
        f'Source: {item.get("source_type") or "-"}',
        f'Added for part: {item.get("added_for_part") or "-"}',
        f'Saved: {str(item.get("created_at") or "")[:10] or "-"}',
        f'Approved by: {item.get("approved_by") or "-"}',
        f'Stored in: {item["storage"]}',
    ]
    st.caption(" · ".join(details))
    if item.get("source_url"):
        st.markdown(f'[Open product page]({item["source_url"]})')


st.title("Admin")

password = admin_password()
if not password:
    st.error("The admin page is locked. Add ADMIN_PASSWORD to the Streamlit secrets to use it.")
    st.stop()

if not st.session_state.get("admin_signed_in"):
    locked_for = st.session_state.get("admin_locked_until", 0) - time.time()
    if locked_for > 0:
        st.error(f"Too many wrong passwords. Try again in {int(locked_for // 60) + 1} minute(s).")
        st.stop()
    entered = st.text_input("Admin password", type="password", key="admin_password_input")
    if st.button("Sign in"):
        if hmac.compare_digest(entered.encode("utf-8"), str(password).encode("utf-8")):
            st.session_state["admin_signed_in"] = True
            st.session_state["admin_failed_attempts"] = 0
            st.session_state.pop("admin_password_input", None)
            st.rerun()
        time.sleep(1)
        attempts = st.session_state.get("admin_failed_attempts", 0) + 1
        st.session_state["admin_failed_attempts"] = attempts
        if attempts >= MAX_PASSWORD_ATTEMPTS:
            st.session_state["admin_failed_attempts"] = 0
            st.session_state["admin_locked_until"] = time.time() + LOCKOUT_SECONDS
            st.rerun()
        st.error(f"Wrong password ({MAX_PASSWORD_ATTEMPTS - attempts} tries left).")
    st.stop()

with st.sidebar:
    admin_name = st.text_input("Your name (recorded on approvals and imports)", key="admin_name")
    if not admin_name.strip():
        st.caption("Enter your name before approving, adding or importing.")
    if st.button("Sign out"):
        st.session_state["admin_signed_in"] = False
        st.rerun()

if st.session_state.get("admin_message"):
    st.success(st.session_state.pop("admin_message"))

if catalog_storage_name() == "Supabase":
    st.caption("Catalog and learned corrections are stored in Supabase.")
else:
    st.warning(
        "Supabase is not configured. Changes are saved in a local file on the server, "
        "which Streamlit Cloud deletes when the app restarts."
    )

try:
    catalog_items = list_catalog_items(raise_errors=True)
except Exception as error:
    st.error(f"Could not load the catalog from Supabase: {error}")
    st.stop()

price_data = load_price_data()
price_items = price_data["items"]

try:
    pricing = load_pricing(price_data["markups"], price_data["project_multiplier"], raise_errors=True)
except Exception as error:
    st.error(f"Could not load the coefficients from Supabase: {error}")
    st.stop()

categories = sorted(
    {str(item["category"]) for item in price_items + catalog_items if item.get("category")}
    | {key.upper() for key in pricing["markups"]}
)
categories_in_use = {str(item["category"]).upper() for item in catalog_items if item.get("category")}
units = sorted(
    set(BASE_UNITS)
    | {normalize_unit(item.get("unit")) for item in price_items + catalog_items if item.get("unit")}
)

imported = price_list_imported(catalog_items)

try:
    all_feedback = load_match_feedback()
except Exception as error:
    all_feedback = []
    st.error(f"Could not load learned corrections: {error}")

try:
    missing_corrections = local_feedback_missing_from_supabase()
except Exception as error:
    missing_corrections = []
    st.error(f"Could not compare learned corrections with Supabase: {error}")

if not imported or missing_corrections:
    with st.container(border=True):
        st.markdown("**Setup**")
        if not imported:
            st.write(
                f"The price list ({len(price_items)} rows) is still read from `data/prices.xlsx`. "
                "Import it once so every item can be searched, edited and disabled here. "
                "After the import the catalog here is the live catalog and prices.xlsx is only a backup."
            )
            if st.button("Import price list from prices.xlsx", type="primary"):
                run_change(
                    lambda: import_catalog_items(price_items, admin_name),
                    import_message,
                )
        if missing_corrections:
            st.write(
                f"{len(missing_corrections)} learned corrections are in `data/match_feedback.json` "
                "but not yet in Supabase. Copy them so they are kept and used."
            )
            if st.button("Copy learned corrections to Supabase"):
                run_change(
                    lambda: copy_local_feedback_to_supabase(missing_corrections),
                    lambda count: f"Copied {count} learned corrections to Supabase.",
                )

pending_items = [item for item in catalog_items if catalog_item_status(item) == "pending"]
reviewed_items = sorted(
    [item for item in catalog_items if catalog_item_status(item) != "pending"],
    key=lambda item: (str(item["category"]).lower(), str(item["name"]).lower()),
)

pending_tab, catalog_tab, coefficients_tab, transfer_tab, corrections_tab = st.tabs([
    f"Pending approval ({len(pending_items)})",
    f"Catalog ({len(reviewed_items)})",
    "Coefficients",
    "Import / export Excel",
    "Learned corrections",
])


with pending_tab:
    st.caption(
        "Items sent by estimators from NO MATCH parts. They are not used in estimates "
        "until approved. Check the price, unit and category before approving."
    )
    if not pending_items:
        st.info("Nothing is waiting for approval.")

    for item in pending_items:
        with st.expander(
            f'{item["name"]} · €{item["price"]:.2f}/{item["unit"]} · {item["category"]}',
            expanded=True,
        ):
            item_details(item)
            with st.form(f'pending_form_{item["id"]}'):
                values = item_fields(item, f'pending_{item["id"]}', categories, units)
                approve_col, reject_col = st.columns(2)
                approve = approve_col.form_submit_button("Approve", type="primary")
                reject = reject_col.form_submit_button("Reject and delete")
            if approve:
                run_change(
                    lambda item=item, values=values: approve_catalog_item(item, admin_name, values),
                    f'Approved: {values["name"]}',
                )
            if reject:
                run_change(
                    lambda item=item: delete_catalog_item(item),
                    f'Rejected and deleted: {item["name"]}',
                )


with catalog_tab:
    if not imported:
        st.info(
            f"Only items added through the app are shown. The {len(price_items)} rows of prices.xlsx "
            "appear here after the price list is imported (see Setup above)."
        )

    with st.expander("Add item"):
        with st.form("add_item_form", clear_on_submit=True):
            values = item_fields({}, "add", categories, units)
            add = st.form_submit_button("Add to catalog", type="primary")
        if add:
            def add_item(values=values):
                created, existing = add_catalog_item(
                    {**values, "source_type": "admin"},
                    approved=True,
                    approved_by=admin_name,
                )
                if not created:
                    raise ValueError(
                        f'"{existing["name"]}" already exists ({catalog_item_status(existing)}).'
                    )

            run_change(add_item, f'Added: {values["name"]}')

    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input("Search", key="catalog_search")
    source_filter = f2.selectbox("Source", ["All", "Price list", "Added in the app"], key="catalog_source_filter")
    status_filter = f3.selectbox("Status", ["All", "Active", "Disabled"], key="catalog_status_filter")

    search_text = normalize_text(search)
    shown_items = [
        item
        for item in reviewed_items
        if (
            not search_text
            or search_text in normalize_text(" ".join(str(item.get(key) or "") for key in SEARCH_KEYS))
        )
        and (
            source_filter == "All"
            or (source_filter == "Price list") == (item.get("source_type") == "price_list")
        )
        and (
            status_filter == "All"
            or (status_filter == "Active") == item["active"]
        )
    ]

    if not shown_items:
        st.info("No catalog items found.")
    else:
        rows = [
            {
                "Name": item["name"],
                "Category": item["category"],
                "Unit": item["unit"],
                "Price": round(item["price"], 2),
                "Status": "Active" if item["active"] else "Disabled",
                "Source": item.get("source_type") or "",
                "Brand / model": item.get("brand_model") or "",
                "Supplier": item.get("supplier") or "",
                "Needs review": "Yes" if item.get("needs_review") else "",
                "Approved by": item.get("approved_by") or "",
            }
            for item in shown_items
        ]
        st.caption(f"{len(shown_items)} of {len(reviewed_items)} items")
        st.dataframe(rows, hide_index=True)

        items_by_id = {item["id"]: item for item in shown_items}
        selected_id = st.selectbox(
            "Edit item",
            list(items_by_id),
            format_func=lambda item_id: (
                f'{items_by_id[item_id]["name"]} · {items_by_id[item_id]["category"]} · '
                f'€{items_by_id[item_id]["price"]:.2f}/{items_by_id[item_id]["unit"]}'
                + ("" if items_by_id[item_id]["active"] else " (disabled)")
            ),
            key="catalog_edit_select",
        )
        selected = items_by_id[selected_id]
        item_details(selected)
        linked_corrections = feedback_for_item(selected, all_feedback)
        if linked_corrections:
            st.caption(
                f"Used by {len(linked_corrections)} learned correction(s). Renaming the item or changing "
                "its category or unit moves them to the new name automatically."
            )

        with st.form(f"catalog_form_{selected_id}"):
            values = item_fields(selected, f"catalog_{selected_id}", categories, units)
            save = st.form_submit_button("Save changes", type="primary")
        if save:
            run_change(
                lambda: update_catalog_item(selected, values),
                f'Saved: {values["name"]}',
            )

        toggle_col, delete_col = st.columns(2)
        with toggle_col:
            if st.button("Enable" if not selected["active"] else "Disable", key="catalog_toggle"):
                run_change(
                    lambda: update_catalog_item(selected, {"active": not selected["active"]}),
                    f'{"Enabled" if not selected["active"] else "Disabled"}: {selected["name"]}',
                )
            st.caption("Disabled items are kept but not used in estimates.")
        with delete_col:
            if linked_corrections:
                st.warning(
                    f"{len(linked_corrections)} learned correction(s) point to this item. After deleting it they "
                    "will be ignored. Disabling is usually safer."
                )
            confirm = st.checkbox(
                "I want to delete this item permanently",
                key=f"catalog_delete_confirm_{selected_id}",
            )
            if st.button("Delete", disabled=not confirm, key="catalog_delete"):
                run_change(
                    lambda: delete_catalog_item(selected),
                    f'Deleted: {selected["name"]}',
                )


with coefficients_tab:
    if pricing["stored"]:
        st.caption("Company coefficients and default multiplier, stored in the database.")
    else:
        st.info(
            "These values are still read from prices.xlsx. Saving them here stores them in the "
            "database, and from then on they are changed only here."
        )
    st.caption(
        "Every estimate starts with these values. Estimators can adjust them for a single estimate "
        "in the Calculator. Add a row for a new category; a category used by catalog items cannot be removed."
    )
    item_counts = {}
    for item in catalog_items if imported else price_items + catalog_items:
        category = str(item.get("category") or "").upper()
        item_counts[category] = item_counts.get(category, 0) + 1
    coefficient_rows = [
        {"Category": category.upper(), "Coefficient": float(value), "Items": item_counts.get(category.upper(), 0)}
        for category, value in sorted(pricing["markups"].items())
    ]
    edited_coefficients = st.data_editor(
        coefficient_rows,
        num_rows="dynamic",
        hide_index=True,
        disabled=["Items"],
        column_config={
            "Category": st.column_config.TextColumn("Category", required=True),
            "Coefficient": st.column_config.NumberColumn(
                "Coefficient", min_value=0.01, step=0.05, format="×%.2f", required=True
            ),
            "Items": st.column_config.NumberColumn("Items using it"),
        },
        key="coefficients_editor",
    )
    default_multiplier = st.number_input(
        "Default project multiplier",
        min_value=0.01,
        value=float(pricing["project_multiplier"]),
        step=0.05,
        key="default_multiplier",
    )
    if st.button("Save coefficients", type="primary", key="save_coefficients"):
        pairs = [
            (row.get("Category"), row.get("Coefficient"))
            for row in editor_rows(edited_coefficients)
            if row.get("Category") or row.get("Coefficient") is not None
        ]
        run_change(
            lambda: save_pricing_settings(pairs, default_multiplier, admin_name, categories_in_use),
            "Coefficients saved. New estimates use them now.",
        )


with transfer_tab:
    st.markdown("**Export**")
    export_items = reviewed_items if imported else price_items + reviewed_items
    st.caption(
        "Downloads the catalog in the same format as prices.xlsx (All_Items, Categories and "
        "Global_Settings sheets), so it can be kept as a backup, edited in Excel and imported again."
    )
    st.download_button(
        f"Download catalog as Excel ({len(export_items)} items)",
        data=catalog_to_excel(export_items, pricing["markups"], pricing["project_multiplier"]),
        file_name="catalog.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()
    st.markdown("**Import**")
    st.caption(
        "Upload an Excel file with an All_Items sheet (or a first sheet) with the columns Name, Category, "
        "Unit and Price (EUR). Material_Type, Component_Type, Brand/Model, Thickness_mm, Keywords, Synonyms, "
        "Description, Allowed_Use, Needs_Review, Notes, Supplier, Source_URL and Active are optional. "
        "Items with the same name, category and unit are updated; new ones are added as approved. "
        "Nothing is deleted. If the file also has Categories and Global_Settings sheets (like prices.xlsx), "
        "the coefficients and default multiplier can be replaced too."
    )
    uploaded = st.file_uploader("Excel file", type=["xlsx"], key="catalog_import_file")
    if uploaded is not None:
        try:
            rows = read_catalog_rows(io.BytesIO(uploaded.getvalue()))
            preview = import_catalog_items(rows, admin_name, dry_run=True)
            file_pricing = read_pricing_sheets(io.BytesIO(uploaded.getvalue()))
        except Exception as error:
            st.error(f"Could not read this file: {error}")
        else:
            st.write(
                f'{len(rows)} rows: **{len(preview["added"])} new**, **{len(preview["updated"])} changed**, '
                f'{preview["unchanged"]} unchanged.'
            )
            if preview["updated"]:
                st.caption("Changed: " + ", ".join(preview["updated"][:30]) + (" …" if len(preview["updated"]) > 30 else ""))
            if preview["duplicates"]:
                st.warning("Repeated rows skipped: " + ", ".join(preview["duplicates"]))
            if preview["skipped"]:
                st.warning("Invalid rows skipped: " + "; ".join(preview["skipped"]))

            pricing_changes = []
            if file_pricing["markups"]:
                pricing_changes = coefficient_changes(
                    pricing,
                    file_pricing["markups"],
                    file_pricing["project_multiplier"],
                )
            update_pricing = False
            if pricing_changes:
                st.write("Coefficients in this file differ: " + "; ".join(pricing_changes))
                update_pricing = st.checkbox(
                    "Also replace the coefficients and default multiplier with the ones in this file",
                    value=True,
                    key="catalog_import_pricing",
                )
            elif file_pricing["markups"]:
                st.caption("Coefficients and default multiplier in this file are the same as now.")

            def import_file():
                if update_pricing:
                    save_pricing_settings(
                        list(file_pricing["markups"].items()),
                        file_pricing["project_multiplier"] or pricing["project_multiplier"],
                        admin_name,
                        categories_in_use | {str(row.get("category") or "").upper() for row in rows},
                    )
                return import_catalog_items(rows, admin_name, source_type="excel_import")

            if st.button(
                "Import",
                type="primary",
                disabled=not (preview["added"] or preview["updated"] or update_pricing),
                key="catalog_import_button",
            ):
                run_change(
                    import_file,
                    lambda result: import_message(result) + (" Coefficients updated." if update_pricing else ""),
                )


with corrections_tab:
    if feedback_storage_name() == "Supabase":
        st.caption("Stored in Supabase: shared by all users and kept permanently.")
    else:
        st.warning(
            "Stored in a local file on the server. On Streamlit Cloud it is "
            "lost when the app restarts. Set up Supabase to keep it."
        )

    corrections = sorted(
        [record for record in all_feedback if record_votes(record) > 0],
        key=lambda record: str(record.get("updated_at") or ""),
        reverse=True,
    )

    if not corrections:
        st.info("No corrections saved yet.")
    else:
        correction_rows = [
            {
                "Part": record.get("source_name"),
                "Material": record.get("source_material"),
                "Type": record.get("component_type"),
                "Chosen item": record.get("catalog_name"),
                "Category": record.get("catalog_category"),
                "Unit": record.get("catalog_unit"),
                "AI suggested": record.get("ai_catalog_name") or "-",
                "Times chosen": record_votes(record),
                "Last chosen": str(record.get("updated_at") or "")[:10],
            }
            for record in corrections
        ]
        st.dataframe(correction_rows, hide_index=True)
        st.download_button(
            "Download CSV",
            data=csv_bytes(correction_rows),
            file_name="learned_corrections.csv",
            mime="text/csv",
            key="corrections_csv",
        )

        corrections_by_key = {record["source_key"]: record for record in corrections}
        selected_for_delete = st.multiselect(
            "Wrong corrections to delete",
            list(corrections_by_key),
            format_func=lambda source_key: (
                f'{corrections_by_key[source_key].get("source_name")} '
                f'→ {corrections_by_key[source_key].get("catalog_name")} '
                f'({record_votes(corrections_by_key[source_key])}×)'
            ),
            key="feedback_delete_select",
        )
        if st.button("Delete selected", disabled=not selected_for_delete):
            def delete_corrections():
                for source_key in selected_for_delete:
                    delete_match_feedback(source_key)
                st.session_state.pop("feedback_delete_select", None)

            run_change(delete_corrections, f"Deleted {len(selected_for_delete)} learned correction(s).")
