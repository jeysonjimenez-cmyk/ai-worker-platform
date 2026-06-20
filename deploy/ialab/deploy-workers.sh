#!/usr/bin/env bash
# deploy-workers.sh — runs ON ialab after rsync from `make deploy-ialab`.
# Usage: bash deploy-workers.sh [DISK_MIN_GB]
#   DISK_MIN_GB: minimum free GB on docker root before build (default: 5)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
DISK_MIN_GB="${1:-5}"

# ── 1. disk guard ─────────────────────────────────────────────────────────────
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "/var/lib/docker")
FREE_GB=$(df -BG "$DOCKER_ROOT" | awk 'NR==2{gsub(/G/,""); print $4}')
if [ "$FREE_GB" -lt "$DISK_MIN_GB" ]; then
  echo "error: only ${FREE_GB}G free on $(hostname) (docker root: $DOCKER_ROOT) — need ≥${DISK_MIN_GB}G before build" >&2
  echo "       run: docker image prune -f   to reclaim space from dangling images" >&2
  exit 1
fi
echo "✓ disk: ${FREE_GB}G free on $DOCKER_ROOT (≥${DISK_MIN_GB}G required)"

# ── 2. rebuild and force-recreate all worker services ─────────────────────────
# --force-recreate ensures containers re-read env files (docker restart does not).
echo "→ docker compose up --build -d --force-recreate..."
docker compose -f "$COMPOSE_FILE" up --build -d --force-recreate

echo ""
echo "✓ workers started — container status:"
docker compose -f "$COMPOSE_FILE" ps
