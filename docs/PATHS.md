# Mapa de rutas

## VPS

| Ruta | Contenido | Notas |
|---|---|---|
| `/home/ubuntu/ai-worker-platform` | Raíz del proyecto | Sincronizada por `make deploy` (rsync) |
| `/home/ubuntu/ai-worker-platform/deploy/vps` | Docker Compose + `.env` | `.env` nunca versionado |
| `/opt/backups/postgres` | Dumps diarios de PostgreSQL | Retención 7 días; fuera del volumen Docker |

### Volúmenes Docker (VPS)

| Volumen | Contenido |
|---|---|
| `postgres_data` | Datos de PostgreSQL (gestionado por Docker) |

## IA Lab

| Ruta | Contenido | Notas |
|---|---|---|
| `/opt/ai-platform` | Raíz de runtime de la plataforma | |
| `/opt/ai-platform/files/{job_id}/` | Archivos de salida de jobs | Servidos por el servidor HTTP interno |
| `/opt/ai-platform/workers` | Docker Compose de workers | |
| `/opt/models` | Modelos en disco | No se descargan en cada arranque |

## Máquina de desarrollo

| Ruta | Contenido |
|---|---|
| `~/PROJECTS/ai-worker-platform` | Repositorio local (esta raíz) |

## Reglas

- Los archivos de jobs viven **siempre** en ialab bajo `/opt/ai-platform/files/`. El VPS los proxea, no los almacena.
- Los modelos viven **siempre** en `/opt/models` de ialab. Nunca se descargan durante la ejecución de un job.
- Los backups de PostgreSQL viven en `/opt/backups/postgres` del VPS, **fuera** del volumen Docker, para que sobrevivan un `docker compose down -v`.
