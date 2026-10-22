from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


DEFAULT_SUPPLIER_NAME = 'IĮ "Buki kaltai"'
DEFAULT_SUPPLIER_DETAILS = [
    "305189636",
    "LT100012484610",
    "Šilo g. 6, Lekėčiai, Šakių r.",
    "062533752",
]

DEFAULT_NOTES = [
    "Kainos gali keistis atsisakius vienos ar kitos pozicijos iš pasiūlymo",
    "Kainos gali keistis keičiant užklausoje pateiktus ir/ar telefonu aptartus duomenis iki pasiūlymo galiojimo pabaigos",
    "Pristatymo kaštai ir montavimo paslauga neįskaičiuota, nebent pasiūlyme nurodyta kitaip",
]

PINK_HEADER = colors.HexColor("#E8CCD8")
BLACK = colors.HexColor("#111111")


def _register_fonts():
    """Register a Unicode serif font if one is available."""
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
        ),
        (
            r"C:\Windows\Fonts\times.ttf",
            r"C:\Windows\Fonts\timesbd.ttf",
        ),
    ]

    for regular, bold in candidates:
        if Path(regular).exists() and Path(bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("QuoteSerif", regular))
                pdfmetrics.registerFont(TTFont("QuoteSerif-Bold", bold))
                return "QuoteSerif", "QuoteSerif-Bold"
            except Exception:
                pass

    return "Times-Roman", "Times-Bold"


FONT_REGULAR, FONT_BOLD = _register_fonts()


def safe_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def sanitize_filename(value):
    value = str(value or "quote").strip()
    cleaned = "".join(
        char if char.isalnum() or char in ("-", "_") else "_"
        for char in value
    )
    cleaned = cleaned.strip("_")
    return cleaned or "quote"


def format_date(value):
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    return str(value or "")


def money(value, decimals=2):
    return f"{safe_number(value):,.{decimals}f}"


def calculate_quote_totals(
    furniture_price,
    services,
    vat_percent=21.0,
    discount_value=0.0,
    discount_mode="amount",
):
    furniture_price = max(0.0, safe_number(furniture_price))

    clean_services = []
    for service in services or []:
        name = str(service.get("name") or "").strip()
        price = max(0.0, safe_number(service.get("price")))
        quantity = max(0.0, safe_number(service.get("quantity"), 1.0))

        if not name or price <= 0 or quantity <= 0:
            continue

        clean_services.append({
            "name": name,
            "price": price,
            "quantity": quantity,
            "total": price * quantity,
        })

    subtotal_before_discount = furniture_price + sum(
        service["total"] for service in clean_services
    )

    discount_value = max(0.0, safe_number(discount_value))

    if discount_mode == "percent":
        discount_amount = (
            subtotal_before_discount
            * min(discount_value, 100.0)
            / 100.0
        )
    else:
        discount_amount = min(
            discount_value,
            subtotal_before_discount,
        )

    subtotal_after_discount = max(
        0.0,
        subtotal_before_discount - discount_amount,
    )

    vat_percent = max(0.0, safe_number(vat_percent))
    vat_amount = subtotal_after_discount * vat_percent / 100.0
    total_with_vat = subtotal_after_discount + vat_amount

    return {
        "furniture_price": furniture_price,
        "services": clean_services,
        "subtotal_before_discount": subtotal_before_discount,
        "discount_amount": discount_amount,
        "subtotal_after_discount": subtotal_after_discount,
        "vat_percent": vat_percent,
        "vat_amount": vat_amount,
        "total_with_vat": total_with_vat,
    }


def _logo_flowable(logo_path):
    logo_file = Path(logo_path) if logo_path else None
    if not logo_file or not logo_file.exists():
        return Spacer(1, 16 * mm)

    try:
        image = Image(str(logo_file))
        image._restrictSize(34 * mm, 16 * mm)
        image.hAlign = "LEFT"
        return image
    except Exception as error:
        print("Could not add quote logo:", error)
        return Spacer(1, 16 * mm)


def build_quote_pdf(
    quote_number,
    quote_date,
    validity_date,
    client_name,
    furniture_name,
    furniture_price,
    services,
    vat_percent=21.0,
    discount_value=0.0,
    discount_mode="amount",
    supplier_name=DEFAULT_SUPPLIER_NAME,
    supplier_details=None,
    notes=None,
    logo_path=Path("data") / "logo.jpg",
    project_name=None,
):
    """Build the final client quotation as a PDF."""

    totals = calculate_quote_totals(
        furniture_price=furniture_price,
        services=services,
        vat_percent=vat_percent,
        discount_value=discount_value,
        discount_mode=discount_mode,
    )

    supplier_details = (
        list(supplier_details)
        if supplier_details is not None
        else list(DEFAULT_SUPPLIER_DETAILS)
    )
    note_lines = list(notes) if notes is not None else list(DEFAULT_NOTES)

    output = BytesIO()

    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=9 * mm,
        title="KOMERCINIS PASIŪLYMAS",
        author="Chisels Play",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "QuoteTitle",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=17,
        leading=19,
        alignment=TA_CENTER,
        textColor=BLACK,
        spaceAfter=0,
    )

    normal = ParagraphStyle(
        "QuoteNormal",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=8.6,
        leading=10.4,
        textColor=BLACK,
    )

    bold = ParagraphStyle(
        "QuoteBold",
        parent=normal,
        fontName=FONT_BOLD,
    )

    small = ParagraphStyle(
        "QuoteSmall",
        parent=normal,
        fontSize=7.4,
        leading=8.5,
    )

    right = ParagraphStyle(
        "QuoteRight",
        parent=normal,
        alignment=TA_RIGHT,
    )

    right_bold = ParagraphStyle(
        "QuoteRightBold",
        parent=right,
        fontName=FONT_BOLD,
    )

    story = []

    # --------------------------------------------------
    # HEADER: logo aligned with the title block
    # --------------------------------------------------
    logo = _logo_flowable(logo_path)
    title = Paragraph("KOMERCINIS PASIŪLYMAS", title_style)

    header = Table(
        [[logo, title, ""]],
        colWidths=[42 * mm, 110 * mm, 42 * mm],
        rowHeights=[18 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 1.5 * mm))

    # Metadata directly below title, compact like the source quote.
    project_rows = (
        [[Paragraph("Projektas", bold), Paragraph(escape(str(project_name).strip()), normal)]]
        if str(project_name or "").strip()
        else []
    )
    metadata = Table(
        project_rows + [
            [Paragraph("Numeris", bold), Paragraph(escape(str(quote_number or "")), normal)],
            [Paragraph("Data", bold), Paragraph(format_date(quote_date), normal)],
            [Paragraph("Galiojimo data", bold), Paragraph(format_date(validity_date), normal)],
        ],
        colWidths=[32 * mm, 70 * mm],
        hAlign="CENTER",
    )
    metadata.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
    ]))
    story.append(metadata)
    story.append(Spacer(1, 6 * mm))

    supplier_lines = [
        Paragraph("<u>Tiekėjas</u>", bold),
        Paragraph(escape(str(supplier_name)), normal),
    ]
    supplier_lines.extend(
        Paragraph(escape(str(detail)), normal)
        for detail in supplier_details
        if str(detail).strip()
    )

    client_lines = [
        Paragraph("<u>Užsakovas</u>", bold),
        Paragraph(escape(str(client_name or "")), normal),
    ]

    supplier_block = Table(
        [[supplier_lines, client_lines]],
        colWidths=[98 * mm, 96 * mm],
    )
    supplier_block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(supplier_block)
    story.append(Spacer(1, 6 * mm))

    # --------------------------------------------------
    # PRICE TABLE
    # --------------------------------------------------
    rows = [{
        "name": str(furniture_name or "Baldai"),
        "price": totals["furniture_price"],
        "quantity": 1.0,
        "total": totals["furniture_price"],
    }]
    rows.extend(totals["services"])

    table_data = [[
        Paragraph("Nr.", bold),
        Paragraph("Produktas/paslauga", bold),
        Paragraph("Kaina", bold),
        Paragraph("Kiekis", bold),
        Paragraph("Kaina už visus", bold),
        Paragraph("PVM", bold),
        Paragraph("Kaina su<br/>mokesčiais", bold),
    ]]

    for index, item in enumerate(rows, start=1):
        line_total = safe_number(item.get("total"))
        line_vat = line_total * totals["vat_percent"] / 100.0
        line_gross = line_total + line_vat

        table_data.append([
            str(index),
            Paragraph(escape(str(item.get("name") or "")), normal),
            money(item.get("price"), 1),
            f'{safe_number(item.get("quantity"), 1.0):g}',
            money(line_total, 2),
            money(line_vat, 2),
            money(line_gross, 2),
        ])

    price_table = Table(
        table_data,
        repeatRows=1,
        colWidths=[10 * mm, 65 * mm, 24 * mm, 15 * mm, 30 * mm, 22 * mm, 30 * mm],
        hAlign="CENTER",
    )

    price_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PINK_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, -1), BLACK),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, 0), 8.3),
        ("FONTSIZE", (0, 1), (-1, -1), 8.3),
        ("LEADING", (0, 0), (-1, -1), 9.4),
        ("GRID", (0, 0), (-1, -1), 0.55, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 4.3),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4.3),
        ("TOPPADDING", (0, 1), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(price_table)
    story.append(Spacer(1, 5 * mm))

    # --------------------------------------------------
    # TOTALS - right aligned to the table's right edge
    # --------------------------------------------------
    summary_data = [
        [Paragraph("Kaina", right_bold), Paragraph(f'€{money(totals["subtotal_before_discount"], 2)}', right)],
    ]

    if totals["discount_amount"] > 0:
        summary_data.append([
            Paragraph("Nuolaida", right_bold),
            Paragraph(f'-€{money(totals["discount_amount"], 2)}', right),
        ])

    summary_data.extend([
        [
            Paragraph("PVM", right_bold),
            Paragraph(f'€{money(totals["vat_amount"], 2)}', right),
        ],
        [
            Paragraph("Viso", ParagraphStyle(
                "TotalLabel",
                parent=right_bold,
                fontSize=11.5,
                leading=13,
            )),
            Paragraph(f'€{money(totals["total_with_vat"], 2)}', ParagraphStyle(
                "TotalValue",
                parent=right,
                fontName=FONT_BOLD,
                fontSize=11.5,
                leading=13,
            )),
        ],
    ])

    summary = Table(
        summary_data,
        colWidths=[32 * mm, 32 * mm],
        hAlign="RIGHT",
    )
    summary_style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LINEABOVE", (0, -1), (-1, -1), 0.7, colors.black),
    ]
    summary.setStyle(TableStyle(summary_style))
    story.append(summary)
    story.append(Spacer(1, 8 * mm))

    # --------------------------------------------------
    # FOOTER CONDITIONS
    # --------------------------------------------------
    note_flowables = []
    for note in note_lines:
        note_flowables.append(
            Paragraph(f"• {escape(str(note))}", small)
        )

    story.append(KeepTogether(note_flowables))

    doc.build(story)
    output.seek(0)
    return output.getvalue()


def quote_filename(quote_number, client_name, project_name=None):
    parts = [sanitize_filename(quote_number)]
    if str(project_name or "").strip():
        parts.append(sanitize_filename(project_name))
    parts.append(sanitize_filename(client_name))
    return "_".join(parts) + ".pdf"
