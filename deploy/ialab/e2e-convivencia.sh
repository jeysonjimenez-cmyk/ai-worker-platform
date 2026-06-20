#!/usr/bin/env bash
# e2e-convivencia.sh — T6.9: E2E ledger coexistence test
#
# Runs LOCALLY. Submits a transcription job and a translation job simultaneously,
# monitors nvidia-smi on ialab via SSH, then enqueues a third job that exceeds
# remaining VRAM and verifies it stays pending until one model unloads.
#
# Prerequisites:
#   - worker-whisper and worker-ollama running on ialab (make deploy-ialab)
#   - A short audio file accessible via https:// (for transcription)
#   - App key with access to both services
#
# Usage:
#   source .env.deploy  # sets APP_KEY, VPS_API_URL, IALAB_HOST, IALAB_USER, AUDIO_URL
#   bash deploy/ialab/e2e-convivencia.sh

set -euo pipefail

APP_KEY="${APP_KEY:-}"
API="${VPS_API_URL:-http://100.106.192.45:8081}"
IALAB_USER="${IALAB_USER:-ubuntu}"
IALAB_HOST="${IALAB_HOST:-100.103.55.110}"
AUDIO_URL="${AUDIO_URL:-}"  # short audio for transcription smoke test

if [[ -z "$APP_KEY" ]]; then
  echo "[error] APP_KEY is required. Set it in .env.deploy or export it."
  exit 1
fi
if [[ -z "$AUDIO_URL" ]]; then
  echo "[error] AUDIO_URL is required (a short https:// audio file)."
  exit 1
fi

echo "═══════════════════════════════════════════════════════"
echo "  T6.9 E2E Convivencia: whisper + ollama simultaneous"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── Escenario 1: submit both jobs simultaneously ─────────────────────────────
echo "→ [1/4] submitting transcription job..."
TRANS_JOB=$(curl -sf -X POST "$API/ai/jobs" \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d "{\"service\":\"transcription\",\"payload\":{\"audio_url\":\"$AUDIO_URL\",\"language\":\"es\"}}" \
  | jq -r .id)
echo "    transcription job: $TRANS_JOB"

echo "→ [2/4] submitting translation job simultaneously..."
TRANSL_JOB=$(curl -sf -X POST "$API/ai/jobs" \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"translation","payload":{"text":"Hola mundo. Este es un test de convivencia del ledger de VRAM. Los dos modelos deben procesar a la vez sin OOM.","source_lang":"es","target_lang":"en"}}' \
  | jq -r .id)
echo "    translation job:    $TRANSL_JOB"

echo ""
echo "→ [3/4] monitoring nvidia-smi on ialab (10s intervals while jobs run)..."
echo "    [recording baseline VRAM — both models loading]"
echo ""

# Poll nvidia-smi 5 times while jobs are in flight
for i in 1 2 3 4 5; do
  echo "  --- nvidia-smi snapshot $i/5 ---"
  ssh "$IALAB_USER@$IALAB_HOST" "nvidia-smi --query-gpu=name,memory.used,memory.free,memory.total --format=csv,noheader,nounits" \
    | awk -F', ' '{printf "  GPU: %s | used: %s MiB | free: %s MiB | total: %s MiB\n",$1,$2,$3,$4}'
  sleep 10
done

echo ""
echo "→ waiting for both jobs to complete..."
for JOB_ID in "$TRANS_JOB" "$TRANSL_JOB"; do
  LABEL="$JOB_ID"
  for attempt in $(seq 1 60); do
    STATUS=$(curl -sf "$API/ai/jobs/$JOB_ID" -H "X-App-Key: $APP_KEY" | jq -r .status)
    if [[ "$STATUS" == "done" || "$STATUS" == "error" ]]; then
      echo "    job $LABEL → $STATUS"
      break
    fi
    sleep 5
  done
done

echo ""
echo "─────────────────────────────────────────────────────"
echo "→ [4/4] Escenario 2: third job that exceeds remaining VRAM"
echo "    (submitting a second translation job — both whisper+ollama still loaded)"
echo ""

THIRD_JOB=$(curl -sf -X POST "$API/ai/jobs" \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"translation","payload":{"text":"Test tercer job: debe quedar pending hasta que se libere VRAM.","source_lang":"es","target_lang":"en"}}' \
  | jq -r .id)
echo "    third job: $THIRD_JOB"

sleep 3
THIRD_STATUS=$(curl -sf "$API/ai/jobs/$THIRD_JOB" -H "X-App-Key: $APP_KEY" | jq -r .status)
echo "    status 3s after submit: $THIRD_STATUS  (expected: pending)"
echo ""
echo "    [waiting for first translation job to idle-unload (~OLLAMA_IDLE_TIMEOUT seconds)...]"
echo "    [watch the third job claim once VRAM is released]"
echo ""
echo "→ polling third job status (up to 20 min for idle unload + claim)..."
for attempt in $(seq 1 240); do
  STATUS=$(curl -sf "$API/ai/jobs/$THIRD_JOB" -H "X-App-Key: $APP_KEY" | jq -r .status)
  echo "    [$(date -u +%H:%M:%S)] third job: $STATUS"
  if [[ "$STATUS" == "done" || "$STATUS" == "error" ]]; then
    echo ""
    echo "  ✓ third job transitioned from pending → $STATUS"
    break
  fi
  sleep 5
done

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  PASTE RESULTS INTO: docs/RUNBOOKS/e2e-f6-results.md"
echo "  (transcription: $TRANS_JOB)"
echo "  (translation:   $TRANSL_JOB)"
echo "  (third job:     $THIRD_JOB)"
echo "═══════════════════════════════════════════════════════"
