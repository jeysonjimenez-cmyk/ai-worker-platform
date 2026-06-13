#!/usr/bin/env bash
# deploy.sh — deploys the VPS API in place.
#
# Run ON the VPS from any directory:
#   bash ~/ai-worker-platform/deploy/vps/deploy.sh
#
# When called from `make deploy` (code already synced via rsync), pass
# --no-pull to skip git pull:
#   bash deploy/vps/deploy.sh --no-pull
#
# Aborts before touching Docker if the .env is missing or incomplete.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
MIGRATIONS_PATH="$REPO_ROOT/migrations"
MIGRATE_BIN="${MIGRATE_BIN:-migrate}"
SKIP_PULL="${1:-}"

REQUIRED_VARS=(POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD ADMIN_API_KEY DATABASE_URL)

# ── 1. validate .env ──────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found" >&2
  echo "       copy deploy/vps/.env.example and fill in real values" >&2
  exit 1
fi

# shellcheck source=/dev/null
set -a; source "$ENV_FILE"; set +a

MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  [[ -z "${!var:-}" ]] && MISSING+=("$var")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "error: missing required variables in .env: ${MISSING[*]}" >&2
  exit 1
fi

echo "✓ .env validated"

# ── 2. git pull (skipped when called with --no-pull) ─────────────────────────
if [[ "$SKIP_PULL" != "--no-pull" ]]; then
  echo "→ git pull..."
  cd "$REPO_ROOT"
  git pull
fi

# ── 3. rebuild and restart ───────────────────────────────────────────────────
echo "→ docker compose down..."
docker compose -f "$COMPOSE_FILE" down

echo "→ docker compose up --build..."
docker compose -f "$COMPOSE_FILE" up --build -d

# ── 4. wait for API healthcheck ───────────────────────────────────────────────
echo "→ waiting for API to be healthy..."
for i in $(seq 1 30); do
  if curl -sf http://100.106.192.45:8081/healthz > /dev/null 2>&1; then
    echo "✓ API is up"
    break
  fi
  if [[ $i -eq 30 ]]; then
    echo "error: API did not respond at /healthz after 30s" >&2
    docker compose -f "$COMPOSE_FILE" logs api | tail -20 2>&1 >&2
    exit 1
  fi
  sleep 1
done

# ── 5. apply pending migrations ───────────────────────────────────────────────
echo "→ applying migrations..."
"$MIGRATE_BIN" -path "$MIGRATIONS_PATH" -database "$DATABASE_URL" up
echo "✓ migrations applied"

echo ""
echo "✓ deploy complete"
echo "  logs:   docker compose -f $COMPOSE_FILE logs -f api"
echo "  status: docker compose -f $COMPOSE_FILE ps"
