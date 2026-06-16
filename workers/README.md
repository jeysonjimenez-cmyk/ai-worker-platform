# Workers — scaffold `worker_base`

Paquete que resuelve **una sola vez** todo lo común a los workers de inferencia:
registro, heartbeat, claim loop con long-poll, manejo de fencing (409), helpers de
progreso/logs y graceful shutdown. Un worker real solo implementa `execute(job, ctx)`.

`worker_echo/` es el worker de ejemplo de referencia.

---

## Crear un worker nuevo

1. Crear un paquete `worker_<nombre>/` con un `__init__.py` que defina `execute` y `main`:

```python
import logging
from worker_base import Scaffold, from_env
from worker_base.scaffold import JobContext

def execute(job: dict, ctx: JobContext) -> dict:
    # job: el job reclamado (id, service, payload, ...)
    # ctx: helpers que no requieren saber de HTTP ni de fencing
    ctx.log(f"procesando job {job['id']}")
    ctx.report_progress(50)
    # ... lógica real del worker ...
    return {"resultado": "..."}      # se persiste vía /complete

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    scaffold = Scaffold(from_env(), execute=execute)
    scaffold.run()                   # bloquea hasta SIGTERM
```

2. Añadir un `__main__.py` que llame a `main()` y, si aplica, un entry-point en `pyproject.toml`.
3. Añadir el servicio al `deploy/ialab/docker-compose.yml` (o un compose análogo).

**Eso es todo.** El worker no toca HTTP, registro, heartbeat, claim ni fencing — todo vive en el scaffold.

---

## Contrato de `execute(job, ctx)`

| | |
|---|---|
| **Recibe** | `job: dict` (el job reclamado) y `ctx: JobContext` |
| **Devuelve** | `dict` → se envía como `result` a `PATCH /ai/jobs/{id}/complete`. Un valor no-dict se envuelve en `{"output": ...}` |
| **Si lanza excepción** | el scaffold la captura y reporta el job como `error` (la API decide retry/backoff) |
| **`ctx.report_progress(pct)`** | `PATCH /ai/jobs/{id}/progress`; un 409 se descarta (fencing) |
| **`ctx.log(msg, level="info")`** | `POST /ai/jobs/{id}/logs`; un 409 se descarta (fencing) |

`execute()` corre en el hilo principal; el **heartbeat va en un hilo aparte**, así que un
`execute()` largo no provoca falsos timeouts (el monitor de F1 corta a los 90s sin heartbeat).

---

## Configuración (variables de entorno)

`worker_base/config.py` las exige al arrancar — falta una requerida → falla con error claro,
sin defaults silenciosos para credenciales ni URL.

| Variable | Requerida | Descripción | Ejemplo |
|---|---|---|---|
| `WORKER_API_URL` | Sí | URL base de la API del VPS (vía Tailscale) | `http://100.106.192.45:8081` |
| `WORKER_KEY` | Sí | Worker key (`X-Worker-Key`) para claim/heartbeat/progress/logs/complete | `wk-...` |
| `WORKER_ADMIN_KEY` | Sí | Admin key (`X-Admin-Key`) para el registro al arrancar | `adm-...` |
| `WORKER_ID` | Sí | **ID estable** del worker. Debe ser determinístico (no UUID por arranque) — el registro es idempotente por este id | `w-echo-ialab` |
| `WORKER_CAPABILITIES` | No | JSON con los services que el worker atiende (default `{}`) | `{"services":["echo"]}` |
| `WORKER_HEARTBEAT_INTERVAL` | No | Segundos entre heartbeats (default `10`) | `10` |
| `WORKER_POLL_WAIT` | No | Segundos de long-poll enviados a `/claim?wait=N` (default `30`) | `30` |
| `WORKER_BACKOFF_MAX` | No | Tope de backoff entre reintentos de claim (default `30`) | `30` |

> **`WORKER_ID` estable es obligatorio para la idempotencia.** El registro hace UPSERT por
> `workers.id` en el servidor; si el id cambia en cada arranque, cada reinicio crea una fila
> nueva. Derivarlo de env/servicio, nunca generarlo al vuelo.

---

## Ciclo de vida y graceful shutdown

`scaffold.run()`: registra el worker → instala el handler de SIGTERM → arranca el hilo de
heartbeat → corre el claim loop (long-poll + backoff).

Ante **SIGTERM** (`docker compose stop/restart`): el scaffold deja de reclamar y sale limpio.
**No existe** ningún endpoint para "devolver el job a `pending`** desde el cliente — si había un
job en curso que no alcanza a terminar (p. ej. Docker hace SIGKILL al vencer el grace period),
el **monitor de heartbeat de F1 lo re-encola a `pending` a los ≤90s**. Si el worker termina el
job justo después de perder la titularidad, el reporte recibe 409 y se descarta (fencing). No se
usa `complete`-con-error para forzar el requeue: eso gastaría un reintento y marcaría error.

---

## Tests y lint

```bash
cd workers
uv run python -m pytest --tb=short -q   # tests del scaffold con API mockeada
uv run ruff check .
```
