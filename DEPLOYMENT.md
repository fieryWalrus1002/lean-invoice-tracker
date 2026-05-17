# Deployment Guide — Lean Invoice Tracker

This guide covers production deployment, backup automation, and operational
best practices for the Lean Invoice Tracker (LIT).

---

## Production Deployment with Docker

### Prerequisites

- Docker & Docker Compose installed
- Port 8085 (or your chosen port) available on the host

### Steps

```bash
git clone <your-repo> lean-invoice-tracker
cd lean-invoice-tracker
docker compose up --build -d
```

The application will be available at `http://<host>:8085`.

### Configuration

Edit `docker-compose.yml` to adjust:

| Setting | Default | Description |
|---------|---------|-------------|
| `ports` | `8085:8000` | Host-to-container port mapping |
| `restart` | `unless-stopped` | Always restart on failure |
| `volumes` | `./data:/app/data` | Persist database across restarts |

---

## Reverse Proxy (Nginx)

For production, place LIT behind a reverse proxy with TLS:

```nginx
server {
    listen 443 ssl;
    server_name invoices.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/invoices.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/invoices.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8085;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Automated Backups

### Backup Script

`run_backup.sh` performs a safe WAL-consistent backup:

```bash
#!/bin/bash
set -e
cd "$(dirname "$0")"

# 1. Clean snapshot via SQLite backup API (no locks)
sqlite3 data/invoices.db ".backup backups/temp_snapshot.db"

# 2. Flatten to SQL text
sqlite3 backups/temp_snapshot.db ".dump" > backups/db_dump.sql

# 3. Cleanup temp file
rm backups/temp_snapshot.db

# 4. Commit if changed
git add backups/db_dump.sql backups/config.json
if git diff-index --quiet HEAD --; then
    exit 0
fi

git commit -m "Automated backup: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
git push origin main
```

### Cron Setup

Run `sudo crontab -e` (root required for volume mount access):

```
45 23 * * * /bin/bash /opt/docker-utils/lean-invoice-tracker/run_backup.sh >> /opt/docker-utils/lean-invoice-tracker/backups/backup.log 2>&1
```

**SSH key note:** When running via `sudo crontab`, Git uses root's SSH identity
(`/root/.ssh/id_rsa`). Before deploying:

```bash
sudo cat /root/.ssh/id_rsa.pub
# Add this key to your remote Git repo's authorized keys
sudo ssh -T git@github.com   # verify connectivity
```

---

## Production Hardening Checklist

- [ ] **Invoice race condition** — For high-concurrency scenarios, consider a
      database-level sequence table or retry logic (see `services.py`)
- [ ] **Input validation** — Hours must be positive; dates cannot be in the future
      (enforced via Pydantic validators)
- [ ] **Structured logging** — All modules now emit structured logs at INFO level
- [ ] **Environment variables** — Set `DATABASE_URL` if deploying to a custom path
- [ ] **Resource limits** — Configure Docker memory/CPU limits in docker-compose.yml
- [ ] **Health checks** — Add a `/health` endpoint for container orchestrators
- [ ] **Monitoring** — Wire up application metrics (e.g., Prometheus client)
- [ ] **Security review** — Add rate limiting, API keys, or auth if exposing publicly

---

## Troubleshooting

### Database locked

If you see `database is locked` errors:

1. Ensure WAL mode is enabled (it is by default — check `src/database.py`)
2. Verify no other processes hold a write lock on `data/invoices.db`
3. Run `sqlite3 data/invoices.db "PRAGMA journal_mode;"` — should return `WAL`

### PDF generation fails

```
RuntimeError: reportlab is required for PDF generation
```

Install ReportLab:

```bash
uv pip install reportlab
# or
pip install reportlab
```

For Docker builds, ensure `libpango` is installed (already in the Dockerfile):

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
```

### Port already in use

Change the port in `docker-compose.yml` or kill the existing process:

```bash
lsof -i :8085
kill <PID>
```

---

## Disaster Recovery

To restore from a SQL dump:

```bash
sqlite3 data/invoices.db < backups/db_dump.sql
```

Or restore from a binary snapshot:

```bash
cp backups/temp_snapshot.db data/invoices.db
# Restart the application
docker compose restart
```

---

## Support

See `PROJECT_STATUS.md` for current project status and known issues.
See `specs/project.md` for the system design specification.
