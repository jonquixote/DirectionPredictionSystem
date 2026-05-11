#!/usr/bin/env bash
# Database backup script for ofi-lab-v3
# Backs up SQLite database every 6 hours
# Retains 30 days of backups
# 
# Install to /opt/ofi-lab-v3/deploy/cron/v3-db-backup.sh
# Add to crontab: 0 */6 * * * /opt/ofi-lab-v3/deploy/cron/v3-db-backup.sh
#
# Or use systemd timer instead:
#   [Timer] in v3-db-backup.timer
#   [Service] in v3-db-backup.service

set -euo pipefail

# Configuration
DB_PATH="${V3_DB_PATH:-/data/v3.db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS=30

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Create backup filename with timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="$BACKUP_DIR/v3-$TIMESTAMP.db"
BACKUP_FILE_GZ="$BACKUP_FILE.gz"

# Check if source database exists
if [[ ! -f "$DB_PATH" ]]; then
    echo "ERROR: Database not found at $DB_PATH" >&2
    exit 1
fi

# Create backup using sqlite3
sqlite3 "$DB_PATH" ".backup $BACKUP_FILE"

# Verify backup was created
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: Backup creation failed" >&2
    exit 1
fi

# Compress backup
gzip -f "$BACKUP_FILE"

# Log backup completion
echo "$(date '+%Y-%m-%d %H:%M:%S') Backed up $DB_PATH to $BACKUP_FILE_GZ ($(du -h "$BACKUP_FILE_GZ" | cut -f1))"

# Clean up old backups (older than RETENTION_DAYS)
find "$BACKUP_DIR" -name "v3-*.db.gz" -mtime +$RETENTION_DAYS -delete

# Report cleanup
OLD_COUNT=$(find "$BACKUP_DIR" -name "v3-*.db.gz" | wc -l)
echo "$(date '+%Y-%m-%d %H:%M:%S') Retained $OLD_COUNT backups (older than $RETENTION_DAYS days deleted)"
