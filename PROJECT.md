# AI Worker Platform — Estado del proyecto

> Última actualización: 2026-06-16
> Arquitectura congelada en DESIGN.md v1.4. Plan de fases en IMPLEMENTATION_PLAN.md v1.2.

## Estado actual

**Fase activa: F6 — worker-ollama**

| Fase | Estado | Fecha de cierre |
|---|---|---|
| F0 — Fundaciones e infraestructura | ✅ Completada | 2026-06-09 |
| F1 — API Go + schema PostgreSQL | ✅ Completada | 2026-06-10 |
| F2 — Node Agent | ✅ Completada | 2026-06-11 |
| F3 — Worker base Python (scaffold) | ✅ Completada y verificada en hardware¹ | 2026-06-13 |
| F4 — worker-whisper | ✅ Completada y verificada en hardware³ | 2026-06-15 |
| F4.5 — SDK mínimo + Video Crack transcribe | ✅ Completada y verificada en producción⁴ | 2026-06-16 |
| F5 — Dashboard mínimo | ✅ Completada y verificada en producción⁵ | 2026-06-16 |
| F6 — worker-ollama | ⏳ Pendiente | — |
| F7 — worker-tts + migración completa | ⏳ Pendiente | — |

> ¹ Implementación F3 completa (T3.1–T3.13) y e2e en hardware ejecutado el 2026-06-13: **4/4 escenarios PASS** (`docs/RUNBOOKS/e2e-f3-results.md`). La corrida expuso y corrigió 4 defectos reales (bind a Tailscale, bug del claim con `services` nil, fallback del `env_file`, doc de puertos).

> ³ Implementación F4 completa (T4.1–T4.11) y e2e en hardware ejecutado el 2026-06-15: **6/6 escenarios PASS** (`docs/RUNBOOKS/e2e-f4-results.md`). Audio real de 4.2h procesado (3131 segmentos), concurrencia 1 verificada, caos mid-job resuelto en 99s, 503 con file-server caído. T4.9 calibrado: `MIN_VRAM_TRANSCRIPTION_MB=5500` (medido: 4667 MB pico, anterior teórico 10000 MB).

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
- **Deploy del VPS scripteado** (F3): `deploy/vps/deploy.sh` valida `.env` → `up --build` → healthcheck API → healthcheck dashboard → `migrate up`
- **Dashboard mínimo** (F5): React + Vite servido en `http://100.106.192.45:3000` (Tailscale). Una vista: workers (online/offline/busy) + jobs por estado. Acciones: cancelar `pending`, reintentar `error`. Polling 5s. Auth con admin key. Reemplaza el runbook SQL `f4.5-ops-without-dashboard.md` para operación diaria.

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

## Próximos pasos (F6)

**F6 — worker-ollama** · Segundo servicio: traducción local vía Ollama. Valida convivencia de dos modelos en la misma GPU vía ledger. Al cerrar F6, Video Crack adopta traducción en producción (doble ejecución, mismo patrón F4.5).

**F5 cerrada.** Ver `docs/CHANGELOG/f5.md` y `docs/RUNBOOKS/e2e-f5-results.md`. El dashboard reemplaza al runbook SQL `f4.5-ops-without-dashboard.md` para operación diaria.

> ⁴ F4.5 completa (T4.5.1–T4.5.10) y corrida de producción ejecutada el 2026-06-16 (`docs/RUNBOOKS/e2e-f4.5-results.md`): **5/5 escenarios PASS**. Video Crack transcribe vía plataforma con SSRF estricto. Corpus de 5 videos (2.2 min–4.2 h) con similitud 90.5–97.3% (PARIDAD_ACEPTABLE). Caos recovery: 54s requeue, <10s a running. VRAM pico 5060 MiB (margen 440 MiB sobre threshold de 5500). Flag de Video Crack en `both` (sistema viejo activo como respaldo — se apaga en F7).

> ⁵ F5 completa (T5.1–T5.9) y corrida de producción ejecutada el 2026-06-16 (`docs/RUNBOOKS/e2e-f5-results.md`): **5/5 escenarios PASS**. Dashboard operativo en `http://100.106.192.45:3000`. Cancelar y reintentar funcionan desde la UI. Diagnóstico de job fallido (error + payload) sin `psql`. Fix de CORS detectado y resuelto en la corrida de verificación visual. Worker offline detectado en ≤130s (vs. criterio escrito <90s — es el tick de ~30s del monitor de heartbeat de F1; mejora a F11).
