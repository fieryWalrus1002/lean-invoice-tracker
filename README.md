# Lean Invoice Tracker (LIT)

A self-hosted, lightweight invoice tracker for freelancers and consultants.
Log hours, generate invoices, and export PDFs — all from a single SQLite-backed
FastAPI application.

---

## Features

- **Time tracking** — Log hours per client via web form or CLI
- **CSV bulk import** — Upload CSV files with client\_id, date, hours, description
- **Invoice generation** — Aggregate unbilled logs into numbered invoices
- **PDF export** — Download professional invoices as PDF (Jinja2 template + WeasyPrint)
- **HTMX-powered UI** — Fast, reactive dashboard without JavaScript frameworks
- **SQLite + WAL** — Safe concurrent access and clean backups
- **Docker-ready** — One-command deployment with docker-compose

---

## Quick Start

### 1. Install dependencies

```bash
cd lean-invoice-tracker
uv sync
```

### 2. Run the server

```bash
uv run uvicorn src.main:app --reload
```

The dashboard is now available at [http://localhost:8000](http://localhost:8000).

### 3. (Optional) Docker

```bash
docker compose up --build -d
```

The app will be available at [http://localhost:8085](http://localhost:8085).

---

## CLI Integration

Add this to your `~/.bashrc` (or `~/.zshrc`) for quick time logging from the terminal:

```bash
log_hours() {
    local CLIENT_ID=$1
    local HOURS=$2
    local DESCRIPTION=$3
    local LOG_DATE=${4:-$(date +%Y-%m-%d)}

    if [ -z "$CLIENT_ID" ] || [ -z "$HOURS" ] || [ -z "$DESCRIPTION" ]; then
        echo -e "\033[31mError: Missing arguments.\033[0m"
        echo "Usage: log_hours <client_id> <hours> \"description\" [yyyy-mm-dd]"
        return 1
    fi

    curl -s -X POST "http://localhost:8000/api/logs" \
         -H "Content-Type: application/json" \
         -d "{
               \"client_id\": ${CLIENT_ID},
               \"date\": \"${LOG_DATE}\",
               \"hours\": ${HOURS},
               \"description\": \"${DESCRIPTION}\"
             }" \
         -w "\n[LIT] Ingestion Status: %{http_code}\n"
}
```

Usage:

```bash
log_hours 1 4.5 "Network configuration"
log_hours 1 2.0 "Code review" "2026-05-15"
```

---

## API Reference

### Clients

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/clients` | Create a client |
| `GET` | `/api/clients` | List all clients |

**Create client example:**

```bash
curl -X POST http://localhost:8000/api/clients \
     -H "Content-Type: application/json" \
     -d '{"name":"Acme Corp","email":"billing@acme.com","billing_address":"123 Main St","default_hourly_rate":100.00}'
```

### Time Logs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/logs` | Create a time log (JSON or form) |
| `POST` | `/api/logs/upload-csv` | Bulk import from CSV |
| `GET` | `/api/logs/unbilled` | List unbilled logs (HTML fragment) |

**Create log (JSON):**

```bash
curl -X POST http://localhost:8000/api/logs \
     -H "Content-Type: application/json" \
     -d '{"client_id":1,"hours":4.5,"description":"Network config","date":"2026-05-17"}'
```

**CSV format:**

```csv
client_id,date,hours,description
1,2026-05-15,4.0,Task A
1,2026-05-16,3.5,Task B
```

### Invoices

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/clients/{id}/invoices` | Generate invoice from unbilled logs |
| `GET` | `/api/invoices/{id}` | Get invoice metadata |
| `GET` | `/api/invoices/{id}/pdf` | Download invoice as PDF |

**Generate invoice:**

```bash
curl -X POST http://localhost:8000/api/clients/1/invoices
```

**Download PDF:**

```bash
curl -o invoice-2026-0001.pdf http://localhost:8000/api/invoices/1/pdf
```

---

## Validation Rules

| Field | Rule |
|-------|------|
| `hours` | Must be a positive number |
| `date` | Cannot be in the future |
| `client_id` | Required, must reference an existing client |

Invalid submissions return `422 Unprocessable Entity` with a detail message.

---

## Project Structure

```
lean-invoice-tracker/
├── src/
│   ├── main.py            # FastAPI app & API routes
│   ├── models.py          # SQLModel database schemas + InvoiceSequence
│   ├── database.py        # Engine + WAL configuration
│   ├── services.py        # Business logic & schemas
│   ├── templates/
│   │   ├── dashboard.html # HTMX-powered dashboard
│   │   └── invoice.html   # PDF template (Jinja2 + WeasyPrint)
│   └── utils/
│       └── pdf.py         # PDF generation (Jinja2 + WeasyPrint)
├── data/
│   └── invoices.db        # SQLite database (git-ignored)
├── backups/
│   └── db_dump.sql        # Automated SQL dumps
├── tests/                 # 83 pytest tests
├── scripts/
│   ├── README.md          # Script documentation
│   └── smoke_test.py      # Live integration tests
├── specs/
│   └── project.md         # System design specification
├── requirements.txt       # Dependencies (includes weasyprint)
├── docker-compose.yml
├── Dockerfile             # Includes WeasyPrint fonts
├── DEPLOYMENT.md          # Production deployment guide
├── PROJECT_STATUS.md      # Current project status
└── run_backup.sh          # Automated backup script
```

---

## Testing

### Unit Tests

```bash
uv run pytest
```

83 tests covering models, database, services, API endpoints, and PDF generation.

### Smoke Tests

```bash
# 1. Start the server in one terminal
uv run uvicorn src.main:app --port 8000

# 2. Run smoke tests in another terminal
uv run python scripts/smoke_test.py
```

8 live integration tests that verify the full workflow end-to-end.

---

## Backups

The included `run_backup.sh` script:

1. Creates a clean SQLite snapshot via the `.backup` command
2. Dumps it to `backups/db_dump.sql`
3. Commits and pushes to Git

Run manually:

```bash
bash run_backup.sh
```

Or set up a cron job (see `specs/project.md` section 7 for crontab instructions).

---

## License

Private / internal use.
