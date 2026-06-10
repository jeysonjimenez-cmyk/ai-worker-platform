#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/opt/backups/postgres"
COMPOSE_DIR="$(dirname "$0")"
RETAIN_DAYS=7

mkdir -p "$BACKUP_DIR"

# Load DB credentials
set -a; source "$COMPOSE_DIR/.env"; set +a

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/aiworker_${TIMESTAMP}.dump"

docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$FILE"

echo "✓ backup written: $FILE ($(du -sh "$FILE" | cut -f1))"

# Rotate: delete dumps older than RETAIN_DAYS
find "$BACKUP_DIR" -name "aiworker_*.dump" -mtime "+${RETAIN_DAYS}" -delete
echo "✓ rotated backups older than ${RETAIN_DAYS} days"
