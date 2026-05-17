import logging
import os
from datetime import date as _date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, Header, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import HTTPException
import jinja2
from pydantic import ValidationError
from sqlmodel import select
from sqlalchemy.orm import Session

from src.database import create_db_and_tables, get_session
from src.models import Client, Invoice, TimeLog
from src.services import (
    ClientCreate,
    ClientResponse,
    InvoiceResponse,
    TimeLogCreate,
    TimeLogResponse,
    generate_invoice_transaction,
    get_unbilled_logs,
)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lean Invoice Tracker")

# Resolve the templates directory relative to this file
BASE_DIR = Path(__file__).resolve().parent
# Disable Jinja2 template cache to avoid TypeError with unhashable context dicts
template_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=jinja2.select_autoescape(),
    cache_size=0,  # Disable template caching
)
templates = Jinja2Templates(env=template_env)

# Create tables on startup
create_db_and_tables()
logger.info("Database tables created / verified")


# ── Dashboard UI ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Renders the front-end dashboard UI template."""
    logger.debug("Dashboard request received")
    return templates.TemplateResponse(request, "dashboard.html")


# ── API: Clients ─────────────────────────────────────────────────────────────

@app.post("/api/clients", response_model=ClientResponse)
async def create_client(client: ClientCreate, session: Session = Depends(get_session)):
    """Creates a new tracking client profile.

    Expects a JSON body with ``name``, ``email``, ``billing_address``,
    and optionally ``default_hourly_rate`` (defaults to 0.00).
    """
    logger.info("Creating client: %s", client.name)
    db_client = Client(
        name=client.name,
        email=client.email,
        billing_address=client.billing_address,
        default_hourly_rate=client.default_hourly_rate,
    )
    session.add(db_client)
    session.commit()
    session.refresh(db_client)
    logger.info("Client created: id=%s name=%s", db_client.id, db_client.name)
    return db_client


@app.get("/api/clients", response_model=list[ClientResponse])
async def list_clients(session: Session = Depends(get_session)):
    """Lists all active client profiles."""
    clients = session.exec(select(Client)).all()
    logger.debug("Listed %d clients", len(clients))
    return clients


@app.post("/api/logs", response_model=TimeLogResponse)
async def create_time_log(
    request: Request,
    client_id: Optional[int] = Form(None),
    hours: Optional[float] = Form(None),
    description: Optional[str] = Form(None),
    date_str: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """Records a manual time-tracking entry. Accepts JSON (CLI) or form data (HTMX).

    **JSON body (CLI):**
    ```json
    {"client_id": 1, "hours": 4.5, "description": "Task", "date": "2026-05-17"}
    ```

    **Form data (HTMX):**
    Fields: ``client_id``, ``hours``, ``description``, ``date`` (optional).
    """
    logger.debug(
        "Creating time log: client_id=%s hours=%s", client_id, hours,
    )

    # Try parsing from form data first (HTMX submission)
    form_data = client_id is not None and hours is not None and description is not None

    if form_data:
        log_date = _date.fromisoformat(date_str) if date_str else None
        create_data = TimeLogCreate(
            client_id=client_id,
            hours=hours,
            description=description,
            date=log_date,
        )
    else:
        # Fall back to JSON body (CLI submission)
        try:
            body = await request.json()
        except Exception:
            logger.warning("Invalid JSON body on /api/logs")
            return JSONResponse(
                status_code=422,
                content={"detail": "Invalid JSON body"},
            )
        try:
            create_data = TimeLogCreate(**body)
        except ValidationError as e:
            logger.warning("Validation error on /api/logs: %s", e)
            return JSONResponse(
                status_code=422,
                content={"detail": str(e)},
            )

    if create_data.date is None:
        create_data.date = _date.today()

    db_log = TimeLog(
        client_id=create_data.client_id,
        hours=create_data.hours,
        description=create_data.description,
        date=create_data.date,
    )
    session.add(db_log)
    session.commit()
    session.refresh(db_log)
    logger.info(
        "Time log created: id=%s client_id=%s date=%s hours=%.2f",
        db_log.id, db_log.client_id, db_log.date, db_log.hours,
    )
    return db_log


@app.post("/api/logs/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Multi-part form upload for bulk CSV parsing.

    Expects a CSV file with columns: ``client_id,date,hours,description``.
    Malformed rows are skipped and logged as warnings.
    """
    logger.info("CSV upload received: %s", file.filename)
    contents = await file.read()
    text = contents.decode("utf-8")
    lines = text.strip().splitlines()

    created = 0
    skipped = 0
    for line_num, line in enumerate(lines[1:], start=2):  # skip header
        parts = line.split(",")
        if len(parts) < 4:
            logger.warning("CSV line %d: malformed row (expected 4 columns, got %d)", line_num, len(parts))
            skipped += 1
            continue
        try:
            client_id = int(parts[0])
            log_date = _date.fromisoformat(parts[1])
            hours = float(parts[2])
            description = parts[3]
        except (ValueError, IndexError) as exc:
            logger.warning("CSV line %d: parse error – %s", line_num, exc)
            skipped += 1
            continue

        # Validate hours is positive
        if hours <= 0:
            logger.warning("CSV line %d: hours must be positive (%.2f), skipping", line_num, hours)
            skipped += 1
            continue

        # Validate date is not in the future
        if log_date > _date.today():
            logger.warning("CSV line %d: date is in the future (%s), skipping", line_num, log_date)
            skipped += 1
            continue

        db_log = TimeLog(
            client_id=client_id,
            date=log_date,
            hours=hours,
            description=description,
        )
        session.add(db_log)
        created += 1

    session.commit()
    detail = f"Imported {created} time logs."
    if skipped:
        detail += f" Skipped {skipped} invalid row(s)."
    logger.info("CSV upload complete: %s", detail)
    return JSONResponse(content={"message": detail})


@app.get("/api/logs/unbilled", response_class=HTMLResponse)
async def unbilled_logs_html(
    client_id: Optional[int] = Query(None),
    session: Session = Depends(get_session),
):
    """Returns an HTML table fragment of unbilled logs (used by HTMX).

    Optional query param ``client_id`` filters results to a specific client.
    """
    logs = get_unbilled_logs(session, client_id)

    if not logs:
        return HTMLResponse(
            content='<p class="text-gray-500 py-2">No unbilled time logs found.</p>'
        )

    table_rows = ""
    for log in logs:
        table_rows += f"""<tr class="border-b border-gray-700">
            <td class="py-2 pr-4">{log.date.isoformat()}</td>
            <td class="py-2 pr-4">{log.hours}</td>
            <td class="py-2 pr-4">{log.description}</td>
            <td class="py-2">
                <span class="px-2 py-1 text-xs rounded bg-yellow-900 text-yellow-300">Unbilled</span>
            </td>
        </tr>"""

    html = f"""<table class="w-full text-sm text-left">
        <thead class="text-gray-400 border-b border-gray-700">
            <tr>
                <th class="py-2 pr-4">Date</th>
                <th class="py-2 pr-4">Hours</th>
                <th class="py-2 pr-4">Description</th>
                <th class="py-2">Status</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>"""
    return HTMLResponse(content=html)


# ── API: Invoices ────────────────────────────────────────────────────────────

@app.post("/api/clients/{client_id}/invoices")
async def create_invoice(
    client_id: int,
    session: Session = Depends(get_session),
):
    """Compiles unbilled logs into an invoice.

    Generates a new invoice from all unbilled time logs for the given client.
    Returns 404 if the client doesn't exist, 400 if there are no unbilled logs.
    """
    invoice = generate_invoice_transaction(session, client_id)
    return InvoiceResponse.model_validate(invoice)


@app.get("/api/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: int, session: Session = Depends(get_session)):
    """Fetches raw structured metadata of an invoice."""
    logger.debug("Fetching invoice: id=%s", invoice_id)
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        logger.warning("Invoice not found: id=%s", invoice_id)
        raise HTTPException(status_code=404, detail="Invoice not found.")
    logger.info("Invoice fetched: %s", invoice.invoice_number)
    return InvoiceResponse.model_validate(invoice)


# ── API: PDF Export ──────────────────────────────────────────────────────────

@app.get("/api/invoices/{invoice_id}/pdf")
async def export_invoice_pdf(
    invoice_id: int,
    session: Session = Depends(get_session),
):
    """Generates and streams a compiled PDF document."""
    from src.utils.pdf import compile_invoice_pdf

    logger.debug("Generating PDF for invoice: id=%s", invoice_id)
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        logger.warning("Invoice not found for PDF: id=%s", invoice_id)
        raise HTTPException(status_code=404, detail="Invoice not found.")

    pdf_bytes = compile_invoice_pdf(invoice, session)
    logger.info("PDF generated: %s (%d bytes)", invoice.invoice_number, len(pdf_bytes))

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.invoice_number}.pdf"'
        },
    )


# ── CLI Helper (legacy entry) ────────────────────────────────────────────────

def main():
    """Entry point for running the application with uvicorn."""
    import uvicorn
    logger.info("Starting Lean Invoice Tracker server on 0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
