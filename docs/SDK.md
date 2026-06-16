# Contrato de la API — Guía de integración

> Versión del contrato: F4.5 · API base: `http://100.106.192.45:8081` (VPS, vía Tailscale o proxy reverso)
> Autenticación: `X-App-Key: ak-<tu-key>` en todos los endpoints `/ai/jobs/*`.

Este documento muestra el ciclo completo (crear → esperar → descargar) con ejemplos `curl` y con el SDK Python, lado a lado. Está dirigido a la primera integración de una app nueva con la plataforma.

---

## ⚠️ Restricción obligatoria: `audio_url` debe ser `https://` público

El campo `audio_url` del payload de transcripción **debe** ser una URL `https://` accesible desde internet. El worker rechaza:

- URLs `http://` (sin TLS)
- IPs privadas RFC 1918 (`10.x`, `172.16–31.x`, `192.168.x`)
- Rango Tailscale `100.64.0.0/10`
- Loopback (`127.x`) y link-local (`169.254.x`)

Este bloqueo (SSRF) es permanente y protege a todos los workers. Si la app sirve el audio en red interna, debe exponerlo vía un CDN o un endpoint público con TLS antes de enviar el job.

---

## Ciclo completo: crear → esperar → descargar

### Con `curl`

```bash
API="http://100.106.192.45:8081"
KEY="ak-<tu-app-key>"

# 1. Crear el job
JOB=$(curl -s -X POST "$API/ai/jobs" \
  -H "X-App-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "transcription",
    "payload": {
      "audio_url": "https://cdn.tuapp.com/audio/episodio-42.mp3",
      "language": "es"
    }
  }')
echo $JOB
# {"id":"abc123","status":"pending",...}

JOB_ID=$(echo $JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. Consultar estado (repetir hasta "done" o "error")
curl -s "$API/ai/jobs/$JOB_ID" -H "X-App-Key: $KEY" | python3 -m json.tool
# {"id":"abc123","status":"running","progress":42,...}
# {"id":"abc123","status":"done","progress":100,...}

# 3. Descargar el resultado (VTT)
curl -s "$API/ai/jobs/$JOB_ID/files/output.vtt" \
  -H "X-App-Key: $KEY" \
  -o episodio-42.vtt

head -5 episodio-42.vtt
# WEBVTT
#
# 00:00:00.000 --> 00:00:05.120
# Bienvenidos al podcast...
```

### Con el SDK Python

```python
from ai_platform_client import Client

client = Client(
    api_url="http://100.106.192.45:8081",
    app_key="ak-<tu-app-key>",
)

# 1. Crear el job
job_id = client.create_job(
    "transcription",
    {"audio_url": "https://cdn.tuapp.com/audio/episodio-42.mp3", "language": "es"},
)
print(f"job creado: {job_id}")

# 2. Esperar con backoff exponencial (lanza JobTimeoutError si excede timeout)
job = client.wait(job_id, timeout=3600)   # 1 hora máximo
print(f"status: {job['status']}")

# 3. Descargar el VTT (streaming, sin cargar en memoria)
client.download(job_id, "output.vtt", "episodio-42.vtt")
print("descargado: episodio-42.vtt")
```

---

## Referencia del SDK

Instalar desde el monorepo:

```bash
# desde la raíz del repo
pip install ./client
# o con uv:
uv add ./client
```

### `Client(api_url, app_key, *, request_timeout=30.0)`

Crea un cliente. `request_timeout` aplica a cada llamada HTTP individual (no al tiempo total del job).

### `create_job(service, payload, *, priority=None, requirements=None, routing=None, webhook_url=None) → str`

Crea un job. Devuelve el `job_id`.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `service` | `str` | Servicio a ejecutar: `"transcription"`, `"translation"`, etc. |
| `payload` | `dict` | Payload específico del servicio (ver abajo) |
| `priority` | `str \| None` | `"high"` / `"normal"` / `"low"` (default: `"normal"`) |
| `requirements` | `dict \| None` | Override de requisitos de hardware (ej. `{"min_vram_mb": 12000}`) |
| `routing` | `dict \| None` | Override de routing (ej. `{"prefer": "local", "allow_external": false}`) |
| `webhook_url` | `str \| None` | URL `https://` para notificación cuando el job termine |

### `wait(job_id, *, timeout=None, interval=2.0) → dict`

Hace polling con backoff exponencial (empieza en `interval` segundos, duplica en cada poll, tope 30s). Devuelve el dict del job cuando llega a estado terminal (`done` / `error` / `cancelled`). Lanza `JobTimeoutError` si `timeout` (segundos) expira.

### `download(job_id, filename, dest)`

Descarga `filename` del job a `dest` (ruta en disco) por streaming. Nunca carga el archivo entero en memoria. Lanza `RuntimeError` si la API devuelve 503 (ialab offline).

---

## Payload de transcripción

```json
{
  "audio_url": "https://cdn.tuapp.com/audio.mp3",   // ← obligatorio, https:// público
  "language": "es",                                   // opcional: fuerza el idioma
  "translate_to": ["en"]                              // opcional: traducir (F6+)
}
```

Archivos de salida disponibles tras `status: done`:

| Filename | Formato |
|---|---|
| `output.vtt` | WebVTT (subtítulos con timestamps) |
| `output.srt` | SRT (compatible con la mayoría de reproductores) |
| `output.json` | JSON con segmentos, timestamps y metadata de idioma |

---

## Referencia de `curl` completa

```bash
# Crear job con prioridad alta
curl -s -X POST "$API/ai/jobs" \
  -H "X-App-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"service":"transcription","priority":"high","payload":{"audio_url":"https://..."}}'

# Consultar estado
curl -s "$API/ai/jobs/$JOB_ID" -H "X-App-Key: $KEY"

# Descargar VTT
curl -s "$API/ai/jobs/$JOB_ID/files/output.vtt" -H "X-App-Key: $KEY" -o output.vtt

# Cancelar un job pendiente
curl -s -X DELETE "$API/ai/jobs/$JOB_ID" -H "X-App-Key: $KEY"
```

---

## Errores frecuentes

| HTTP | Causa | Solución |
|---|---|---|
| `401` | `X-App-Key` inválida o revocada | Verificar la key con el runbook de provisión |
| `422` | Payload inválido (falta `service` o `audio_url`) | Revisar el payload |
| `503` al descargar | ialab offline o file-server no disponible | Reintentar; el job sigue en `done`, el archivo sigue en ialab |
| Job en `error` | El worker no pudo procesar el audio | Ver `error_msg` en el dict del job; causas frecuentes: URL inaccesible, audio corrupto |

---

## Servicios disponibles

| `service` | Descripción | VRAM |
|---|---|---|
| `transcription` | faster-whisper → VTT/SRT/JSON | ~10000 MB |
| `translation` | Ollama → texto traducido | ~4000 MB |
| `llm_chat` | LLM local o externo (Anthropic/OpenRouter) | ~4000 MB |
| `tts` | TTS → audio sintetizado | ~2000 MB |
| `embeddings` | Vector embeddings | ~2000 MB |

Los servicios de imagen/video (`image_generation`, `video_generation`, etc.) están disponibles pero son bajo demanda. Consultar DESIGN.md para la lista completa.
