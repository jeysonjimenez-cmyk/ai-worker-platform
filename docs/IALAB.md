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

## Regla de acceso

ialab **nunca** se accede desde internet directamente. Todo pasa por Tailscale.
El VPS es el único nodo autorizado a conectarse a ialab (ACLs del tailnet).

## Comportamiento ante apagado

Al apagarse ialab: jobs en vuelo vuelven a `pending` en el VPS a los 90s.
Al volver: el Node Agent arranca, registra los workers, que retoman la cola automáticamente.
