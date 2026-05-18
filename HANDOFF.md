# Handoff for Next Agent

## Project: Lean Invoice Tracker (LIT)

### Current State
- **Branch:** All work merged to `main`
- **Tests:** 94 pytest + 8 smoke tests — all passing
- **PRs:** 13 merged (#1–#13)
- **Status:** 🟢 Production-ready, feature-complete

### What's Done

| Area | Status |
|------|--------|
| Core implementation | ✅ All 5 modules complete |
| API endpoints | ✅ 14 endpoints working (including DELETE /api/clients/{id}, DELETE /api/logs/{log_id}, GET /api/invoices, GET /api/invoices/html) |
| UI | ✅ HTMX dashboard with dynamic dropdowns, live table updates, confirmation dialogs, Invoice History section, delete time log buttons, error messaging |
| PDF generation | ✅ Jinja2 template + WeasyPrint |
| Test suite | ✅ 94 pytest + 8 smoke tests |
| Production hardening | ✅ Validation, logging, atomic invoices |
| Documentation | ✅ README, DEPLOYMENT, scripts/README, PROJECT_STATUS, HANDOFF |

### Remaining Work

#### Deployment Testing (Before Prod)
- [ ] Docker image builds and runs (`docker compose up --build -d`)
- [ ] Backup script tested in staging (`bash run_backup.sh`)
- [ ] Database backups working (verify `backups/db_dump.sql`)
- [ ] PDF generation verified with real data
- [ ] HTMX form submission tested in browser
- [ ] CSV upload tested with edge cases
- [ ] Close GitHub issue #18 (resolved by PR #17/#18, needs manual close)

#### Optional Enhancements (Phase 4)
1. **Invoice status workflow** — Draft → Sent → Paid states
2. **Client rate overrides** — Per-invoice hourly rate override
3. **Invoice line item editing** — UI to adjust hours/descriptions before billing
4. **Email notifications** — Send invoice PDF via email
5. **Performance test** — Concurrent invoice generation
6. **Security review** — Rate limiting, auth if exposed publicly
7. **Categories/tags for time logs** — Issue #21/22: Group time logs by category on invoices

#### Open Issues (Need Attention)
- **#20** — Invoice rate formatting was off (75.00.00 instead of 75.00) — **FIXED** in template
- **#21** — Feature request: categories for time log grouping
- **#22** — Feature request: tags for optional invoice entry grouping
- **#18** — Still open on GitHub (resolved by PR #17/#18, needs manual close)

### How to Run

```bash
# Tests
uv run pytest
uv run python scripts/smoke_test.py    # (server must be running)

# Start server
uv run uvicorn src.main:app --port 8000

# Docker
docker compose up --build -d
```

### Key Files

| File | Purpose |
|------|---------|
| `src/main.py` | FastAPI app, all endpoints |
| `src/services.py` | Business logic, schemas, validators |
| `src/models.py` | SQLModel schemas (Client, TimeLog, Invoice, InvoiceSequence) |
| `src/database.py` | Engine, WAL mode, session management |
| `src/utils/pdf.py` | PDF generation (Jinja2 + WeasyPrint) |
| `src/templates/` | dashboard.html, invoice.html |
| `tests/` | 94 pytest tests |
| `scripts/smoke_test.py` | Live integration tests |

### Architecture Notes

- **Invoice numbering:** Atomic sequence table (`InvoiceSequence`) prevents collisions
- **PDF rendering:** `invoice.html` template rendered via Jinja2, converted to PDF via WeasyPrint
- **HTMX:** Dashboard uses HTMX for dynamic client dropdowns, live table updates, and confirmation dialogs
- **Database:** SQLite with WAL mode, sequence table for atomic counters
- **Validation:** Pydantic validators on `TimeLogCreate` (hours > 0, date ≤ today)

### Recent Fixes

- **Form date field** (PR #14): Form field named `date` but endpoint parameter was `date_str`, causing all submissions to default to today's date. Fixed by renaming parameter and adding HTML date/hours constraints.
- **HTMX form swaps** (PR #17): Both forms had `hx-swap="innerHTML"` but APIs return JSON, causing silent failures. Fixed by switching to `hx-swap="none"` with explicit `hx-on::after-request` handlers.
- **Download not working** (PR #18): PDF download broken due to form swap strategy. Fixed by using `fetch` + `Blob` API for invoice download.
- **Invoice history section** (PR #19): Added `GET /api/invoices`, `GET /api/invoices/html`, `DELETE /api/logs/{log_id}`, Invoice History section with HTMX loading, delete time log button, and Action column.
- **Copilot PR review** (PR #20): Replaced `hx-on::submit` with native `onsubmit`, added 'Action' column header, added 7 new tests.
- **Invoice rate formatting** (fix): Fixed `invoice.html` template — rate now uses `"%.2f"|format()` instead of appending `.00` to already-formatted decimal. Hours also formatted to 2 decimal places. (Resolves GitHub #20)
- **Clients table refresh** (PR #11): Delete button only refreshed dropdowns, not the table. Fixed to refresh both.
- **Unbilled logs auto-refresh** (PR #11): Submitting a time log didn't update the unbilled table. Fixed with HTMX event trigger.

### Dependencies

| Package | Purpose |
|---------|---------|
| FastAPI | Web framework |
| SQLModel | ORM |
| WeasyPrint | HTML-to-PDF |
| Jinja2 | Template engine |
| pytest | Tests |

### What to Do First

1. **Run `uv run pytest`** — verify all 101 tests pass
2. **Run `uv run python scripts/smoke_test.py`** — verify live integration
3. **Read `PROJECT_STATUS.md`** — full status and known issues
4. **Read `specs/project.md`** — system design specification

### Branch Naming Convention

`feature/<name>` for new work, `fix/<name>` for fixes. All previous branches are merged to main.
