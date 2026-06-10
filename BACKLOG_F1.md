# Backlog — Fase 1: Schema PostgreSQL + API Go (núcleo)

> Fuente: IMPLEMENTATION_PLAN.md v1.1, Fase 1 · Duración estimada: ~2 semanas
> Arquitectura congelada: no introducir componentes ni endpoints fuera de esta lista.

---

## T1.1 — Migración inicial: todas las tablas

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T0.9

**Descripción:** Crear `migrations/0002_schema.up.sql` con las tablas definidas en DESIGN.md: `apps`, `workers`, `gpus`, `jobs`, `job_logs`, `job_files`, `worker_metrics`. Incluir los índices mínimos necesarios para los queries del claim y del dashboard. Crear el correspondiente `0002_schema.down.sql`.

**Criterios de aceptación:**
- [ ] `make migrate-up` aplica la migración limpiamente desde cero.
- [ ] `make migrate-down` la revierte limpiamente.
- [ ] Todas las columnas descritas en DESIGN.md existen con los tipos correctos.
- [ ] Índices creados: `jobs(status, priority, created_at)`, `jobs(worker_id)`, `worker_metrics(worker_id, recorded_at)`.

---

## T1.2 — Estructura del proyecto Go + middleware de autenticación

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.1

**Descripción:** Inicializar la estructura del servidor Go en `api/`: router (stdlib `net/http` o `chi`), conexión a PostgreSQL con pool, middleware de autenticación por header `X-App-Key`. Tres roles: app, worker, admin — validados contra la tabla `apps`. Configuración vía variables de entorno.

**Criterios de aceptación:**
- [ ] `go build ./...` y `go test ./...` pasan en CI.
- [ ] Un request sin `X-App-Key` recibe 401.
- [ ] Un request con key de app no puede usar endpoints de worker (403).
- [ ] La conexión al pool se cierra limpiamente en shutdown.

---

## T1.3 — `POST /ai/jobs` y `GET /ai/jobs/{id}`

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.2

**Descripción:** Crear un job en la tabla `jobs` con validación de payload (campos requeridos: `service`, `app`), mapeo de priority string→int (`high=1`, `normal=5`, `low=10`), inferencia de `requirements` por service (tabla interna, no en BD), defaults de `routing` (`prefer: local`, `allow_external: false`). `GET /ai/jobs/{id}` devuelve el estado completo. Al crear, hacer `NOTIFY jobs_channel`.

**Criterios de aceptación:**
- [ ] `POST` con payload válido devuelve 201 con `id` y `status: pending`.
- [ ] `POST` sin `service` devuelve 422 con mensaje claro.
- [ ] `priority: "high"` se almacena como `1`; `"normal"` como `5`; `"low"` como `10`.
- [ ] `GET` devuelve 404 si el job no existe.
- [ ] `webhook_url` con IP privada (RFC 1918) es rechazado con 422 en el `POST`.

---

## T1.4 — `POST /workers/register` y `POST /workers/{id}/heartbeat`

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.2

**Descripción:** Un worker se registra con sus capabilities (incluye `vram_total_mb`). El registro inserta o actualiza la fila en `workers` y, si hay GPU, inserta o actualiza la fila en `gpus` (con `vram_reserved_mb = 0`). El heartbeat actualiza `last_heartbeat` y escribe una fila en `worker_metrics`.

**Criterios de aceptación:**
- [ ] Registro idempotente: registrar dos veces el mismo `worker_id` no duplica filas.
- [ ] Heartbeat escribe en `worker_metrics` con los valores enviados.
- [ ] Heartbeat con `worker_id` no registrado devuelve 404.
- [ ] La tabla `gpus` tiene fila para el worker tras su registro (si `capabilities.cuda = true`).

---

## T1.5 — `POST /workers/{id}/claim` con SKIP LOCKED + reserva atómica de VRAM

**Tipo:** Desarrollo · **Esfuerzo:** 6 h · **Dependencias:** T1.4

**Descripción:** El endpoint más crítico de la plataforma. En una única transacción: (1) seleccionar el job de mayor prioridad que el worker puede atender (`FOR UPDATE SKIP LOCKED`, filtrado por `service` en capabilities y `min_vram_mb` ≤ `vram_free_mb` del último heartbeat), (2) hacer `UPDATE gpus SET vram_reserved_mb = vram_reserved_mb + $min_vram_mb WHERE ... AND vram_reserved_mb + $min_vram_mb <= vram_total_mb - margen_sistema RETURNING id`, (3) actualizar el job a `running`. Si la reserva falla (no hay VRAM), no asignar el job. El routing `prefer/allow_external/provider` afecta la selección: un API worker solo ve jobs con `allow_external=true` o `prefer=external`.

**Criterios de aceptación:**
- [ ] Con 1 GPU simulada (14637 MB) y 2 workers compitiendo por un job de 10000 MB: exactamente uno lo reclama.
- [ ] Con jobs que suman más VRAM que la GPU: nunca se sobre-reserva (test con 10 workers concurrentes).
- [ ] Worker sin GPU en capabilities no puede reclamar jobs con `cuda: true`.
- [ ] Job reclamado pasa a `status: running` y tiene `worker_id` y `started_at`.
- [ ] Si no hay jobs disponibles, devuelve 204.

---

## T1.6 — `PATCH /ai/jobs/{id}/progress` y `PATCH /ai/jobs/{id}/complete` con fencing

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.5

**Descripción:** `progress` actualiza `jobs.progress` y opcionalmente escribe en `job_logs`. `complete` actualiza `status → done`, `completed_at`, `result`, y **libera la reserva de VRAM** en `gpus`. Ambos validan fencing: `WHERE id = $job_id AND worker_id = $reporter_id`; si no coincide, 409. Una función `releaseVRAMReservation(jobID)` centraliza la liberación — se llamará desde complete, error, cancel y el heartbeat monitor.

**Criterios de aceptación:**
- [ ] `progress` con el worker correcto actualiza el porcentaje.
- [ ] `progress` con worker incorrecto devuelve 409.
- [ ] `complete` libera la reserva en `gpus` (`vram_reserved_mb` baja).
- [ ] `complete` con worker incorrecto devuelve 409 y no libera VRAM.
- [ ] Tras `complete`, `GET /ai/jobs/{id}` devuelve `status: done` con el resultado.

---

## T1.7 — `POST /ai/jobs/{id}/cancel`

**Tipo:** Desarrollo · **Esfuerzo:** 2 h · **Dependencias:** T1.6

**Descripción:** Cancelar un job. Si está `pending`: pasa a `cancelled` directamente. Si está `running`: pasa a `cancelled` y libera la reserva de VRAM (usando la misma función de T1.6). Jobs en `done` o `error` no se pueden cancelar (409).

**Criterios de aceptación:**
- [ ] Job `pending` cancelado → `status: cancelled` inmediatamente.
- [ ] Job `running` cancelado → `status: cancelled` + VRAM liberada.
- [ ] Job `done` cancelado → 409.
- [ ] Solo la app propietaria del job puede cancelarlo (verificar `jobs.app`).

---

## T1.8 — Heartbeat monitor (goroutine)

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.6

**Descripción:** Goroutine que corre cada 30s. Para cada worker con `last_heartbeat` de más de 90s: (1) sus jobs `running` vuelven a `pending` con `worker_id = NULL` y `started_at = NULL`, (2) la reserva de VRAM se libera vía `releaseVRAMReservation`, (3) el worker pasa a `offline`. El timeout es 90s (no 30s) para tolerar GPUs saturadas durante generación de video.

**Criterios de aceptación:**
- [ ] Worker que deja de hacer heartbeat → sus jobs vuelven a `pending` en ≤120s (90s timeout + hasta 30s de tick).
- [ ] VRAM liberada correctamente tras el timeout.
- [ ] Worker que vuelve a hacer heartbeat pasa de `offline` a `online`.
- [ ] Goroutine no crashea si la BD no responde temporalmente.

---

## T1.9 — Reintentos con backoff exponencial

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.6, T1.7

**Descripción:** Cuando un job falla (`PATCH /complete` con `status: error`): si `retry_count < max_retries`, el job vuelve a `pending` con `retry_count++` y un `retry_after` calculado con backoff exponencial (30s → 60s → 120s). El claim ignora jobs con `retry_after > now()`. Casos donde `max_retries = 0`: errores de payload inválido, jobs cancelados.

**Criterios de aceptación:**
- [ ] Job con `max_retries: 3` que siempre falla pasa por `pending` 3 veces y termina en `error` definitivo.
- [ ] El tiempo entre reintentos sigue el patrón 30s → 60s → 120s (verificado en test con tiempo acelerado).
- [ ] Job con `max_retries: 0` va directamente a `error` sin reintentar.
- [ ] El claim no toma jobs con `retry_after` en el futuro.

---

## T1.10 — Webhook dispatcher

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.6

**Descripción:** Cuando un job pasa a `done`, `error` o `cancelled`, si tiene `webhook_url`, disparar el webhook en una goroutine. Validación SSRF antes de conectar: solo `https://`, bloqueados rangos RFC 1918, localhost, y `100.64.0.0/10` (Tailscale). Reintentos con backoff (3 intentos). Timeout de conexión de 10s.

**Criterios de aceptación:**
- [ ] Webhook a URL válida recibe POST con el estado final del job.
- [ ] `webhook_url` con IP privada es rechazado en el `POST /ai/jobs` (T1.3) — no llega a disparar.
- [ ] Fallo del webhook (servidor no responde) no bloquea la finalización del job.
- [ ] Webhook con URL de Tailscale (`100.x.x.x`) es rechazado.

---

## T1.11 — Worker simulado + tests de integración

**Tipo:** Desarrollo · **Esfuerzo:** 5 h · **Dependencias:** T1.5, T1.6, T1.8, T1.9

**Descripción:** Worker simulado en Go (o Python) que hace register → claim loop → sleep(N) → complete. Usado para los tests de integración que verifican el ciclo completo. Tests contra PostgreSQL real (no mocks — SKIP LOCKED y el ledger solo son correctos con Postgres de verdad). Usar testcontainers o un compose de test.

**Test de carrera del ledger** (el test más importante): N workers concurrentes con una sola GPU simulada, jobs que suman más VRAM que la disponible. Verificar que `gpus.vram_reserved_mb` nunca excede `vram_total_mb`.

**Criterios de aceptación:**
- [ ] Ciclo completo end-to-end: create job → claim → progress → complete → estado consultable.
- [ ] 10 workers simulados compitiendo con 1 GPU de 14637 MB y jobs de 10000 MB: nunca se sobre-reserva.
- [ ] Worker simulado sin heartbeat → job vuelve a `pending` en ≤120s.
- [ ] Worker reporta `complete` tras haber perdido el job por timeout → 409.
- [ ] Job con 3 fallos → `error` definitivo, en el orden correcto de reintentos.

---

## Resumen

| # | Tarea | Tipo | Horas | Depende de | Estado |
|---|---|---|---|---|---|
| T1.1 | Migración inicial | Desarrollo | 3 | T0.9 | ☐ |
| T1.2 | Estructura Go + autenticación | Desarrollo | 3 | T1.1 | ☐ |
| T1.3 | POST /ai/jobs + GET /ai/jobs/{id} | Desarrollo | 4 | T1.2 | ☐ |
| T1.4 | Register + heartbeat | Desarrollo | 3 | T1.2 | ☐ |
| T1.5 | Claim SKIP LOCKED + reserva VRAM | Desarrollo | 6 | T1.4 | ☐ |
| T1.6 | Progress + complete + fencing | Desarrollo | 4 | T1.5 | ☐ |
| T1.7 | Cancel | Desarrollo | 2 | T1.6 | ☐ |
| T1.8 | Heartbeat monitor (goroutine) | Desarrollo | 4 | T1.6 | ☐ |
| T1.9 | Reintentos con backoff | Desarrollo | 3 | T1.6, T1.7 | ☐ |
| T1.10 | Webhook dispatcher + SSRF | Desarrollo | 3 | T1.6 | ☐ |
| T1.11 | Worker simulado + tests integración | Desarrollo | 5 | T1.5, T1.6, T1.8, T1.9 | ☐ |

**Total: ~40 h** — consistente con las 2 semanas estimadas en el plan.

---

## Criterios de cierre de F1

- Un job creado por API es reclamado por un worker simulado, progresa y termina; el estado es consultable en cada paso.
- 10 workers simulados compitiendo por jobs que suman más VRAM que la GPU: nunca se sobre-reserva.
- Un worker simulado que deja de hacer heartbeat pierde su job en ≤90s; si luego reporta `complete`, recibe 409.
- Un job que falla se reintenta con backoff y termina en `error` tras `max_retries`.
- Webhook se dispara al completar; un `webhook_url` hacia IP privada es rechazado al crear el job.

---

## Advertencia de implementación

**T1.5 (ledger) es el test más valioso del proyecto.** Postponerlo convierte cualquier bug en un OOM intermitente en producción, imposible de reproducir. El test de carrera va en CI desde el primer día.

**`releaseVRAMReservation` se llama desde cuatro caminos**: complete, error, cancel y heartbeat timeout. Una función, no cuatro copias.
