# AI Worker Platform — Contexto para Claude

## ⚠️ Entorno de desarrollo: esta máquina ES ialab

El desarrollo ocurre **sobre ialab** (`hostname=ialab`, RTX 4070 Ti SUPER, Tailscale `100.103.55.110`). **No hagas `ssh ialab`** — el filesystem, la GPU, los env files (`~/.config/ai-platform/...`), Docker Compose de los workers y `nvidia-smi` están **aquí, localmente**. Aunque DESIGN.md/IALAB.md describan ialab como nodo remoto accesible por Tailscale desde el VPS, eso aplica al VPS; para el dev local, ialab es esta máquina. El target `make deploy-ialab` (rsync) existe por el flujo formal de deploy, pero los comandos sobre ialab se corren directos. **El VPS sí es remoto** (Tailscale).

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

**🏁 F7 ✅ cerrada 2026-06-21 — MVP completo. Video Crack opera 100 % sobre la plataforma; sistema viejo apagado.**
**Siguiente: F9–F11 son mejoras post-MVP (workers externos, dashboard avanzado, métricas).**

Estado al 2026-06-21:
- F0–F6 ✅ cerradas
- F7 ✅ cerrada — worker-tts (Higgs v3, 14720 MiB calibrado); Video Crack 100 % en plataforma; LM Studio apagado; `higgs-tradu` N/A.

Postura de producción al cerrar F7:
- Dashboard en `100.106.192.45:3000` (Tailscale)
- Auth por admin key (`X-Admin-Key`), sin sistema de usuarios
- Worker offline detectado en ≤70s (heartbeatTimeout=60s, tickInterval=10s)
- Runbook SQL `f4.5-ops-without-dashboard.md` sigue válido como respaldo (sin cambios de schema en F7)
- `worker-whisper`: `ALLOW_HTTP_AUDIO=1` activo (Video Crack sirve audio por loopback)
- `worker-ollama`: `qwen2.5:7b` @ digest `845dbda0...`, `MIN_VRAM_TRANSLATION_MB=5300`
- `worker-tts`: Higgs Audio v3 `aibum-higgs-tts:latest` @ `ccbf6652d973`, `MIN_VRAM_TTS_MB=14720`; unload via `docker stop` inmediato (no idle timeout — SGLang-Omni sin endpoint de flush)
- Video Crack: `TRANSCRIBE_MODE=platform`, `TRANSLATE_MODE=platform`, `TTS_MODE=platform`; TTS se dispara manualmente por el usuario (no auto-chain)
- Ledger VRAM ialab: `vram_total_mb=15946`; TTS es mutuamente excluyente con whisper/ollama (14720+5500 > 15946)
