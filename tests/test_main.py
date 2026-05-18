"""
Tests for the API layer (all endpoints) using FastAPI TestClient.

Uses a test database file to avoid polluting the production database.
"""

import datetime
import io
import os
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
    """Extract text from PDF bytes using pypdf (WeasyPrint produces standard PDFs)."""
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages:
            text = page.extract_text() or ""
            text_parts.append(text)
        return "\n".join(text_parts)
    except Exception:
        return pdf_bytes.decode("utf-8", errors="replace")

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "test_invoices.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"

# Fallback to temp dir if data/ is not writable (permission issues)
if not TEST_DB_PATH.parent.exists() or not os.access(TEST_DB_PATH.parent, os.W_OK):
    import tempfile
    TEST_DB_PATH = Path(tempfile.mkdtemp()) / "test_invoices.db"
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
            resp1 = client.post("/api/clients", json={
                "name": "Client Alpha", "email": "a@test.com",
                "billing_address": "123 A St", "default_hourly_rate": 100.00,
            })
            resp2 = client.post("/api/clients", json={
                "name": "Client Beta", "email": "b@test.com",
                "billing_address": "456 B St", "default_hourly_rate": 120.00,
            })
            assert resp1.status_code == 200
            assert resp2.status_code == 200
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

    def test_create_client_returns_existing_on_duplicate_name(self):
        """POST /api/clients returns existing client when name already exists."""
        with TestClient(app) as client:
            resp1 = _create_client(client)
            cid1 = resp1.json()["id"]

            # Try creating same name again
            resp2 = _create_client(client)
            assert resp2.status_code == 200
            assert resp2.json()["id"] == cid1

    def test_list_clients_no_duplicates(self):
        """GET /api/clients returns unique clients even after duplicate POSTs."""
        with TestClient(app) as client:
            _create_client(client)
            _create_client(client)
            _create_client(client)
            response = client.get("/api/clients")
            assert response.status_code == 200
            data = response.json()
            # Should only have 1 client despite 3 POSTs
            assert len(data) == 1
            assert data[0]["name"] == "Test Corp"

    def test_list_clients_html_no_duplicates(self):
        """GET /api/clients/html returns unique clients."""
        with TestClient(app) as client:
            _create_client(client)
            _create_client(client)
            response = client.get("/api/clients/html")
            assert response.status_code == 200
            # Should only contain one "Test Corp" table row (name appears twice:
            # once in the table cell, once in the hx-confirm attribute)
            assert response.text.count("Test Corp") == 2
            # Verify delete button is present
            assert "hx-delete" in response.text
            assert "hx-confirm" in response.text


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
            assert "Imported 2 time logs." in data["message"]

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
            resp1 = client.post("/api/clients", json={
                "name": "Filter Client 1", "email": "f1@test.com",
                "billing_address": "123 F1 St", "default_hourly_rate": 100.00,
            })
            resp2 = client.post("/api/clients", json={
                "name": "Filter Client 2", "email": "f2@test.com",
                "billing_address": "456 F2 St", "default_hourly_rate": 100.00,
            })
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
            assert invoice_number in searchable

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
            assert "Test Corp" in searchable

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
            assert "500.00" in searchable


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


class TestDeleteClient:
    """Tests for the DELETE /api/clients/{client_id} endpoint."""

    def test_delete_client_success(self):
        """DELETE /api/clients/{id} removes the client."""
        with TestClient(app) as client:
            resp = client.post("/api/clients", json={
                "name": "Delete Me", "email": "delete@test.com",
                "billing_address": "123 Delete St", "default_hourly_rate": 50.00,
            })
            client_id = resp.json()["id"]

            # Delete the client
            delete_resp = client.delete(f"/api/clients/{client_id}")
            assert delete_resp.status_code == 200
            data = delete_resp.json()
            assert "deleted successfully" in data["message"]

            # Verify client is gone
            list_resp = client.get("/api/clients")
            assert all(c["name"] != "Delete Me" for c in list_resp.json())

    def test_delete_client_not_found(self):
        """DELETE /api/clients/999 returns 404."""
        with TestClient(app) as client:
            response = client.delete("/api/clients/999")
            assert response.status_code == 404
            assert "Client not found" in response.json()["detail"]

    def test_delete_client_with_unbilled_logs(self):
        """DELETE returns 409 if client has unbilled time logs."""
        with TestClient(app) as client:
            resp = client.post("/api/clients", json={
                "name": "Stubborn Client", "email": "stub@test.com",
                "billing_address": "456 Stub St", "default_hourly_rate": 75.00,
            })
            client_id = resp.json()["id"]

            # Create an unbilled log
            _create_log(client, client_id, hours=3.0, description="Unbilled work")

            # Try to delete — should fail with 409
            delete_resp = client.delete(f"/api/clients/{client_id}")
            assert delete_resp.status_code == 409
            assert "time log" in delete_resp.json()["detail"].lower()

    def test_delete_client_with_billed_logs(self):
        """DELETE returns 409 if client has any time logs (even billed ones)."""
        with TestClient(app) as client:
            resp = client.post("/api/clients", json={
                "name": "Billed Client", "email": "billed@test.com",
                "billing_address": "789 Billed Ave", "default_hourly_rate": 100.00,
            })
            client_id = resp.json()["id"]

            # Create logs and generate an invoice
            _create_log(client, client_id, hours=5.0, description="Billed work")
            client.post(f"/api/clients/{client_id}/invoices")

            # Try to delete — should fail because time logs exist
            delete_resp = client.delete(f"/api/clients/{client_id}")
            assert delete_resp.status_code == 409

    def test_delete_client_empty_logs(self):
        """DELETE succeeds when client has no time logs."""
        with TestClient(app) as client:
            resp = client.post("/api/clients", json={
                "name": "Empty Client", "email": "empty@test.com",
                "billing_address": "000 Empty Rd", "default_hourly_rate": 200.00,
            })
            client_id = resp.json()["id"]

            # No logs created — should delete fine
            delete_resp = client.delete(f"/api/clients/{client_id}")
            assert delete_resp.status_code == 200
            assert "deleted successfully" in delete_resp.json()["message"]

    def test_delete_client_removes_from_dropdown(self):
        """Deleted client is removed from the client dropdown."""
        with TestClient(app) as client:
            # Create two clients
            resp1 = client.post("/api/clients", json={
                "name": "Keep This", "email": "keep@test.com",
                "billing_address": "111 Keep St", "default_hourly_rate": 100.00,
            })
            resp2 = client.post("/api/clients", json={
                "name": "Delete This", "email": "del@test.com",
                "billing_address": "222 Drop St", "default_hourly_rate": 100.00,
            })
            keep_id = resp1.json()["id"]
            del_id = resp2.json()["id"]

            # Delete one
            client.delete(f"/api/clients/{del_id}")

            # Verify only one remains
            list_resp = client.get("/api/clients")
            data = list_resp.json()
            assert len(data) == 1
            assert data[0]["name"] == "Keep This"

    def test_delete_html_response(self):
        """GET /api/clients/html includes delete buttons with correct data."""
        with TestClient(app) as client:
            resp = client.post("/api/clients", json={
                "name": "HTML Test", "email": "html@test.com",
                "billing_address": "333 Html Ave", "default_hourly_rate": 100.00,
            })
            client_id = resp.json()["id"]

            html_resp = client.get("/api/clients/html")
            assert html_resp.status_code == 200
            assert "text/html" in html_resp.headers["content-type"]
            assert "HTML Test" in html_resp.text
            # Verify delete button is present
            assert f'hx-delete="/api/clients/{client_id}"' in html_resp.text
            assert "hx-confirm" in html_resp.text


class TestTimeLogDeletion:
    """Tests for DELETE /api/logs/{log_id}."""

    def test_delete_unbilled_log_success(self):
        """DELETE returns 200 and confirms deletion for an unbilled log."""
        with TestClient(app) as client:
            # Create client and log
            client_resp = client.post("/api/clients", json={
                "name": "Del Client", "email": "del@test.com",
                "billing_address": "123 Test St", "default_hourly_rate": 50.00,
            })
            client_id = client_resp.json()["id"]
            log_resp = client.post("/api/logs", json={
                "client_id": client_id, "hours": 2.0,
                "description": "Test log", "date": "2026-01-01",
            })
            log_id = log_resp.json()["id"]

            # Delete the log
            resp = client.delete(f"/api/logs/{log_id}")
            assert resp.status_code == 200
            assert resp.json()["message"] == "Time log deleted successfully"
            assert resp.json()["id"] == log_id

            # Verify it's gone from the unbilled list
            unbilled_resp = client.get("/api/logs/unbilled")
            assert str(resp.json()["id"]) not in unbilled_resp.text

    def test_delete_billed_log_rejected(self):
        """DELETE returns 409 for a log that is already billed."""
        with TestClient(app) as client:
            client_resp = client.post("/api/clients", json={
                "name": "Billed Client", "email": "billed@test.com",
                "billing_address": "123 Test St", "default_hourly_rate": 50.00,
            })
            client_id = client_resp.json()["id"]

            # Create a log, generate invoice (bills it)
            log_resp = client.post("/api/logs", json={
                "client_id": client_id, "hours": 2.0,
                "description": "Billed log", "date": "2026-01-01",
            })
            log_id = log_resp.json()["id"]
            client.post(f"/api/clients/{client_id}/invoices")

            # Now delete the billed log — should fail
            resp = client.delete(f"/api/logs/{log_id}")
            assert resp.status_code == 409
            assert "already billed" in resp.json()["detail"]

    def test_delete_missing_log_404(self):
        """DELETE returns 404 for a non-existent log."""
        with TestClient(app) as client:
            resp = client.delete("/api/logs/99999")
            assert resp.status_code == 404


class TestInvoiceListing:
    """Tests for GET /api/invoices."""

    def test_list_invoices_populated(self):
        """GET /api/invoices returns all invoices when they exist."""
        with TestClient(app) as client:
            client_resp = client.post("/api/clients", json={
                "name": "List Client", "email": "list@test.com",
                "billing_address": "123 Test St", "default_hourly_rate": 50.00,
            })
            client_id = client_resp.json()["id"]
            client.post("/api/logs", json={
                "client_id": client_id, "hours": 3.0,
                "description": "Test", "date": "2026-01-01",
            })
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")

            resp = client.get("/api/invoices")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) >= 1
            # The last invoice should match what we just created
            assert data[-1]["invoice_number"] == inv_resp.json()["invoice_number"]
            assert data[-1]["total_amount"] == 150.0

    def test_list_invoices_empty(self):
        """GET /api/invoices returns empty list when no invoices exist."""
        with TestClient(app) as client:
            resp = client.get("/api/invoices")
            assert resp.status_code == 200
            assert resp.json() == []


class TestInvoiceHistoryHTML:
    """Tests for GET /api/invoices/html."""

    def test_invoices_html_populated(self):
        """GET /api/invoices/html returns HTML table when invoices exist."""
        with TestClient(app) as client:
            client_resp = client.post("/api/clients", json={
                "name": "HTML Invoice Client", "email": "htmlinv@test.com",
                "billing_address": "123 Test St", "default_hourly_rate": 50.00,
            })
            client_id = client_resp.json()["id"]
            client.post("/api/logs", json={
                "client_id": client_id, "hours": 1.0,
                "description": "Test", "date": "2026-01-01",
            })
            inv_resp = client.post(f"/api/clients/{client_id}/invoices")

            resp = client.get("/api/invoices/html")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert inv_resp.json()["invoice_number"] in resp.text
            assert "Download PDF" in resp.text
            assert "Draft" in resp.text

    def test_invoices_html_empty(self):
        """GET /api/invoices/html returns empty message when no invoices."""
        with TestClient(app) as client:
            resp = client.get("/api/invoices/html")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "No invoices generated yet" in resp.text
