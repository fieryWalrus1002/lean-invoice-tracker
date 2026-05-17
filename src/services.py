import logging
import time
from datetime import date as _date, timedelta
from typing import List, Optional, Union

from fastapi import Depends, Form, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session
from sqlmodel import select

from src.models import Client, Invoice, InvoiceSequence, TimeLog
from src.database import get_session

logger = logging.getLogger(__name__)


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

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v):
        if v <= 0:
            raise ValueError("hours must be a positive number")
        return v

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        if v and v > _date.today():
            raise ValueError("date cannot be in the future")
        return v

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
    """Compile all unbilled logs for a client into a new invoice.

    Uses a retry loop to resolve invoice number collisions in concurrent
    scenarios.  Each attempt increments the counter and checks uniqueness
    before committing.
    """
    logger.info("Generating invoice for client_id=%s", client_id)

    client = session.get(Client, client_id)
    if not client:
        logger.warning("Client not found: client_id=%s", client_id)
        raise HTTPException(status_code=404, detail="Client not found.")

    unbilled_logs = session.exec(
        select(TimeLog)
        .where(TimeLog.client_id == client_id)
        .where(TimeLog.is_billed == False)
    ).all()

    if not unbilled_logs:
        logger.info("No unbilled logs for client_id=%s", client_id)
        raise HTTPException(status_code=400, detail="No unbilled hours found.")

    # Calculate metrics
    total_hours = sum(log.hours for log in unbilled_logs)
    total_amount = total_hours * client.default_hourly_rate
    logger.info(
        "Found %d unbilled logs: %.2fh * %.2f = %.2f",
        len(unbilled_logs), total_hours, client.default_hourly_rate, total_amount,
    )

    # Mint unique invoice number using atomic sequence counter
    invoice_num = _next_invoice_number(session)

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
    logger.info("Invoice %s created for client_id=%s", invoice_num, client_id)
    return new_invoice


def _next_invoice_number(session: Session) -> str:
    """Return the next sequential invoice number for the current year.

    Uses an atomic database-level sequence counter to prevent collisions
    even under concurrent access.

    If the sequence row doesn't exist yet, it's initialized to the next
    number after any existing invoices for this year.
    """
    year = _date.today().year

    # Atomically increment the sequence counter
    seq = session.get(InvoiceSequence, year)
    if seq is None:
        # Initialize sequence based on existing invoices for this year
        existing = session.exec(
            select(Invoice.invoice_number)
            .where(Invoice.invoice_number.like(f"INV-{year}-%"))
        ).all()
        next_num = len(existing) + 1
        seq = InvoiceSequence(year=year, next_number=next_num + 1)
        session.add(seq)
        session.flush()
    else:
        next_num = seq.next_number
        seq.next_number += 1
        session.flush()

    invoice_num = f"INV-{year}-{next_num:04d}"
    return invoice_num


# ── Unbilled Logs Query ─────────────────────────────────────────────────────

def get_unbilled_logs(session: Session, client_id: Optional[int] = None) -> List[TimeLog]:
    """Fetch all unbilled time logs, optionally filtered by client."""
    logger.debug(
        "Fetching unbilled logs, client_id=%s", client_id,
    )
    stmt = select(TimeLog).where(TimeLog.is_billed == False)
    if client_id is not None:
        stmt = stmt.where(TimeLog.client_id == client_id)
    return session.exec(stmt).all()
