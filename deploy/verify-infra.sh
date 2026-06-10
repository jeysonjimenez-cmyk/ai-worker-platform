#!/usr/bin/env bash
set -uo pipefail

VPS_HOST="${VPS_HOST:-vps-15a6511a}"
VPS_USER="${VPS_USER:-ubuntu}"
COMPOSE_DIR="/home/ubuntu/ai-worker-platform/deploy/vps"
ENV_FILE="$COMPOSE_DIR/.env"
LOAD_ENV="set -a && source $ENV_FILE && set +a"

PASS=0
FAIL=0

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo "  ✓ $label"
        ((PASS++)) || true
    else
        echo "  ✗ $label"
        ((FAIL++)) || true
    fi
}

echo "=== Tailscale connectivity ==="
check "VPS reachable from here"        "ping -c1 -W2 $VPS_HOST"
check "ialab reachable from here"      "ping -c1 -W2 ialab"
check "VPS→ialab ping"                 "ssh $VPS_USER@$VPS_HOST 'ping -c1 -W2 ialab'"

echo ""
echo "=== PostgreSQL (VPS) ==="
check "postgres container healthy"     "ssh $VPS_USER@$VPS_HOST 'docker compose -f $COMPOSE_DIR/docker-compose.yml ps postgres | grep -q healthy'"
check "psql connects (localhost)"      "ssh $VPS_USER@$VPS_HOST '$LOAD_ENV && docker compose -f $COMPOSE_DIR/docker-compose.yml exec -T postgres pg_isready -U \$POSTGRES_USER'"

echo ""
echo "=== API placeholder (VPS) ==="
check "curl localhost:8081 returns 200" "ssh $VPS_USER@$VPS_HOST 'curl -sf http://localhost:8081'"

echo ""
echo "=== Migrations ==="
check "schema_migrations table exists" "ssh $VPS_USER@$VPS_HOST '$LOAD_ENV && docker compose -f $COMPOSE_DIR/docker-compose.yml exec -T postgres psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \"SELECT 1 FROM schema_migrations LIMIT 1\"'"

echo ""
echo "=== GPU (ialab) ==="
check "GPU visible in Docker on ialab" "docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi -L 2>/dev/null | grep -q GPU"

echo ""
echo "=== Backup (VPS) ==="
check "backup from today exists"       "ssh $VPS_USER@$VPS_HOST 'find /opt/backups/postgres -name \"aiworker_\$(date +%Y%m%d)*.dump\" | grep -q .'"

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "✓ All $PASS checks passed"
    exit 0
else
    echo "✗ $FAIL of $((PASS + FAIL)) checks failed"
    exit 1
fi
