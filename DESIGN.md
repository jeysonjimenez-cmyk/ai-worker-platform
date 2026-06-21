# AI Worker Platform — Diseño

> Última actualización: 2026-06-09 · v1.4

## Objetivo

Plataforma de orquestación de tareas de IA donde múltiples aplicaciones solicitan servicios a través de una API unificada. Las tareas entran a una cola centralizada y el scheduler las asigna a workers según capacidades de hardware, disponibilidad de recursos y prioridad.

La plataforma actúa como **router de IA**: elige automáticamente entre workers locales (GPU propia, costo cero) y workers externos (Anthropic, OpenRouter, OpenAI) según disponibilidad, prioridad y política de routing de cada job.

Infraestructura: VPS público + IA Lab local (Tailscale) + APIs externas.

---

## Infraestructura

```
Internet → VPS (API Gateway + PostgreSQL + Dashboard + API Workers externos)
                ↕ Tailscale                    ↕ HTTPS
           ialab (GPU Workers)          Anthropic / OpenRouter / OpenAI
```

**VPS — siempre activo:**
- API Go: recibe jobs, gestiona estado, autentica, sirve resultados
- PostgreSQL: jobs, workers, resultados, costos, sesiones
- Proxy de archivos: `/files/{job_id}/...` → ialab vía Tailscale
- Dashboard web: estado de workers, cola, métricas, costos en tiempo real
- Frontend React + Vite por app cliente
- **API Workers externos**: procesos ligeros en el VPS que llaman a Anthropic, OpenRouter, OpenAI

**ialab — puede apagarse:**
- GPU Workers Python en Docker, uno por tipo de tarea
- GPU(s): hoy RTX 4070 Ti Super 16 GB, próximamente AMD Radeon AI PRO R9700 (modelo por confirmar)
- Modelos en disco (no se descargan de internet en cada arranque)
- Cada worker arranca, se registra en el VPS, y jala jobs de la cola

**APIs externas — siempre disponibles (con crédito):**
- Anthropic (Claude), OpenRouter (multi-modelo), OpenAI (GPT, embeddings)
- Usadas como fallback cuando ialab está offline o la cola local está saturada
- La app puede forzar un provider específico si lo necesita

**Conectividad:**
- ialab **nunca** se expone a internet directamente
- Todo pasa por Tailscale (VPS → ialab es llamada interna privada)
- ialab puede iniciar la conexión al VPS sin depender de IP fija

---

## Flujo de ejecución

```
App → POST /ai/jobs → PostgreSQL (pending)
                           ↑
                    Worker en ialab polling
                    "dame el próximo job que puedo atender"
                           ↓
                    Worker ejecuta modelo
                           ↓
                    PATCH /ai/jobs/{id}/progress  (streaming de progreso)
                           ↓
                    PATCH /ai/jobs/{id}/complete  (resultado + archivos)
                           ↓
                    App hace GET /ai/jobs/{id} → done
```

El worker **jala** trabajo del VPS en lugar de que el VPS lo empuje. Motivo: si ialab se apaga y vuelve, automáticamente retoma sin que el VPS sepa que estuvo offline.

---

## Schema de la API

### Crear job

```
POST /ai/jobs
```

```json
{
  "service": "transcription",
  "app": "video-crack",
  "priority": "normal",
  "payload": {
    "audio_url": "https://...",
    "language": "es",
    "translate_to": ["en"]
  },
  "requirements": {
    "min_vram_mb": 10000,
    "cuda": true
  },
  "routing": {
    "prefer": "local",
    "allow_external": false,
    "provider": null
  },
  "webhook_url": "https://mi-app.com/callbacks/job-done"
}
```

**`requirements`** es opcional. Si se omite, el scheduler infiere los requisitos según `service`.

**`priority`** acepta `"high"` | `"normal"` | `"low"` en la API, mapeado a los enteros de la tabla `jobs`: `high = 1`, `normal = 5`, `low = 10`.

**`routing`** es opcional. Valores por defecto: `prefer: local`, `allow_external: false`.

| Campo | Valores | Descripción |
|---|---|---|
| `prefer` | `local` \| `external` \| `any` | Qué tipo de worker preferir. `any` = el más rápido disponible |
| `allow_external` | `true` \| `false` | Permite usar APIs de pago si local no está disponible |
| `provider` | `"anthropic"` \| `"openrouter"` \| `"openai"` \| `null` | Fuerza un provider específico. `null` = scheduler decide |

Ejemplo — job que puede usar API externa como fallback si ialab está offline:
```json
{
  "service": "llm_chat",
  "payload": { "messages": [{"role": "user", "content": "Traduce esto..."}] },
  "routing": { "prefer": "local", "allow_external": true }
}
```

### Servicios disponibles

Los valores de VRAM son en MB y representan el mínimo requerido para ejecutar el job. La RTX 4070 Ti Super tiene 16376 MB totales pero ~14637 MB disponibles en uso normal (el resto lo consume el compositor del escritorio).

| service | provider local | min_vram_mb | CUDA | Worker | Provider externo |
|---|---|---|---|---|---|
| `transcription` | faster-whisper | 10000 | sí | permanente | — |
| `translation` | Ollama / llama.cpp | 4000 | no | permanente | OpenRouter, Anthropic |
| `llm_chat` | Ollama / llama.cpp | 4000 | no | permanente | Anthropic, OpenRouter, OpenAI |
| `tts` | XTTS / Higgs | 2000 | pref. | permanente | — |
| `image_generation` | ComfyUI FLUX | 15000 | sí | bajo demanda | — |
| `video_generation` | ComfyUI LTXV | 14000 | sí | bajo demanda | — |
| `lipsync` | ComfyUI InfiniteTalk | 13000 | sí | bajo demanda | — |
| `image_compose` | ComfyUI Kontext | 11000 | sí | bajo demanda | — |
| `image_understanding` | Ollama vision | 4000 | no | permanente | Anthropic (vision), OpenAI |
| `embeddings` | Ollama / local | 2000 | no | permanente | OpenAI, OpenRouter |

Los servicios sin provider externo (`—`) son capacidades exclusivamente locales — no tienen equivalente en APIs públicas o el costo no tiene sentido (ej. video generation en Replicate es mucho más caro que local).

### Consultar estado

```
GET /ai/jobs/{id}
```

```json
{
  "id": "abc123",
  "service": "transcription",
  "status": "running",
  "progress": 42,
  "worker": "worker-whisper-nvidia",
  "created_at": "...",
  "started_at": "...",
  "eta_sec": 30,
  "result": null
}
```

`status`: `pending` → `running` → `done` | `error` | `cancelled`

---

## Base de datos — PostgreSQL

### `workers`

```sql
id              TEXT PRIMARY KEY,   -- "worker-whisper-nvidia"
hostname        TEXT,               -- "ialab"
status          TEXT,               -- online | offline | busy
capabilities    JSONB,              -- ver abajo
current_job_id  TEXT,
last_heartbeat  TIMESTAMPTZ,
registered_at   TIMESTAMPTZ
```

`capabilities`:
```json
{
  "services": ["transcription", "tts"],
  "gpu": "nvidia",
  "gpu_name": "RTX 4070 Ti SUPER",
  "vram_total_mb": 16376,
  "cuda": true,
  "rocm": false,
  "max_concurrency": 1
}
```

`vram_total_mb` es el total del hardware. La VRAM libre real la reporta el Node Agent en cada heartbeat — el scheduler toma decisiones sobre `vram_free_mb`, no sobre `vram_total_mb`.

`max_concurrency` define cuántos jobs puede procesar el worker en paralelo. Los workers GPU-intensivos lo tienen en 1. Workers ligeros (embeddings, CPU) pueden tener 2–4.

### `jobs`

```sql
id              TEXT PRIMARY KEY,
app             TEXT,
service         TEXT,
priority        INT DEFAULT 5,       -- 1 alta, 5 normal, 10 baja
status          TEXT DEFAULT 'pending',
payload         JSONB,
requirements    JSONB,
routing         JSONB,               -- prefer, allow_external, provider
worker_id       TEXT,                -- id del worker que tomó el job
provider_used   TEXT,                -- "local/worker-llm" | "anthropic/claude-sonnet-4-6" | "openrouter/..."
progress        INT DEFAULT 0,
result          JSONB,
cost_usd        NUMERIC(10,6),       -- null para jobs locales, costo real para APIs externas
error_msg       TEXT,
webhook_url     TEXT,
workflow_id     TEXT,                -- agrupa jobs relacionados (para el dashboard)
retry_count     INT DEFAULT 0,
max_retries     INT DEFAULT 3,
retry_delay_sec INT DEFAULT 30,      -- espera entre reintentos (se duplica en cada intento)
created_at      TIMESTAMPTZ DEFAULT now(),
started_at      TIMESTAMPTZ,
completed_at    TIMESTAMPTZ
```

`workflow_id` agrupa jobs de un mismo pipeline (ej. transcripción + traducción de un mismo video). No implica dependencias automáticas — la app cliente las orquesta. El dashboard usa `workflow_id` para mostrar los jobs relacionados juntos.

`provider_used` registra exactamente quién ejecutó el job. Formato `"tipo/identificador"`: `"local/worker-llm-nvidia"` para jobs locales, `"anthropic/claude-sonnet-4-6"` para externos. Sirve para auditoría y métricas de costo.

### `job_logs`

```sql
id          BIGSERIAL PRIMARY KEY,
job_id      TEXT,
level       TEXT,    -- info | warn | error
message     TEXT,
created_at  TIMESTAMPTZ DEFAULT now()
```

Logs de ejecución por job, accesibles desde el dashboard en tiempo real.

### `job_files`

```sql
job_id      TEXT,
filename    TEXT,
path        TEXT,   -- ruta en ialab
size_bytes  BIGINT,
created_at  TIMESTAMPTZ
```

Los archivos viven en ialab. El VPS los proxea vía Tailscale cuando una app los pide.

### `worker_metrics`

```sql
worker_id       TEXT,
gpu_util_pct    INT,
vram_total_mb   INT,
vram_used_mb    INT,
vram_free_mb    INT,       -- campo clave para el scheduler
temperature_c   INT,
power_w         INT,
cpu_pct         INT,
ram_used_gb     FLOAT,
recorded_at     TIMESTAMPTZ DEFAULT now()
```

El Node Agent (ver sección Node Agent) reporta estas métricas cada 10s. `vram_free_mb` es el campo que el scheduler consulta para decidir si un job puede ejecutarse. Temperatura y watts son informativos — el dashboard los muestra y pueden usarse para throttling futuro.

**Retención:** a ~6 filas/min por worker esta tabla crece sin límite. Los registros con más de 7 días se agregan a promedios por hora (tabla `worker_metrics_hourly`) y se borran. Un job diario en el VPS lo gestiona.

### `gpus`

```sql
id               TEXT PRIMARY KEY,   -- "ialab/nvidia-0"
hostname         TEXT,
vram_total_mb    INT,
vram_reserved_mb INT DEFAULT 0       -- suma de reservas activas
```

Ledger de reservas de VRAM por GPU física. Ver "Reserva de VRAM" en la sección Scheduler.

---

## Cola — PostgreSQL SKIP LOCKED

Sin NATS ni Redis. PostgreSQL maneja la cola nativamente. Cuando llega un job nuevo, el VPS hace `NOTIFY jobs_channel` y los workers se despiertan inmediatamente en lugar de esperar el próximo tick de polling.

El claim filtra por `vram_free_mb` real (reportado por el Node Agent), no por VRAM total del hardware:

```sql
-- Worker pide su próximo job
SELECT * FROM jobs
WHERE status = 'pending'
  AND (requirements->>'cuda' IS NULL OR requirements->>'cuda' = 'true')
  AND (
    requirements->>'min_vram_mb' IS NULL
    OR (requirements->>'min_vram_mb')::int <= (
      SELECT vram_free_mb FROM worker_metrics
      WHERE worker_id = $worker_id
      ORDER BY recorded_at DESC LIMIT 1
    )
  )
ORDER BY priority ASC, created_at ASC
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Esto evita errores CUDA OOM: si la GPU tiene 14637 MB libres y el job requiere 15000 MB, el job espera en cola hasta que se libere VRAM.

El filtro por métrica **no es suficiente por sí solo** — la decisión final la toma la reserva atómica de VRAM (ver "Reserva de VRAM" en la sección Scheduler). La métrica del Node Agent es verificación y telemetría, no la fuente de verdad del claim.

---

## Reintentos

Cuando un job falla, el worker reporta el error al VPS. El VPS decide si reintentarlo o marcarlo como error definitivo:

```
Job falla
↓
retry_count < max_retries
↓ sí                          ↓ no
status = 'pending'            status = 'error'
retry_count++                 webhook con error
esperar retry_delay_sec       (no más reintentos)
(se duplica cada intento)
```

Backoff exponencial: primer reintento a los 30s, segundo a los 60s, tercero a los 120s. Evita que un modelo inestable sature la cola.

Casos donde **no** se reintenta automáticamente (max_retries = 0):
- Jobs cancelados por el usuario
- Errores de payload inválido (el reintento daría el mismo error)

La app puede sobreescribir `max_retries` al crear el job.

---

## Scheduler — distribuido en los workers

El scheduler no es un servicio separado. Cada worker conoce sus propias capacidades y solo toma jobs que puede atender. La decisión de routing es el query SKIP LOCKED filtrado por capabilities.

### Reserva de VRAM

El filtro por `vram_free_mb` no previene OOM por sí solo: varios workers comparten la misma GPU, la métrica tiene hasta 10s de lag, y dos claims simultáneos pueden ver ambos la misma VRAM "libre" (SKIP LOCKED bloquea filas de jobs, no coordina VRAM entre workers). Además, los workers permanentes suman más VRAM que la disponible si todos cargan modelo a la vez.

La fuente de verdad es un ledger de reservas en la tabla `gpus`. El claim de un job reserva atómicamente:

```sql
UPDATE gpus
SET vram_reserved_mb = vram_reserved_mb + $min_vram_mb
WHERE id = $gpu_id
  AND vram_reserved_mb + $min_vram_mb <= vram_total_mb - $margen_sistema
RETURNING id;
```

Si el UPDATE no retorna fila, no hay VRAM: el job sigue `pending`. La reserva se libera cuando el job termina **y** el worker descarga el modelo (un modelo que queda caliente en VRAM mantiene su reserva). `vram_free_mb` del Node Agent sirve para verificar que el ledger no se desvíe de la realidad y para el dashboard.

**Semántica por-job del ledger:** la reserva de VRAM se ata al ciclo de vida del *job*, no al del *modelo cargado*. Cuando un worker reclama un job, incrementa `vram_reserved_mb`; cuando el job completa (done/error/cancel) y el worker llama `POST /workers/{id}/unload-model`, decrementa. Un modelo que queda caliente entre jobs (sin idle-unload) mantiene su reserva en el ledger aunque `nvidia-smi` muestre la VRAM ocupada — esto es correcto: el ledger bloquea nuevos claims sobre esa VRAM hasta que el unload se notifica explícitamente. Consecuencia operativa: `vram_reserved_mb = 0` con modelo caliente en GPU es una deriva entre ledger y realidad; el diagnóstico es verificar que el worker haya llamado `unload-model` correctamente.

### Servicios centralizados en el VPS

- **Heartbeat monitor**: si un worker no hace heartbeat en 90s, sus jobs `running` vuelven a `pending` y el worker pasa a `offline`. El timeout es 90s (no 30s) para tolerar GPUs saturadas durante generación de video
- **Fencing**: `PATCH /progress` y `PATCH /complete` validan que `jobs.worker_id` siga siendo el worker que reporta. Si el job fue reasignado tras un timeout de heartbeat (ej. el worker perdió red 90s pero siguió ejecutando), el worker original recibe `409 Conflict` y descarta su resultado. Sin esto, una caída de red produce doble ejecución y doble costo
- **Priority escalation**: jobs que llevan más de N minutos en `pending` suben de prioridad automáticamente
- **Webhook dispatcher**: cuando un job termina, llama al `webhook_url` de la app
- **Métricas agregadas**: consolida `worker_metrics` para el dashboard

---

## Docker en ialab — validado

Probado con `docker run --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`:

- Docker detecta la GPU correctamente
- NVIDIA Container Toolkit funciona
- Rendimiento **prácticamente nativo** — Docker no virtualiza la GPU, usa el driver del host directamente
- CUDA disponible dentro de los contenedores con `--gpus all`

**Lo que Docker no controla:** no existe flag `--gpu-memory` equivalente a `--memory`. La plataforma gestiona VRAM a nivel del scheduler, no a nivel de Docker.

Los workers corren en Docker Compose con `--gpus all`. La VRAM disponible real de la RTX 4070 Ti Super en uso de escritorio normal: **~14637 MB** (de 16376 MB totales).

---

## Node Agent

Proceso ligero en ialab (fuera de Docker, para tener acceso directo a `nvidia-smi`). Se ejecuta como servicio systemd y reporta al VPS cada 10s.

**Responsabilidades:**
- Leer métricas de GPU vía `nvidia-smi` (o `pynvml`)
- Reportar CPU, RAM, temperatura, consumo eléctrico
- Exponer un endpoint local `/metrics` que los workers también pueden consultar
- **Gestionar los workers bajo demanda**: consulta la cola del VPS (vía la misma API, modelo pull) y hace `docker compose up / down` de los workers bajo demanda cuando hay jobs pendientes de esos servicios. El VPS nunca empuja comandos hacia ialab — el Node Agent decide localmente

**Payload de heartbeat:**
```json
{
  "worker_id": "worker-whisper-nvidia",
  "gpu_name": "RTX 4070 Ti SUPER",
  "gpu_util_pct": 0,
  "vram_total_mb": 16376,
  "vram_used_mb": 1739,
  "vram_free_mb": 14637,
  "temperature_c": 41,
  "power_w": 51,
  "cpu_pct": 18,
  "ram_used_gb": 12.3
}
```

El scheduler usa `vram_free_mb` de este payload — no `vram_total_mb` — para decidir si un job puede ejecutarse ahora o debe esperar.

---

## Workers

Cada worker es un contenedor Docker en ialab. Se registra al arrancar, hace heartbeat cada 10s, y jala jobs en loop.

El heartbeat corre en un **thread separado** del loop de inferencia. Un worker Python bloqueado minutos en una generación de video debe seguir reportando heartbeat — si comparten thread, todo job largo marca al worker como offline a los 90s y dispara reasignaciones falsas.

Hay dos estrategias según el costo de arranque y el uso de VRAM:

**Permanentes** — siempre corriendo, arranque inmediato, modelo puede quedar cargado en VRAM:
```
worker-whisper     → transcription             (CUDA, ~10000 MB, concurrency: 1)
worker-llm         → translation, llm_chat     (CUDA o ROCm,     concurrency: 1)
worker-tts         → tts                       (CUDA preferible, concurrency: 2)
worker-vision      → image_understanding       (CUDA o ROCm,     concurrency: 1)
worker-embeddings  → embeddings                (CPU o ROCm,      concurrency: 4)
```

**Bajo demanda** — se levantan cuando llega un job, se bajan al terminar, liberan VRAM completamente:
```
worker-comfyui     → image_generation, image_compose  (CUDA, ~11000–15000 MB, concurrency: 1)
worker-video       → video_generation                 (CUDA, ~14000 MB,       concurrency: 1)
worker-lipsync     → lipsync                          (CUDA, ~13000 MB,       concurrency: 1)
```

Los workers bajo demanda usan `docker compose up / down` gestionado por el **Node Agent** cuando detecta jobs pendientes de esos tipos en la cola (consultando al VPS, modelo pull — el VPS nunca inicia comandos hacia ialab).

### API Workers — externos

Procesos ligeros que corren en el **VPS** (no en ialab). No tienen GPU, no tienen VRAM. Son wrappers HTTP hacia APIs de terceros. Se registran igual que cualquier worker y compiten en la misma cola.

```
worker-anthropic   → llm_chat, translation, image_understanding  (Claude, concurrency: 20)
worker-openrouter  → llm_chat, translation, embeddings            (multi-modelo, concurrency: 20)
worker-openai      → llm_chat, embeddings                         (GPT, concurrency: 20)
```

`capabilities` de un API worker:
```json
{
  "services": ["llm_chat", "translation"],
  "type": "api",
  "provider": "anthropic",
  "models": ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"],
  "cuda": false,
  "rocm": false,
  "vram_total_mb": 0,
  "max_concurrency": 20
}
```

**Lógica de routing con API workers:**

```
Job llega con routing.prefer = "local"
↓
¿Hay worker local disponible con VRAM suficiente?
↓ sí                          ↓ no
Asignar a worker local        ¿routing.allow_external = true?
                              ↓ sí            ↓ no
                              API worker      Esperar en cola
                              (fallback)      hasta que haya local
```

```
Job llega con routing.prefer = "external"
↓
Asignar directamente a API worker (ignora workers locales)
```

```
Job llega con routing.provider = "anthropic"
↓
Asignar a worker-anthropic (forzado, sin evaluar local)
```

**Presupuesto por app (cap duro):** cada API key de app tiene un `max_daily_usd`. Antes de llamar al provider, el API worker verifica el gasto acumulado del día de esa app; si el job lo excedería, vuelve a `pending` (espera worker local) o pasa a `error` si `prefer: external`. Esto previene que un bug en una app cliente encole miles de jobs con `allow_external: true` y queme el crédito durante la noche — la alerta del dashboard avisa, el cap previene.

Con la GPU AMD (Radeon AI PRO R9700, modelo por confirmar):
- `worker-llm-amd` y `worker-vision-amd` corren en paralelo con la NVIDIA
- Jobs CUDA-only (video, lipsync, imagen) siguen yendo a la NVIDIA
- El campo `capabilities.cuda/rocm` hace el routing automáticamente
- **Pendiente de validar**: soporte ROCm real de las cargas previstas (Ollama sobre ROCm es maduro; faster-whisper y ComfyUI sobre ROCm requieren prueba antes de comprometer servicios)

### Gestión de modelos en el worker

```
Job llega → ¿modelo ya cargado? → ejecutar directo
                ↓ no
           ¿hay VRAM libre? → cargar modelo → ejecutar
                ↓ no
           descargar modelo menos usado recientemente → cargar → ejecutar
```

Inactividad > 10 min → descargar modelo, liberar VRAM (configurable por worker).

---

## Dashboard web

Aplicación React + Vite en el VPS. Actualizaciones en tiempo real vía **Server-Sent Events** (SSE) desde la API Go.

### Vistas

**Panel principal**

```
┌─────────────────────────────────────────────────┐
│  Workers locales                                │
│  ● worker-whisper-nvidia   RUNNING  42% GPU     │
│    transcription · job abc123 · 1m 20s          │
│  ● worker-llm-nvidia       IDLE     0% GPU      │
│  ○ worker-llm-amd          OFFLINE              │
│                                                 │
│  API Workers                                    │
│  ● worker-anthropic        IDLE  (online)       │
│  ● worker-openrouter       IDLE  (online)       │
│                                                 │
│  Cola                                           │
│  3 pending  1 running  142 done  2 error        │
│                                                 │
│  GPU                                            │
│  NVIDIA  ████████░░  78%  VRAM 12.1 / 16 GB    │
│  AMD     ──────────  offline                   │
│                                                 │
│  Costos hoy                                     │
│  Anthropic  $0.42  ·  OpenRouter  $0.18         │
└─────────────────────────────────────────────────┘
```

**Workers**

- Tabla con: id, hostname, status, GPU actual, VRAM usada/total, job en curso
- Al hacer clic: historial de jobs del worker, gráfica de uso de GPU en el tiempo
- Badge de alerta si el worker lleva > 5 min sin heartbeat

**Cola de jobs**

- Filtros: por app, por service, por status, por fecha
- Columnas: id, app, service, status, priority, worker asignado, tiempo en cola, tiempo de ejecución
- Al hacer clic en un job: detalle completo con logs en tiempo real (SSE), payload, resultado, archivos generados
- Acciones: cancelar job pendiente, reintentar job en error, cambiar prioridad

**Métricas**

- Jobs completados por hora (últimas 24h), desglosado por service
- Tiempo promedio de ejecución por service
- Tasa de errores por app
- Uso de VRAM en el tiempo por worker
- Throughput: jobs/hora en la última semana
- **% de jobs locales vs externos** — cuántas veces se usó fallback de API

**Costos**

- Gasto total por día/semana/mes desglosado por provider (Anthropic, OpenRouter, OpenAI)
- Gasto por app — qué aplicación genera más costo en APIs externas
- Gasto por service — qué tipo de tarea consume más presupuesto
- Comparativa: jobs resueltos local (gratis) vs API (costo) — para evaluar si vale la pena ampliar ialab
- Alerta configurable: "avisar si el gasto del día supera $X"
- Configuración del cap duro `max_daily_usd` por app (ver "API Workers — externos")

**Logs de job en tiempo real**

Al abrir un job `running`:
```
[14:32:01] Analizando contexto del audio...
[14:32:08] Descargando modelo large-v3...
[14:32:45] Transcribiendo... 23%
[14:33:10] Transcribiendo... 67%
[14:33:38] Corrigiendo transcript con LLM...
[14:33:52] Done. 142 segmentos · idioma: es
```

Implementado con SSE: `GET /ai/jobs/{id}/logs/stream`

### Autenticación del dashboard

El dashboard usa la misma API key de admin. No requiere sistema de usuarios separado por ahora.

---

## Archivos generados

Los archivos viven en ialab:

```
ialab/ai-worker/files/{job_id}/
  output.mp4
  audio.mp3
  subtitles.es.vtt
  ...
```

El VPS expone:
```
GET /ai/jobs/{id}/files/{filename}
→ proxy a ialab:8001/files/{job_id}/{filename} vía Tailscale
```

Si ialab está offline, responde 503 con mensaje claro.

Para jobs con resultado texto/JSON (traducción, LLM chat, embeddings), el resultado va directo en `jobs.result` sin archivos.

**Cuándo migrar a almacenamiento externo (MinIO en VPS o Backblaze B2):**
- Si las apps necesitan servir archivos generados 24/7 aunque ialab esté offline
- Si el volumen de archivos crece más allá de lo manejable en disco de ialab
- Por ahora el modelo proxy es suficiente — los archivos son resultados que el usuario descarga una vez

---

## ialab offline — comportamiento esperado

| Situación | Comportamiento |
|---|---|
| ialab offline, llega job | Job queda en `pending`, app ve "en cola", dashboard muestra workers offline |
| ialab vuelve | Workers arrancan, se registran, jalan jobs pendientes |
| ialab se cae mid-job | Heartbeat monitor detecta en 90s, job vuelve a `pending` |
| Archivos pedidos con ialab offline | 503 con `{"error": "worker offline"}` |

---

## Autenticación

- Apps → VPS API: API key por app (header `X-App-Key`)
- Workers → VPS API: API key de worker (permisos limitados: leer/actualizar jobs propios, reportar heartbeat)
- Dashboard → VPS API: API key de admin
- VPS → ialab (proxy de archivos): Tailscale (red privada, sin credenciales adicionales)

**SSRF:** el webhook dispatcher y los workers manejan URLs provistas por las apps (`webhook_url`, `audio_url`, etc.). Ambos validan antes de conectar: solo `https://`, y se bloquean rangos privados (RFC 1918, localhost) y la red Tailscale (`100.64.0.0/10`). Sin esta validación, un `webhook_url` malicioso permitiría usar el VPS como puente hacia ialab.

---

## Stack

| Componente | Tecnología |
|---|---|
| API Gateway | Go |
| Cola + estado | PostgreSQL (SKIP LOCKED + LISTEN/NOTIFY) |
| Workers | Python 3.12 + uv |
| Dashboard | React + Vite + SSE |
| Red privada | Tailscale |
| Contenedores (workers) | Docker Compose en ialab |
| Modelos | Ollama, llama.cpp, faster-whisper, ComfyUI, XTTS, Higgs |

LM Studio queda como herramienta interactiva de escritorio en ialab — es una app GUI, no apta para workers headless en Docker. Los workers usan Ollama o llama.cpp server.

---

## Orden de construcción

1. **Node Agent** — reporta métricas reales de GPU/CPU/RAM al VPS cada 10s. Base para que el scheduler tome decisiones con datos reales.
2. **Schema PostgreSQL + API básica en Go** — `POST /ai/jobs`, `GET /ai/jobs/{id}`, registro de workers, heartbeat con métricas, SKIP LOCKED con filtro de VRAM libre y routing.
3. **Worker base en Python (Docker)** — loop de polling, registro, heartbeat, claim de job. Sin lógica de IA todavía — solo el scaffold.
4. **Dashboard v1** — panel de workers (locales + API), cola de jobs, estado en tiempo real (SSE).
5. **Primer worker real: worker-whisper** — valida el contrato end-to-end con transcripción.
6. **SDK mínimo + migrar transcripción de video-crack** como primer cliente real (doble ejecución).
7. **worker-llm** — traducción y chat local.
8. **worker-anthropic + worker-openrouter** — fallback de API para llm_chat y translation. Valida routing local→externo y tracking de costos.
9. **worker-comfyui** (bajo demanda) — imagen y video.
10. **Dashboard v2** — métricas históricas, logs en tiempo real, vista de costos por provider/app, alertas de gasto.
11. **Cuando llegue la AMD** — worker-llm-amd y worker-vision-amd, el routing por capabilities ya funciona.

---

## Decisiones descartadas

Justificaciones consolidadas para no re-litigarlas más adelante:

- **NATS / Redis como cola** — PostgreSQL con SKIP LOCKED + LISTEN/NOTIFY cubre el volumen actual con un componente menos que operar. NATS entra solo si se necesita fanout real (notificar múltiples servicios de un evento) o miles de mensajes/segundo.
- **Push del VPS hacia los workers** — el modelo pull hace que ialab se recupere solo tras apagarse: los workers arrancan, se registran y jalan pendientes sin que el VPS gestione su ciclo de vida. Push requeriría que el VPS rastree qué workers existen y reintente entregas.
- **Almacenamiento externo (MinIO / Backblaze B2)** — los archivos son resultados que el usuario descarga una vez; el proxy vía Tailscale basta. Migrar solo si las apps necesitan servir archivos 24/7 con ialab apagado o el volumen supera el disco de ialab.
- **Scheduler como servicio central** — la lógica de matching vive en el query de claim de cada worker (capabilities + reserva de VRAM). Un scheduler central sería un componente más que mantener para decisiones que el worker puede tomar solo.
- **Límite de VRAM a nivel Docker** — no existe flag `--gpu-memory`; la gestión de VRAM es responsabilidad del ledger de reservas del scheduler.
- **Sistema de usuarios para el dashboard** — API key de admin es suficiente para un solo operador. Se revisa si el dashboard se comparte.

---

## Visión a largo plazo

```
                      VPS
               ┌────────────┐
               │ API Gateway│
               │ Dashboard  │
               └─────┬──────┘
                     │ Tailscale
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
    ialab-1      ialab-2      ialab-3
    RTX 4070     AMD R9700    CPU only
    CUDA jobs    ROCm/LLM     Whisper/OCR
```

Cada lab registra sus workers con sus capabilities. El scheduler asigna automáticamente. Agregar un nuevo lab es arrancar los workers — sin cambiar nada en el VPS.
