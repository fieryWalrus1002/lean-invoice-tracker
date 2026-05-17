"""Database models for the Lean Invoice Tracker.

Defines the SQLModel schemas for Client, TimeLog, and Invoice tables
along with their relationships.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, List, Optional

from sqlalchemy import Column, Date as SqlDate
from sqlmodel import SQLModel, Field, Relationship


class Client(SQLModel, table=True):
    """A client profile for time tracking and invoicing.

    Attributes:
        id: Primary key.
        name: Client display name.
        email: Contact email address.
        billing_address: Billing address string.
        default_hourly_rate: Default rate used when generating invoices.
        time_logs: Related time log entries.
        invoices: Related invoices.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    email: str
    billing_address: str
    default_hourly_rate: Decimal = Field(default=0.00, decimal_places=2)

    # Relationships
    time_logs: List["TimeLog"] = Relationship(back_populates="client")
    invoices: List["Invoice"] = Relationship(back_populates="client")


class TimeLog(SQLModel, table=True):
    """A single time-tracking entry linked to a client.

    Attributes:
        id: Primary key.
        date: Date the work was performed.
        hours: Number of hours logged.
        description: Description of the work performed.
        is_billed: Whether this log has been included in an invoice.
        client_id: FK to the associated client.
        invoice_id: FK to the invoice this log was billed under (null if unbilled).
        client: Related client profile.
        invoice: Related invoice (null if unbilled).
    """

    __tablename__ = "timelog"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: Annotated[
        date,
        Field(
            default_factory=date.today,
            sa_column=Column("date", SqlDate, index=True),
        ),
    ]
    hours: Decimal = Field(decimal_places=2)
    description: str
    is_billed: bool = Field(default=False, index=True)

    # Foreign Keys
    client_id: int = Field(foreign_key="client.id")
    invoice_id: Optional[int] = Field(default=None, foreign_key="invoice.id")

    # Relationships
    client: Client = Relationship(back_populates="time_logs")
    invoice: Optional["Invoice"] = Relationship(back_populates="time_logs")


class InvoiceSequence(SQLModel, table=True):
    """Atomic sequence counter for invoice numbers.

    Stores the next sequence number per year to ensure unique, collision-free
    invoice numbering even under concurrent access.

    Attributes:
        year: The year this sequence applies to (e.g. 2026).
        next_number: The next available sequence number (starts at 1).
    """

    __tablename__ = "invoice_sequence"

    year: int = Field(primary_key=True, index=True)
    next_number: int = Field(default=1)


class Invoice(SQLModel, table=True):
    """An invoice aggregating unbilled time logs for a client.

    Attributes:
        id: Primary key.
        invoice_number: Unique identifier (e.g. ``INV-2026-0001``).
        issue_date: Date the invoice was issued.
        due_date: Payment due date (typically 30 days after issue).
        total_amount: Total amount in the client's currency.
        status: Invoice status – ``Draft``, ``Sent``, ``Paid``, or ``Void``.
        client_id: FK to the billed client.
        client: Related client profile.
        time_logs: Time log entries included in this invoice.
    """

    __tablename__ = "invoice"

    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_number: str = Field(unique=True, index=True)  # e.g., INV-2026-0001
    issue_date: date = Field(default_factory=date.today)
    due_date: date
    total_amount: Decimal = Field(decimal_places=2)
    status: str = Field(default="Draft")  # Draft, Sent, Paid, Void

    # Foreign Keys
    client_id: int = Field(foreign_key="client.id")

    # Relationships
    client: Client = Relationship(back_populates="invoices")
    time_logs: List[TimeLog] = Relationship(back_populates="invoice")
