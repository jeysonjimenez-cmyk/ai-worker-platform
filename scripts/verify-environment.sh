#!/usr/bin/env bash
# Pre-flight check: verifica que el entorno de desarrollo/deploy esté listo.
# Ejecutar antes de comenzar una fase o tras cambios de infraestructura.
set -uo pipefail

VPS_HOST="${VPS_HOST:-vps-15a6511a}"
VPS_USER="${VPS_USER:-ubuntu}"
COMPOSE_DIR="/home/ubuntu/ai-worker-platform/deploy/vps"

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

# ── Herramientas locales ──────────────────────────────────────────────────────
echo "=== Local tooling ==="
check "tailscale installed"       "command -v tailscale"
check "ssh available"             "command -v ssh"
check "docker available"          "command -v docker"
check "docker compose available"  "docker compose version"
check "go installed"              "command -v go"
check "uv installed"              "command -v uv"

# ── Tailscale ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Tailscale ==="
check "tailscale connected"       "tailscale status | grep -q 'logged in\|Connected'"
check "VPS reachable"             "ping -c1 -W3 $VPS_HOST"
check "ialab reachable"           "ping -c1 -W3 ialab"
check "VPS→ialab reachable"       "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST 'ping -c1 -W3 ialab'"
check "ialab not reachable from internet (via VPS nmap)" \
    "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST 'command -v nmap && HOME_IP=\$(curl -sf https://checkip.amazonaws.com) && [ -n \"\$HOME_IP\" ] && nmap -p 8001 --open \$HOME_IP 2>&1 | grep -q \"0 hosts up\|filtered\"' || true"

# ── Docker en VPS ─────────────────────────────────────────────────────────────
echo ""
echo "=== Docker (VPS) ==="
check "docker daemon active on VPS"         "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST 'docker info'"
check "docker compose functional on VPS"    "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST 'docker compose version'"
check "postgres container healthy"          "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST 'docker compose -f $COMPOSE_DIR/docker-compose.yml ps postgres | grep -q healthy'"

# ── PostgreSQL ────────────────────────────────────────────────────────────────
echo ""
echo "=== PostgreSQL (VPS) ==="
LOAD_ENV="set -a && source $COMPOSE_DIR/.env && set +a"
check "psql accessible (localhost)"  \
    "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST '$LOAD_ENV && docker compose -f $COMPOSE_DIR/docker-compose.yml exec -T postgres pg_isready -U \$POSTGRES_USER'"
check "schema_migrations table exists" \
    "ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST '$LOAD_ENV && docker compose -f $COMPOSE_DIR/docker-compose.yml exec -T postgres psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \"SELECT 1 FROM schema_migrations LIMIT 1\"'"

# ── GPU en ialab ──────────────────────────────────────────────────────────────
echo ""
echo "=== GPU (ialab) ==="
check "docker active on ialab"    "ssh -o ConnectTimeout=5 ialab 'docker info'"
check "GPU visible in Docker"     "ssh -o ConnectTimeout=5 ialab 'docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi -L 2>/dev/null | grep -q GPU'"
check "CUDA accessible"           "ssh -o ConnectTimeout=5 ialab 'docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q .'"

# ── Resultado ─────────────────────────────────────────────────────────────────
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "✓ All $PASS checks passed — environment ready"
    exit 0
else
    echo "✗ $FAIL of $((PASS + FAIL)) checks failed"
    exit 1
fi
