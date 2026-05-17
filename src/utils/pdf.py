from io import BytesIO
from typing import Optional

from sqlalchemy.orm import Session

from src.models import Client, Invoice, TimeLog

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate,
        Table,
        TableStyle,
        Paragraph,
        Spacer,
        HRFlowable,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


def compile_invoice_pdf(invoice: Invoice, session: Session) -> bytes:
    """Compile an invoice into a PDF document using ReportLab."""
    if not HAS_REPORTLAB:
        raise RuntimeError(
            "reportlab is required for PDF generation. "
            "Install it with: pip install reportlab"
        )

    # Fetch related data
    client: Client = session.get(Client, invoice.client_id)
    time_logs: list[TimeLog] = (
        session.query(TimeLog)
        .filter(TimeLog.invoice_id == invoice.id)
        .all()
    )

    # Build PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=HexColor("#1a1a1a"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=HexColor("#666666"),
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        textColor=HexColor("#333333"),
    )
    bold_style = ParagraphStyle(
        "Bold",
        parent=styles["Normal"],
        fontSize=10,
        textColor=HexColor("#333333"),
        fontName="Helvetica-Bold",
    )
    total_style = ParagraphStyle(
        "Total",
        parent=styles["Normal"],
        fontSize=12,
        textColor=HexColor("#1a1a1a"),
        fontName="Helvetica-Bold",
    )

    elements = []

    # Header
    elements.append(Paragraph("INVOICE", title_style))
    elements.append(Paragraph(invoice.invoice_number, subtitle_style))
    elements.append(Spacer(1, 10))

    # Client info
    elements.append(Paragraph(f"Bill To: {client.name}", bold_style))
    elements.append(Paragraph(client.email, body_style))
    elements.append(Paragraph(client.billing_address, body_style))
    elements.append(Spacer(1, 10))

    # Date info
    elements.append(Paragraph(f"Issue Date: {invoice.issue_date.isoformat()}", body_style))
    elements.append(Paragraph(f"Due Date: {invoice.due_date.isoformat()}", body_style))
    elements.append(Spacer(1, 10))

    # Separator
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor("#cccccc")))
    elements.append(Spacer(1, 10))

    # Line items table
    rows = []
    # Table header
    rows.append([
        Paragraph("Description", bold_style),
        Paragraph("Date", bold_style),
        Paragraph("Hours", bold_style),
        Paragraph("Rate", bold_style),
        Paragraph("Amount", bold_style),
    ])

    for log in time_logs:
        amount = float(log.hours) * float(client.default_hourly_rate)
        rows.append([
            Paragraph(log.description, body_style),
            Paragraph(log.date.isoformat(), body_style),
            Paragraph(str(log.hours), body_style),
            Paragraph(f"{client.default_hourly_rate:.2f}", body_style),
            Paragraph(f"{amount:.2f}", body_style),
        ])

    # Total row
    rows.append([
        Paragraph("", body_style),
        Paragraph("", body_style),
        Paragraph("", body_style),
        Paragraph("TOTAL", total_style),
        Paragraph(f"{invoice.total_amount:.2f}", total_style),
    ])

    col_widths = [None, 60 * mm, 40 * mm, 40 * mm, 50 * mm]
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, HexColor("#cccccc")),
        ("BACKGROUND", (0, -1), (-1, -1), HexColor("#f0f0f0")),
        ("TOPPADDING", (0, 0), (-1, -1), (5,)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), (5,)),
    ]))
    elements.append(table)

    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Status: {invoice.status}", body_style))

    # Build PDF
    doc.build(elements)
    return buffer.getvalue()
