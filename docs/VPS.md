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

## Despliegue de la API

### Desde la máquina de desarrollo (recomendado)

```bash
# Sincroniza el repo, reconstruye la imagen y aplica migraciones
make deploy
```

El target hace `rsync` al VPS y luego llama a `deploy/vps/deploy.sh --no-pull` vía SSH.

### Directamente en el VPS (si ya estás en SSH)

```bash
cd ~/ai-worker-platform
git pull
bash deploy/vps/deploy.sh --no-pull
```

O con pull incluido (si el repo en el VPS está desactualizado):

```bash
bash ~/ai-worker-platform/deploy/vps/deploy.sh
```

El script:
1. Valida que el `.env` existe y contiene todas las variables requeridas — aborta con error claro si falta alguna.
2. (Opcional) `git pull` para actualizar el código.
3. `docker compose down` + `docker compose up --build -d` para reconstruir la imagen.
4. Espera a que `/healthz` responda antes de continuar.
5. Aplica las migraciones pendientes con `migrate up`.

### Variables requeridas en `.env`

Ver `deploy/vps/.env.example`. El `.env` real vive solo en el VPS, nunca versionado.
El script aborta con el listado de variables faltantes si el `.env` está incompleto.

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
