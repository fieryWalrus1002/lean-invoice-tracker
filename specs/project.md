# System Design Document: Lean Invoice Tracker (LIT)

This document establishes the architecture, data schemas, API surface, and automation design for the **Lean Invoice Tracker (LIT)**. This specification is optimized for execution by autonomous coding agents.

---

## 1. System Architecture & Project Layout

LIT is a self-hosted, lightweight monolithic application built with FastAPI and SQLite.

### File Structure

```text
my-invoice-app/
├── .gitignore
├── README.md
├── requirements.txt
├── run_backup.sh
├── data/
│   └── invoices.db       # Local SQLite binary database (git-ignored)
├── backups/
│   ├── config.json       # Metadata, client default rates, profile info
│   └── db_dump.sql       # SQL text dump of data state (tracked by Git)
└── src/
    ├── __init__.py
    ├── main.py           # Application entry point & API Router
    ├── database.py       # Engine initialization and session lifecycle
    ├── models.py         # SQLModel database schemas
    ├── services.py       # Core invoice aggregation & parsing business logic
    ├── templates/
    │   ├── dashboard.html # Single-page interface (HTMX/Tailwind)
    │   └── invoice.html   # Jinja2 printable semantic HTML for PDF export
    └── utils/
        └── pdf.py        # PDF compilation subsystem

```

---

## 2. Data Models & Schemas

The application utilizes `SQLModel` (SQLAlchemy + Pydantic) for data access mapping.

```python
# src/models.py
from typing import List, Optional
from datetime import date, timedelta
from decimal import Decimal
from sqlmodel import SQLModel, Field, Relationship

class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    email: str
    billing_address: str
    default_hourly_rate: Decimal = Field(default=0.00, decimal_places=2)
    
    # Relationships
    time_logs: List["TimeLog"] = Relationship(back_populates="client")
    invoices: List["Invoice"] = Relationship(back_populates="client")

class TimeLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: date = Field(default_factory=date.today, index=True)
    hours: Decimal = Field(decimal_places=2)
    description: str
    is_billed: bool = Field(default=False, index=True)
    
    # Foreign Keys
    client_id: int = Field(foreign_key="client.id")
    invoice_id: Optional[int] = Field(default=None, foreign_key="invoice.id")
    
    # Relationships
    client: Client = Relationship(back_populates="time_logs")
    invoice: Optional["Invoice"] = Relationship(back_populates="time_logs")

class Invoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_number: str = Field(unique=True, index=True) # e.g., INV-2026-0001
    issue_date: date = Field(default_factory=date.today)
    due_date: date
    total_amount: Decimal = Field(decimal_places=2)
    status: str = Field(default="Draft") # Draft, Sent, Paid, Void
    
    # Foreign Keys
    client_id: int = Field(foreign_key="client.id")
    
    # Relationships
    client: Client = Relationship(back_populates="invoices")
    time_logs: List[TimeLog] = Relationship(back_populates="invoice")

```

### 2.1 Database Initialization (src/database.py)

SQLite must be initialized in **WAL (Write-Ahead Logging)** mode to safely handle concurrent access and backups:

```python
# src/database.py
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

DATABASE_URL = "sqlite:///./data/invoices.db"
engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}
)

# Enable WAL mode for safer concurrent access and backups
@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with SessionLocal() as session:
        yield session
```

---

## 3. API Route Matrix

| Method | Endpoint | Description | Payload / Query |
| --- | --- | --- | --- |
| **GET** | `/` | Renders front-end dashboard UI template | None |
| **POST** | `/api/clients` | Creates a new tracking client profile | `ClientCreate` JSON |
| **GET** | `/api/clients` | Lists all active client profiles | None |
| **POST** | `/api/logs` | Records manual time-tracking entry | `TimeLogCreate` JSON |
| **POST** | `/api/logs/upload-csv` | Multi-part form upload for bulk CSV parsing | File binary |
| **GET** | `/api/logs/unbilled` | Returns HTML table fragment of unbilled logs | None (Used by HTMX) |
| **POST** | `/api/clients/{id}/invoices` | Compiles unbilled logs into an invoice | None |
| **GET** | `/api/invoices/{id}` | Fetches raw structured metadata of invoice | None |
| **GET** | `/api/invoices/{id}/pdf` | Generates and streams compiled PDF document | None |

### 3.1 Ingestion Contracts

* **`POST /api/logs` Request Handling:**
  The dashboard form (HTMX) submits `application/x-www-form-urlencoded` data, while the CLI tool sends JSON. FastAPI must handle both:
  - **JSON (CLI):** Accept `TimeLogCreate` JSON payload directly
  - **Form Data (HTMX):** Parse form fields and map to `TimeLogCreate` schema
  
  **Implementation Note:** Use FastAPI's `Body()` with `embed=False` or add middleware to normalize form data to JSON.

* **`POST /api/logs` JSON Format:**
  ```json
  {
    "client_id": 1,
    "hours": 4.50,
    "description": "Network configuration",
    "date": "2026-05-17" 
  }
  ```
  
  **Required Fields:** `client_id`, `hours`, `description`
  
  **Optional Fields:** `date` (defaults to current system ISO date if omitted to support rapid CLI logging)

---

## 4. Aggregation Logic Blueprint

When executing `POST /api/clients/{id}/invoices`, the `services.py` core engine runs under an atomic transaction wrapper:

```python
def generate_invoice_transaction(session: Session, client_id: int) -> Invoice:
    # 1. Fetch client info to extract base hourly rate
    client = session.get(Client, client_id)
    
    # 2. Query all logs matching criteria
    unbilled_logs = session.query(TimeLog).filter(
        TimeLog.client_id == client_id,
        TimeLog.is_billed == False
    ).all()
    
    if not unbilled_logs:
        raise HTTPException(status_code=400, detail="No unbilled hours found.")
        
    # 3. Calculate metrics
    total_hours = sum(log.hours for log in unbilled_logs)
    total_amount = total_hours * client.default_hourly_rate
    
    # 4. Mint unique tracking sequence string (atomic counter to prevent race conditions)
    max_invoice = session.query(Invoice).filter(
        Invoice.invoice_number.like(f"INV-{date.today().year}-%")
    ).count()
    invoice_num = f"INV-{date.today().year}-{max_invoice + 1:04d}"
    
    # 5. Commit state updates
    new_invoice = Invoice(
        invoice_number=invoice_num,
        due_date=date.today() + timedelta(days=30),
        total_amount=total_amount,
        client_id=client_id
    )
    session.add(new_invoice)
    session.flush() # Extract assigned id
    
    for log in unbilled_logs:
        log.is_billed = True
        log.invoice_id = new_invoice.id
        session.add(log)
        
    session.commit()
    return new_invoice

```

---

## 5. UI Layout Blueprint (HTMX + Tailwind HTML)

The front-end is served dynamically via Jinja2 templates directly out of the application package. It allows instant text-based updates utilizing `htmx` swaps.

```html
<!-- src/templates/dashboard.html -->
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 text-gray-100 p-8">
    <main class="max-w-4xl mx-auto space-y-8">
        <header class="border-b border-gray-800 pb-4">
            <h1 class="text-2xl font-mono tracking-wider text-green-400">LEAN_INVOICE_TRACKER //</h1>
        </header>

        <!-- Time Log Input Form Component -->
        <section class="bg-gray-800 p-6 rounded border border-gray-700">
            <h2 class="text-lg font-bold mb-4">Log Daily Operations</h2>
            <form hx-post="/api/logs" 
                  hx-target="#unbilled-table-container"
                  hx-on::after-request="if(event.detail.successful) this.reset()"
                  class="grid grid-cols-5 gap-4">
                <select name="client_id" required class="bg-gray-900 border border-gray-700 p-2 rounded">
                    <option value="">Select Client</option>
                </select>
                <input type="date" name="date" class="bg-gray-900 border border-gray-700 p-2 rounded">
                <input type="number" step="0.25" name="hours" placeholder="Hours" class="bg-gray-900 border border-gray-700 p-2 rounded">
                <input type="text" name="description" placeholder="Task execution metrics..." class="bg-gray-900 border border-gray-700 p-2 rounded col-span-2">
                <button type="submit" class="bg-green-600 hover:bg-green-700 text-white font-bold p-2 rounded col-span-5">
                    Commit Work Log
                </button>
            </form>
            <p class="text-gray-400 text-sm mt-4">
                Note: Client dropdown should be populated via HTMX on page load from <code>/api/clients</code> endpoint.
            </p>
        </section>

        <!-- Dynamic Unbilled State Display -->
        <section id="unbilled-table-container" hx-get="/api/logs/unbilled" hx-trigger="load">
            <!-- Dynamic tabular response component loaded natively via HTMX fragments -->
        </section>
    </main>
</body>
</html>

```

---

## 6. Infrastructure Deployment

LIT is containerized and runs on the home server (`jormungandr`) within a multi-app utility Linux Container (LXC). 

### .dockerignore

Create this file to exclude dynamic data from build layers and prevent cache invalidation:

``` 
data/
backups/
__pycache__
*.pyc
.git
.gitignore
README.md
```

### src/Dockerfile

``` dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### src/docker-compose.yml

``` yaml

version: '3.8'

services:
  invoice-tracker:
    build:
      context: .
      dockerfile: src/Dockerfile
    container_name: lean_invoice_tracker
    ports:
      - "8085:8000"
    volumes:
      - ./data:/app/data
      - ./backups:/app/backups
    environment:
      - DATABASE_URL=sqlite:///app/data/invoices.db
      - ENV=production
    restart: unless-stopped
```

---

## 7. Infrastructure Automation & State Backups

To completely mitigate dynamic state corruption or binary repo-bloat overhead, backup tracking targets a flat `.sql` text generation paradigm via a headless `cron` script executor running natively on the LXC host filesystem.

### Automation Shell Engine Script (`run_backup.sh`)

This script runs natively on the LXC host. It safely reads the volume-mounted SQLite file, generates a clean structural dump, evaluates diff metrics, and commits changes to a remote private repository.

```bash
#!/bin/bash
set -e

# Establish direct absolute directory targeting context on the LXC host
cd "$(dirname "$0")"

# 1. State extraction via SQLite clean online backup API to avoid transactional locks
# This creates a temporary clean binary state out of the active WAL stream
sqlite3 data/invoices.db ".backup backups/temp_snapshot.db"

# 2. Flatten the clean binary snapshot into a deterministic flat text SQL file
sqlite3 backups/temp_snapshot.db ".dump" > backups/db_dump.sql

# 3. Purge the temporary working snapshot file
rm backups/temp_snapshot.db

# 4. Stage textual state transformations 
git add backups/db_dump.sql backups/config.json

# 5. Halt routine processing pipelines early if no state mutation occurred
if git diff-index --quiet HEAD --; then
    exit 0
fi

# 6. Commit atomic snapshot entry state
git commit -m "Automated backup snapshot dump: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"

# 7. Synchronize secure remote repo storage targets
# Note: Ensure the host cron-user's SSH keys are explicitly authorized on the remote Git repo
git push origin main

```

### Crontab Entry Deployment Instruction

To install this job into the LXC host background daemon stack, run `sudo crontab -e` (to guarantee access permissions over volume mounts) and paste the execution line:

```text
45 23 * * * /bin/bash /opt/docker-utils/my-invoice-app/run_backup.sh >> /opt/docker-utils/my-invoice-app/backups/backup.log 2>&1
```

**Critical:** When running via `sudo crontab` (as root), Git will use root's SSH identity (`/root/.ssh/id_rsa`), not the user's SSH keys. Before deploying:

1. Generate or copy root's SSH public key: `sudo cat /root/.ssh/id_rsa.pub`
2. Add this public key to your remote Git repository's authorized keys
3. Verify connectivity: `sudo ssh -T git@<your-git-host>`

---

## 8. Developer Workstation Shell Integration

The system supports local CLI logging from workstation terminals directly to the `jormungandr` server instance.

```bash
# Append to local development shell configuration (~/.bashrc)
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

    curl -s -X POST "http://jormungandr:8085/api/logs" \
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
