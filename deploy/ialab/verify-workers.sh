#!/usr/bin/env bash
# verify-workers.sh — runs LOCALLY after deploy-ialab.
# Confirms workers are registered and claiming via logs and optionally the VPS admin API.
#
# Env vars (set by make deploy-ialab):
#   IALAB_USER, IALAB_HOST, IALAB_DIR  — SSH target and repo path on ialab
#   VPS_API_URL                         — VPS API base URL
#   ADMIN_API_KEY                       — if set, verify via GET /admin/workers

set -euo pipefail

IALAB_USER="${IALAB_USER:-ubuntu}"
IALAB_HOST="${IALAB_HOST:-100.103.55.110}"
IALAB_DIR="${IALAB_DIR:-/home/ubuntu/ai-worker-platform}"
VPS_API_URL="${VPS_API_URL:-http://100.106.192.45:8081}"
COMPOSE_FILE="$IALAB_DIR/deploy/ialab/docker-compose.yml"

echo "→ waiting 8s for workers to register..."
sleep 8

# ── 1. container status ───────────────────────────────────────────────────────
echo "→ container status on ialab:"
ssh "$IALAB_USER@$IALAB_HOST" "docker compose -f $COMPOSE_FILE ps --format 'table {{.Name}}\t{{.Status}}'"

# ── 2. log check for registration ─────────────────────────────────────────────
echo ""
echo "→ checking worker logs for registration / long-poll lines:"
ssh "$IALAB_USER@$IALAB_HOST" \
  "docker compose -f $COMPOSE_FILE logs --tail=40 2>/dev/null \
   | grep -iE 'registered|polling|long.poll|claim|worker_id' \
   || echo '[warn] no registration lines in last 40 log lines — run: make workers-logs to tail live'"

# ── 3. VPS API check (optional) ───────────────────────────────────────────────
echo ""
if [[ -n "${ADMIN_API_KEY:-}" ]]; then
  echo "→ checking worker status on VPS ($VPS_API_URL/admin/workers):"
  RESULT=$(curl -sf "$VPS_API_URL/admin/workers" \
    -H "X-Admin-Key: $ADMIN_API_KEY" 2>/dev/null || echo "")
  if [[ -n "$RESULT" ]]; then
    echo "$RESULT" | grep -o '"id":"[^"]*"\|"status":"[^"]*"' | paste - - | sed 's/"id":"//;s/","status":"/ → /;s/"//'
    echo "✓ workers verified via VPS API"
  else
    echo "[warn] could not reach VPS API — check connectivity and ADMIN_API_KEY"
  fi
else
  echo "[info] set ADMIN_API_KEY in .env.deploy to verify worker status via VPS API"
fi

echo ""
echo "✓ deploy-ialab complete"
