# Test Plan: Lean Invoice Tracker (LIT)

This document outlines the testing strategy for all modules in the LIT system, covering unit tests, integration tests, and end-to-end workflows.

---

## Executive Summary

The LIT application consists of 5 core modules with interdependent functionality. Testing must cover:
1. **Data Layer** (`models.py`, `database.py`) — Schema validation, relationships, WAL mode
2. **Business Logic** (`services.py`) — Invoice generation, log queries, transaction atomicity
3. **API Layer** (`main.py`) — Route handlers, payload parsing, HTTP responses
4. **PDF Generation** (`utils/pdf.py`) — Document compilation, data rendering
5. **UI Layer** (`templates/`) — HTML rendering, form submission, HTMX interactions

---

## Module 1: Data Models & Database Layer

### Files: `src/models.py`, `src/database.py`

#### 1.1 Database Schema Tests

**Test: SQLite WAL Mode Initialization**
- Verify `PRAGMA journal_mode=WAL` is enabled on engine connect
- Confirm WAL files (`.db-wal`, `.db-shm`) are created after first write
- Purpose: Ensures safe concurrent access and backup operations

```python
# Unit Test
def test_wal_mode_enabled(session):
    """Verify SQLite WAL mode is active."""
    result = session.execute("PRAGMA journal_mode").scalar()
    assert result == "wal"
```

**Test: Table Creation on Startup**
- Verify `create_db_and_tables()` creates all three tables
- Check table schemas match `Client`, `TimeLog`, `Invoice` models
- Confirm indexes are created for `name`, `date`, `is_billed`, `invoice_number`

```python
# Unit Test
def test_all_tables_created(session):
    """Verify all tables exist with correct schemas."""
    inspector = inspect(session.get_bind())
    tables = inspector.get_table_names()
    assert "client" in tables
    assert "timelog" in tables
    assert "invoice" in tables
```

#### 1.2 Model Relationship Tests

**Test: Client ↔ TimeLog Relationship**
- Create a client, add time logs, verify bidirectional relationship
- Delete client, confirm cascade behavior (if configured)

**Test: Client ↔ Invoice Relationship**
- Create client, generate invoice, verify relationship integrity

**Test: Invoice ↔ TimeLog Relationship**
- Link time logs to invoice, verify `invoice_id` foreign key constraint
- Attempt orphaned log (no invoice_id), confirm it's allowed

#### 1.3 Constraint & Validation Tests

**Test: Invoice Number Uniqueness**
- Create two invoices with same `invoice_number`, expect `IntegrityError`
- Verify uniqueness constraint is at database level

**Test: Decimal Precision**
- Store `hours: 4.25`, `default_hourly_rate: 150.50`, retrieve and verify exact precision
- Test Decimal serialization in response models

**Test: Non-Nullable Foreign Keys**
- Attempt to create `TimeLog` without `client_id`, expect constraint error
- Verify `client_id` is required

---

## Module 2: Business Logic Layer

### Files: `src/services.py`

#### 2.1 Invoice Generation Tests

**Test: Happy Path — Generate Invoice**
- Precondition: Client with `default_hourly_rate: 100.00`, 5 unbilled logs (10, 5, 3, 2, 5 hours)
- Action: Call `generate_invoice_transaction(session, client_id)`
- Assertions:
  - Invoice created with correct `invoice_number` format: `INV-2026-XXXX`
  - `total_amount == 25 * 100 = 2500.00`
  - All 5 logs marked `is_billed = True`
  - All 5 logs linked to invoice via `invoice_id`
  - Invoice `status == "Draft"`
  - Invoice `issue_date == today()`
  - Invoice `due_date == today() + 30 days`

```python
# Integration Test
def test_generate_invoice_happy_path(session, client_fixture):
    """Test successful invoice generation from unbilled logs."""
    # Setup
    logs = [TimeLog(...) for _ in range(5)]
    session.add_all(logs)
    session.commit()
    
    # Action
    invoice = generate_invoice_transaction(session, client_fixture.id)
    
    # Assertions
    assert invoice.invoice_number.startswith("INV-2026-")
    assert invoice.total_amount == Decimal("2500.00")
    for log in logs:
        assert log.is_billed == True
        assert log.invoice_id == invoice.id
```

**Test: Invoice Number Race Condition**
- Simulate two concurrent requests calling `generate_invoice_transaction` simultaneously
- Expected: Both succeed with unique invoice numbers (e.g., `INV-2026-0001`, `INV-2026-0002`)
- Implementation: Use threading or async to stress-test atomicity

```python
# Concurrency Test
def test_invoice_number_no_duplicates(session, client_fixture):
    """Verify concurrent invoice generation produces unique numbers."""
    import threading
    invoices = []
    
    def create_inv():
        invoices.append(generate_invoice_transaction(session, client_fixture.id))
    
    threads = [threading.Thread(target=create_inv) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    invoice_nums = [inv.invoice_number for inv in invoices]
    assert len(set(invoice_nums)) == len(invoice_nums), "Duplicate invoice numbers detected!"
```

**Test: No Unbilled Logs**
- Precondition: Client exists with zero unbilled logs
- Action: Call `generate_invoice_transaction(session, client_id)`
- Expected: HTTPException with status 400 and message "No unbilled hours found."

```python
# Unit Test
def test_generate_invoice_no_unbilled_logs(session, client_fixture):
    """Should raise 400 when no unbilled logs exist."""
    with pytest.raises(HTTPException) as exc_info:
        generate_invoice_transaction(session, client_fixture.id)
    assert exc_info.value.status_code == 400
```

**Test: Client Not Found**
- Precondition: No client with ID 999
- Action: Call `generate_invoice_transaction(session, 999)`
- Expected: HTTPException with status 404

**Test: Decimal Precision in Totals**
- Setup logs with fractional hours (3.25, 2.75)
- Use hourly rate with cents (99.99)
- Verify total_amount preserves full precision

#### 2.2 Unbilled Logs Query Tests

**Test: Fetch All Unbilled Logs**
- Precondition: 3 unbilled, 2 billed logs across 2 clients
- Action: `get_unbilled_logs(session, client_id=None)`
- Expected: Returns all 3 unbilled logs

**Test: Fetch Unbilled Logs by Client**
- Precondition: 2 unbilled for client 1, 3 unbilled for client 2
- Action: `get_unbilled_logs(session, client_id=1)`
- Expected: Returns only 2 logs for client 1

**Test: Empty Result Set**
- Precondition: No unbilled logs
- Action: `get_unbilled_logs(session)`
- Expected: Returns empty list (not error)

#### 2.3 Pydantic Schema Validation Tests

**Test: TimeLogCreate Date Parsing**
- Input: `{"client_id": 1, "hours": 5.0, "description": "...", "date": "2026-05-15"}`
- Expected: Date string parsed to `date(2026, 5, 15)`

**Test: TimeLogCreate Optional Date**
- Input: `{"client_id": 1, "hours": 5.0, "description": "..."}`  (no date)
- Expected: Schema accepts it, date set to None (API endpoint handles default)

**Test: TimeLogCreate Invalid Hours**
- Input: `{"client_id": 1, "hours": -5.0, "description": "..."}`
- Expected: Validation passes (no range check at schema level) — May be app business logic decision

---

## Module 3: API Layer

### Files: `src/main.py`

#### 3.1 Dashboard Route

**Test: GET /  Returns HTML**
- Request: `GET /`
- Expected: HTTP 200, `Content-Type: text/html`, dashboard.html content

#### 3.2 Client Routes

**Test: POST /api/clients — Create Client**
- Request: `POST /api/clients` with JSON `{"name": "ACME Corp", "email": "...", "billing_address": "...", "default_hourly_rate": 100.0}`
- Expected: HTTP 201, response contains `id`, all input fields echoed back

**Test: GET /api/clients — List Clients**
- Precondition: 3 clients in database
- Request: `GET /api/clients`
- Expected: HTTP 200, JSON array with 3 client objects

**Test: GET /api/clients — Empty List**
- Precondition: No clients
- Request: `GET /api/clients`
- Expected: HTTP 200, empty JSON array `[]`

#### 3.3 Time Logs Routes

**Test: POST /api/logs — Form Data (HTMX)**
- Request: `POST /api/logs` with `Content-Type: application/x-www-form-urlencoded`
  - Body: `client_id=1&hours=5.0&description=Testing&date=2026-05-17`
- Expected: HTTP 200, TimeLogResponse JSON with created log

**Test: POST /api/logs — JSON Body (CLI)**
- Request: `POST /api/logs` with `Content-Type: application/json`
  - Body: `{"client_id": 1, "hours": 5.0, "description": "Testing", "date": "2026-05-17"}`
- Expected: HTTP 200, TimeLogResponse JSON

**Test: POST /api/logs — Default Date**
- Request: `POST /api/logs` with form data, no `date` field
- Expected: Log created with `date = today()`

**Test: POST /api/logs — Missing client_id**
- Request: `POST /api/logs` with form data, missing `client_id`
- Expected: HTTP 422 (Unprocessable Entity)

**Test: POST /api/logs/upload-csv**
- Request: `POST /api/logs/upload-csv` with CSV file
  - Format: `client_id,date,hours,description` (header row)
  - 3 data rows
- Expected: HTTP 200, JSON response `{"message": "Imported 3 time logs."}`
- Verify: All 3 logs created in database

**Test: POST /api/logs/upload-csv — Invalid CSV**
- Request: CSV with only 2 columns per row
- Expected: Rows with < 4 columns skipped, remaining imported

**Test: GET /api/logs/unbilled**
- Precondition: 3 unbilled logs
- Request: `GET /api/logs/unbilled`
- Expected: HTTP 200, HTML table with 3 rows, each row has date, hours, description, status badge

**Test: GET /api/logs/unbilled — No Logs**
- Precondition: No unbilled logs
- Request: `GET /api/logs/unbilled`
- Expected: HTTP 200, HTML with message "No unbilled time logs found."

**Test: GET /api/logs/unbilled?client_id=1**
- Precondition: 5 total unbilled, 3 for client 1
- Request: `GET /api/logs/unbilled?client_id=1`
- Expected: HTTP 200, HTML table with 3 rows filtered to client 1

#### 3.4 Invoice Routes

**Test: POST /api/clients/{client_id}/invoices**
- Precondition: Client exists with 5 unbilled logs
- Request: `POST /api/clients/1/invoices`
- Expected: HTTP 200, InvoiceResponse JSON with generated invoice

**Test: POST /api/clients/{client_id}/invoices — No Unbilled Logs**
- Precondition: Client exists with 0 unbilled logs
- Request: `POST /api/clients/1/invoices`
- Expected: HTTP 400, error detail "No unbilled hours found."

**Test: POST /api/clients/{client_id}/invoices — Client Not Found**
- Request: `POST /api/clients/999/invoices`
- Expected: HTTP 404, error detail "Client not found."

**Test: GET /api/invoices/{invoice_id}**
- Precondition: Invoice exists
- Request: `GET /api/invoices/1`
- Expected: HTTP 200, InvoiceResponse JSON

**Test: GET /api/invoices/{invoice_id} — Not Found**
- Request: `GET /api/invoices/999`
- Expected: HTTP 404, error detail "Invoice not found."

#### 3.5 PDF Export Route

**Test: GET /api/invoices/{invoice_id}/pdf**
- Precondition: Invoice with related client and time logs
- Request: `GET /api/invoices/1/pdf`
- Expected:
  - HTTP 200
  - `Content-Type: application/pdf`
  - `Content-Disposition: attachment; filename="INV-2026-0001.pdf"`
  - Response body is valid PDF (not empty, starts with `%PDF`)

**Test: GET /api/invoices/{invoice_id}/pdf — Not Found**
- Request: `GET /api/invoices/999/pdf`
- Expected: HTTP 404

**Test: PDF Content Validation**
- Precondition: Invoice with specific client, logs, and amounts
- Action: Generate PDF, extract text or validate structure
- Expected: PDF contains invoice number, client name, billing address, date, due date, line items, total

---

## Module 4: PDF Generation Utility

### Files: `src/utils/pdf.py`

#### 4.1 PDF Compilation Tests

**Test: PDF Generation — Happy Path**
- Input: Invoice with client, 3 time logs, known amounts
- Action: Call `compile_invoice_pdf(invoice, session)`
- Expected:
  - Returns bytes (not None)
  - Bytes length > 500 (not a stub)
  - Bytes start with `%PDF-` (valid PDF header)

```python
# Unit Test
def test_compile_invoice_pdf_happy_path(session, invoice_fixture):
    """Test PDF generation produces valid bytes."""
    pdf_bytes = compile_invoice_pdf(invoice_fixture, session)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")
```

**Test: ReportLab Import Handling**
- Verify graceful error if `reportlab` not installed
- Expected: `RuntimeError` with message about missing dependency

**Test: PDF with Large Data**
- Input: Invoice with 100 line items (time logs)
- Expected: PDF generated successfully (multi-page if needed)

**Test: PDF with Decimal Amounts**
- Input: Invoice with `total_amount: Decimal("1234.56")`
- Expected: PDF displays as `1234.56` (not scientific notation)

---

## Module 5: UI Templates

### Files: `src/templates/dashboard.html`, `src/templates/invoice.html`

#### 5.1 Dashboard Template Tests

**Test: Dashboard Loads Without Errors**
- Request: `GET /`
- Expected: HTTP 200, valid HTML, no JS console errors

**Test: Client Dropdown Populated**
- Precondition: 3 clients in database
- Request: `GET /` (render dashboard)
- Expected: `<select name="client_id">` contains 3 `<option>` elements

**Test: Form Submission via HTMX**
- Action: Fill form (client, date, hours, description), click submit
- Expected:
  - HTMX POST to `/api/logs` with form data
  - Unbilled table updates with new log
  - Form fields reset

**Test: Unbilled Table Loads on Page Load**
- Precondition: 2 unbilled logs in database
- Request: `GET /`
- Expected: HTMX triggers `GET /api/logs/unbilled`, table renders with 2 rows

#### 5.2 Invoice Template Tests

**Test: Invoice HTML Renders**
- Precondition: Invoice exists
- Request: `GET /api/invoices/{invoice_id}` (if HTML endpoint exists)
- Expected: HTML contains invoice number, client name, line items, total

---

## Integration Tests (End-to-End)

### Workflow 1: Create Client → Log Hours → Generate Invoice

1. **Setup**
   - DELETE all clients, logs, invoices (clean slate)

2. **Steps**
   - POST `/api/clients` with client data → Assert 200, get client_id
   - POST `/api/logs` (3x) with form data for client → Assert 200 each
   - GET `/api/logs/unbilled` → Assert HTML table shows 3 rows
   - POST `/api/clients/{client_id}/invoices` → Assert 200, get invoice_id
   - GET `/api/invoices/{invoice_id}` → Assert invoice.total_amount matches sum of logs
   - Verify all logs now have `is_billed = True`

3. **Assertions**
   - Invoice total = sum of (hours × hourly_rate) for all logs
   - Invoice number is unique
   - All logs linked to invoice

### Workflow 2: CSV Upload → Generate Invoice

1. **Steps**
   - POST `/api/logs/upload-csv` with 10-row CSV → Assert 200
   - GET `/api/logs/unbilled` → Assert 10 rows
   - POST invoice generation
   - Verify invoice total matches

### Workflow 3: Concurrent Invoice Generation

1. **Setup**
   - Client with 100 unbilled logs
   - Divide into 5 groups of 20

2. **Action**
   - Spawn 5 threads, each calling invoice generation endpoint

3. **Expected**
   - All 5 succeed
   - Each generates unique invoice_number
   - No race condition errors

### Workflow 4: PDF Export Full Flow

1. **Setup**
   - Client with hourly_rate = 150.00
   - 3 logs: 5 hours, 3.5 hours, 2.25 hours
   - Total should be 1618.75

2. **Steps**
   - Generate invoice
   - GET `/api/invoices/{id}/pdf`
   - Validate PDF contains correct data

---

## Testing Tools & Setup

### Required Dependencies

```bash
pip install pytest pytest-asyncio httpx sqlalchemy-utils
```

### Test Database Configuration

```python
# conftest.py
@pytest.fixture
def test_db():
    """Provide test SQLite database."""
    database_url = "sqlite:///:memory:"
    engine = create_engine(database_url)
    SQLModel.metadata.create_all(engine)
    yield SessionLocal(bind=engine)
```

### Running Tests

```bash
# All tests
pytest

# Specific module
pytest tests/test_services.py

# With coverage
pytest --cov=src

# Verbose output
pytest -v
```

---

## Test Coverage Goals

| Module | Target Coverage | Priority |
|--------|-----------------|----------|
| `models.py` | 95% | High |
| `database.py` | 85% | High |
| `services.py` | 90% | High |
| `main.py` | 85% | High |
| `utils/pdf.py` | 80% | Medium |
| `templates/` | Manual | Medium |

---

## Known Gaps & TODOs

- [ ] Error recovery tests (DB connection loss, disk full)
- [ ] Backup script validation (`run_backup.sh`)
- [ ] Docker container tests
- [ ] Load testing (1000+ concurrent requests)
- [ ] Security tests (SQL injection, XSS in PDF)
- [ ] Invoice template HTML tests
- [ ] Client dropdown dynamic loading via HTMX
- [ ] Form validation (negative hours, missing fields)

---

## Execution Order

1. **Phase 1 (Critical):** Data layer + business logic unit tests
2. **Phase 2 (Essential):** API route tests + integration tests
3. **Phase 3 (Important):** PDF utility + UI template tests
4. **Phase 4 (Polish):** Concurrency tests, edge cases, documentation

---

*Last Updated: 2026-05-17*
