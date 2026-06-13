# AI Worker Platform — Estado del proyecto

> Última actualización: 2026-06-13
> Arquitectura congelada en DESIGN.md v1.4. Plan de fases en IMPLEMENTATION_PLAN.md v1.2.

## Estado actual

**Fase activa: F4 — worker-whisper**

| Fase | Estado | Fecha de cierre |
|---|---|---|
| F0 — Fundaciones e infraestructura | ✅ Completada | 2026-06-09 |
| F1 — API Go + schema PostgreSQL | ✅ Completada | 2026-06-10 |
| F2 — Node Agent | ✅ Completada | 2026-06-11 |
| F3 — Worker base Python (scaffold) | ✅ Completada y verificada en hardware¹ | 2026-06-13 |
| F4 — worker-whisper | 🔄 Siguiente | — |
| F4.5 — SDK mínimo + Video Crack transcribe | ⏳ Pendiente | — |
| F5 — Dashboard mínimo | ⏳ Pendiente | — |
| F6 — worker-ollama | ⏳ Pendiente | — |
| F7 — worker-tts + migración completa | ⏳ Pendiente | — |

> ¹ Implementación F3 completa (T3.1–T3.13) y e2e en hardware ejecutado el 2026-06-13: **4/4 escenarios PASS** (`docs/RUNBOOKS/e2e-f3-results.md`). La corrida expuso y corrigió 4 defectos reales (bind a Tailscale, bug del claim con `services` nil, fallback del `env_file`, doc de puertos).

## Infraestructura activa

| Nodo | IP Tailscale | Rol |
|---|---|---|
| VPS (`vps-15a6511a`) | `100.106.192.45` | PostgreSQL + API Go |
| ialab | `100.103.55.110` | GPU RTX 4070 Ti SUPER · Node Agent activo |

## Lo que está corriendo en producción

- **PostgreSQL 17** en el VPS con las migraciones F0–F2 aplicadas (`0001_initial`, `0002_worker_metrics`, `0003_worker_metrics_hourly`)
- **API Go** en el VPS: todos los endpoints de F1 + ingesta de métricas (F2) + retención (F2) + ingesta de logs de job `POST /ai/jobs/{id}/logs` (F3)
- **Node Agent** en ialab bajo systemd: reporte de métricas cada 10s, buffer de reconexión, endpoint `/metrics` en `100.103.55.110:9100`
- **Scaffold `worker_base`** (F3): paquete Python reutilizable (registro, heartbeat en thread, claim long-poll, fencing, graceful shutdown). `worker-echo` corre en Docker en ialab vía `deploy/ialab/docker-compose.yml`
- **Deploy del VPS scripteado** (F3): `deploy/vps/deploy.sh` valida `.env` → `up --build` → healthcheck → `migrate up`

## Decisiones de diseño registradas

| Decisión | Razonamiento | Fase |
|---|---|---|
| No mocks de DB en tests Go | SKIP LOCKED y el ledger solo se pueden probar contra PostgreSQL real | F1 |
| `recorded_at` viene del cliente | El agente conoce el timestamp de captura; el servidor no lo re-deriva | F1 |
| `gpu_id` declarado por el cliente | El cliente que conoce la GPU define su identidad; el servidor no la deriva | F2 |
| Node Agent en Python + pynvml (fuera de Docker) | Acceso directo a nvidia-smi sin complicaciones de pass-through GPU en Docker | F2 |
| `AGENT_METRICS_BIND` requerida, sin default | Sin default silencioso a `0.0.0.0`; bind explícito a la IP Tailscale del nodo | F2 |
| Alerta de deriva = solo log warning | La corrección automática del ledger es F3+; el warning es insumo diagnóstico | F2 |
| Retención por goroutine (no cron externo) | Mismo patrón que el heartbeat monitor; sin tecnologías nuevas | F2 |
| `WORKER_ID` estable y requerido | El registro es UPSERT por `id`; un id por arranque duplicaría filas — la idempotencia depende del cliente | F3 |
| Graceful shutdown sin endpoint de "return to pending" | No existe tal endpoint; en SIGTERM el worker sale y el monitor de heartbeat re-encola a `pending` en ≤90s | F3 |
| Heartbeat en thread separado de `execute()` | Un job largo no dispara falsos timeouts; el heartbeat es ortogonal a la inferencia | F3 |
| Workers en Docker con `network_mode: host` | Acceso a Tailscale (API del VPS y `/metrics` local) sin publicar puertos | F3 |

## Parámetros operativos clave (ialab)

| Parámetro | Valor |
|---|---|
| GPU | RTX 4070 Ti SUPER |
| VRAM total | 16376 MB |
| VRAM disponible (sin workers) | ~14637 MB |
| Intervalo de reporte del agente | 10s |
| Buffer máximo del agente | 360 muestras (1 hora a 10s/muestra) |
| Margen de deriva ledger (`VRAM_DRIFT_MARGIN_MB`) | 512 MB (configurable) |
| Retención de métricas crudas | 7 días → agrega a `worker_metrics_hourly` |

## Próximos pasos (F4)

Ver `docs/BACKLOG/f4.md` cuando se cree. Objetivos de la fase (worker-whisper):
- `worker-whisper` (faster-whisper, CUDA, concurrency 1) sobre el scaffold de F3
- Gestión de modelo: cargar al primer job, descargar tras inactividad (liberando la reserva del ledger)
- Almacenamiento y servidor de archivos en ialab + proxy en el VPS (`GET /ai/jobs/{id}/files/{filename}`)
- Helper `upload_file_metadata()` + registro en `job_files` (deferido de F3)
- **Pendiente de F2/F3 para F4:** el claim usa el piso `vram_total_mb - VRAMMarginMB` (hoy 500 MB); con whisper (10 GB) revisar el margen efectivo contra el overhead real de ~1.7 GB del SO
- **Deuda de F3 a atender en F4:** test del caso `WORKER_CAPABILITIES` sin array `services` (bug del claim que el e2e expuso); codificar la regla "bind a IP Tailscale, no `127.0.0.1`" como check de deploy; actualizar `deploy/ialab/worker-echo.env.example` con el `services` array.
