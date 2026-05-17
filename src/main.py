import os
from datetime import date as _date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, Header, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import HTTPException
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

app = FastAPI(title="Lean Invoice Tracker")

# Resolve the templates directory relative to this file
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Create tables on startup
create_db_and_tables()


# ── Dashboard UI ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Renders the front-end dashboard UI template."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── API: Clients ─────────────────────────────────────────────────────────────

@app.post("/api/clients", response_model=ClientResponse)
async def create_client(client: ClientCreate, session: Session = Depends(get_session)):
    """Creates a new tracking client profile."""
    db_client = Client(
        name=client.name,
        email=client.email,
        billing_address=client.billing_address,
        default_hourly_rate=client.default_hourly_rate,
    )
    session.add(db_client)
    session.commit()
    session.refresh(db_client)
    return db_client


@app.get("/api/clients", response_model=list[ClientResponse])
async def list_clients(session: Session = Depends(get_session)):
    """Lists all active client profiles."""
    clients = session.exec(select(Client)).all()
    return clients


# ── API: Time Logs ───────────────────────────────────────────────────────────

@app.post("/api/logs", response_model=TimeLogResponse)
async def create_time_log(
    request: Request,
    client_id: Optional[int] = Form(None),
    hours: Optional[float] = Form(None),
    description: Optional[str] = Form(None),
    date_str: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """Records a manual time-tracking entry. Accepts JSON (CLI) or form data (HTMX)."""
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
        body = await request.json()
        create_data = TimeLogCreate(**body)

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
    return db_log


@app.post("/api/logs/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Multi-part form upload for bulk CSV parsing."""
    contents = await file.read()
    text = contents.decode("utf-8")
    lines = text.strip().splitlines()

    created = 0
    for line in lines[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 4:
            continue
        client_id = int(parts[0])
        log_date = _date.fromisoformat(parts[1])
        hours = float(parts[2])
        description = parts[3]

        db_log = TimeLog(
            client_id=client_id,
            date=log_date,
            hours=hours,
            description=description,
        )
        session.add(db_log)
        created += 1

    session.commit()
    return JSONResponse(content={"message": f"Imported {created} time logs."})


@app.get("/api/logs/unbilled", response_class=HTMLResponse)
async def unbilled_logs_html(
    client_id: Optional[int] = Query(None),
    session: Session = Depends(get_session),
):
    """Returns an HTML table fragment of unbilled logs (used by HTMX)."""
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
    """Compiles unbilled logs into an invoice."""
    invoice = generate_invoice_transaction(session, client_id)
    return InvoiceResponse.model_validate(invoice)


@app.get("/api/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: int, session: Session = Depends(get_session)):
    """Fetches raw structured metadata of an invoice."""
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return InvoiceResponse.model_validate(invoice)


# ── API: PDF Export ──────────────────────────────────────────────────────────

@app.get("/api/invoices/{invoice_id}/pdf")
async def export_invoice_pdf(
    invoice_id: int,
    session: Session = Depends(get_session),
):
    """Generates and streams a compiled PDF document."""
    from src.utils.pdf import compile_invoice_pdf

    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    pdf_bytes = compile_invoice_pdf(invoice, session)

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.invoice_number}.pdf"'
        },
    )


# ── CLI Helper (legacy entry) ────────────────────────────────────────────────

def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
