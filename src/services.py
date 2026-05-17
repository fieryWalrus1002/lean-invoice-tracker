from datetime import date as _date, timedelta
from typing import List, Optional, Union

from fastapi import Depends, Form, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session
from sqlmodel import select

from src.models import Client, Invoice, TimeLog
from src.database import get_session


# ── Request Schemas ──────────────────────────────────────────────────────────

class ClientCreate(BaseModel):
    name: str
    email: str
    billing_address: str
    default_hourly_rate: float = 0.00


class TimeLogCreate(BaseModel):
    client_id: int
    hours: float
    description: str
    date: Optional[_date] = Field(default=None, alias="date")

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def normalize_date(cls, data):
        if isinstance(data, dict):
            if "date" in data and isinstance(data["date"], str):
                data["date"] = _date.fromisoformat(data["date"])
        return data


class TimeLogResponse(BaseModel):
    id: int
    date: _date
    hours: float
    description: str
    is_billed: bool
    client_id: int
    invoice_id: Optional[int] = None

    model_config = {"from_attributes": True}


class ClientResponse(BaseModel):
    id: int
    name: str
    email: str
    billing_address: str
    default_hourly_rate: float

    model_config = {"from_attributes": True}


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    issue_date: _date
    due_date: _date
    total_amount: float
    status: str
    client_id: int

    model_config = {"from_attributes": True}


# ── Core Invoice Aggregation Logic ───────────────────────────────────────────

def generate_invoice_transaction(session: Session, client_id: int) -> Invoice:
    """Compile all unbilled logs for a client into a new invoice."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    unbilled_logs = session.query(TimeLog).filter(
        TimeLog.client_id == client_id,
        TimeLog.is_billed == False
    ).all()

    if not unbilled_logs:
        raise HTTPException(status_code=400, detail="No unbilled hours found.")

    # Calculate metrics
    total_hours = sum(log.hours for log in unbilled_logs)
    total_amount = total_hours * client.default_hourly_rate

    # Mint unique tracking sequence string (atomic counter to prevent race conditions)
    max_invoice = session.query(Invoice).filter(
        Invoice.invoice_number.like(f"INV-{_date.today().year}-%")
    ).count()
    invoice_num = f"INV-{_date.today().year}-{max_invoice + 1:04d}"

    # Commit state updates
    new_invoice = Invoice(
        invoice_number=invoice_num,
        due_date=_date.today() + timedelta(days=30),
        total_amount=total_amount,
        client_id=client_id,
    )
    session.add(new_invoice)
    session.flush()  # Extract assigned id

    for log in unbilled_logs:
        log.is_billed = True
        log.invoice_id = new_invoice.id
        session.add(log)

    session.commit()
    return new_invoice


# ── Unbilled Logs Query ─────────────────────────────────────────────────────

def get_unbilled_logs(session: Session, client_id: Optional[int] = None) -> List[TimeLog]:
    """Fetch all unbilled time logs, optionally filtered by client."""
    stmt = select(TimeLog).where(TimeLog.is_billed == False)
    if client_id is not None:
        stmt = stmt.where(TimeLog.client_id == client_id)
    return session.exec(stmt).all()
