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
