#!/usr/bin/env bash
# timebox-model.sh — T6.2: evalúa un modelo de traducción en Ollama.
# Corre ON ialab con el servicio ollama ya arriba.
# Uso: bash timebox-model.sh [modelo]
#   modelo: spec de Ollama (default: qwen2.5:7b)
#
# Resultado a pegar en docs/RUNBOOKS/t6.2-model-timebox.md.

set -euo pipefail

MODEL="${1:-qwen2.5:7b}"
OLLAMA_URL="http://127.0.0.1:11434"
COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-compose.yml"

# ── 1. check Ollama ───────────────────────────────────────────────────────────
if ! curl -sf "$OLLAMA_URL/" > /dev/null 2>&1; then
  echo "error: Ollama no responde en $OLLAMA_URL" >&2
  echo "       arrancarlo: docker compose -f $COMPOSE_FILE up -d ollama" >&2
  exit 1
fi
echo "✓ Ollama en $OLLAMA_URL"

# ── 2. pull del modelo ────────────────────────────────────────────────────────
echo "→ pull $MODEL..."
curl -sf "$OLLAMA_URL/api/pull" \
  -H 'Content-Type: application/json' \
  -d "{\"name\": \"$MODEL\", \"stream\": false}" \
  > /dev/null
echo "✓ modelo listo"

# ── 3. VRAM antes ─────────────────────────────────────────────────────────────
echo ""
echo "=== VRAM antes de inferencia ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader

# ── 4. traducciones de prueba ─────────────────────────────────────────────────
run_translation() {
  local src_lang="$1"
  local tgt_lang="$2"
  local text="$3"
  local label="$4"

  PROMPT="Translate the following ${src_lang} text to ${tgt_lang}. Output ONLY the translation, no explanations.

Text: ${text}"

  echo ""
  echo "--- $label ---"
  echo "Entrada: $text"
  START=$(date +%s%N)
  RESULT=$(curl -sf "$OLLAMA_URL/api/generate" \
    -H 'Content-Type: application/json' \
    -d "{\"model\": \"$MODEL\", \"prompt\": $(printf '%s' "$PROMPT" | jq -R -s .), \"stream\": false}" \
    | jq -r '.response // "ERROR: no response field"')
  END=$(date +%s%N)
  ELAPSED_MS=$(( (END - START) / 1000000 ))
  echo "Salida:  $RESULT"
  echo "Tiempo:  ${ELAPSED_MS}ms"
}

# Samples del estilo corpus Video Crack (subtítulos cortos, técnicos y narrativos)
run_translation "Spanish" "English" \
  "El doctor afirma que el paciente necesita cirugía inmediata para sobrevivir." \
  "ES→EN #1 (narrativo)"

run_translation "Spanish" "English" \
  "No podemos proceder sin la aprobación del comité de ética." \
  "ES→EN #2 (técnico)"

run_translation "Spanish" "English" \
  "La señal de GPS se perdió a los treinta metros de profundidad." \
  "ES→EN #3 (técnico)"

run_translation "English" "Spanish" \
  "The algorithm failed to converge after one thousand iterations." \
  "EN→ES #1 (técnico)"

run_translation "English" "Spanish" \
  "She realized the map had been wrong all along." \
  "EN→ES #2 (narrativo)"

# ── 5. VRAM con el modelo cargado ─────────────────────────────────────────────
echo ""
echo "=== VRAM con modelo cargado (post-inferencia) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader

# ── 6. digest para pinning ────────────────────────────────────────────────────
echo ""
echo "=== Digest del modelo (para pinear en docker-compose) ==="
docker compose -f "$COMPOSE_FILE" exec ollama ollama list | grep -F "$(echo "$MODEL" | cut -d: -f1)" || true

echo ""
echo "=== Para obtener el digest completo ==="
echo "docker compose -f $COMPOSE_FILE exec ollama ollama show $MODEL --verbose 2>&1 | head -20"

echo ""
echo "Pegar resultados en: docs/RUNBOOKS/t6.2-model-timebox.md"
