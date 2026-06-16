# AI Worker Platform — Contexto para Claude

## Fuentes de verdad

| Qué | Dónde |
|---|---|
| Arquitectura (congelada en v1.4) | `DESIGN.md` |
| Plan de fases y criterios de aceptación | `IMPLEMENTATION_PLAN.md` |
| Backlog ejecutable por fase | `docs/BACKLOG/f<N>.md` |
| Changelog de implementación por fase | `docs/CHANGELOG/f<N>.md` |
| Retrospectivas | `docs/RETROSPECTIVES/f<N>.md` |
| Infra operativa (puertos, procesos, troubleshooting) | `docs/VPS.md`, `docs/IALAB.md`, `docs/PORTS.md` |

**Regla:** antes de implementar cualquier tarea, leer el backlog de la fase activa. Antes de proponer cambios de arquitectura, leer DESIGN.md — está congelada.

---

## Estructura del monorepo

```
api/          Go — API REST + scheduler (módulo: github.com/jeysonjimenez-cmyk/ai-worker-platform)
agent/        Python 3.12 + uv — Node Agent (pynvml + psutil)
workers/      Python 3.12 + uv — Workers de inferencia (fase 3+)
dashboard/    React + Vite
migrations/   SQL con golang-migrate (0001_, 0002_, ...)
deploy/vps/   Docker Compose del VPS (PostgreSQL + API)
docs/         Documentación operativa y de desarrollo
scripts/      Scripts de verificación de entorno
```

---

## Cómo correr tests

```bash
# API Go (testcontainers levanta PostgreSQL real — no mocks de DB)
cd api && go test ./...

# Agent Python
cd agent && uv run pytest tests/ -q

# Workers Python
cd workers && uv run python -m pytest --tb=short -q

# Todo a la vez
make test
```

## Cómo hacer lint

```bash
make lint
# equivale a:
cd api && go vet ./...
cd agent && uv run ruff check .
cd workers && uv run ruff check .
```

## Migraciones

```bash
# Aplicar al VPS
make migrate-up VPS_DEPLOY=1

# Localmente (requiere DATABASE_URL en el entorno)
migrate -path ./migrations -database $DATABASE_URL up
```

---

## Convenciones establecidas — no negociables

### Tests
- **Nunca mocks de base de datos** en los tests de Go. SKIP LOCKED y el ledger de VRAM solo se pueden probar con PostgreSQL real. Se usa testcontainers (`postgres:17-alpine`). Ver `api/integration_test.go`.
- Los tests del agente **sí** mockean pynvml (no hay GPU en CI). Ver `agent/tests/test_collector.py`.

### API Go
- **`recorded_at` viene del cliente**, no de `now()` del servidor. El agente envía el timestamp de captura; la API lo persiste tal cual.
- **`gpu_id` es declarado por el cliente** en el payload de registro, no derivado por el servidor. El fallback `hostname+"/gpu-0"` existe solo por compatibilidad; el agente real siempre declara su `gpu_id` explícitamente.
- **Fencing en todos los endpoints de worker**: `WHERE worker_id = $reporter` — un worker no puede tocar el job de otro. 409 si no coincide.
- **`ReleaseVRAMReservation` centralizada**: los cuatro caminos de liberación (complete, error, cancel, heartbeat timeout) convergen en una sola función en `api/internal/jobs/store.go`.

### Autenticación
- `X-App-Key` → endpoints de app (`/ai/jobs/*`)
- `X-Worker-Key` → endpoints de worker (`/workers/*`)
- `X-Admin-Key` → registro de workers (`POST /workers/register`)

### Python (agent/ y workers/)
- Gestor de dependencias: **uv**. Siempre con `uv.lock` commiteado (versiones congeladas).
- Dependencia GPU: `nvidia-ml-py` (no `pynvml` — está deprecado; ambos exponen el mismo namespace `import pynvml`).

---

## Fase activa

**F5 ✅ cerrada 2026-06-16** — Dashboard mínimo, operación de producción sin `psql`.
**Siguiente: F6 — worker-ollama + Video Crack adopta traducción.**

Estado al 2026-06-16:
- F0–F4 ✅ cerradas
- F4.5 ✅ cerrada — Video Crack transcribe vía plataforma (SSRF estricto, app key activa, corpus 5 videos PARIDAD_ACEPTABLE, caos recovery 54s)
- F5 ✅ cerrada — Dashboard en `http://100.106.192.45:3000`, cancelar/reintentar desde UI, diagnóstico sin `psql`. El runbook SQL `f4.5-ops-without-dashboard.md` queda como respaldo.
- F6 ⏳ worker-ollama + Video Crack adopta traducción

Postura de producción al cerrar F5:
- Dashboard servido en `100.106.192.45:3000` (Tailscale, no expuesto a internet)
- Auth por admin key (`X-Admin-Key`), sin sistema de usuarios
- CORS configurado en la API para `/admin/*` (`withAdminCORS` en `api/main.go`)
- `VITE_API_URL=http://100.106.192.45:8081` en el `.env` del VPS (build-time del dashboard)
- Worker offline detectado en ≤70s (heartbeatTimeout=60s, tickInterval=10s en `monitor.go`)
- Runbook SQL `f4.5-ops-without-dashboard.md` sigue activo como respaldo
