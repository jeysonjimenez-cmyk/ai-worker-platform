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

# ── 2. worker guard: verify each worker service has its Dockerfile and code dir ─
# Closes R7 (each new worker needs its own Dockerfile; missing ones are discovered
# only when the build exits without the module). Fails fast with a clear message.
WORKERS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/workers"
GUARD_FAILED=0
declare -A WORKER_SERVICES=(
    ["worker-echo"]="Dockerfile worker_echo"
    ["worker-whisper"]="Dockerfile.whisper worker_whisper"
    ["worker-ollama"]="Dockerfile.ollama worker_ollama"
    ["worker-tts"]="Dockerfile.tts worker_tts"
)
for service in "${!WORKER_SERVICES[@]}"; do
    read -r dockerfile codedir <<< "${WORKER_SERVICES[$service]}"
    if [[ ! -f "$WORKERS_DIR/$dockerfile" ]]; then
        echo "error: $service missing Dockerfile: workers/$dockerfile" >&2
        GUARD_FAILED=1
    fi
    if [[ ! -d "$WORKERS_DIR/$codedir" ]]; then
        echo "error: $service missing code directory: workers/$codedir" >&2
        GUARD_FAILED=1
    fi
done
if [[ "$GUARD_FAILED" -eq 1 ]]; then
    echo "abort: worker guard failed — add the missing Dockerfile and/or code directory and retry" >&2
    exit 1
fi
echo "✓ worker guard: all Dockerfiles and code directories present"

# ── 3. rebuild and force-recreate all worker services ─────────────────────────
# --force-recreate ensures containers re-read env files (docker restart does not).
echo "→ docker compose up --build -d --force-recreate..."
docker compose -f "$COMPOSE_FILE" up --build -d --force-recreate

echo ""
echo "✓ workers started — container status:"
docker compose -f "$COMPOSE_FILE" ps
