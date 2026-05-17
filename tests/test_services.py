"""
Tests for business logic: invoice generation, unbilled log queries, schemas.
"""

import datetime
import sys
from pathlib import Path
from decimal import Decimal

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Client, Invoice, TimeLog  # noqa: E402
from src.services import (  # noqa: E402
    generate_invoice_transaction,
    get_unbilled_logs,
    TimeLogCreate,
)


class TestInvoiceGeneration:
    """Tests for generate_invoice_transaction()."""

    def test_happy_path(self, session, client_obj, unbilled_logs):
        """Generate invoice from 5 unbilled logs (10+5+3+2+5 = 25h * 100 = 2500)."""
        invoice = generate_invoice_transaction(session, client_obj.id)

        assert invoice.invoice_number.startswith("INV-")
        # invoice_number format: INV-YYYY-NNNN
        parts = invoice.invoice_number.split("-")
        assert len(parts) == 3
        assert parts[0] == "INV"
        assert parts[1] == str(datetime.date.today().year)

        assert invoice.total_amount == Decimal("2500.00")
        assert invoice.status == "Draft"
        assert invoice.issue_date == datetime.date.today()
        assert invoice.due_date == datetime.date.today() + datetime.timedelta(days=30)

        # All logs should be billed and linked
        for log in unbilled_logs:
            session.refresh(log)
            assert log.is_billed is True
            assert log.invoice_id == invoice.id

    def test_invoice_number_format(self, session, client_obj, unbilled_logs):
        """Invoice number follows INV-YYYY-NNNN format."""
        invoice = generate_invoice_transaction(session, client_obj.id)
        parts = invoice.invoice_number.split("-")
        assert len(parts) == 3
        assert parts[0] == "INV"
        assert parts[1] == str(datetime.date.today().year)
        assert len(parts[2]) == 4  # 4-digit zero-padded number
        assert parts[2].isdigit()

    def test_no_unbilled_logs_raises_400(self, session, client_obj, unbilled_logs):
        """Raises HTTPException(400) when no unbilled logs exist."""
        # First call bills all logs
        generate_invoice_transaction(session, client_obj.id)

        with pytest.raises(HTTPException) as exc_info:
            generate_invoice_transaction(session, client_obj.id)

        assert exc_info.value.status_code == 400
        assert "No unbilled hours found" in exc_info.value.detail

    def test_client_not_found_raises_404(self, session):
        """Raises HTTPException(404) for non-existent client."""
        with pytest.raises(HTTPException) as exc_info:
            generate_invoice_transaction(session, 9999)

        assert exc_info.value.status_code == 404
        assert "Client not found" in exc_info.value.detail

    def test_decimal_precision_in_totals(self, session, client_high_rate):
        """Decimal precision preserved: hours 3.25+2.75=6.0 * rate 150.50 = 903.00."""
        today = datetime.date.today()
        logs = [
            TimeLog(date=today - datetime.timedelta(days=1), hours=3.25,
                    description="Precision 1", client_id=client_high_rate.id),
            TimeLog(date=today, hours=2.75, description="Precision 2",
                    client_id=client_high_rate.id),
        ]
        session.add_all(logs)
        session.commit()

        invoice = generate_invoice_transaction(session, client_high_rate.id)
        assert invoice.total_amount == Decimal("903.00")

    def test_invoice_number_increments(self, session, client_obj, unbilled_logs):
        """Multiple invoice generations produce sequential numbers."""
        inv1 = generate_invoice_transaction(session, client_obj.id)
        # Create new unbilled logs for second invoice
        today = datetime.date.today()
        log2 = TimeLog(date=today, hours=5.0, description="New log",
                       client_id=client_obj.id)
        session.add(log2)
        session.commit()

        inv2 = generate_invoice_transaction(session, client_obj.id)

        num1 = int(inv1.invoice_number.split("-")[2])
        num2 = int(inv2.invoice_number.split("-")[2])
        assert num2 == num1 + 1

    def test_year_scoped_invoice_numbers(self, session, client_obj, unbilled_logs):
        """Invoice numbers are scoped to the current year."""
        invoice = generate_invoice_transaction(session, client_obj.id)
        year_prefix = f"INV-{datetime.date.today().year}"
        assert invoice.invoice_number.startswith(year_prefix)

    def test_invoice_total_calculation(self, session, client_obj):
        """Total = sum(hours) * hourly_rate."""
        today = datetime.date.today()
        logs = [
            TimeLog(date=today - datetime.timedelta(days=2), hours=4.0,
                    description="Log 1", client_id=client_obj.id),
            TimeLog(date=today - datetime.timedelta(days=1), hours=6.5,
                    description="Log 2", client_id=client_obj.id),
        ]
        session.add_all(logs)
        session.commit()

        invoice = generate_invoice_transaction(session, client_obj.id)
        expected = (4.0 + 6.5) * 100.00  # 1050.00
        assert invoice.total_amount == Decimal(str(expected))


class TestUnbilledLogsQuery:
    """Tests for get_unbilled_logs()."""

    def test_fetch_all_unbilled(self, session, mixed_logs):
        """Returns all unbilled logs when no client_id filter."""
        # mixed_logs: 3 unbilled for client 1, 2 unbilled for client 2 = 5 total
        logs = get_unbilled_logs(session)
        assert len(logs) == 5
        for log in logs:
            assert log.is_billed is False

    def test_fetch_unbilled_by_client(self, session, mixed_logs, client_obj, client_high_rate):
        """Returns only logs for the specified client_id."""
        logs_c1 = get_unbilled_logs(session, client_id=client_obj.id)
        assert len(logs_c1) == 3

        logs_c2 = get_unbilled_logs(session, client_id=client_high_rate.id)
        assert len(logs_c2) == 2

    def test_empty_result_set(self, session, client_obj):
        """Returns empty list when no unbilled logs exist."""
        logs = get_unbilled_logs(session)
        assert logs == []

    def test_billed_logs_excluded(self, session, client_obj, unbilled_logs):
        """After invoice generation, unbilled query returns empty."""
        generate_invoice_transaction(session, client_obj.id)
        logs = get_unbilled_logs(session, client_id=client_obj.id)
        assert logs == []


class TestTimeLogCreateSchema:
    """Tests for TimeLogCreate Pydantic schema."""

    def test_date_string_parsing(self):
        """Date string is converted to datetime.date."""
        data = TimeLogCreate(
            client_id=1,
            hours=5.0,
            description="Test",
            date="2026-05-15",
        )
        assert data.date == datetime.date(2026, 5, 15)

    def test_optional_date_defaults_to_none(self):
        """When date is omitted, it defaults to None."""
        data = TimeLogCreate(
            client_id=1,
            hours=5.0,
            description="Test",
        )
        assert data.date is None

    def test_required_fields(self):
        """client_id, hours, description are required."""
        with pytest.raises(Exception):  # ValidationError
            TimeLogCreate(hours=5.0, description="Test")

    def test_all_fields_accepted(self):
        """All fields including optional date are accepted."""
        data = TimeLogCreate(
            client_id=42,
            hours=7.5,
            description="Full test",
            date="2026-01-01",
        )
        assert data.client_id == 42
        assert data.hours == 7.5
        assert data.description == "Full test"
        assert data.date == datetime.date(2026, 1, 1)
