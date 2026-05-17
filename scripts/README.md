# Scripts

Utility scripts for the Lean Invoice Tracker. Each script is self-contained and runnable with `uv run`.

---

## smoke_test.py

A live integration test that verifies all core API endpoints are working correctly. It connects to a running server and exercises the full workflow: create client → log time → generate invoice → export PDF.

### Prerequisites

- The server must be running at `http://localhost:8000`
- `requests` must be installed (included in `requirements.txt`)

### Quick Start

**1. Start the server:**

```bash
cd lean-invoice-tracker
uv run uvicorn src.main:app --port 8000
```

Keep this terminal open (or run it in the background).

**2. Run the smoke test in a new terminal:**

```bash
cd lean-invoice-tracker
uv run python scripts/smoke_test.py
```

**Expected output:**

```
=== Smoke Test: Lean Invoice Tracker ===

✓ Dashboard loads
✓ Create client
✓ List clients
✓ Create time log
✓ Get unbilled logs
✓ Create invoice
✓ Get invoice details
✓ Export PDF

=== Results ===
Passed: 8
Failed: 0
```

### One-Liner (Server + Test)

```bash
uv run uvicorn src.main:app --port 8000 & sleep 2 && uv run python scripts/smoke_test.py
```

### What It Tests

| Test | Endpoint | Verifies |
|------|----------|----------|
| Dashboard loads | `GET /` | HTML response with content |
| Create client | `POST /api/clients` | Returns 200 with client ID |
| List clients | `GET /api/clients` | Returns 200 with non-empty list |
| Create time log | `POST /api/logs` | Returns 200 with log ID |
| Get unbilled logs | `GET /api/logs/unbilled` | Returns 200 with HTML table |
| Create invoice | `POST /api/clients/{id}/invoices` | Returns 200 with invoice ID |
| Get invoice details | `GET /api/invoices/{id}` | Returns 200 with invoice data |
| Export PDF | `GET /api/invoices/{id}/pdf` | Returns 200 with valid PDF |

### Configuration

Edit `smoke_test.py` to change:

- **`BASE_URL`** — Server URL (default: `http://localhost:8000`)
- **`TIMEOUT`** — Request timeout in seconds (default: `5`)

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All tests passed |
| `1` | One or more tests failed, or server not reachable |

---

## Upcoming Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| `smoke_test.py` | Live integration test | ✅ Done |
| `setup.sh` | One-command local setup (venv, deps, DB) | 📋 Planned |
| `db_backup.sh` | Automated SQLite backup (replaces `run_backup.sh`) | 📋 Planned |
| `dev.sh` | Start server with auto-reload and log viewer | 📋 Planned |
