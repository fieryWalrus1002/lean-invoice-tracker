# Handoff for Next Agent

## Project: Lean Invoice Tracker (LIT)

### Current State
- **Branch:** All work merged to `main`
- **Tests:** 94 pytest + 8 smoke tests — all passing
- **PRs:** 12 merged (#1–#12)
- **Status:** 🟢 Production-ready, feature-complete

### What's Done

| Area | Status |
|------|--------|
| Core implementation | ✅ All 5 modules complete |
| API endpoints | ✅ 11 endpoints working (including DELETE /api/clients/{id}) |
| UI | ✅ HTMX dashboard with dynamic dropdowns, live table updates, confirmation dialogs |
| PDF generation | ✅ Jinja2 template + WeasyPrint |
| Test suite | ✅ 94 pytest + 8 smoke tests |
| Production hardening | ✅ Validation, logging, atomic invoices |
| Documentation | ✅ README, DEPLOYMENT, scripts/README, PROJECT_STATUS |

### Remaining Work

#### Deployment Testing (Before Prod)
- [ ] Docker image builds and runs (`docker compose up --build -d`)
- [ ] Backup script tested in staging (`bash run_backup.sh`)
- [ ] Database backups working (verify `backups/db_dump.sql`)
- [ ] PDF generation verified with real data
- [ ] HTMX form submission tested in browser
- [ ] CSV upload tested with edge cases

#### Optional Enhancements (Phase 4)
1. **Invoice status workflow** — Draft → Sent → Paid states
2. **Client rate overrides** — Per-invoice hourly rate override
3. **Invoice line item editing** — UI to adjust hours/descriptions before billing
4. **Email notifications** — Send invoice PDF via email
5. **Performance test** — Concurrent invoice generation
6. **Security review** — Rate limiting, auth if exposed publicly

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

### Recent HTMX Fixes

- **Client table refresh:** Delete button now refreshes both the clients table and dropdowns (was only refreshing dropdowns)
- **Unbilled logs auto-refresh:** Submitting a time log now immediately updates the unbilled table (was requiring page reload)
- **Form date field fix:** Form field named `date` but endpoint parameter was `date_str`, causing all submissions to use today's date (was fixed in #13)

### Known Issues

- [#13] Form date field not submitted — Fixed (parameter renamed to `date`, HTML constraints added)
- [#14] CSV upload error handling — Silently skips malformed rows (should log warnings)
- [#15] Limited error messages — Generic HTTP errors, not user-friendly

### Dependencies

| Package | Purpose |
|---------|---------|
| FastAPI | Web framework |
| SQLModel | ORM |
| WeasyPrint | HTML-to-PDF |
| Jinja2 | Template engine |
| pytest | Tests |

### What to Do First

1. **Run `uv run pytest`** — verify all 94 tests pass
2. **Run `uv run python scripts/smoke_test.py`** — verify live integration
3. **Read `PROJECT_STATUS.md`** — full status and known issues
4. **Read `specs/project.md`** — system design specification

### Branch Naming Convention

`feature/<name>` for new work, `fix/<name>` for fixes. All previous branches are merged to main.
