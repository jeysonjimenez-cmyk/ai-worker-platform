# Checklist e2e — Fase 4.5: Video Crack en producción con doble ejecución

> Ejecutado el 2026-06-16 contra hardware real (ialab RTX 4070 Ti SUPER + VPS).
> Regla del plan: no avanzar con criterios en rojo.
> Audio objetivo: video más largo del catálogo real de Video Crack (4.2h).

---

## Prerrequisitos

- [x] `ALLOW_HTTP_AUDIO` removido de `~/.config/ai-platform/worker-whisper.env` en ialab + container recreado
- [x] `worker-whisper` y `file-server` corriendo en ialab
- [x] API del VPS en producción
- [x] App key de `video-crack` activa en la DB del VPS
- [x] Video Crack backend reiniciado con endpoint `/tradu/jobs/{id}/audio` (T4.5.5) activo
- [x] URL pública: `https://video.iafull.app/api/tradu/jobs/{id}/audio`

```bash
# Desde ialab — ALLOW_HTTP_AUDIO ausente del proceso confirmado
docker exec ialab-worker-whisper-1 /bin/sh -c 'cat /proc/1/environ | tr "\0" "\n" | grep ALLOW || echo "ausente"'
# → ALLOW_HTTP_AUDIO ausente del proceso — ok
```

---

## Escenario 0 — Verificación de postura SSRF

**Objetivo:** confirmar que el worker rechaza `http://` con el escape hatch inactivo.

```bash
APP_KEY="ak-72d08e59edd9776abe1a91776149cd09224d6e6f"
API="http://100.106.192.45:8081"

SSRF_JOB=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"http://example.com/test.mp3"}}' | jq -r .id)
# job: 377e0e29-c33a-4329-a4d7-5edc92e5aecf
# Después de 3 reintentos con backoff → status=error
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job con `http://` → `error` con mensaje SSRF | ✅ PASS | 2026-06-16 ~01:15 UTC | `"audio_url blocked by SSRF policy: audio_url must use https, got scheme='http'"` |
| Agotó reintentos (retry_count=3) antes de error terminal | ✅ PASS | 2026-06-16 ~01:15 UTC | Esperado: SSRF siempre falla, no es transitorio |

---

## Escenario 1 — Smoke test (audio corto 2.2 min)

**Objetivo:** ciclo completo con audio corto vía URL `https://` pública antes del corpus pesado.

```bash
AUDIO_URL="https://video.iafull.app/api/tradu/jobs/932e5693/audio"  # 1.2 MB
SMOKE_JOB="7039120e-638f-482e-a29e-bbc5183922c8"
```

Comparación con transcripción legacy:

```
Segmentos:  32 (legacy) / 23 (plataforma)
Similitud:  95.7%
Veredicto: ✅ PARIDAD_ACEPTABLE
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job → `done`, VTT descargable vía proxy | ✅ PASS | 2026-06-16 01:20 UTC | 129s audio, 23 segmentos, `es` |
| VTT inicia con `WEBVTT` y tiene contenido | ✅ PASS | 2026-06-16 01:20 UTC | `"Venga, vamos a probar esta nueva función de X..."` |
| `compare_transcripts.py` → `PARIDAD_ACEPTABLE` | ✅ PASS | 2026-06-16 01:20 UTC | 95.7% similitud |

---

## Escenario 2 — Video más largo del catálogo (4.2h)

**Objetivo:** criterio principal — el video más largo procesa sin intervención manual.

```bash
AUDIO_URL="https://video.iafull.app/api/tradu/jobs/071d840e/audio"  # 150 MB, 15042s
LONG_JOB="244742f6-9468-406f-a2a5-865b90b9aa39"
```

> **Nota:** este job fue interrumpido deliberadamente en el Escenario 4 (caos) y completó
> en la segunda ejecución sin intervención. Los datos de VRAM corresponden a la segunda corrida.

VRAM observada durante inferencia (segunda ejecución, cada 60s):

| Tiempo | Progress | VRAM (MiB) | GPU util |
|---|---|---|---|
| 01:26:50 | 5% | 4402 | — |
| 01:27:51 | 7% | **5060** | 97% |
| 01:28:52 | 11% | 4932 | 4% |
| 01:33:55 | 28% | 4932 | — |
| 01:36:28 | 36% | 4900 | 100% |
| 01:38:28 | 43% | 4827 | 100% |
| 01:40:29 | 50% | 4667 | 100% |
| 01:42:30 | 57% | 4891 | 92% |
| 01:44:31 | 63% | 4379 | 0% |
| 01:53:07 | 92% | 4859 | — |

**Pico VRAM: 5060 MiB** (segunda corrida) / **4996 MiB** (primera corrida antes del caos).
`MIN_VRAM_TRANSCRIPTION_MB = 5500 MB` → margen de **440 MB** → no requiere ajuste.

Comparación de salidas:

```
Segmentos:  5860 (legacy) / 3131 (plataforma)
Palabras:   57293 (legacy) / 57296 (plataforma)
Similitud:  95.8%
Veredicto: ✅ PARIDAD_ACEPTABLE
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job → `done` sin intervención manual | ✅ PASS | 2026-06-16 01:54 UTC | 15042s audio, 3131 segmentos, `en` |
| VRAM pico dentro del margen (≤5500 MB) | ✅ PASS | 2026-06-16 | Pico 5060 MiB, margen 440 MiB — sin ajuste |
| `compare_transcripts.py` → `PARIDAD_ACEPTABLE` | ✅ PASS | 2026-06-16 | 95.8% similitud sobre 57k palabras |

---

## Escenario 3 — Corpus (muestra representativa)

**Objetivo:** procesar una muestra del catálogo y comparar salidas.

| VC job | Duración | Platform job | Status | Similitud | Veredicto |
|---|---|---|---|---|---|
| `932e5693` | 129s (2.2 min) | `7039120e` | done | 95.7% | ✅ PARIDAD_ACEPTABLE |
| `071d840e` | 15042s (4.2h) | `244742f6` | done | 95.8% | ✅ PARIDAD_ACEPTABLE |
| `4eca1cb2` | 259s (4.3 min) | `14661da0` | done | 97.2% | ✅ PARIDAD_ACEPTABLE |
| `3b9bc3f3` | 572s (9.5 min) | `db9b4d8a` | done | 90.5% | ✅ PARIDAD_ACEPTABLE |
| `ac922ace` | 713s (11.9 min) | `ac9d6bb8` | done | 97.3% | ✅ PARIDAD_ACEPTABLE |

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Todos los jobs del corpus → `done` sin intervención | ✅ PASS | 2026-06-16 | 5/5 jobs completados |
| Ningún veredicto `REGRESION` (<70%) | ✅ PASS | 2026-06-16 | Mínimo 90.5%; todos ≥ 85% |

---

## Escenario 4 — Reinicio mid-job + recuperación

**Objetivo:** job en curso con worker parado vuelve a `pending` y se recupera sin intervención.

```bash
# Job: 244742f6 — audio largo (071d840e), estaba en progress=17% cuando se paró el worker

# Parar worker-whisper (ejecutado manualmente desde terminal)
ssh cracksonj@100.103.55.110 \
  "cd ~/ai-worker-platform && docker compose -f deploy/ialab/docker-compose.yml stop worker-whisper"

# Medir tiempo hasta pending: 54 segundos
# Reiniciar:
ssh cracksonj@100.103.55.110 \
  "cd ~/ai-worker-platform && docker compose -f deploy/ialab/docker-compose.yml start worker-whisper"

# El worker retomó el job en < 10 segundos tras el restart
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job vuelve a `pending` tras parar el worker | ✅ PASS | 2026-06-16 01:26 UTC | **54s** — dentro del rango ≤120s |
| Worker reiniciado retoma el job sin intervención | ✅ PASS | 2026-06-16 01:26 UTC | Retomado en **<10s** tras `docker compose start` |
| Job completa → `done` sin intervención | ✅ PASS | 2026-06-16 01:54 UTC | 15042s, 3131 segmentos |

> El tiempo de requeue depende del tick del monitor (hasta 30s adicionales sobre el timeout de 90s).
> 54s es dentro del rango normal; no indica un problema.

---

## Calibración VRAM (actualización desde F4)

| Métrica | F4 (T4.9) | F4.5 (este run) | Delta |
|---|---|---|---|
| VRAM pico medida | 4667 MiB | **5060 MiB** | +393 MiB |
| `MIN_VRAM_TRANSCRIPTION_MB` | 5500 MB | 5500 MB | sin cambio |
| Margen sobre el pico | 833 MB | **440 MB** | −393 MB |

El pico subió 393 MiB con el corpus de Video Crack (audio en inglés, contenido denso). El margen de 440 MB es ajustado pero suficiente. Si se añaden audios sustancialmente más pesados, revisar si el pico supera 5500 MB y ajustar en consecuencia.

---

## Resultado final

| Escenario | Resultado | Fecha | Notas |
|---|---|---|---|
| 0 — SSRF sin escape hatch | ✅ PASS | 2026-06-16 | `http://` → error SSRF; escape hatch inactivo confirmado en proceso |
| 1 — Smoke test (audio corto, https://) | ✅ PASS | 2026-06-16 | 129s, 95.7% similitud |
| 2 — Video más largo del catálogo (4.2h) | ✅ PASS | 2026-06-16 | 15042s, 95.8% similitud, VRAM pico 5060 MiB |
| 3 — Corpus (5 videos) | ✅ PASS | 2026-06-16 | 5/5 PARIDAD_ACEPTABLE, mínimo 90.5% |
| 4 — Parar worker mid-job + recuperación | ✅ PASS | 2026-06-16 | Requeue 54s, retoma <10s, completa solo |

**Fase 4.5 lista para cerrar:** ✅ SÍ — todos los criterios en verde.
