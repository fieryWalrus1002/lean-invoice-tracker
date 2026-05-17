import logging
import time
from datetime import date as _date, timedelta
from typing import List, Optional, Union

from fastapi import Depends, Form, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session
from sqlmodel import select

from src.models import Client, Invoice, TimeLog
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

    # Mint unique tracking sequence string with retry for collisions
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        invoice_num = _next_invoice_number(session)
        try:
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
        except Exception:
            session.rollback()
            if attempt < max_retries:
                logger.warning(
                    "Invoice number collision on attempt %d, retrying…",
                    attempt,
                    exc_info=True,
                )
                time.sleep(0.05)
            else:
                logger.error(
                    "Failed to generate invoice after %d attempts, client_id=%s",
                    max_retries, client_id,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Failed to generate invoice after multiple attempts.",
                )


def _next_invoice_number(session: Session) -> str:
    """Return the next sequential invoice number for the current year.

    Raises an HTTPException on collision after checking.
    """
    year = _date.today().year
    existing = session.exec(
        select(Invoice.invoice_number)
        .where(Invoice.invoice_number.like(f"INV-{year}-%"))
    ).all()
    count = len(existing)
    candidate = f"INV-{year}-{count + 1:04d}"

    # Verify the candidate isn't already taken (collision check)
    if any(inv == candidate for inv in existing):
        # Collision: try the next number
        candidate = f"INV-{year}-{count + 2:04d}"

    return candidate


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
