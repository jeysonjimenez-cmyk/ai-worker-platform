# IA Lab — Inventario operativo

> Nodo: `ialab` · IP Tailscale: `100.103.55.110` · Puede apagarse
> GPU: RTX 4070 Ti SUPER · VRAM total: 16376 MB · VRAM disponible: ~14637 MB

## Servicios

| Servicio | Gestión | Puerto | Notas |
|---|---|---|---|
| Node Agent | systemd | — | Reporta métricas al VPS cada 10s |
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

# Logs del Node Agent
journalctl -u node-agent -f

# Estado de workers
docker compose -f /opt/ai-platform/workers/docker-compose.yml ps

# Verificar GPU
nvidia-smi

# Verificar GPU desde Docker
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

## Regla de acceso

ialab **nunca** se accede desde internet directamente. Todo pasa por Tailscale.
El VPS es el único nodo autorizado a conectarse a ialab (ACLs del tailnet).

## Comportamiento ante apagado

Al apagarse ialab: jobs en vuelo vuelven a `pending` en el VPS a los 90s.
Al volver: el Node Agent arranca, registra los workers, que retoman la cola automáticamente.
