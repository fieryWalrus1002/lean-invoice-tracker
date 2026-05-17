"""
Tests for the API layer (all endpoints) using FastAPI TestClient.

Uses a test database file to avoid polluting the production database.
"""

import datetime
import io
import re
import sys
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session as SQLModelSession, SQLModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_session as original_get_session  # noqa: E402
from src.models import Client, Invoice, TimeLog  # noqa: E402
from src.main import app  # noqa: E402


def _decompress_pdf_content(pdf_bytes):
    """Decompress PDF streams (ReportLab uses ASCII85 + FlateDecode)."""
    try:
        from reportlab.lib.rl_accel import asciiBase85Decode
        decompressed = b""
        for match in re.finditer(rb'stream\n(.*?)~>', pdf_bytes, re.DOTALL):
            stream_data = match.group(1)
            try:
                decoded = asciiBase85Decode(stream_data + b'~>')
                decompressed += zlib.decompress(decoded)
            except Exception:
                decompressed += stream_data
        return decompressed
    except Exception:
        return pdf_bytes

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "test_invoices.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"


def _get_test_session_generator():
    """Generator that yields a SQLModel session backed by test DB."""
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_wal(dbapi_conn, _):
        dbapi_conn.cursor().execute("PRAGMA journal_mode=WAL;")
        dbapi_conn.cursor().close()

    SQLModel.metadata.create_all(engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    sess = SQLModelSession(bind=engine)
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture(autouse=True)
def _patch_session():
    """Patch get_session to use the test database for API tests.

    Uses a single shared session for all requests within a test,
    so state persists across multiple API calls.
    Cleans the database before each test to avoid leftover data.
    """
    # Remove leftover DB file from previous runs
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    # Create one session that lives for the entire test
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_wal(dbapi_conn, _):
        dbapi_conn.cursor().execute("PRAGMA journal_mode=WAL;")
        dbapi_conn.cursor().close()

    SQLModel.metadata.create_all(engine)
    test_session = SQLModelSession(bind=engine)

    # Override with a generator that yields the shared session
    def shared_session():
        yield test_session

    app.dependency_overrides[original_get_session] = shared_session
    yield
    # Clean up
    test_session.close()
    app.dependency_overrides.clear()


# ── Helper functions ─────────────────────────────────────────────────────────


def _create_client(c):
    """Helper to create a client via the API."""
    return c.post("/api/clients", json={
        "name": "Test Corp",
        "email": "test@test.com",
        "billing_address": "123 Test St",
        "default_hourly_rate": 100.00,
    })


def _create_log(c, client_id, hours=5.0, description="Test log", date_str=None):
    """Helper to create a time log via the API (JSON mode)."""
    data = {"client_id": client_id, "hours": hours, "description": description}
    if date_str:
        data["date"] = date_str
    return c.post("/api/logs", json=data)


def _create_log_form(c, client_id, hours=5.0, description="Test log", date_str=None):
    """Helper to create a time log via the API (form data mode)."""
    data = {"client_id": client_id, "hours": hours, "description": description}
    if date_str:
        data["date"] = date_str
    return c.post("/api/logs", data=data)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestDashboard:
    """Tests for the dashboard route."""

    def test_dashboard_returns_html(self):
        """GET / returns HTML content."""
        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "LEAN_INVOICE_TRACKER" in response.text

    def test_dashboard_contains_form(self):
        """Dashboard HTML contains the time log form."""
        with TestClient(app) as client:
            response = client.get("/")
            assert "hx-post" in response.text
            assert 'name="hours"' in response.text
            assert 'name="description"' in response.text

    def test_dashboard_contains_unbilled_section(self):
        """Dashboard contains the unbilled table container."""
        with TestClient(app) as client:
            response = client.get("/")
            assert "unbilled-table-container" in response.text
            assert 'hx-get="/api/logs/unbilled"' in response.text


class TestClientEndpoints:
    """Tests for /api/clients endpoints."""

    def test_create_client(self):
        """POST /api/clients creates a client and returns 200."""
        with TestClient(app) as client:
            response = _create_client(client)
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Test Corp"
            assert data["email"] == "test@test.com"
            assert data["default_hourly_rate"] == 100.00
            assert "id" in data

    def test_list_clients(self):
        """GET /api/clients returns all clients."""
        with TestClient(app) as client:
            _create_client(client)
            _create_client(client)
            response = client.get("/api/clients")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2

    def test_list_clients_empty(self):
        """GET /api/clients returns empty list when no clients."""
        with TestClient(app) as client:
            response = client.get("/api/clients")
            assert response.status_code == 200
            assert response.json() == []


class TestTimeLogEndpoints:
    """Tests for /api/logs endpoints."""

    def test_create_log_json(self):
        """POST /api/logs with JSON body (CLI mode)."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            response = _create_log(client, client_id, hours=4.5,
                                   description="Network config",
                                   date_str="2026-05-17")
            assert response.status_code == 200
            data = response.json()
            assert data["hours"] == 4.5
            assert data["description"] == "Network config"
            assert data["is_billed"] is False
            assert "id" in data

    def test_create_log_form_data(self):
        """POST /api/logs with form data (HTMX mode)."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            response = _create_log_form(client, client_id, hours=3.0,
                                        description="Form test",
                                        date_str="2026-05-17")
            assert response.status_code == 200
            data = response.json()
            assert data["hours"] == 3.0
            assert data["description"] == "Form test"

    def test_create_log_default_date(self):
        """POST /api/logs without date defaults to today."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            response = _create_log(client, client_id, hours=2.0,
                                   description="Default date test")
            assert response.status_code == 200
            data = response.json()
            assert data["date"] == datetime.date.today().isoformat()

    def test_create_log_missing_client_id(self):
        """POST /api/logs without client_id returns 422."""
        with TestClient(app) as client:
            response = client.post("/api/logs", json={
                "hours": 5.0,
                "description": "No client",
            })
            assert response.status_code == 422

    def test_create_log_missing_hours(self):
        """POST /api/logs without hours returns 422."""
        with TestClient(app) as client:
            response = client.post("/api/logs", json={
                "client_id": 1,
                "description": "No hours",
            })
            assert response.status_code == 422

    def test_csv_upload(self):
        """POST /api/logs/upload-csv imports multiple logs."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]

            csv_content = (
                "client_id,date,hours,description\n"
                f"{client_id},2026-05-15,4.0,CSV Log 1\n"
                f"{client_id},2026-05-16,3.5,CSV Log 2\n"
                f"{client_id},2026-05-17,2.0,CSV Log 3\n"
            )
            csv_file = io.BytesIO(csv_content.encode("utf-8"))
            response = client.post(
                "/api/logs/upload-csv",
                files={"file": ("test.csv", csv_file, "text/csv")},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Imported 3 time logs."

            # Verify logs were created
            unbilled_resp = client.get("/api/logs/unbilled")
            assert "CSV Log 1" in unbilled_resp.text
            assert "CSV Log 2" in unbilled_resp.text
            assert "CSV Log 3" in unbilled_resp.text

    def test_csv_upload_invalid_rows(self):
        """CSV with malformed rows skips bad rows, imports valid ones."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]

            csv_content = (
                "client_id,date,hours,description\n"
                f"{client_id},2026-05-15,4.0,Valid Row\n"
                "bad,row\n"  # only 2 columns, should be skipped
                f"{client_id},2026-05-16,2.5,Another Valid\n"
            )
            csv_file = io.BytesIO(csv_content.encode("utf-8"))
            response = client.post(
                "/api/logs/upload-csv",
                files={"file": ("test.csv", csv_file, "text/csv")},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Imported 2 time logs."

    def test_unbilled_logs_html(self):
        """GET /api/logs/unbilled returns HTML table."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="Test log")

            response = client.get("/api/logs/unbilled")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "<table" in response.text
            assert "Test log" in response.text
            assert "Unbilled" in response.text

    def test_unbilled_logs_empty(self):
        """GET /api/logs/unbilled returns message when no logs."""
        with TestClient(app) as client:
            response = client.get("/api/logs/unbilled")
            assert response.status_code == 200
            assert "No unbilled time logs found" in response.text

    def test_unbilled_logs_filter_by_client(self):
        """GET /api/logs/unbilled?client_id=N filters results."""
        with TestClient(app) as client:
            resp1 = _create_client(client)
            resp2 = _create_client(client)
            cid1 = resp1.json()["id"]
            cid2 = resp2.json()["id"]

            _create_log(client, cid1, hours=5.0, description="C1 log")
            _create_log(client, cid1, hours=3.0, description="C1 log 2")
            _create_log(client, cid2, hours=2.0, description="C2 log")

            response = client.get(f"/api/logs/unbilled?client_id={cid1}")
            assert response.status_code == 200
            assert "C1 log" in response.text
            assert "C1 log 2" in response.text
            assert "C2 log" not in response.text

    def test_unbilled_logs_after_invoice(self):
        """Unbilled table updates after invoice generation."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="To be billed")

            # Generate invoice
            client.post(f"/api/clients/{client_id}/invoices")

            response = client.get("/api/logs/unbilled")
            assert response.status_code == 200
            assert "No unbilled time logs found" in response.text


class TestInvoiceEndpoints:
    """Tests for invoice-related endpoints."""

    def test_create_invoice(self):
        """POST /api/clients/{id}/invoices generates an invoice."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]

            # Create some unbilled logs
            _create_log(client, client_id, hours=5.0, description="Log 1")
            _create_log(client, client_id, hours=3.0, description="Log 2")

            response = client.post(f"/api/clients/{client_id}/invoices")
            assert response.status_code == 200
            data = response.json()
            assert data["invoice_number"].startswith("INV-")
            assert data["status"] == "Draft"
            assert data["total_amount"] == 800.00  # 8h * 100/hr
            assert "id" in data

    def test_create_invoice_no_unbilled(self):
        """POST /api/clients/{id}/invoices returns 400 when no unbilled logs."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]

            response = client.post(f"/api/clients/{client_id}/invoices")
            assert response.status_code == 400
            assert "No unbilled hours found" in response.json()["detail"]

    def test_create_invoice_client_not_found(self):
        """POST /api/clients/999/invoices returns 404."""
        with TestClient(app) as client:
            response = client.post("/api/clients/999/invoices")
            assert response.status_code == 404
            assert "Client not found" in response.json()["detail"]

    def test_get_invoice(self):
        """GET /api/invoices/{id} returns invoice metadata."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="Log 1")
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            invoice_id = inv_resp.json()["id"]

            response = client.get(f"/api/invoices/{invoice_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["invoice_number"] == inv_resp.json()["invoice_number"]
            assert data["total_amount"] == inv_resp.json()["total_amount"]

    def test_get_invoice_not_found(self):
        """GET /api/invoices/999 returns 404."""
        with TestClient(app) as client:
            response = client.get("/api/invoices/999")
            assert response.status_code == 404
            assert "Invoice not found" in response.json()["detail"]


class TestPDFExport:
    """Tests for PDF export endpoint."""

    def test_pdf_export(self):
        """GET /api/invoices/{id}/pdf returns valid PDF."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="Log 1")
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            invoice_id = inv_resp.json()["id"]

            response = client.get(f"/api/invoices/{invoice_id}/pdf")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert "attachment" in response.headers["content-disposition"]
            pdf_data = response.content
            assert len(pdf_data) > 0
            assert pdf_data.startswith(b"%PDF")

    def test_pdf_export_not_found(self):
        """GET /api/invoices/999/pdf returns 404."""
        with TestClient(app) as client:
            response = client.get("/api/invoices/999/pdf")
            assert response.status_code == 404
            assert "Invoice not found" in response.json()["detail"]

    def test_pdf_content_includes_invoice_number(self):
        """PDF content includes the invoice number."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="Log 1")
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            invoice_id = inv_resp.json()["id"]

            response = client.get(f"/api/invoices/{invoice_id}/pdf")
            pdf_data = response.content
            invoice_number = inv_resp.json()["invoice_number"]
            # PDF content is compressed; decompress before searching
            searchable = _decompress_pdf_content(pdf_data)
            assert invoice_number.encode() in searchable

    def test_pdf_content_includes_client_name(self):
        """PDF content includes the client name."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="Log 1")
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            invoice_id = inv_resp.json()["id"]

            response = client.get(f"/api/invoices/{invoice_id}/pdf")
            pdf_data = response.content
            # PDF content is compressed; decompress before searching
            searchable = _decompress_pdf_content(pdf_data)
            assert b"Test Corp" in searchable

    def test_pdf_content_includes_total(self):
        """PDF content includes the total amount."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]
            _create_log(client, client_id, hours=5.0, description="Log 1")
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            invoice_id = inv_resp.json()["id"]

            response = client.get(f"/api/invoices/{invoice_id}/pdf")
            pdf_data = response.content
            # Total should be 500.00 (5h * 100/hr)
            # PDF content is compressed; decompress before searching
            searchable = _decompress_pdf_content(pdf_data)
            assert b"500.00" in searchable


class TestIntegrationWorkflows:
    """End-to-end workflow tests."""

    def test_create_client_log_invoice_pdf(self):
        """Full workflow: create client → log hours → generate invoice → PDF."""
        with TestClient(app) as client:
            # 1. Create client
            resp = _create_client(client)
            assert resp.status_code == 200
            client_id = resp.json()["id"]

            # 2. Create 3 time logs
            _create_log(client, client_id, hours=4.0, description="Task A")
            _create_log(client, client_id, hours=2.5, description="Task B")
            _create_log(client, client_id, hours=1.5, description="Task C")

            # 3. Verify unbilled table shows 3 entries
            unbilled_resp = client.get("/api/logs/unbilled")
            assert "Task A" in unbilled_resp.text
            assert "Task B" in unbilled_resp.text
            assert "Task C" in unbilled_resp.text

            # 4. Generate invoice
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            assert inv_resp.status_code == 200
            invoice_id = inv_resp.json()["id"]

            # 5. Verify invoice total: (4.0+2.5+1.5) * 100 = 800.00
            assert inv_resp.json()["total_amount"] == 800.00

            # 6. Verify unbilled table is now empty
            unbilled_resp = client.get("/api/logs/unbilled")
            assert "No unbilled time logs found" in unbilled_resp.text

            # 7. Get invoice metadata
            get_inv = client.get(f"/api/invoices/{invoice_id}")
            assert get_inv.status_code == 200
            assert get_inv.json()["invoice_number"] == inv_resp.json()["invoice_number"]

            # 8. Export PDF
            pdf_resp = client.get(f"/api/invoices/{invoice_id}/pdf")
            assert pdf_resp.status_code == 200
            assert pdf_resp.content.startswith(b"%PDF")

    def test_csv_upload_then_invoice(self):
        """CSV upload → verify logs → generate invoice."""
        with TestClient(app) as client:
            resp = _create_client(client)
            client_id = resp.json()["id"]

            csv_content = (
                "client_id,date,hours,description\n"
                f"{client_id},2026-05-10,5.0,CSV Task 1\n"
                f"{client_id},2026-05-11,3.0,CSV Task 2\n"
            )
            csv_file = io.BytesIO(csv_content.encode("utf-8"))
            client.post(
                "/api/logs/upload-csv",
                files={"file": ("test.csv", csv_file, "text/csv")},
            )

            # Verify 2 unbilled
            unbilled_resp = client.get("/api/logs/unbilled")
            assert "CSV Task 1" in unbilled_resp.text
            assert "CSV Task 2" in unbilled_resp.text

            # Generate invoice: 8.0h * 100 = 800.00
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")
            assert inv_resp.json()["total_amount"] == 800.00

            # Unbilled should be empty
            assert "No unbilled time logs found" in client.get("/api/logs/unbilled").text
