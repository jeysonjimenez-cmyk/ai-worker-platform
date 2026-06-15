# IA Lab — Inventario operativo

> Nodo: `ialab` · IP Tailscale: `100.103.55.110` · Puede apagarse
> GPU: RTX 4070 Ti SUPER · VRAM total: 16376 MB · VRAM disponible: ~14637 MB

## Servicios

| Servicio | Gestión | Puerto | Notas |
|---|---|---|---|
| Node Agent | systemd | — | Reporta métricas al VPS cada 10s |
| worker-echo | Docker Compose | — | Scaffold de referencia (F3); `network_mode: host`, sin puerto propio |
| Servidor de archivos | Docker Compose | `8001` (interno) | Archivos de jobs; solo accesible desde VPS vía Tailscale |
| worker-whisper | Docker Compose | — | Permanente; transcripción CUDA ~10000 MB |
| worker-llm | Docker Compose | — | Permanente; traducción + llm_chat ~4000 MB |
| worker-tts | Docker Compose | — | Permanente; TTS ~2000 MB |
| worker-vision | Docker Compose | — | Permanente; image_understanding ~4000 MB |
| worker-embeddings | Docker Compose | — | Permanente; CPU ~2000 MB |
| worker-comfyui | Node Agent (bajo demanda) | — | Imagen; ~11000–15000 MB |
| worker-video | Node Agent (bajo demanda) | — | Video LTXV; ~14000 MB |
| worker-lipsync | Node Agent (bajo demanda) | — | Lipsync; ~13000 MB |

## Servicios systemd

```
node-agent.service     — Node Agent (fuera de Docker, acceso directo a nvidia-smi)
```

## Directorios

| Ruta | Contenido |
|---|---|
| `/opt/ai-platform` | Raíz de runtime de la plataforma en ialab |
| `/opt/ai-platform/files/{job_id}/` | Archivos de salida de jobs (output.mp4, audio.mp3, etc.) |
| `/opt/models` | Modelos en disco (no se descargan en cada arranque) |

## Comandos de operación

```bash
# Estado del Node Agent
systemctl status node-agent

# Logs del Node Agent (en vivo)
journalctl -u node-agent -f

# Logs desde una fecha concreta
journalctl -u node-agent --since "2026-06-11 10:00:00"

# Reiniciar / parar / arrancar el agente
sudo systemctl restart node-agent
sudo systemctl stop node-agent
sudo systemctl start node-agent

# Estado de workers
docker compose -f /opt/ai-platform/workers/docker-compose.yml ps

# Verificar GPU
nvidia-smi

# Verificar GPU desde Docker
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi

# Consultar la última muestra de métricas del agente (endpoint local)
curl http://100.103.55.110:9100/metrics
```

## Instalación del Node Agent (desde cero)

Ejecutar en ialab desde la raíz del repo (clonar o copiar primero si es la primera vez):

```bash
# 1. Clonar/actualizar el repo en ialab
git clone <repo_url> ~/ai-worker-platform   # primera vez
# o: cd ~/ai-worker-platform && git pull

# 2. Ejecutar el instalador (idempotente — seguro de volver a correr para actualizar)
bash deploy/ialab/install-agent.sh
```

El script:
- Copia `agent/` a `/opt/ai-platform/agent`
- Sincroniza dependencias Python con `uv sync --frozen`
- Crea `/etc/ai-platform/agent.env` desde la plantilla **solo si no existe** (las credenciales reales sobreviven re-instalaciones)
- Instala y habilita `node-agent.service` en systemd

**Primera instalación:** editar el archivo de entorno antes de iniciar el servicio:

```bash
sudo $EDITOR /etc/ai-platform/agent.env   # rellenar AGENT_API_KEY, AGENT_ADMIN_KEY, etc.
sudo systemctl restart node-agent
```

### Variables de entorno del agente

Archivo: `/etc/ai-platform/agent.env` (chmod 600, solo root). Plantilla en `deploy/ialab/agent.env.example`.

| Variable | Requerida | Descripción | Ejemplo |
|---|---|---|---|
| `AGENT_API_URL` | Sí | URL base de la API en el VPS (vía Tailscale) | `http://100.106.192.45:8081` |
| `AGENT_API_KEY` | Sí | Worker key para ingesta de métricas (`X-Worker-Key`) | `wk-...` |
| `AGENT_ADMIN_KEY` | Sí | Admin key para el registro inicial al arrancar (`X-Admin-Key`) | `adm-...` |
| `AGENT_WORKER_ID` | Sí | ID único del nodo en la plataforma | `w-ialab` |
| `AGENT_METRICS_BIND` | Sí | `host:port` del endpoint local `/metrics` — usar IP Tailscale de ialab, nunca `0.0.0.0` | `100.103.55.110:9100` |
| `AGENT_INTERVAL_SEC` | No | Intervalo de reporte en segundos (default: `10`) | `10` |

El agente falla al arrancar con error claro si falta cualquier variable requerida.

### Endpoint local `/metrics`

El agente expone la última muestra en `http://100.103.55.110:9100/metrics` (JSON).
Los workers de F3+ consultan esta dirección desde sus contenedores Docker para conocer el estado del nodo sin pasar por el VPS.

## Troubleshooting del Node Agent

### El agente no reporta métricas

```bash
# 1. Verificar que el servicio está activo
systemctl status node-agent

# 2. Ver los últimos errores
journalctl -u node-agent -n 50 --no-pager

# 3. Verificar conectividad al VPS
curl -s http://100.106.192.45:8081/healthz   # debe responder 200

# 4. Verificar que la API key es válida (debe devolver 401, no timeout)
curl -s -o /dev/null -w "%{http_code}" http://100.106.192.45:8081/workers/w-ialab/metrics \
  -H "X-Worker-Key: bad-key" -d '{"samples":[]}'

# 5. Comprobar el archivo de entorno
sudo cat /etc/ai-platform/agent.env   # verificar que las variables están rellenas
```

Causas frecuentes:
- `AGENT_API_KEY` o `AGENT_API_URL` incorrectos → `401`/`403` en logs
- VPS sin responder → el agente acumula en buffer y reintenta (comportamiento normal, no reiniciar)
- `uv` no en PATH o dependencias desactualizadas → reinstalar con `bash deploy/ialab/install-agent.sh`

### Warnings de deriva ledger en logs de la API

```bash
# En el VPS
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml logs api | grep "vram drift"
```

El warning `vram drift exceeded` indica que el VRAM libre reportado por el agente difiere del ledger
(`vram_total_mb - vram_reserved_mb`) en más de `VRAM_DRIFT_MARGIN_MB` (default: 512 MB).

Causas posibles:
- Procesos externos ocupan VRAM (Docker, drivers) → normal; ajustar el margen si es constante
- Bug en la reserva del ledger (jobs no liberados) → revisar `gpus.vram_reserved_mb` en la DB

```sql
-- Estado del ledger
SELECT id, vram_total_mb, vram_reserved_mb, vram_total_mb - vram_reserved_mb AS ledger_free_mb
FROM gpus;

-- Última muestra real del agente
SELECT vram_free_mb, recorded_at FROM worker_metrics ORDER BY recorded_at DESC LIMIT 1;
```

### El agente no arranca tras un reinicio de ialab

```bash
journalctl -u node-agent -b   # logs del boot actual
systemctl status network-online.target   # el unit depende de la red
```

Si la red Tailscale tarda en establecerse, el agente puede fallar en el registro y ser reiniciado por systemd (`RestartSec=5`). Es transitorio; tras unos intentos se conecta solo.

---

## Workers (Docker Compose)

Los workers de inferencia (F3+) corren en Docker, no en systemd. Todos usan el scaffold
`worker_base` (ver `workers/README.md` para escribir uno nuevo). El primero es `worker-echo`,
el worker de ejemplo del scaffold.

Compose: `deploy/ialab/docker-compose.yml`. Los contenedores usan `network_mode: host` para
alcanzar la API del VPS y el endpoint `/metrics` local vía Tailscale; **no publican puertos**.

### Primer despliegue de `worker-echo`

```bash
# 1. Crear el archivo de entorno (las credenciales no van en la imagen)
sudo cp <repo>/deploy/ialab/worker-echo.env.example /etc/ai-platform/worker-echo.env
sudo $EDITOR /etc/ai-platform/worker-echo.env   # rellenar WORKER_KEY, WORKER_ADMIN_KEY, WORKER_ID, ...

# 2. Build + arranque
docker compose -f <repo>/deploy/ialab/docker-compose.yml up --build -d worker-echo
```

El `WORKER_KEY` (worker key) se obtiene registrando el worker con el `ADMIN_KEY`, igual que el
Node Agent. `WORKER_ID` debe ser **estable** (no cambiar entre arranques) — el registro es
idempotente por ese id.

### Operación

```bash
# Estado
docker compose -f <repo>/deploy/ialab/docker-compose.yml ps

# Logs (en vivo)
docker compose -f <repo>/deploy/ialab/docker-compose.yml logs -f worker-echo

# Reiniciar / parar / arrancar
docker compose -f <repo>/deploy/ialab/docker-compose.yml restart worker-echo
docker compose -f <repo>/deploy/ialab/docker-compose.yml stop worker-echo
docker compose -f <repo>/deploy/ialab/docker-compose.yml up -d worker-echo
```

Ver `workers/README.md` para la tabla completa de variables de entorno del worker.

---

### Primer despliegue de `worker-whisper`

`worker-whisper` requiere GPU NVIDIA y el runtime `nvidia` de Docker instalado en el host.

```bash
# 1. Crear el archivo de entorno
cp <repo>/deploy/ialab/worker-whisper.env.example ~/.config/ai-platform/worker-whisper.env
$EDITOR ~/.config/ai-platform/worker-whisper.env
# Rellenar WORKER_KEY, WORKER_ADMIN_KEY, WORKER_ID.
# WORKER_CAPABILITIES: sustituir vram_total_mb por el valor real de nvidia-smi, no el placeholder.

# 2. Build + arranque (worker-whisper y file-server comparten el volumen files_data)
docker compose -f <repo>/deploy/ialab/docker-compose.yml up --build -d worker-whisper file-server
```

#### Variables de entorno de `worker-whisper`

Archivo: `~/.config/ai-platform/worker-whisper.env` (o `/etc/ai-platform/worker-whisper.env`).  
Plantilla en `deploy/ialab/worker-whisper.env.example`.

| Variable | Requerida | Default | Descripción |
|---|---|---|---|
| `WORKER_API_URL` | Sí | — | URL base de la API en el VPS (vía Tailscale) |
| `WORKER_KEY` | Sí | — | Worker key (`X-Worker-Key`) |
| `WORKER_ADMIN_KEY` | Sí | — | Admin key para el registro inicial (`X-Admin-Key`) |
| `WORKER_ID` | Sí | — | ID único y estable del worker (ej. `w-whisper-ialab`) |
| `WORKER_CAPABILITIES` | Sí | — | JSON con `services:["transcription"]` y `vram_total_mb` real de `nvidia-smi` |
| `WORKER_FILES_DIR` | No | `/app/files` | Directorio de salida dentro del contenedor (montado en `files_data`) |
| `WHISPER_MODEL` | No | `large-v2` | Nombre del modelo faster-whisper |
| `WHISPER_DEVICE` | No | `cuda` | Dispositivo de inferencia |
| `WHISPER_COMPUTE_TYPE` | No | `float16` | Tipo de cómputo |
| `WHISPER_IDLE_TIMEOUT` | No | `600` | Segundos de inactividad antes de descargar el modelo |
| `AUDIO_MAX_SIZE_BYTES` | No | `2147483648` | Límite de tamaño del audio a descargar (2 GB) |

#### Gestión del modelo

El modelo se carga de forma **lazy** (al primer job, no al arrancar) y se **descarga automáticamente** tras `WHISPER_IDLE_TIMEOUT` segundos sin jobs. Al descargar llama `POST /workers/{id}/unload-model` para liberar la reserva en el ledger de VRAM.

```bash
# Verificar VRAM antes/durante/después de un job
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
# Al primer job: memory.used sube ~10000 MB (large-v2)
# Tras idle timeout: memory.used vuelve al baseline del SO
```

#### Directorio de archivos

Los archivos de salida se escriben en el volumen `files_data` compartido con `file-server`:

```
files/{job_id}/output.vtt   — subtítulos WebVTT
files/{job_id}/output.srt   — subtítulos SRT
files/{job_id}/output.json  — segmentos con timestamps completos
```

Los metadatos (filename, path, tamaño) quedan registrados en `job_files` en la DB del VPS.

#### Operación

```bash
# Estado
docker compose -f <repo>/deploy/ialab/docker-compose.yml ps

# Logs en vivo
docker compose -f <repo>/deploy/ialab/docker-compose.yml logs -f worker-whisper

# Reiniciar / parar / arrancar (un job en curso vuelve a pending en ≤90s)
docker compose -f <repo>/deploy/ialab/docker-compose.yml restart worker-whisper
docker compose -f <repo>/deploy/ialab/docker-compose.yml stop worker-whisper
docker compose -f <repo>/deploy/ialab/docker-compose.yml up -d worker-whisper
```

### Comportamiento ante shutdown

`docker compose stop/restart` envía SIGTERM: el worker deja de reclamar y sale limpio. Si tenía
un job en curso que no alcanza a terminar antes del SIGKILL de Docker, **el job vuelve a
`pending` a los ≤90s** por el timeout de heartbeat del VPS (no hay endpoint cliente de "return to
pending"). Otro worker lo retoma; si este lo completa tarde, recibe 409 y lo descarta (fencing).

### Troubleshooting — el worker no reclama jobs

```bash
# 1. ¿El contenedor está arriba?
docker compose -f <repo>/deploy/ialab/docker-compose.yml ps

# 2. Logs: ¿se registró y está haciendo long-poll?
docker compose -f <repo>/deploy/ialab/docker-compose.yml logs --tail 50 worker-echo

# 3. ¿La API responde desde ialab vía Tailscale?
curl -s http://100.106.192.45:8081/healthz
```

Causas frecuentes:
- Falta una variable de entorno requerida → el contenedor arranca y muere en loop (revisar logs: `required env var ... is not set`).
- `WORKER_KEY`/`WORKER_ADMIN_KEY` inválidos → `401`/`403` en el registro o el claim.
- `WORKER_CAPABILITIES` sin el service del job → el claim nunca devuelve trabajo (filtra por capabilities).
- `WORKER_ID` distinto en cada arranque → workers duplicados en la tabla `workers` (usar un id estable).

---

## Servidor de archivos

El servicio `file-server` sirve `files/{job_id}/*` en el puerto `8001`, **bindeado a la IP Tailscale** de ialab (`100.103.55.110`) — nunca a `0.0.0.0` ni `127.0.0.1`. Solo alcanzable desde el VPS.

```bash
# Verificar bind en ialab
ss -tlnp | grep 8001
# Esperado: LISTEN ... 100.103.55.110:8001

# Acceder desde el VPS (vía Tailscale)
curl -s http://100.103.55.110:8001/files/<job_id>/output.vtt | head -3

# Arrancar / parar (normalmente corre junto con worker-whisper)
docker compose -f <repo>/deploy/ialab/docker-compose.yml up -d file-server
docker compose -f <repo>/deploy/ialab/docker-compose.yml stop file-server
```

Los clientes externos acceden a los archivos exclusivamente a través del **proxy de la API Go**:
`GET /ai/jobs/{id}/files/{filename}` — el VPS hace streaming hacia ialab sin exponer ialab a internet.
Con ialab o `file-server` apagados, el proxy devuelve `503` con mensaje claro.

---

## Transcripción — contrato y ejemplo curl

```bash
APP_KEY="<tu_app_key>"
API="http://100.106.192.45:8081"

# 1. Crear job de transcripción
JOB_ID=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"https://example.com/audio.mp3"}}' \
  | jq -r .id)
echo "job_id: $JOB_ID"

# 2. Sondear progreso hasta done (audio de 1h: ~8–15 min de transcripción con large-v2)
until curl -s $API/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" \
    | jq -e '.status == "done"' > /dev/null; do
  curl -s $API/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq '{status,progress}'
  sleep 30
done

# 3. Descargar archivos vía proxy (ialab no necesita ser alcanzable directamente por el cliente)
curl -s "$API/ai/jobs/$JOB_ID/files/output.vtt"  -H "X-App-Key: $APP_KEY" -o output.vtt
curl -s "$API/ai/jobs/$JOB_ID/files/output.srt"  -H "X-App-Key: $APP_KEY" -o output.srt
curl -s "$API/ai/jobs/$JOB_ID/files/output.json" -H "X-App-Key: $APP_KEY" -o output.json
```

**Restricciones del `audio_url`:**
- Solo `https://` (no `http://`).
- No se permiten IPs privadas (RFC 1918) ni el rango Tailscale `100.64.0.0/10`.
- Tamaño máximo configurable con `AUDIO_MAX_SIZE_BYTES` (default 2 GB).

---

## Regla de acceso

ialab **nunca** se accede desde internet directamente. Todo pasa por Tailscale.
El VPS es el único nodo autorizado a conectarse a ialab (ACLs del tailnet).

## Comportamiento ante apagado

Al apagarse ialab: jobs en vuelo vuelven a `pending` en el VPS a los 90s.
Al volver: el Node Agent arranca, registra los workers, que retoman la cola automáticamente.
