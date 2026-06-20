#!/usr/bin/env bash
# pull-model.sh — T6.3: pre-pull del modelo de traducción en el servicio Ollama.
# Corre ON ialab con el servicio ollama ya arriba.
# Uso: bash pull-model.sh <modelo>
#   modelo: spec de Ollama, preferiblemente con digest: nombre@sha256:<digest>
#
# Ejecutar una vez tras el primer `make deploy-ialab` que incluye el servicio ollama.
# El modelo queda en el volumen `ollama_models` y sobrevive recreaciones.

set -euo pipefail

MODEL="${1:-}"
COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-compose.yml"

if [[ -z "$MODEL" ]]; then
  echo "uso: bash pull-model.sh <modelo>" >&2
  echo "     ejemplo: bash pull-model.sh qwen2.5:7b@sha256:abc123..." >&2
  exit 1
fi

OLLAMA_URL="http://127.0.0.1:11434"

# Verificar que Ollama está arriba
if ! curl -sf "$OLLAMA_URL/" > /dev/null 2>&1; then
  echo "error: Ollama no responde en $OLLAMA_URL" >&2
  echo "       arrancarlo: docker compose -f $COMPOSE_FILE up -d ollama" >&2
  exit 1
fi

echo "→ pulling $MODEL..."
curl -sf "$OLLAMA_URL/api/pull" \
  -H 'Content-Type: application/json' \
  -d "{\"name\": \"$MODEL\", \"stream\": false}" \
  > /dev/null
echo "✓ modelo listo"

echo ""
echo "Modelos disponibles en Ollama:"
docker compose -f "$COMPOSE_FILE" exec ollama ollama list
