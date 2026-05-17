"""PDF compilation for invoices using Jinja2 + WeasyPrint.

Provides ``compile_invoice_pdf()`` which renders ``invoice.html`` via
Jinja2 then converts the HTML to PDF with WeasyPrint.
"""

import jinja2
from pathlib import Path
from sqlalchemy.orm import Session
from sqlmodel import select

from src.models import Client, Invoice, TimeLog

try:
    from weasyprint import HTML
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False

# Resolve the templates directory relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=jinja2.select_autoescape(),
    cache_size=0,
)


def compile_invoice_pdf(invoice: Invoice, session: Session) -> bytes:
    """Compile an invoice into a PDF document using Jinja2 + WeasyPrint.

    Renders the ``invoice.html`` template with the invoice, client, and
    time logs, then converts the resulting HTML to a PDF.

    Args:
        invoice: The ``Invoice`` object to render.
        session: Active SQLModel session for fetching related data.

    Returns:
        Bytes object containing the compiled PDF.

    Raises:
        RuntimeError: If weasyprint is not installed.
    """
    if not HAS_WEASYPRINT:
        raise RuntimeError(
            "weasyprint is required for PDF generation. "
            "Install it with: pip install weasyprint"
        )

    # Fetch related data
    client: Client = session.get(Client, invoice.client_id)
    time_logs: list[TimeLog] = session.exec(
        select(TimeLog).where(TimeLog.invoice_id == invoice.id)
    ).all()

    # Render the template
    template = TEMPLATE_ENV.get_template("invoice.html")
    html_content = template.render(
        invoice=invoice,
        client=client,
        time_logs=time_logs,
    )

    # Convert HTML to PDF
    pdf_bytes = HTML(string=html_content, base_url=".").write_pdf()
    return pdf_bytes
