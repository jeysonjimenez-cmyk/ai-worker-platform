# Backlog — Fase 1: Schema PostgreSQL + API Go (núcleo)

> Fuente única: IMPLEMENTATION_PLAN.md v1.1, Fase 1 · Duración estimada: ~2 semanas
> Arquitectura congelada (DESIGN.md v1.4): no introducir componentes ni endpoints fuera de esta lista.
> Orden cronológico de ejecución. Tareas de 1–4 h.

---

## T1.1 — Migración inicial: todas las tablas

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T0.9 (tooling de migraciones)

**Descripción:** Crear `migrations/0002_schema.up.sql` con las tablas definidas en DESIGN.md: `apps` (API keys + `max_daily_usd`), `workers`, `gpus`, `jobs`, `job_logs`, `job_files`, `worker_metrics`. Incluir los índices necesarios para el claim y las consultas de estado. Crear el `0002_schema.down.sql` correspondiente.

**Criterios de aceptación:**
- [ ] `make migrate-up` aplica la migración limpiamente desde cero.
- [ ] `make migrate-down` la revierte limpiamente.
- [ ] Todas las columnas descritas en DESIGN.md existen con los tipos correctos.
- [ ] Índices creados: `jobs(status, priority, created_at)`, `jobs(worker_id)`, `worker_metrics(worker_id, recorded_at)`.

**Resultado esperado:** Schema completo de la plataforma versionado en `migrations/`, aplicable y reversible.

---

## T1.2 — Estructura del proyecto Go + conexión a PostgreSQL

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.1

**Descripción:** Inicializar el servidor Go en `api/`: router, pool de conexiones a PostgreSQL, configuración por variables de entorno, graceful shutdown, healthcheck. Integrar `go build` / `go test` en la CI existente (T0.13).

**Criterios de aceptación:**
- [ ] `go build ./...` y `go test ./...` pasan en CI.
- [ ] El servidor arranca, responde un healthcheck y conecta al PostgreSQL del compose.
- [ ] El pool se cierra limpiamente en shutdown (SIGTERM).

**Resultado esperado:** Esqueleto de la API corriendo contra la BD real, listo para recibir handlers.

---

## T1.3 — Middleware de autenticación (X-App-Key / worker / admin)

**Tipo:** Seguridad · **Esfuerzo:** 3 h · **Dependencias:** T1.2

**Descripción:** Middleware de autenticación por header: API key de app (`X-App-Key`, validada contra la tabla `apps`), API key de worker y API key de admin. Cada grupo de endpoints exige su rol; una key de app no puede usar endpoints de worker ni de admin.

**Criterios de aceptación:**
- [ ] Request sin key → 401.
- [ ] Request con key de app a un endpoint de worker → 403.
- [ ] Key inexistente o revocada → 401.
- [ ] Tests unitarios del middleware para los tres roles.

**Resultado esperado:** Ningún endpoint de la API es accesible sin la credencial del rol correcto.

---

## T1.4 — `POST /ai/jobs` y `GET /ai/jobs/{id}`

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.3

**Descripción:** Crear un job con validación de payload (campos requeridos), mapeo de priority string→int, inferencia de `requirements` por service (tabla interna en código), defaults de routing. Al insertar, emitir `NOTIFY jobs_channel`. Incluir aquí la validación SSRF del `webhook_url` en el momento de creación: solo `https://`, bloqueo de RFC 1918, localhost y `100.64.0.0/10`. `GET /ai/jobs/{id}` devuelve el estado completo del job.

**Criterios de aceptación:**
- [ ] `POST` válido → 201 con `id` y `status: pending`; se emite `NOTIFY jobs_channel`.
- [ ] `POST` sin `service` → 422 con mensaje claro.
- [ ] `priority: "high"/"normal"/"low"` se almacena con el mapeo a int correcto.
- [ ] `webhook_url` hacia IP privada o `100.x.x.x` → 422 (test unitario de SSRF con casos RFC 1918, localhost, Tailscale).
- [ ] `GET` de un job inexistente → 404.

**Resultado esperado:** Las apps pueden crear y consultar jobs; ningún `webhook_url` peligroso entra a la BD.

---

## T1.5 — `POST /workers/register` y `POST /workers/{id}/heartbeat`

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.3

**Descripción:** Registro de worker con capabilities (services, `cuda`, `vram_total_mb`): inserta/actualiza la fila en `workers` y, si tiene GPU, la fila en `gpus` con `vram_reserved_mb = 0`. El heartbeat actualiza `last_heartbeat` y marca al worker `online`.

**Criterios de aceptación:**
- [ ] Registro idempotente: registrar dos veces el mismo worker no duplica filas en `workers` ni `gpus`.
- [ ] Heartbeat actualiza `last_heartbeat`.
- [ ] Heartbeat de un worker no registrado → 404.
- [ ] Tras el registro de un worker con GPU existe su fila en `gpus`.

**Resultado esperado:** Los workers tienen identidad y latido en la BD; la tabla `gpus` queda lista para el ledger.

---

## T1.6 — Claim transaccional: SKIP LOCKED + reserva atómica de VRAM

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.4, T1.5

**Descripción:** El endpoint más crítico de la plataforma: `POST /workers/{id}/claim`. En una única transacción: (1) seleccionar el job elegible de mayor prioridad con `FOR UPDATE SKIP LOCKED`, filtrado por capabilities del worker; (2) reservar VRAM con UPDATE condicional atómico en `gpus` (`SET vram_reserved_mb = vram_reserved_mb + $req WHERE vram_reserved_mb + $req <= vram_total_mb - margen RETURNING id`); (3) pasar el job a `running` con `worker_id` y `started_at`. Si la reserva falla, el job no se asigna. Sin jobs elegibles → 204.

**Criterios de aceptación:**
- [ ] Job reclamado queda `running` con `worker_id` y `started_at`; la reserva sube en `gpus`.
- [ ] Si la VRAM requerida no cabe, el claim no devuelve ese job.
- [ ] Worker cuyo capabilities no incluye el `service` del job no lo recibe.
- [ ] Sin jobs disponibles → 204.
- [ ] Selección + reserva + transición ocurren en una sola transacción (verificable en el código y por test).

**Resultado esperado:** Asignación de jobs concurrencia-segura con el ledger de VRAM como guardián.

---

## T1.7 — Claim con long-polling (LISTEN/NOTIFY)

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.6

**Descripción:** El endpoint de claim soporta long-polling: si no hay jobs elegibles, la request queda abierta hasta `timeout` segundos escuchando `jobs_channel` (LISTEN/NOTIFY emitido en T1.4); al llegar un job compatible se reintenta el claim. Si vence el timeout, 204.

**Criterios de aceptación:**
- [ ] Claim con `wait=30` y cola vacía: al crear un job durante la espera, el worker lo recibe en <2s.
- [ ] Vencido el timeout sin jobs → 204.
- [ ] N workers esperando y 1 job creado → exactamente uno lo reclama.
- [ ] La conexión LISTEN se recupera sola si la BD se reinicia.

**Resultado esperado:** Los workers obtienen jobs sin polling agresivo, con latencia de asignación de segundos.

---

## T1.8 — `releaseVRAMReservation()` + endpoint de liberación por descarga de modelo

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.6

**Descripción:** Una única función `releaseVRAMReservation()` que resta la reserva en `gpus` — será llamada desde **todos** los caminos: complete, error, cancel y heartbeat timeout (una función, no cuatro copias). Incluye el endpoint para que un worker libere la reserva al descargar un modelo de la GPU.

**Criterios de aceptación:**
- [ ] La función decrementa `vram_reserved_mb` sin dejarlo negativo y es idempotente por job (liberar dos veces no resta dos veces).
- [ ] El endpoint de descarga de modelo libera la reserva del worker.
- [ ] Test unitario que cubre la doble liberación.
- [ ] Existe un único punto de liberación en el código (sin duplicación).

**Resultado esperado:** Liberación de VRAM centralizada y a prueba de los cuatro caminos de salida de un job.

---

## T1.9 — `PATCH /ai/jobs/{id}/progress` y `/complete` con fencing

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.8

**Descripción:** `progress` actualiza `jobs.progress` y opcionalmente escribe en `job_logs`. `complete` actualiza `status` (`done` o `error`), `completed_at`, `result`, y libera la reserva vía `releaseVRAMReservation()`. Ambos con fencing: `WHERE id = $job AND worker_id = $reporter`; si no coincide → 409.

**Criterios de aceptación:**
- [ ] `progress` del worker asignado actualiza el porcentaje; de otro worker → 409.
- [ ] `complete` exitoso → `status: done`, `result` consultable por `GET`, reserva liberada.
- [ ] `complete` de un worker que ya no es dueño del job → 409 y la VRAM no se toca.
- [ ] Logs enviados con el progreso aparecen en `job_logs`.

**Resultado esperado:** Un worker zombi no puede pisar el estado de un job que ya perdió.

---

## T1.10 — `POST /ai/jobs/{id}/cancel`

**Tipo:** Desarrollo · **Esfuerzo:** 2 h · **Dependencias:** T1.9

**Descripción:** Cancelar un job. `pending` → `cancelled` directo. `running` → `cancelled` + liberación de reserva vía `releaseVRAMReservation()`. `done`/`error` → 409. Solo la app propietaria puede cancelar.

**Criterios de aceptación:**
- [ ] Job `pending` cancelado → `cancelled` inmediato.
- [ ] Job `running` cancelado → `cancelled` + VRAM liberada.
- [ ] Job `done` → 409.
- [ ] Una app no puede cancelar jobs de otra app.

**Resultado esperado:** Las apps pueden abortar trabajo sin tocar la base de datos.

---

## T1.11 — Heartbeat monitor (goroutine)

**Tipo:** Desarrollo · **Esfuerzo:** 4 h · **Dependencias:** T1.8, T1.9

**Descripción:** Goroutine periódica: para cada worker con `last_heartbeat` > 90s: (1) sus jobs `running` vuelven a `pending` con `worker_id = NULL`, (2) reserva liberada vía `releaseVRAMReservation()`, (3) worker → `offline`. No crashea si la BD falla temporalmente.

**Criterios de aceptación:**
- [ ] Worker sin heartbeat → sus jobs vuelven a `pending` en ≤90s (+ tick del monitor).
- [ ] VRAM liberada tras el timeout.
- [ ] Si el worker zombi luego reporta `complete` → 409 (fencing de T1.9).
- [ ] Worker que vuelve a latir pasa de `offline` a `online`.
- [ ] El monitor sobrevive a una caída temporal de la BD.

**Resultado esperado:** Ningún job queda colgado por la muerte de un worker; el sistema se autorrepara en ≤90s.

---

## T1.12 — Reintentos con backoff exponencial

**Tipo:** Desarrollo · **Esfuerzo:** 3 h · **Dependencias:** T1.9, T1.10

**Descripción:** Cuando `complete` reporta error: si `retry_count < max_retries`, el job vuelve a `pending` con `retry_count++` y `retry_after` con backoff exponencial (30s → 60s → 120s); el claim ignora jobs con `retry_after` futuro. Agotados los reintentos → `error` definitivo.

**Criterios de aceptación:**
- [ ] Job con `max_retries: 3` que siempre falla pasa 3 veces por `pending` y termina en `error`.
- [ ] Intervalos 30s → 60s → 120s verificados en test (tiempo acelerado o inyectado).
- [ ] `max_retries: 0` → `error` directo.
- [ ] El claim no entrega jobs con `retry_after > now()`.

**Resultado esperado:** Fallos transitorios se recuperan solos; fallos permanentes terminan en `error` sin loops infinitos.

---

## T1.13 — Webhook dispatcher

**Tipo:** Seguridad · **Esfuerzo:** 3 h · **Dependencias:** T1.9, T1.10, T1.12

**Descripción:** Al pasar un job a `done`, `error` o `cancelled`, si tiene `webhook_url`, disparar POST con el estado final en una goroutine. Re-validar SSRF en el momento del envío (la IP se resuelve al conectar, no solo al crear el job): solo `https://`, bloqueo de RFC 1918, localhost y `100.64.0.0/10`. Reintentos con backoff (3 intentos), timeout 10s. Un webhook fallido nunca afecta el estado del job.

**Criterios de aceptación:**
- [ ] Webhook a URL válida recibe POST con el estado final.
- [ ] URL cuya resolución DNS apunta a IP privada o Tailscale → no se conecta (test del validador).
- [ ] Servidor destino caído → 3 reintentos con backoff y el job queda `done` igualmente.
- [ ] El dispatcher no bloquea el request de `complete`.

**Resultado esperado:** Notificaciones push a las apps sin que la plataforma pueda ser usada para alcanzar la red interna.

---

## T1.14 — Worker simulado

**Tipo:** Desarrollo · **Esfuerzo:** 2 h · **Dependencias:** T1.7, T1.9

**Descripción:** Worker simulado (Go o Python, sin GPU ni modelos): register → claim loop → sleep(N) → progress → complete, con modos de fallo configurables (fallar siempre, dejar de latir, completar tarde). Es la herramienta de prueba del ciclo completo y de los tests de la T1.15.

**Criterios de aceptación:**
- [ ] Ciclo end-to-end manual: create job → claim → progress → complete → `done` consultable en cada paso.
- [ ] Modo "dejar de latir" y modo "fallar siempre" funcionan por flag.
- [ ] Se pueden lanzar N instancias en paralelo contra la misma API.

**Resultado esperado:** Ciclo de vida completo demostrable sin GPU, ejecutable en CI.

---

## T1.15 — Tests de integración + test de carrera del ledger

**Tipo:** DevOps · **Esfuerzo:** 4 h · **Dependencias:** T1.11, T1.12, T1.13, T1.14

**Descripción:** Suite de integración contra PostgreSQL real (testcontainers o compose de test — **no** mocks de DB: SKIP LOCKED y el ledger solo se prueban con Postgres de verdad), integrada en la CI de T0.13. Incluye **el test de carrera del ledger, el test más importante de la plataforma**: N workers concurrentes, 1 GPU simulada, jobs que suman más VRAM que la disponible → `vram_reserved_mb` nunca excede `vram_total_mb`. Más un test por cada camino de liberación de VRAM (complete, error, cancel, heartbeat timeout).

**Criterios de aceptación:**
- [ ] 10 workers simulados compitiendo con 1 GPU y jobs que suman más VRAM que la total: nunca se sobre-reserva (automatizado, en CI).
- [ ] Test por cada uno de los 4 caminos de liberación: la reserva vuelve a su valor previo.
- [ ] Worker sin heartbeat pierde su job en ≤90s; su `complete` posterior recibe 409 (automatizado).
- [ ] Job que falla 3 veces termina en `error` con la secuencia de backoff correcta.
- [ ] La suite corre en CI en cada push.

**Resultado esperado:** Los 5 criterios de aceptación de la Fase 1 verificados de forma automatizada y repetible.

---

## Resumen

| # | Tarea | Tipo | Horas | Depende de | Estado |
|---|---|---|---|---|---|
| T1.1 | Migración inicial (todas las tablas) | Desarrollo | 3 | T0.9 | ✅ |
| T1.2 | Estructura Go + conexión a PostgreSQL | Desarrollo | 3 | T1.1 | ✅ |
| T1.3 | Middleware de autenticación (3 roles) | Seguridad | 3 | T1.2 | ✅ |
| T1.4 | POST /ai/jobs + GET + SSRF + NOTIFY | Desarrollo | 4 | T1.3 | ✅ |
| T1.5 | Register + heartbeat de workers | Desarrollo | 3 | T1.3 | ✅ |
| T1.6 | Claim SKIP LOCKED + reserva atómica VRAM | Desarrollo | 4 | T1.4, T1.5 | ✅ |
| T1.7 | Long-polling del claim (LISTEN/NOTIFY) | Desarrollo | 3 | T1.6 | ✅ |
| T1.8 | releaseVRAMReservation + endpoint descarga modelo | Desarrollo | 3 | T1.6 | ✅ |
| T1.9 | Progress + complete con fencing | Desarrollo | 4 | T1.8 | ✅ |
| T1.10 | Cancel | Desarrollo | 2 | T1.9 | ✅ |
| T1.11 | Heartbeat monitor (goroutine) | Desarrollo | 4 | T1.8, T1.9 | ✅ |
| T1.12 | Reintentos con backoff exponencial | Desarrollo | 3 | T1.9, T1.10 | ✅ |
| T1.13 | Webhook dispatcher + SSRF | Seguridad | 3 | T1.9, T1.10, T1.12 | ✅ |
| T1.14 | Worker simulado | Desarrollo | 2 | T1.7, T1.9 | ✅ |
| T1.15 | Tests de integración + carrera del ledger en CI | DevOps | 4 | T1.11–T1.14 | ✅ |

**Total: ~48 h** — consistente con las 2 semanas estimadas en el plan.

---

## Criterios de cierre de F1 (del plan, verbatim)

- Un job creado por API es reclamado por un worker simulado, progresa y termina; el estado es consultable en cada paso.
- 10 workers simulados compitiendo por jobs que suman más VRAM que la GPU: nunca se sobre-reserva (test automatizado).
- Un worker simulado que deja de hacer heartbeat pierde su job en ≤90s; si luego reporta `complete`, recibe 409.
- Un job que falla se reintenta con backoff y termina en `error` tras `max_retries`.
- Webhook se dispara al completar; un `webhook_url` hacia IP privada es rechazado al crear el job.

---

## Advertencias de implementación (del plan)

- **El test de carrera del ledger (T1.15) es el test más valioso del proyecto.** Si se posterga, el bug aparecerá en F6 como OOM intermitente imposible de reproducir. Va en CI desde el primer día.
- **`releaseVRAMReservation()` se llama desde cuatro caminos** (complete, error, cancel, heartbeat timeout): una función, no cuatro copias, y un test por camino.
- **No diseñar de más la API:** solo los endpoints listados; nada especulativo.
