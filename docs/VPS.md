# VPS — Inventario operativo

> Nodo: `vps-15a6511a` · IP Tailscale: `100.106.192.45` · Siempre activo

## Servicios

| Servicio | Estado | Puerto host | Puerto contenedor |
|---|---|---|---|
| PostgreSQL 17 | Docker Compose | `127.0.0.1:5433` | 5432 |
| API Go | Docker Compose | `127.0.0.1:8081` | 8080 |
| Dashboard (React) | Docker Compose | `127.0.0.1:3000` | 3000 |
| API Workers externos | Docker Compose (post-MVP) | — | — |

Ningún puerto está expuesto a internet. Todo acceso externo pasa por Tailscale o el proxy reverso del VPS.

## Procesos críticos

```
docker compose (deploy/vps/docker-compose.yml)
  ├── postgres
  ├── api
  └── dashboard (desde F5)
```

## Directorios

| Ruta | Contenido |
|---|---|
| `/home/ubuntu/ai-worker-platform` | Raíz del proyecto (sincronizada por `make deploy`) |
| `/home/ubuntu/ai-worker-platform/deploy/vps` | Docker Compose + `.env` |
| `/opt/backups/postgres` | Dumps diarios de PostgreSQL (retención 7 días) |

## Comandos de operación

```bash
# Estado de contenedores
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml ps

# Logs de la API
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml logs -f api

# Conectar a PostgreSQL
source ~/ai-worker-platform/deploy/vps/.env
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB

# Backup manual
~/ai-worker-platform/deploy/vps/backup.sh
```

## Variables de entorno

Ver `deploy/vps/.env.example`. El `.env` real vive solo en el VPS, nunca versionado.

## Acceso SSH

```bash
ssh ubuntu@vps-15a6511a   # vía Tailscale
```
