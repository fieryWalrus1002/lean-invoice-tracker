# Test Suite Issues Tracker

**Last updated:** 2026-05-17  
**Current status:** 58 passed / 25 failed (70% pass rate)  
**Goal:** 100% of tests pass (green) before moving to bug fixes

---

## How to Use This Tracker

- ✅ = **PASSING** — no action needed
- 🔴 = **FAILING** — needs investigation and fix
- 🟡 = **KNOWN ISSUE** — acknowledged, fix in progress
- ⏳ = **BLOCKED** — waiting on something else
- 📝 = **LOW PRIORITY** — can be addressed later

---

## 1. PDF Content Extraction 🔴 (10 tests failing)

**Root cause:** ReportLab compresses PDF content streams using a **double encoding**: ASCII85Decode → FlateDecode. Our `_extract_pdf_text()` helper only tries raw `zlib.decompress()`, which fails on the ASCII85-encoded data, returning empty bytes.

**Affected tests:**

| # | Test | Status | Notes |
|---|------|--------|-------|
| 1 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_invoice_number` | 🔴 | Raw zlib can't decode ASCII85 |
| 2 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_client_name` | 🔴 | Same |
| 3 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_client_email` | 🔴 | Same |
| 4 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_billing_address` | 🔴 | Same |
| 5 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_date_info` | 🔴 | Same |
| 6 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_line_items` | 🔴 | Same |
| 7 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_total` | 🔴 | Same |
| 8 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_contains_status` | 🔴 | Same |
| 9 | `tests/test_pdf.py::TestPDFGeneration::test_pdf_with_decimal_amounts` | 🔴 | Same |
| 10 | `tests/test_pdf.py::TestPDFWithLargeData::test_pdf_many_line_items` | 🔴 | Same |

**Working PDF tests (baseline confirms PDF generation works):**

| # | Test | Status |
|---|------|--------|
| ✅ | `test_pdf_returns_bytes` | ✅ |
| ✅ | `test_pdf_valid_header` | ✅ |
| ✅ | `test_pdf_not_empty` | ✅ |
| ✅ | `test_reportlab_missing_raises_runtime_error` | ✅ |

**Fix approach:** Replace `zlib.decompress()` with ReportLab's own decoding pipeline:
```python
from reportlab.lib.rl_accel import ascii85decode
decoded = ascii85decode(stream_data)
decompressed = zlib.decompress(decoded)
```

---

## 2. API Integration Tests — Session State Loss 🔴 (2 tests failing)

**Root cause:** The `autouse=True` `_patch_session` fixture in `test_main.py` creates a **new session for every request**. Integration tests that make multiple sequential requests (create client → log hours → generate invoice → check unbilled) lose state between requests because each `TestClient` call gets a fresh database session.

**Affected tests:**

| # | Test | Status | Root cause |
|---|------|--------|------------|
| 11 | `tests/test_main.py::TestIntegrationWorkflows::test_create_client_log_invoice_pdf` | 🔴 | Session reset between requests; unbilled table still shows all logs after invoice generation |
| 12 | `tests/test_main.py::TestIntegrationWorkflows::test_csv_upload_then_invoice` | 🔴 | Same — state not persisted across requests |

**Evidence from failure output:** After `POST /api/clients/{id}/invoices` succeeds, `GET /api/logs/unbilled` still returns 50+ rows (all logs from all tests), meaning the invoice generation committed against a different session than the one the unbilled query sees.

**Fix approach:** Change the fixture to use a **single shared session** for all requests within a test:
```python
@pytest.fixture
def client():
    """Single session shared across all requests in a test."""
    # ... create one engine, one session ...
    app.dependency_overrides[original_get_session] = lambda: iter([session])
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
```

---

## 3. API PDF Content Assertions (raw bytes search) 🔴 (3 tests failing)

**Root cause:** Same as #1 — these tests search for text in raw `response.content` bytes. PDF content is compressed, so searching for `b"Test Corp"` or `b"500.00"` in the raw stream fails.

**Affected tests:**

| # | Test | Status |
|---|------|--------|
| 13 | `tests/test_main.py::TestPDFExport::test_pdf_content_includes_invoice_number` | 🔴 |
| 14 | `tests/test_main.py::TestPDFExport::test_pdf_content_includes_client_name` | 🔴 |
| 15 | `tests/test_main.py::TestPDFExport::test_pdf_content_includes_total` | 🔴 |

**Fix approach:** Either:
- A) Decompress the PDF stream before searching (same fix as #1), or
- B) Use `PyPDF2` or `pdfminer` to extract text from the PDF properly, or
- C) For the API tests, verify the PDF by checking the HTTP response structure (content-type, filename, non-empty body) and skip content assertions in integration tests.

---

## 4. Database WAL Mode Test 🟡 (1 test failing)

**Affected test:**

| # | Test | Status | Notes |
|---|------|--------|-------|
| 16 | `tests/test_database.py::TestWALMode::test_wal_mode_on_fresh_engine` | 🔴 | In-memory SQLite doesn't support WAL mode properly. The `PRAGMA journal_mode` returns `memory` instead of `wal` because WAL requires a file. |

**Fix approach:** Either:
- A) Skip this test for in-memory databases (test only against file-based SQLite), or
- B) Use `sqlite3` file-based temp database for this specific test, or
- C) Accept that WAL mode is a file-based feature and test it against a temp file, not `:memory:`.

---

## 5. Model Constraint Tests — SQLite FK Enforcement 🟡 (2 tests failing)

**Root cause:** SQLite has foreign key constraints **disabled by default** at the session level. You must run `PRAGMA foreign_keys = ON` for each connection. Our test sessions don't enable this, so invalid FK references don't raise `IntegrityError`.

**Affected tests:**

| # | Test | Status | Notes |
|---|------|--------|-------|
| 17 | `tests/test_models.py::TestModelConstraints::test_timelog_requires_client_id` | 🔴 | FK constraint not enforced |
| 18 | `tests/test_models.py::TestModelConstraints::test_invoice_requires_client_id` | 🔴 | FK constraint not enforced |

**Fix approach:** Enable FK constraints in the test engine:
```python
@event.listens_for(engine, "connect")
def set_fk(dbapi_conn, _):
    dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON;")
```

---

## 6. Already Fixed / Noted

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 19 | `src/utils/pdf.py` padding tuple → int | ✅ FIXED | Changed `("TOPPADDING", ..., (5,))` to `(5)` in source |
| 20 | `test_services.py` session type mismatch | ✅ FIXED | Switched from `Session` to `SQLModelSession` in conftest |
| 21 | `test_main.py` `src_main` not imported | ✅ FIXED | Restructured fixtures to use `TestClient` directly |
| 22 | `test_database.py` wrong event attribute | ✅ FIXED | Use `event.listens_for()` from `sqlalchemy` directly |

---

## Summary

| Category | Count | Priority |
|----------|-------|----------|
| PDF content extraction (decompression) | 10 | 🔴 High — blocks PDF validation |
| API integration session state | 2 | 🔴 High — blocks E2E workflows |
| API PDF raw byte assertions | 3 | 🟡 Medium — can be reworked |
| Database WAL mode (in-memory) | 1 | 🟡 Low — file-based feature |
| SQLite FK enforcement | 2 | 🟡 Medium — should be enabled |
| Already fixed | 4 | ✅ Done |

---

## Recommended Fix Order

1. **Fix PDF content extraction** (#1) — this is the biggest block. Fix the `_extract_pdf_text()` helper to use ReportLab's `ascii85decode` + `zlib.decompress`.
2. **Fix API integration session state** (#2) — restructure the `client` fixture to share a session.
3. **Fix API PDF assertions** (#3) — either decompress or rework to not check content in integration tests.
4. **Enable SQLite FK constraints** (#5) — add `PRAGMA foreign_keys=ON` to test engine setup.
5. **Fix WAL in-memory test** (#4) — use a temp file for this specific test.

Once these are done: **25 → 0 failures**.

---

## Post-Test Suite: Known Bugs to Fix (from PROJECT_STATUS.md)

These are **code bugs** found during manual testing, not test failures. Address these after the test suite is green:

| # | Issue | Priority | File |
|---|-------|----------|------|
| 1 | Invoice race condition — year-scoped count isn't atomic | 🔴 High | `src/services.py` |
| 2 | Form client_id dropdown hardcoded, needs HTMX dynamic loading | 🟡 Medium | `src/templates/dashboard.html` |
| 3 | CSV upload silently skips malformed rows (no logging) | 🟡 Medium | `src/main.py` |
| 4 | No input validation (negative hours, future dates) | 🟡 Medium | `src/services.py` |
| 5 | No structured logging in any module | 🟢 Low | all |
| 6 | README.md is empty | 🟢 Low | `README.md` |
| 7 | Dashboard styling is minimal | 🟢 Low | `src/templates/dashboard.html` |
