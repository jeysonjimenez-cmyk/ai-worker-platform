#!/usr/bin/env bash
# timebox-tts.sh — T7.2: evalúa el motor TTS Higgs en ialab.
# Corre ON ialab con el servidor Higgs ya arriba (ver instrucciones abajo).
#
# Uso:
#   TTS_HOST=http://127.0.0.1:8020 bash timebox-tts.sh
#
# Instrucciones para levantar Higgs (si no está corriendo):
#   pip install higgs-audio   # o: uv pip install higgs-audio
#   python -m higgs_audio.server --port 8020 --device cuda
#
# Resultado a pegar en docs/RUNBOOKS/t7.2-tts-timebox.md.

set -euo pipefail

TTS_HOST="${TTS_HOST:-http://127.0.0.1:8020}"
OUTDIR="$(mktemp -d /tmp/tts-timebox-XXXXXX)"

echo "Motor: Higgs Audio"
echo "Host:  $TTS_HOST"
echo "Audio: $OUTDIR"
echo ""

# ── 1. check servidor ────────────────────────────────────────────────────────
if ! curl -sf "$TTS_HOST/health" > /dev/null 2>&1 && \
   ! curl -sf "$TTS_HOST/" > /dev/null 2>&1; then
  echo "error: Higgs no responde en $TTS_HOST" >&2
  echo "       levantarlo: python -m higgs_audio.server --port 8020 --device cuda" >&2
  exit 1
fi
echo "✓ Higgs en $TTS_HOST"

# ── 2. VRAM antes de cargar el modelo ────────────────────────────────────────
echo ""
echo "=== VRAM antes de inferencia ==="
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader

# ── 3. síntesis de prueba ─────────────────────────────────────────────────────
synthesize() {
  local label="$1"
  local text="$2"
  local outfile="$OUTDIR/${label}.wav"

  echo ""
  echo "--- $label ---"
  echo "Texto: $(echo "$text" | head -c 120)..."
  START=$(date +%s%N)
  HTTP_STATUS=$(curl -sf -w "%{http_code}" -o "$outfile" \
    "$TTS_HOST/v1/audio/speech" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg text "$text" '{model:"higgs-audio-1",input:$text,voice:"en-us-female-1",response_format:"wav"}')")
  END=$(date +%s%N)
  ELAPSED_MS=$(( (END - START) / 1000000 ))

  if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "ERROR: HTTP $HTTP_STATUS"
    return 1
  fi

  FILESIZE=$(du -h "$outfile" | cut -f1)
  if command -v ffprobe > /dev/null 2>&1; then
    DURATION=$(ffprobe -v error -show_entries format=duration \
      -of default=noprint_wrappers=1:nokey=1 "$outfile" 2>/dev/null | xargs printf "%.1fs")
  else
    DURATION="(instalar ffprobe para duración)"
  fi

  echo "Archivo: $outfile  ($FILESIZE, $DURATION)"
  echo "Tiempo:  ${ELAPSED_MS}ms"
}

# Caso 1: texto corto
synthesize "corto" \
  "The algorithm failed to converge after one thousand iterations."

# Caso 2: texto con números y siglas (caso crítico)
synthesize "numeros-siglas" \
  "The GPU processed 3.7 billion parameters in 42 milliseconds using CUDA 12.1 on an NVIDIA RTX 4070 Ti SUPER."

# Caso 3: texto largo (~500 chars, simula subtítulos de 30 seg de video)
synthesize "largo" \
  "In this episode, we explore how neural networks have fundamentally changed the way we approach natural language processing. From the early days of simple recurrent networks to the modern transformer architectures, the field has made remarkable progress. Today we'll focus on the practical implications for real-world applications, including speech synthesis, translation, and content generation at scale."

# ── 4. VRAM con el modelo cargado ────────────────────────────────────────────
echo ""
echo "=== VRAM con modelo cargado (post-síntesis) ==="
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader
echo ""
echo "=== VRAM pico durante síntesis (últimos 30s) ==="
nvidia-smi dmon -s m -d 1 -c 30 2>/dev/null | tail -5 || \
  echo "(nvidia-smi dmon no disponible — usar nvidia-smi en otra terminal durante la síntesis)"

# ── 5. verificar versión/digest para pinnear ─────────────────────────────────
echo ""
echo "=== Versión del servidor Higgs ==="
curl -sf "$TTS_HOST/version" 2>/dev/null || \
  curl -sf "$TTS_HOST/v1/models" 2>/dev/null | python3 -m json.tool 2>/dev/null || \
  echo "(no expone endpoint de versión — anotar versión del paquete instalado)"
echo ""
echo "=== Versión del paquete instalado ==="
pip show higgs-audio 2>/dev/null || uv pip show higgs-audio 2>/dev/null || echo "(no encontrado vía pip/uv)"

# ── 6. resumen ────────────────────────────────────────────────────────────────
echo ""
echo "=== Archivos de audio generados (para escucha manual) ==="
ls -lh "$OUTDIR/"
echo ""
echo "Para escuchar:"
echo "  scp ialab:$OUTDIR/*.wav /tmp/tts-samples/"
echo ""
echo "Pegar resultados en: docs/RUNBOOKS/t7.2-tts-timebox.md"
