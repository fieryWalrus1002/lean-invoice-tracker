from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, List, Optional

from sqlalchemy import Column, Date as SqlDate
from sqlmodel import SQLModel, Field, Relationship


class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    email: str
    billing_address: str
    default_hourly_rate: Decimal = Field(default=0.00, decimal_places=2)

    # Relationships
    time_logs: List["TimeLog"] = Relationship(back_populates="client")
    invoices: List["Invoice"] = Relationship(back_populates="client")


class TimeLog(SQLModel, table=True):
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


class Invoice(SQLModel, table=True):
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
