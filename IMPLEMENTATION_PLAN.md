# AI Worker Platform — Plan de implementación

> Última actualización: 2026-06-10 · v1.2 · Basado en DESIGN.md v1.4 (arquitectura congelada)
>
> Cambios v1.1: Video Crack se integra de forma incremental (cada servicio se adopta apenas existe, en doble ejecución) en lugar de migrar todo al final; SDK mínimo como parte de la primera integración; Dashboard v1 recortado a lo operativo esencial.

## Resumen ejecutivo

- **Adopción incremental**: Video Crack no espera al final — consume transcripción en producción (doble ejecución) desde la Fase 4.5, traducción desde la F6 y TTS desde la F7. La antigua "fase de migración" desaparece como bloque y se reparte.
- **Primer valor real en producción**: final de la Fase 4.5 (~semana 6) — Video Crack transcribe vía plataforma con un SDK mínimo validado por un consumidor real.
- **MVP = Fases 0–7** (incluye 4.5): API + Node Agent + whisper + ollama + tts + archivos + dashboard operativo mínimo + Video Crack migrado por completo.
- **Postergable sin riesgo**: API workers externos, workers bajo demanda (ComfyUI/video), dashboard v2 (incl. SSE), priority escalation, GPU AMD.
- **Duración estimada del MVP**: ~9–10 semanas (1 desarrollador), con valor en producción desde la semana ~6. Plataforma completa: ~13–15 semanas.

```
F0 Fundaciones → F1 API+DB → F2 Node Agent → F3 Worker scaffold → F4 Whisper
   → F4.5 SDK mínimo + Video Crack transcribe en producción (doble ejecución)
   → F5 Dashboard mínimo (~3 días, solapable con F4.5)
   → F6 Ollama  → Video Crack adopta traducción
   → F7 TTS    → migración completa, se apaga el sistema viejo  🏁 MVP
   (post-MVP)
   → F9 API workers + budgets → F10 Bajo demanda → F11 Dashboard v2
```

---

## Fase 0 — Fundaciones e infraestructura ✅ completada 2026-06-09

**Duración:** 3–5 días · **Complejidad: Baja**

### Objetivo
Tener el terreno listo: repos, red, base de datos vacía y pipelines de despliegue. Nada de lógica de negocio.

### Componentes
- Monorepo (`api/` Go, `workers/` Python, `agent/`, `dashboard/`, `migrations/`, `deploy/`)
- PostgreSQL en el VPS (Docker Compose) con backups básicos (`pg_dump` diario + cron)
- Tailscale operativo en VPS e ialab, con ACLs (ialab solo accesible desde el VPS)
- Tooling de migraciones (golang-migrate o similar)
- CI mínima: build Go, lint Python, build dashboard

### Tareas técnicas
1. Crear monorepo con estructura y Makefile/justfile de comandos comunes.
2. Compose del VPS: PostgreSQL + placeholder de la API.
3. Validar Tailscale bidireccional VPS ↔ ialab (ping, curl a un puerto de prueba).
4. Script de despliegue al VPS (rsync/ssh o git pull + compose up — sin sobreingeniería).
5. Validar Docker + GPU en ialab (`docker run --gpus all ... nvidia-smi`) — ya probado según DESIGN, dejar el comando en un script de verificación.

### Dependencias previas
Ninguna.

### Criterios de aceptación
- `make deploy` deja PostgreSQL corriendo en el VPS.
- Desde el VPS se alcanza un servicio HTTP de prueba en ialab vía Tailscale.
- ialab no responde desde internet público.
- Una migración de prueba aplica y revierte.

### Estrategia de pruebas
Verificación manual con checklist + script `verify-infra.sh` que prueba conectividad, GPU y DB.

### Riesgos
- ACLs de Tailscale mal configuradas (ialab expuesto a más nodos de los necesarios). Mitigación: revisar ACLs como tarea explícita, no como default.

---

## Fase 1 — Schema PostgreSQL + API Go (núcleo) ✅ completada 2026-06-10

**Duración:** 2 semanas · **Complejidad: Alta** (es el contrato de todo el sistema)

### Objetivo
API Go funcional con el ciclo de vida completo de un job en base de datos, incluyendo claim con SKIP LOCKED, ledger de VRAM y fencing. Sin workers reales todavía — se prueba con workers simulados.

### Componentes
- Migraciones: `jobs`, `workers`, `gpus`, `job_logs`, `job_files`, `worker_metrics`, `apps` (API keys + `max_daily_usd`)
- API Go:
  - `POST /ai/jobs` (validación de payload, mapeo priority string→int, inferencia de requirements por service)
  - `GET /ai/jobs/{id}`
  - `POST /workers/register` · `POST /workers/{id}/heartbeat`
  - `POST /workers/{id}/claim` — SKIP LOCKED + filtro por capabilities + **reserva atómica de VRAM** en `gpus`
  - `PATCH /ai/jobs/{id}/progress` y `/complete` — **con fencing** (`WHERE worker_id = $reporter`, 409 si no coincide)
  - `POST /ai/jobs/{id}/cancel`
- Heartbeat monitor (goroutine): timeout 60s, tick 10s → jobs `running` vuelven a `pending`, libera reserva de VRAM, worker → `offline`
- Reintentos con backoff exponencial (30s → 60s → 120s)
- LISTEN/NOTIFY: `NOTIFY jobs_channel` al crear job; el endpoint de claim soporta long-polling o los workers escuchan vía la API
- Autenticación: middleware `X-App-Key` / API key de worker / API key de admin
- Webhook dispatcher con validación SSRF (solo `https://`, bloqueo de RFC 1918 y `100.64.0.0/10`)

### Tareas técnicas
1. Migración inicial con todas las tablas de DESIGN.md.
2. Claim transaccional: SKIP LOCKED + UPDATE condicional del ledger en la misma transacción.
3. Liberación de reserva VRAM en complete/error/timeout (y endpoint para liberar al descargar modelo).
4. Tests de concurrencia del claim (N clientes simultáneos, una sola GPU simulada).
5. Worker simulado en Go o Python (claim, sleep, complete) para probar el ciclo completo.
6. Webhook dispatcher con reintentos y validación de URL.

### Dependencias previas
Fase 0.

### Criterios de aceptación
- Un job creado por API es reclamado por un worker simulado, progresa y termina; el estado es consultable en cada paso.
- 10 workers simulados compitiendo por jobs que suman más VRAM que la GPU: nunca se sobre-reserva (test automatizado).
- Un worker simulado que deja de hacer heartbeat pierde su job en ≤70s; si luego reporta `complete`, recibe 409.
- Un job que falla se reintenta con backoff y termina en `error` tras `max_retries`.
- Webhook se dispara al completar; un `webhook_url` hacia IP privada es rechazado al crear el job.

### Estrategia de pruebas
- Tests de integración Go contra PostgreSQL real (testcontainers o compose de test) — **no** mocks de DB: SKIP LOCKED y el ledger solo se prueban con Postgres de verdad.
- Test de carrera dedicado para el ledger (el test más importante de toda la plataforma).
- Tests unitarios para validación de payload, mapeos y SSRF.

### Riesgos
- **Sutilezas del ledger** (liberar reserva en todos los caminos: complete, error, cancel, timeout de heartbeat). Mitigación: una sola función `releaseReservation()` llamada desde todos los caminos + test por cada camino.
- Diseñar de más la API. Mitigación: implementar solo los endpoints listados; nada especulativo.

---

## Fase 2 — Node Agent

**Duración:** 1 semana · **Complejidad: Media**

### Objetivo
Métricas reales de la GPU fluyendo al VPS cada 10s. Base de datos de verdad para el dashboard y verificación del ledger.

### Componentes
- Node Agent en ialab (Python + pynvml, o Go — decidir por afinidad; corre fuera de Docker, systemd)
- Reporte cada 10s: GPU util, VRAM total/usada/libre, temperatura, watts, CPU, RAM
- Endpoint local `/metrics` consultable por los workers
- Registro del nodo y de la(s) GPU(s) en la tabla `gpus` al arrancar
- Job de retención de `worker_metrics` (agregación horaria a los 7 días) en el VPS

### Tareas técnicas
1. Lectura de métricas con pynvml + psutil.
2. Loop de reporte con reintentos (si el VPS no responde, no crashea — acumula y reintenta).
3. Unidad systemd con restart automático.
4. Endpoint de ingesta en la API + escritura en `worker_metrics`.
5. Alerta de deriva: si `vram_free_mb` real contradice el ledger por más de un margen, log warning (insumo para depurar la Fase 1).

### Dependencias previas
Fase 1 (endpoint de ingesta).

### Criterios de aceptación
- `worker_metrics` recibe filas cada 10s con valores plausibles contra `nvidia-smi`.
- Reinicio de ialab → el agente vuelve solo y sigue reportando.
- Caída del VPS de 5 min → el agente se recupera sin intervención.

### Estrategia de pruebas
Manual contra hardware real + test del parser de métricas. Probar el camino de error desconectando la red.

### Riesgos
- Bajo. Es el componente más simple; por eso va temprano (igual que en DESIGN.md).

---

## Fase 3 — Worker base Python (scaffold)

**Duración:** 1 semana · **Complejidad: Media**

### Objetivo
Librería/template de worker que resuelve todo lo común una sola vez: registro, claim loop, heartbeat en thread separado, manejo de fencing, reporte de progreso/logs/archivos. Los workers reales solo implementan `execute(job) -> result`.

### Componentes
- Paquete Python `worker_base` (Python 3.12 + uv):
  - Registro con capabilities al arrancar
  - **Heartbeat en thread separado** del loop de inferencia
  - Claim loop con long-poll/backoff
  - Manejo de 409 (fencing): descartar resultado y loggear
  - Helpers: `report_progress()`, `log()`, `upload_file_metadata()`
  - Graceful shutdown (SIGTERM: terminar job en curso o devolverlo a pending)
- Dockerfile base + entrada en Docker Compose de ialab
- Un worker de ejemplo (`worker-echo`) que usa el scaffold

### Dependencias previas
Fase 1.

### Criterios de aceptación
- `worker-echo` en Docker en ialab procesa jobs creados por API end-to-end a través de Tailscale.
- Matar el contenedor mid-job → el job vuelve a `pending` en ≤70s y otro worker lo toma.
- `docker compose restart` del worker → se re-registra solo.
- El heartbeat sigue llegando mientras `execute()` está bloqueado (test con sleep largo).

### Estrategia de pruebas
Tests unitarios del scaffold con API mockeada + prueba de integración real VPS↔ialab (primer test del sistema completo en red real).

### Riesgos
- Abstraer de más el scaffold antes de tener workers reales. Mitigación: escribir solo lo que `worker-echo` necesita; refactorizar cuando whisper y ollama revelen lo que falta.

---

## Fase 4 — worker-whisper + archivos (primer valor real) 🎯

**Duración:** 1.5–2 semanas · **Complejidad: Alta**

### Objetivo
Transcripción real end-to-end: el servicio principal de Video Crack. Valida el contrato completo con un modelo pesado (10 GB VRAM) y jobs largos.

### Componentes
- `worker-whisper` (faster-whisper, CUDA, concurrency 1, sobre el scaffold)
- Gestión de modelo: cargar al primer job, descargar tras 10 min de inactividad (liberando la reserva del ledger)
- Almacenamiento de archivos en ialab: `files/{job_id}/...`
- Servidor de archivos en ialab (HTTP simple en el puerto interno)
- Proxy en el VPS: `GET /ai/jobs/{id}/files/{filename}` → ialab vía Tailscale; 503 claro si ialab está offline
- Descarga de `audio_url` del payload con validación SSRF y límite de tamaño

### Tareas técnicas
1. Worker whisper: descarga de audio, transcripción con progreso real (% por segmentos), salida VTT/SRT/JSON.
2. Registro de archivos en `job_files` + servidor de archivos.
3. Proxy de archivos en la API Go (streaming, no buffering en memoria — los archivos pueden ser grandes).
4. Probar con audio largo real (1–2 h): progreso, timeout de heartbeat que NO se dispara, memoria estable.
5. Definir timeout máximo por job (configurable por service) — los videos largos de Video Crack lo van a necesitar.

### Dependencias previas
Fases 2 y 3.

### Criterios de aceptación
- `POST /ai/jobs` con `service: transcription` y un audio de 1 hora → `done` con VTT descargable vía el proxy.
- El progreso reportado avanza de forma realista durante todo el job.
- Dos jobs de transcripción encolados → se procesan en serie (concurrency 1, ledger respeta VRAM).
- Apagar ialab mid-job y reencenderlo → el job se reprocesa solo, sin intervención.
- Pedir un archivo con ialab apagado → 503 con mensaje claro.

### Estrategia de pruebas
Suite de audios de referencia (corto, 1 h, multiidioma, audio corrupto). Test de caos manual: apagar ialab, matar el worker, cortar Tailscale.

### Riesgos
- **Jobs muy largos** (videos de horas): heartbeat, timeouts y reintentos pensados para jobs cortos pueden interactuar mal. Mitigación: el criterio de aceptación de 1 h es obligatorio, no opcional.
- VRAM de whisper varía según modelo (large-v3 vs medium). Mitigación: medir y fijar `min_vram_mb` real por modelo, no el valor teórico.

---

## Fase 4.5 — SDK mínimo + Video Crack transcribe en producción 🎯

**Duración:** 1 semana · **Complejidad: Media**

### Objetivo
El primer consumidor real entra en producción con el primer servicio real. A partir de aquí, todo requisito oculto de Video Crack (videos largos, formatos, operación) se descubre con un solo servicio migrado, no con tres.

### Componentes
- **SDK Python mínimo** (`client/` en el monorepo, versionado junto a la API):
  - `create_job(service, payload, ...)`
  - `wait(job_id)` — polling con backoff, soporte de timeout
  - `download(job_id, filename, dest)`
  - Nada más. Sin abstracciones especulativas — crece solo cuando un consumidor real lo pida.
- Integración en Video Crack: el flujo de transcripción usa el SDK, en **doble ejecución** (el sistema viejo sigue corriendo y se comparan salidas)
- API key de app para Video Crack
- Documentación del contrato con ejemplos reales (curl + SDK) — es el onboarding de futuras apps

### Tareas técnicas
1. SDK con los tres métodos, tests contra la API real de staging.
2. Adaptar el flujo de transcripción de Video Crack al SDK detrás de un flag (viejo/plataforma/ambos).
3. Comparador de salidas (transcript viejo vs plataforma) para el período de doble ejecución.
4. Procesar el corpus real: el video más largo y pesado del catálogo incluido.

### Dependencias previas
Fase 4.

### Criterios de aceptación
- Video Crack transcribe en producción vía plataforma, con el sistema viejo como respaldo activo.
- El video más largo del catálogo real procesa sin intervención manual.
- Un reinicio de ialab mid-job se recupera solo y Video Crack recibe su resultado.
- El SDK no contiene ningún método que Video Crack no use hoy.

### Estrategia de pruebas
Doble ejecución con comparación de salidas sobre el corpus real. Tests del SDK contra la API (no mocks).

### Riesgos
- **Acoplamiento temprano**: Video Crack en producción desde ya aumenta la presión por features a medida. Mitigación: regla fija — lo específico de la app vive en la app; la plataforma solo gana endpoints genéricos. Este riesgo se acepta a cambio de descubrir los requisitos reales 4–5 semanas antes.
- Operar producción sin dashboard durante unos días. Mitigación: F5 es solapable con esta fase y dura ~3 días; mientras tanto, queries SQL preparadas en un runbook.

---

## Fase 5 — Dashboard mínimo

**Duración:** ~3 días (solapable con F4.5) · **Complejidad: Baja**

### Objetivo
Lo mínimo para operar producción sin `psql`: ver estados y actuar sobre jobs. Nada más — los dashboards son agujeros negros de tiempo y Video Crack no necesita métricas para generar valor.

### Componentes
- React + Vite servido desde el VPS, autenticado con API key de admin
- Una vista: workers (online/offline/busy, job en curso) + jobs por estado (pending/running/done/error)
- Detalle básico de job: payload, error, worker asignado
- **Acciones: cancelar job pendiente, reintentar job en error** — se mantienen porque sin ellas operar significa hacer `UPDATE jobs SET ...` en producción
- Actualización por **polling cada 5s** (sin SSE — el tiempo real se va a F11 junto con el streaming de logs)

### Explícitamente fuera (→ F11)
SSE, gráficas de GPU/VRAM, filtros avanzados, métricas históricas, vista de costos.

### Dependencias previas
Fases 1–2. Solapable con F4.5.

### Criterios de aceptación
- Workers offline se distinguen en <90s sin refrescar manualmente.
- Cancelar y reintentar funcionan desde la UI.
- Se puede diagnosticar un job fallido (ver error y payload) sin tocar la base de datos.

### Estrategia de pruebas
Manual contra el sistema vivo.

### Riesgos
- Scope creep. Mitigación: la lista "explícitamente fuera" es parte del entregable — todo lo que esté ahí se rechaza hasta F11.

---

## Fase 6 — worker-ollama (traducción + llm_chat) → Video Crack adopta traducción

**Duración:** 1 semana · **Complejidad: Media**

### Objetivo
Segundo servicio de Video Crack: traducción local. Valida convivencia de dos modelos en la misma GPU vía ledger. Al final de la fase, Video Crack lo consume en producción (mismo patrón de F4.5: flag + doble ejecución + comparación de salidas).

### Componentes
- `worker-ollama` sobre el scaffold: services `translation` y `llm_chat` (~4 GB VRAM)
- Ollama en ialab (contenedor propio o junto al worker)
- Prompts de traducción por par de idiomas, resultado en `jobs.result` (sin archivos)
- Carga/descarga de modelo coordinada con el ledger

### Dependencias previas
Fases 3–4.

### Criterios de aceptación
- Job de traducción ES→EN devuelve resultado correcto en `jobs.result`.
- **Whisper y Ollama procesando a la vez**: 10 GB + 4 GB caben en ~14.6 GB; el ledger lo permite y no hay OOM (primer test real de convivencia).
- Encolar un tercer servicio que NO cabe → espera hasta que se libere VRAM.
- Video Crack traduce subtítulos reales vía plataforma en doble ejecución, con salidas comparadas contra el sistema viejo.

### Estrategia de pruebas
Set de textos de referencia + test de convivencia de modelos (el test clave de esta fase). Validar calidad de traducción con subtítulos reales de Video Crack.

### Riesgos
- Calidad de traducción del modelo local insuficiente para Video Crack. Mitigación: evaluar temprano con contenido real; si no alcanza, la Fase 9 (fallback externo) sube de prioridad.

---

## Fase 7 — worker-tts → migración completa de Video Crack

**Duración:** 1.5–2 semanas · **Complejidad: Media-Alta**

### Objetivo
Tercer y último servicio: síntesis de voz. Video Crack adopta TTS, completa su pipeline en plataforma y **se apaga el sistema viejo** — el criterio de éxito de todo el proyecto.

### Componentes
- `worker-tts` (XTTS o Higgs — decidir con una prueba de calidad/VRAM al inicio de la fase) sobre el scaffold
- Salida de audio como archivos (reusa `job_files` + proxy de la Fase 4)
- Concurrency 2 si la VRAM lo permite (~2 GB por instancia)
- Pipeline completo en Video Crack: transcripción → traducción → TTS encadenados con `workflow_id` (las dependencias las orquesta la app, según DESIGN.md)
- Webhooks como mecanismo principal de notificación, polling del SDK como fallback

### Tareas técnicas
1. Timebox de 2 días para elegir motor TTS con prueba real de calidad/VRAM.
2. Worker TTS sobre el scaffold, salida vía `job_files`.
3. Video Crack adopta TTS (flag + doble ejecución, mismo patrón de F4.5 y F6).
4. Validación del pipeline completo sobre el corpus real → switch definitivo → apagar el sistema viejo.

### Dependencias previas
Fases 4.5 y 6 (Video Crack ya consume transcripción y traducción).

### Criterios de aceptación
- Job de TTS con texto largo → audio descargable vía proxy.
- TTS + whisper + ollama conviven en GPU sin OOM bajo el ledger.
- Un video real procesa su pipeline completo vía plataforma sin intervención manual; un reinicio de ialab mid-pipeline se recupera solo.
- El sistema viejo de Video Crack está apagado.

### Estrategia de pruebas
Textos de referencia (corto, largo, números/siglas) y escucha manual de calidad. Test de los tres workers activos simultáneamente. Corpus completo de videos reales antes del switch.

### Riesgos
- XTTS/Higgs tienen instalación más frágil que whisper/ollama. Mitigación: timebox de 2 días; si ambos pelean, empezar con el que funcione y mejorar después.
- Tentación de apagar el sistema viejo antes de validar el corpus completo. Mitigación: el switch es el último criterio de aceptación, no el primero.

> **🏁 Fin del MVP.** Video Crack opera 100% sobre la plataforma. Las fases 9–11 son mejoras, no requisitos.

---

## Fase 9 — API workers externos + budgets (post-MVP)

**Duración:** 1.5 semanas · **Complejidad: Media-Alta**

### Objetivo
Fallback a Anthropic/OpenRouter cuando ialab está offline o saturado, con presupuesto preventivo.

### Componentes
- `api-worker-anthropic` y `api-worker-openrouter` en el VPS (scaffold reusado, sin GPU, concurrency 20)
- Routing completo: `prefer` / `allow_external` / `provider` en el claim
- Tracking de `cost_usd` y `provider_used` por job
- **Budgets preventivos**: `max_daily_usd` (y mensual) por app, verificado *antes* de llamar al provider; excedido → job a `pending` o `error` según routing
- Vista de costos básica en el dashboard

### Dependencias previas
Fases 1, 3, 6 (para comparar local vs externo en translation/llm_chat).

### Criterios de aceptación
- Con ialab apagado y `allow_external: true`, un job de traducción se resuelve vía Anthropic con costo registrado.
- Con `allow_external: false` (default), el job espera — nunca gasta sin permiso explícito.
- Una app que excede su budget diario deja de generar gasto inmediatamente (test con budget de $0.01).
- `routing.provider: "anthropic"` fuerza el provider correcto.

### Estrategia de pruebas
Tests de routing con providers mockeados + smoke test real con crédito mínimo. Test específico del cap con presupuesto ínfimo.

### Riesgos
- Cálculo de costos impreciso por provider/modelo. Mitigación: tabla de precios versionada en código y conciliación mensual contra la factura real.
- Carrera en la verificación de budget bajo concurrencia 20. Mitigación: misma técnica que el ledger de VRAM (UPDATE condicional atómico).

---

## Fase 10 — Workers bajo demanda (post-MVP)

**Duración:** 1.5–2 semanas · **Complejidad: Alta**

### Objetivo
ComfyUI (imagen) y video generation: workers que arrancan al llegar trabajo y se apagan al terminar, gestionados por el Node Agent (modelo pull, según DESIGN.md v1.4).

### Componentes
- Extensión del Node Agent: observa la cola vía API y hace `docker compose up/down` de workers bajo demanda
- `worker-comfyui` (image_generation, image_compose) y `worker-video` (LTXV)
- Política de apagado por inactividad + interacción con el ledger (estos workers exigen 11–15 GB: casi siempre requieren que los permanentes descarguen modelo)

### Dependencias previas
Fases 2, 3, y MVP estable (no antes — es la fase con más fricción operativa).

### Criterios de aceptación
- Job de imagen con todos los workers bajo demanda apagados → el Node Agent levanta ComfyUI, el job procesa, el worker se apaga tras la inactividad configurada.
- Job de video (14 GB) encolado mientras whisper trabaja → espera a que el ledger libere; nunca OOM.
- Crash de ComfyUI mid-job → reintento limpio.

### Estrategia de pruebas
Workflows de ComfyUI de referencia. Test del ciclo arranque→job→apagado repetido 10 veces seguidas (fugas de VRAM/disco).

### Riesgos
- ComfyUI es la pieza más frágil del stack (custom nodes, versiones de modelos). Mitigación: imagen Docker congelada con hashes de modelos fijados.
- Deadlock de VRAM (bajo demanda espera a permanentes que no descargan). Mitigación: el claim de jobs grandes dispara descarga proactiva de modelos inactivos.

---

## Fase 11 — Dashboard v2 + operación (post-MVP)

**Duración:** 1–1.5 semanas · **Complejidad: Media**

### Objetivo
De "funciona" a "se opera con datos": métricas históricas, costos detallados, alertas.

### Componentes
- **SSE para todo el dashboard** (`GET /events/stream`) — reemplaza el polling de 5s de F5
- Métricas históricas: jobs/hora por service, tiempos promedio, tasa de errores por app, VRAM en el tiempo, % local vs externo
- Vista de costos completa: por día/app/service/provider, comparativa local vs API
- Alertas de gasto configurables
- Logs de job en tiempo real vía SSE (`GET /ai/jobs/{id}/logs/stream`)
- Gráficas de GPU/VRAM y filtros avanzados de jobs (recortados de F5)
- Priority escalation (jobs `pending` > N min suben de prioridad)

### Dependencias previas
Fases 5 y 9 (datos de costos reales).

### Criterios de aceptación
- La pregunta "¿conviene ampliar ialab?" se responde con la comparativa local vs externo del dashboard.
- Una alerta de gasto dispara con datos reales.
- Los logs de un job `running` se ven en vivo.

### Estrategia de pruebas
Validación de agregaciones contra queries SQL manuales. Resto, manual.

### Riesgos
- Bajo. Todo es aditivo sobre datos que ya existen.

---

## MVP y postergables

| | Contenido |
|---|---|
| **MVP (F0–F7, incl. 4.5)** | API + ledger + fencing, Node Agent, scaffold, SDK mínimo, whisper, ollama, tts, archivos, dashboard mínimo, Video Crack migrado por completo |
| **Postergable** | API workers externos y budgets (F9) — *salvo que la traducción local no dé la calidad*, workers bajo demanda (F10), dashboard v2 con SSE y alertas (F11), priority escalation, GPU AMD/ROCm, multi-lab |
| **No postergable aunque tiente** | Ledger de VRAM y fencing (F1) — retrofitearlos después obliga a reescribir el claim y todos los workers |

## Duración total estimada (1 desarrollador)

| Tramo | Semanas | Hito |
|---|---|---|
| F0–F4 | ~5–6 | Whisper end-to-end |
| F4.5 + F5 (solapadas) | +1 | **Video Crack transcribe en producción** (~semana 6) |
| F6–F7 | +2.5–3 | Migración completa, sistema viejo apagado |
| F9–F11 (post-MVP) | +4–5 | Fallback externo, bajo demanda, dashboard v2 |
| **Total** | **~13–15** | |

F5 se solapa con F4.5; F5 y F6 son paralelizables si hubiera una segunda persona.

## Recomendaciones para evitar bloqueos

1. **El test de carrera del ledger (F1) es el test más valioso del proyecto.** Si se posterga "para después", el bug aparecerá en F6 como OOM intermitente imposible de reproducir.
2. **No avanzar de fase con criterios de aceptación en rojo.** Cada fase es desplegable; un criterio fallido es deuda que la siguiente fase amplifica.
3. **Probar con jobs largos desde la Fase 4.** El caso de uso real de Video Crack son videos de horas; todo lo que funciona con clips de 30s y falla a la hora (heartbeats, timeouts, disco) debe descubrirse en F4, no con el cliente en producción.
4. **Timeboxear las decisiones de modelo** (motor TTS, modelo de traducción): 2 días máximo, decidir con una prueba real y seguir. Son reemplazables después; el contrato del worker no cambia.
5. **Workers simulados antes que workers reales** (F1): permiten probar concurrencia, fencing y reintentos sin GPU ni modelos, en CI.
6. **Doble ejecución en cada adopción** (F4.5, F6, F7): el sistema viejo de Video Crack corre en paralelo hasta validar salidas de cada servicio. Cada switch es un cambio de flag, no un salto al vacío — y el apagado definitivo del sistema viejo es el último criterio de F7, no el primero.
7. **El SDK crece por demanda, no por anticipación.** Tres métodos hasta que un consumidor real necesite el cuarto. Un SDK gordo antes de tener segundo cliente es sobreingeniería con interfaz bonita.
8. **No tocar F10 hasta que el MVP lleve semanas estable.** Los workers bajo demanda son la mayor fuente de fricción operativa y Video Crack no los necesita.
9. **Congelar versiones desde el día uno** (imágenes Docker, modelos con hash, dependencias con lock). En un stack de IA, "se actualizó solo" es la causa #1 de viernes perdidos.
