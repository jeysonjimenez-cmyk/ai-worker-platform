# VPS — Inventario operativo

> Nodo: `vps-15a6511a` · IP Tailscale: `100.106.192.45` · Siempre activo

## Servicios

| Servicio | Estado | Puerto host | Puerto contenedor |
|---|---|---|---|
| PostgreSQL 17 | Docker Compose | `127.0.0.1:5433` | 5432 |
| API Go | Docker Compose | `127.0.0.1:8081` | 8080 |
| Dashboard (React) | Docker Compose | `100.106.192.45:3000` | 80 |
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
3. `docker compose down` + `docker compose up --build -d` para reconstruir todas las imágenes (API + dashboard).
4. Espera a que `/healthz` (API) responda antes de continuar.
5. Espera a que `http://100.106.192.45:3000` (dashboard) responda.
6. Aplica las migraciones pendientes con `migrate up`.

### Variables requeridas en `.env`

Ver `deploy/vps/.env.example`. El `.env` real vive solo en el VPS, nunca versionado.
El script aborta con el listado de variables faltantes si el `.env` está incompleto.

`VITE_API_URL` es una variable de build-time del dashboard (Vite la hornea en el bundle durante `docker compose up --build`). Debe apuntar a la URL que el **navegador del operador** usa para llamar a la API, p.ej. `http://100.106.192.45:8081`.

## Acceso al dashboard

URL: `http://100.106.192.45:3000` (accesible solo desde la red Tailscale).

Al entrar, el dashboard pide la **admin key** (la misma `ADMIN_KEY` del `.env` del VPS). Se guarda en `sessionStorage` del navegador — se limpia al cerrar la pestaña, no se hornea en el bundle.

El dashboard reemplaza al runbook SQL `docs/RUNBOOKS/f4.5-ops-without-dashboard.md` para la operación diaria. El runbook queda como respaldo para situaciones donde el dashboard no esté disponible.

### Qué puedes hacer desde el dashboard

| Acción | Cómo |
|---|---|
| Ver estado de workers (online/offline/busy) | Panel de workers, polling automático 5s |
| Ver jobs por estado (pending/running/done/error) | Panel de jobs, filtrable por tab |
| Diagnosticar un job fallido (error + payload) | Clic en la fila del job → detalle |
| Cancelar un job `pending` | Detalle del job → "Cancelar job" → confirmar |
| Reintentar un job en `error` | Detalle del job → "Reintentar" → confirmar |

### Comandos de operación del dashboard

```bash
# Logs del contenedor dashboard
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml logs -f dashboard

# Verificar que sirve
curl -sf http://100.106.192.45:3000 && echo "dashboard ok"
```

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
