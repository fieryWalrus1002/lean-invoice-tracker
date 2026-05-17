#!/usr/bin/env python3
"""Smoke test script for Lean Invoice Tracker.

Tests core API endpoints and workflows to verify the application is functioning correctly.
Run this after starting the server: uv run python scripts/smoke_test.py
"""

import sys
import requests
from datetime import date


BASE_URL = "http://localhost:8000"
TIMEOUT = 5


class SmokeTest:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.client_id = None
        self.log_id = None
        self.invoice_id = None

    def test(self, name: str, fn):
        """Run a test and track results."""
        try:
            fn()
            print(f"✓ {name}")
            self.passed += 1
        except Exception as e:
            print(f"✗ {name}: {e}")
            self.failed += 1

    def test_dashboard(self):
        """Test dashboard endpoint."""
        r = requests.get(f"{BASE_URL}/", timeout=TIMEOUT)
        assert r.status_code == 200, f"Got {r.status_code}"
        assert "html" in r.text.lower(), "Response doesn't contain HTML"

    def test_create_client(self):
        """Test client creation (deduplicated — reuses existing if present)."""
        payload = {
            "name": "Test Client",
            "email": "test@example.com",
            "billing_address": "123 Test St",
            "default_hourly_rate": 150.0,
        }
        r = requests.post(f"{BASE_URL}/api/clients", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200, f"Got {r.status_code}"
        data = r.json()
        assert data["id"], "No ID in response"
        self.client_id = data["id"]
        # Verify no duplicate was created
        r2 = requests.get(f"{BASE_URL}/api/clients", timeout=TIMEOUT)
        clients = r2.json()
        test_clients = [c for c in clients if c["name"] == "Test Client"]
        assert len(test_clients) == 1, f"Expected 1 'Test Client', found {len(test_clients)}"

    def test_list_clients(self):
        """Test listing clients."""
        r = requests.get(f"{BASE_URL}/api/clients", timeout=TIMEOUT)
        assert r.status_code == 200, f"Got {r.status_code}"
        data = r.json()
        assert isinstance(data, list), "Response is not a list"
        assert len(data) > 0, "No clients in list"

    def test_create_time_log(self):
        """Test time log creation."""
        assert self.client_id, "Client ID not set"
        payload = {
            "client_id": self.client_id,
            "hours": 5.5,
            "description": "Development work",
            "date": str(date.today()),
        }
        r = requests.post(f"{BASE_URL}/api/logs", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200, f"Got {r.status_code}"
        data = r.json()
        assert data["id"], "No ID in response"
        self.log_id = data["id"]

    def test_unbilled_logs(self):
        """Test retrieving unbilled logs."""
        assert self.client_id, "Client ID not set"
        r = requests.get(
            f"{BASE_URL}/api/logs/unbilled",
            params={"client_id": self.client_id},
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, f"Got {r.status_code}"
        assert "table" in r.text.lower() or "unbilled" in r.text.lower()

    def test_create_invoice(self):
        """Test invoice creation."""
        assert self.client_id, "Client ID not set"
        r = requests.post(
            f"{BASE_URL}/api/clients/{self.client_id}/invoices",
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, f"Got {r.status_code}"
        data = r.json()
        assert data["id"], "No ID in response"
        self.invoice_id = data["id"]

    def test_get_invoice(self):
        """Test retrieving invoice details."""
        assert self.invoice_id, "Invoice ID not set"
        r = requests.get(
            f"{BASE_URL}/api/invoices/{self.invoice_id}",
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, f"Got {r.status_code}"
        data = r.json()
        assert data["id"], "No ID in response"

    def test_export_pdf(self):
        """Test PDF export."""
        assert self.invoice_id, "Invoice ID not set"
        r = requests.get(
            f"{BASE_URL}/api/invoices/{self.invoice_id}/pdf",
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, f"Got {r.status_code}"
        assert r.headers.get("content-type") == "application/pdf"
        assert len(r.content) > 100, f"PDF too small ({len(r.content)} bytes)"

    def run(self):
        """Run all smoke tests."""
        print("=== Smoke Test: Lean Invoice Tracker ===\n")

        self.test("Dashboard loads", self.test_dashboard)
        self.test("Create client", self.test_create_client)
        self.test("List clients", self.test_list_clients)
        self.test("Create time log", self.test_create_time_log)
        self.test("Get unbilled logs", self.test_unbilled_logs)
        self.test("Create invoice", self.test_create_invoice)
        self.test("Get invoice details", self.test_get_invoice)
        self.test("Export PDF", self.test_export_pdf)

        print(f"\n=== Results ===")
        print(f"Passed: {self.passed}")
        print(f"Failed: {self.failed}")
        print()

        return self.failed == 0


if __name__ == "__main__":
    try:
        tester = SmokeTest()
        success = tester.run()
        sys.exit(0 if success else 1)
    except requests.exceptions.ConnectionError:
        print("✗ Could not connect to server at http://localhost:8000")
        print("  Make sure the server is running:")
        print("  python -m uvicorn src.main:app --port 8000")
        sys.exit(1)
