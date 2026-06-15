# AI Worker Platform — Estado del proyecto

> Última actualización: 2026-06-15
> Arquitectura congelada en DESIGN.md v1.4. Plan de fases en IMPLEMENTATION_PLAN.md v1.2.

## Estado actual

**Fase activa: F4.5 — SDK mínimo + Video Crack transcribe**

| Fase | Estado | Fecha de cierre |
|---|---|---|
| F0 — Fundaciones e infraestructura | ✅ Completada | 2026-06-09 |
| F1 — API Go + schema PostgreSQL | ✅ Completada | 2026-06-10 |
| F2 — Node Agent | ✅ Completada | 2026-06-11 |
| F3 — Worker base Python (scaffold) | ✅ Completada y verificada en hardware¹ | 2026-06-13 |
| F4 — worker-whisper | ✅ Completada (e2e en hardware pendiente²) | 2026-06-15 |
| F4.5 — SDK mínimo + Video Crack transcribe | 🔄 Siguiente | — |
| F5 — Dashboard mínimo | ⏳ Pendiente | — |
| F6 — worker-ollama | ⏳ Pendiente | — |
| F7 — worker-tts + migración completa | ⏳ Pendiente | — |

> ¹ Implementación F3 completa (T3.1–T3.13) y e2e en hardware ejecutado el 2026-06-13: **4/4 escenarios PASS** (`docs/RUNBOOKS/e2e-f3-results.md`). La corrida expuso y corrigió 4 defectos reales (bind a Tailscale, bug del claim con `services` nil, fallback del `env_file`, doc de puertos).

> ² Implementación F4 completa (T4.1–T4.11). El checklist e2e (`docs/RUNBOOKS/e2e-f4-results.md`) está redactado y pendiente de ejecución contra hardware real (audio ≥1 hora, caos mid-job). T4.9 (calibración VRAM real de whisper) también queda pendiente de ejecución en ialab.

## Infraestructura activa

| Nodo | IP Tailscale | Rol |
|---|---|---|
| VPS (`vps-15a6511a`) | `100.106.192.45` | PostgreSQL + API Go |
| ialab | `100.103.55.110` | GPU RTX 4070 Ti SUPER · Node Agent activo |

## Lo que está corriendo en producción

- **PostgreSQL 17** en el VPS con las migraciones F0–F2 aplicadas (`0001_initial`, `0002_worker_metrics`, `0003_worker_metrics_hourly`)
- **API Go** en el VPS: todos los endpoints de F1 + ingesta de métricas (F2) + retención (F2) + ingesta de logs de job (F3) + registro de archivos en `job_files` (F4) + proxy de archivos `GET /ai/jobs/{id}/files/{filename}` (F4) + timeout máximo por job por service (F4)
- **Node Agent** en ialab bajo systemd: reporte de métricas cada 10s, buffer de reconexión, endpoint `/metrics` en `100.103.55.110:9100`
- **Scaffold `worker_base`** (F3): paquete Python reutilizable (registro, heartbeat en thread, claim long-poll, fencing, graceful shutdown, `upload_file_metadata()`). `worker-echo` corre en Docker en ialab
- **`worker-whisper`** (F4): faster-whisper (large-v2, CUDA), transcripción con progreso por segmentos, salida VTT/SRT/JSON, descarga de audio con validación SSRF, carga lazy del modelo + descarga por inactividad
- **Servidor de archivos** (F4): `python -m http.server 8001` en ialab, bind a `100.103.55.110`, volumen `files_data` compartido con `worker-whisper`
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

## Próximos pasos (F4.5)

Ver `docs/BACKLOG/f4.md` (pendiente de crear para F4.5). Objetivos: SDK mínimo de cliente + integración con Video Crack para transcripción de vídeos reales.

**Pendientes de F4 para ejecutar en ialab antes de cerrar:**
- Checklist e2e hardware (`docs/RUNBOOKS/e2e-f4-results.md`): audio 1h, caos mid-job, concurrencia 1.
- Calibración de `MIN_VRAM_TRANSCRIPTION_MB` y `VRAM_MARGIN_MB` con whisper-large en ialab (T4.9).
