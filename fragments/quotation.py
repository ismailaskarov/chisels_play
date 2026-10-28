from datetime import date, timedelta

import streamlit as st

from quote_generator import build_quote_pdf, calculate_quote_totals, quote_filename
from ui import step_header
from utils.performance import timed


@st.fragment
def render_quotation(result, project_name, logo_path):
    # --------------------------------------------------
    # CLIENT QUOTATION
    # --------------------------------------------------

    st.divider()

    step_header(5, "Client quotation")

    st.caption(
        "The calculated price stays unchanged internally. "
        "Set the final client price separately if you want "
        "to adjust it based on the market or commercial decision."
    )

    q1, q2, q3 = st.columns(3)

    with q1:
        quote_number = st.text_input(
            "Quote number",
            value=(
                st.session_state.get(
                    "quote_number_value"
                )
                or f"CP-{date.today():%Y%m%d}"
            ),
            key="quote_number_value",
        )

    with q2:
        quote_date = st.date_input(
            "Quote date",
            value=date.today(),
            key="quote_date_value",
        )

    with q3:
        validity_date = st.date_input(
            "Validity date",
            value=date.today() + timedelta(days=30),
            key="quote_validity_value",
        )

    q4, q5 = st.columns(2)

    with q4:
        client_name = st.text_input(
            "Client name",
            key="quote_client_name",
        )

    with q5:
        quote_product_name = st.text_input(
            "Product / furniture name",
            value=(
                st.session_state.get(
                    "furniture_name"
                )
                or project_name
                or "Furniture"
            ),
            key="quote_product_name",
        )

    calculated_total = round(float(result["total"]), 2)
    previous_total = st.session_state.get("quote_calculated_total")
    if (
        "quote_final_client_price" not in st.session_state
        or (
            previous_total is not None
            and previous_total != calculated_total
            and round(float(st.session_state["quote_final_client_price"]), 2) == previous_total
        )
    ):
        st.session_state["quote_final_client_price"] = calculated_total
    st.session_state["quote_calculated_total"] = calculated_total

    def use_calculated_price(total):
        st.session_state["quote_final_client_price"] = total

    price_col1, price_col2, price_col3 = st.columns(3)

    with price_col1:
        st.metric(
            "Calculated price",
            f'€{result["total"]:.2f}',
        )

    with price_col2:
        final_client_price = st.number_input(
            "Final client price (excl. VAT)",
            min_value=0.0,
            step=50.0,
            key="quote_final_client_price",
            help="Follows the calculated price until you type your own price.",
        )

    with price_col3:
        price_adjustment = (
            final_client_price
            - float(result["total"])
        )

        st.metric(
            "Price adjustment",
            f'€{price_adjustment:+.2f}',
        )

        if round(price_adjustment, 2) != 0:
            st.button(
                "Use calculated price",
                on_click=use_calculated_price,
                args=(calculated_total,),
                key="quote_use_calculated_price",
            )

    st.markdown(
        "**Additional services**"
    )

    restored_services = st.session_state.get("project_service_rows") or []
    if restored_services:
        default_services = [
            {
                "Service": row.get("name") or "",
                "Quantity": float(row.get("quantity") or 1.0),
                "Price excl. VAT": float(row.get("price") or 0.0),
            }
            for row in restored_services
        ]
    else:
        default_services = [
            {
                "Service": "Matavimas",
                "Quantity": 1.0,
                "Price excl. VAT": 0.0,
            },
            {
                "Service": "Pristatymas",
                "Quantity": 1.0,
                "Price excl. VAT": 0.0,
            },
            {
                "Service": "Montavimas",
                "Quantity": 1.0,
                "Price excl. VAT": 0.0,
            },
            {
                "Service": "Kita paslauga",
                "Quantity": 1.0,
                "Price excl. VAT": 0.0,
            },
        ]

    edited_services = st.data_editor(
        default_services,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "Service": st.column_config.TextColumn(
                "Service",
                required=True,
            ),
            "Quantity": st.column_config.NumberColumn(
                "Quantity",
                min_value=0.0,
                step=1.0,
            ),
            "Price excl. VAT": st.column_config.NumberColumn(
                "Price excl. VAT",
                min_value=0.0,
                step=10.0,
                format="€%.2f",
            ),
        },
        key="quote_services_editor",
    )

    service_rows = []

    if hasattr(edited_services, "to_dict"):
        edited_service_rows = edited_services.to_dict(
            orient="records"
        )
    else:
        edited_service_rows = list(
            edited_services
        )

    for row in edited_service_rows:
        service_rows.append({
            "name": row.get(
                "Service"
            ),
            "quantity": row.get(
                "Quantity",
                1.0,
            ),
            "price": row.get(
                "Price excl. VAT",
                0.0,
            ),
        })

    st.session_state["project_service_rows"] = [dict(row) for row in service_rows]

    settings1, settings2, settings3 = st.columns(3)

    with settings1:
        vat_percent = st.number_input(
            "VAT %",
            min_value=0.0,
            value=21.0,
            step=1.0,
            key="quote_vat_percent",
        )

    with settings2:
        discount_type_label = st.selectbox(
            "Discount type",
            [
                "Amount (€)",
                "Percent (%)",
            ],
            key="quote_discount_type",
        )

    with settings3:
        discount_value = st.number_input(
            "Discount",
            min_value=0.0,
            value=0.0,
            step=10.0,
            key="quote_discount_value",
        )

    discount_mode = (
        "percent"
        if discount_type_label == "Percent (%)"
        else "amount"
    )

    quote_totals = calculate_quote_totals(
        furniture_price=final_client_price,
        services=service_rows,
        vat_percent=vat_percent,
        discount_value=discount_value,
        discount_mode=discount_mode,
    )

    preview1, preview2, preview3, preview4 = st.columns(4)

    with preview1:
        st.metric(
            "Before discount",
            f'€{quote_totals["subtotal_before_discount"]:.2f}',
        )

    with preview2:
        st.metric(
            "Discount",
            f'€{quote_totals["discount_amount"]:.2f}',
        )

    with preview3:
        st.metric(
            f'VAT {vat_percent:g}%',
            f'€{quote_totals["vat_amount"]:.2f}',
        )

    with preview4:
        st.metric(
            "CLIENT TOTAL",
            f'€{quote_totals["total_with_vat"]:.2f}',
        )

    # --------------------------------------------------
    # QUOTATION PREVIEW
    # --------------------------------------------------

    st.markdown("**Quotation preview**")

    meta1, meta2, meta3 = st.columns(3)
    with meta1:
        st.caption("Client")
        st.write(client_name or "—")
    with meta2:
        st.caption("Quote number")
        st.write(quote_number or "—")
    with meta3:
        st.caption("Valid until")
        st.write(
            validity_date.strftime("%d/%m/%Y")
            if hasattr(validity_date, "strftime")
            else validity_date
        )

    preview_rows = [{
        "Nr.": 1,
        "Product / service": quote_product_name or "Furniture",
        "Unit price": float(final_client_price),
        "Quantity": 1.0,
        "Total excl. VAT": float(final_client_price),
    }]

    for service_index, service in enumerate(
        quote_totals["services"],
        start=2,
    ):
        preview_rows.append({
            "Nr.": service_index,
            "Product / service": service["name"],
            "Unit price": service["price"],
            "Quantity": service["quantity"],
            "Total excl. VAT": service["total"],
        })

    st.dataframe(
        preview_rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Unit price": st.column_config.NumberColumn(
                "Unit price",
                format="€%.2f",
            ),
            "Total excl. VAT": st.column_config.NumberColumn(
                "Total excl. VAT",
                format="€%.2f",
            ),
        },
    )

    pv1, pv2, pv3, pv4 = st.columns(4)
    with pv1:
        st.metric(
            "Subtotal",
            f'€{quote_totals["subtotal_before_discount"]:.2f}',
        )
    with pv2:
        st.metric(
            "Discount",
            f'€{quote_totals["discount_amount"]:.2f}',
        )
    with pv3:
        st.metric(
            f'VAT {vat_percent:g}% ',
            f'€{quote_totals["vat_amount"]:.2f}',
        )
    with pv4:
        st.metric(
            "TOTAL",
            f'€{quote_totals["total_with_vat"]:.2f}',
        )

    # --------------------------------------------------
    # READY-TO-QUOTE WARNINGS (NON-BLOCKING)
    # --------------------------------------------------

    quote_warnings = []

    for issue in st.session_state.get(
        "current_review_issues",
        [],
    ):
        quote_warnings.append(
            f'{issue.get("part_name", "Part")}: '
            f'{issue.get("issue_type", "warning")} — '
            f'{issue.get("detail", "")}'
        )

    if not str(client_name).strip():
        quote_warnings.append("Client name is empty.")

    if not str(quote_number).strip():
        quote_warnings.append("Quote number is empty.")

    if not str(quote_product_name).strip():
        quote_warnings.append("Product / furniture name is empty.")

    if float(final_client_price) <= 0:
        quote_warnings.append("Final client price is zero.")

    if validity_date < quote_date:
        quote_warnings.append(
            "Validity date is earlier than the quote date."
        )

    if quote_warnings:
        st.warning(
            "Ready-to-quote check found warnings. "
            "They do not block PDF generation while the calculator is being tested."
        )
        for warning in quote_warnings:
            st.markdown(f"- {warning}")
    else:
        st.success(
            "Ready-to-quote check: no current warnings."
        )

    if st.button(
        "Generate quotation PDF",
        type="primary",
        key="generate_quote_button",
    ):
        # Warnings above are intentionally advisory only during testing.
        # They never disable or block PDF generation.
        try:
            generated_bytes = timed(
                "build_quote_pdf",
                build_quote_pdf,
                quote_number=quote_number,
                quote_date=quote_date,
                validity_date=validity_date,
                client_name=client_name,
                furniture_name=quote_product_name,
                furniture_price=final_client_price,
                services=service_rows,
                vat_percent=vat_percent,
                discount_value=discount_value,
                discount_mode=discount_mode,
                logo_path=logo_path,
                project_name=project_name,
            )

            if not generated_bytes:
                raise ValueError("The PDF generator returned an empty file.")

            st.session_state["generated_quote_bytes"] = generated_bytes
            st.session_state["generated_quote_filename"] = quote_filename(
                quote_number or "quotation",
                client_name or "client",
                project_name,
            )
            st.success("Quotation PDF generated. Download it below.")

        except Exception as error:
            st.session_state.pop("generated_quote_bytes", None)
            st.session_state.pop("generated_quote_filename", None)
            print("QUOTATION PDF ERROR:", repr(error))
            st.error(
                "Quotation generation failed. The warning check did not block it; "
                f"the PDF generator returned this error: {error}"
            )

    if st.session_state.get(
        "generated_quote_bytes"
    ):

        st.download_button(
            "Download quotation (.pdf)",
            data=st.session_state[
                "generated_quote_bytes"
            ],
            file_name=st.session_state.get(
                "generated_quote_filename",
                "quotation.pdf",
            ),
            mime="application/pdf",
            key="download_quote_button",
        )
