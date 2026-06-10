# AI Worker Platform

Orquestador de tareas de IA: cola centralizada en PostgreSQL, workers GPU en ialab, APIs externas como fallback. Ver [DESIGN.md](DESIGN.md) para la arquitectura completa.

## Mapa de directorios

```
api/          Go — API Gateway (HTTP + cola SKIP LOCKED)
workers/      Python — workers GPU en ialab (uv)
agent/        Node Agent — reporta métricas GPU al VPS
dashboard/    React + Vite — panel de estado y costos
migrations/   SQL — golang-migrate
deploy/
  vps/        docker-compose.yml, backup.sh, .env.example
  ialab/      verify-gpu.sh
  tailscale-acl.md
  verify-infra.sh
```

## Requisitos previos

- Go 1.24+, Python 3.12+, uv, Node 22+, Docker, make
- [golang-migrate](https://github.com/golang-migrate/migrate) en el VPS: `go install -tags 'postgres' github.com/golang-migrate/migrate/v4/cmd/migrate@latest`
- Tailscale activo en esta máquina, VPS (`vps-15a6511a`) e ialab

## Configuración inicial

```bash
git clone https://github.com/jeysonjimenez-cmyk/ai-worker-platform.git
cd ai-worker-platform

# variables de deploy (no se versiona)
cp deploy/vps/.env.example deploy/vps/.env
# editar deploy/vps/.env con credenciales reales

# opcional: sobreescribir host/user de deploy
cat > .env.deploy <<EOF
VPS_HOST=vps-15a6511a
VPS_USER=root
EOF
```

## Comandos

| Comando | Qué hace |
|---|---|
| `make build` | Compila api/ (Go) y valida sintaxis workers/ (ruff) |
| `make lint` | go vet + ruff check |
| `make test` | go test + pytest |
| `make deploy` | rsync → VPS, docker compose up, migrate-up |
| `make migrate-up` | Aplica migraciones pendientes en el VPS |
| `make migrate-down` | Revierte la última migración |
| `make verify-infra` | Chequeo completo de salud de la infraestructura |

## Desplegar

```bash
make deploy
```

Prerequisitos:
- `~/.ssh/config` o agente SSH con acceso a `VPS_USER@VPS_HOST`
- `deploy/vps/.env` copiado y editado en el VPS (en `VPS_DIR/deploy/vps/.env`)

## Verificar infraestructura

```bash
make verify-infra
```

Reporta ✓/✗ para: Tailscale, PostgreSQL, API placeholder, migraciones, GPU en ialab, backup del día.

## Backups de PostgreSQL

El script `deploy/vps/backup.sh` corre vía cron en el VPS:

```bash
# en el VPS, como root:
crontab -e
# agregar:
0 3 * * * /opt/ai-worker-platform/deploy/vps/backup.sh >> /var/log/pg-backup.log 2>&1
```

Backups en `/opt/backups/postgres/`, retención 7 días, formato `pg_dump -Fc`.

### Restaurar backup

```bash
# en el VPS:
docker compose -f /opt/ai-worker-platform/deploy/vps/docker-compose.yml exec -T postgres \
  pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean \
  < /opt/backups/postgres/aiworker_YYYYMMDD_HHMMSS.dump
```

## ACLs de Tailscale

Ver [deploy/tailscale-acl.md](deploy/tailscale-acl.md) — ialab solo accesible desde el VPS.

## CI

GitHub Actions en `.github/workflows/ci.yml`: build Go, lint Python, build Vite — en cada push.
