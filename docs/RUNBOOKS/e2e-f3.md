# Checklist e2e — Fase 3: worker-echo VPS ↔ ialab

> Ejecutar contra hardware y red reales antes de cerrar la Fase 3.
> Regla del plan: no avanzar con criterios en rojo.

## Prerrequisitos

- La API del VPS está corriendo con los cambios de F3 desplegados (`make deploy`).
- `worker-echo` está construido en ialab (`make workers-build` desde ialab o `docker compose build`).
- El archivo de entorno del worker existe: `/etc/ai-platform/worker-echo.env` (ver plantilla `deploy/ialab/worker-echo.env.example`).
- Tailscale activo en VPS e ialab, conectividad verificada:

```bash
# Desde ialab
curl -sf http://100.106.192.45:8081/healthz && echo "ok"
```

---

## Escenario 1 — Ciclo completo de job end-to-end

**Objetivo:** `worker-echo` en Docker procesa un job creado por la API del VPS a través de Tailscale.

```bash
# En ialab — arrancar el worker
docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo

# Verificar que se registró
docker compose -f deploy/ialab/docker-compose.yml logs worker-echo | grep "registered"
```

```bash
# Desde el VPS (o cualquier cliente con X-App-Key) — crear un job de echo
APP_KEY="********************************"

curl -s -X POST http://100.106.192.45:8081/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"echo","payload":{"text":"hola mundo"}}' | jq .

# Anotar el job_id devuelto
JOB_ID="<job_id>"
```

```bash
# Sondear hasta done (el echo es rápido, <5s)
watch -n2 "curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H 'X-App-Key: $APP_KEY' | jq '{status,result}'"
```

Resultado esperado: `"status": "done"`, `"result": {"echo": {"text": "hola mundo"}}`.

Verificar que el worker reportó progreso y log:

```bash
# Desde el VPS — consultar logs del job
source ~/ai-worker-platform/deploy/vps/.env
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT level, message, created_at FROM job_logs WHERE job_id = '$JOB_ID' ORDER BY created_at;"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| `worker-echo` se registra al arrancar | ✅ PASS | 2026-06-13 07:13 UTC | `registered worker w-echo-ialab on ialab` |
| Job creado → status `done` con echo del payload | ✅ PASS | 2026-06-13 07:14 UTC | `{"echo": {"text": "hola mundo"}}` |
| `job_logs` contiene al menos una línea del worker | ✅ PASS | 2026-06-13 07:14 UTC | 2 filas en `job_logs` |

---

## Escenario 2 — Matar el contenedor mid-job

**Objetivo:** un job en vuelo vuelve a `pending` en ≤90s y puede ser retomado.

```bash
# Crear un job largo (usar sleep en el payload si worker-echo lo soporta,
# o lanzar dos workers y matar uno mientras el otro está idle)
# La forma más directa: matar el contenedor con un job en curso.

# 1. Arrancar el worker si no está corriendo
docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo

# 2. Crear un job
JOB_ID=$(curl -s -X POST http://100.106.192.45:8081/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"echo","payload":{"text":"mid-job test"}}' | jq -r .id)
echo "job: $JOB_ID"

# 3. Matar el contenedor inmediatamente (antes de que complete)
docker compose -f deploy/ialab/docker-compose.yml kill worker-echo

# 4. Verificar estado del job — debe quedar 'running' inicialmente
curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq .status
```

```bash
# Esperar hasta 90s y verificar que vuelve a pending
# (el heartbeat monitor del VPS hace el re-enqueue)
sleep 95
curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq .status
# esperado: "pending"
```

```bash
# 5. Volver a arrancar el worker — debe retomar el job
docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo
sleep 10
curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq .status
# esperado: "done"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job queda `running` al matar el contenedor | ✅ PASS | 2026-06-13 07:15 UTC | |
| Job vuelve a `pending` en ≤90s | ✅ PASS | 2026-06-13 07:15 UTC | Delta real: **32s** |
| Worker re-arrancado retoma el job y lo completa | ✅ PASS | 2026-06-13 07:16 UTC | `{"echo": {"text": "mid-job test"}}` |

---

## Escenario 3 — `docker compose restart` → re-registro idempotente

**Objetivo:** reiniciar el worker no crea un worker duplicado en la base de datos.

```bash
# Anotar el worker_id configurado en worker-echo.env
WORKER_ID="w-echo-ialab"

# Estado antes del restart
source ~/ai-worker-platform/deploy/vps/.env
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT id, status, registered_at FROM workers WHERE id = '$WORKER_ID';"

# Reiniciar el worker
docker compose -f deploy/ialab/docker-compose.yml restart worker-echo
sleep 5

# Estado después — misma fila, sin duplicados
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT id, status, registered_at FROM workers WHERE id = '$WORKER_ID';"

# Contar filas — debe ser 1
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT count(*) FROM workers WHERE id = '$WORKER_ID';"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| `docker compose restart` → worker re-registrado y `online` | ✅ PASS | 2026-06-13 07:16 UTC | |
| Sin filas duplicadas en `workers` | ✅ PASS | 2026-06-13 07:16 UTC | count = 1, misma `registered_at` |

---

## Escenario 4 — Heartbeat con `execute()` bloqueado

**Objetivo:** mientras el worker procesa un job largo, el heartbeat llega al VPS y el worker no cae a `offline`.

> Nota: `worker-echo` es instantáneo. Para simular un job largo, o bien se modifica `execute()` para sleep,
> o se observa indirectamente que el worker nunca entra en estado `offline` durante el processing normal.

```bash
# Crear varios jobs en serie y verificar que el worker permanece online
for i in $(seq 1 10); do
  curl -s -X POST http://100.106.192.45:8081/ai/jobs \
    -H "Content-Type: application/json" \
    -H "X-App-Key: $APP_KEY" \
    -d "{\"service\":\"echo\",\"payload\":{\"n\":$i}}" | jq -r .id
done

# Verificar que el worker no cayó a offline en ningún momento
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT id, status, last_heartbeat FROM workers WHERE id = '$WORKER_ID';"
```

Para verificar el criterio completo con bloqueo real: modificar temporalmente `worker_echo/__init__.py`
para que `execute()` incluya `time.sleep(30)` y repetir el escenario, confirmando que el heartbeat llega
en logs de la API cada ~10s:

```bash
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml logs api | grep "heartbeat"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Worker permanece `online` durante el procesamiento | ✅ PASS | 2026-06-13 07:22 UTC | status=online tras job con sleep(30) |
| Heartbeats en logs de la API con cadencia ≤15s | ✅ PASS | 2026-06-13 07:21 UTC | Cadencia real: ~10s |

---

## Resultado final

| Escenario | Resultado | Fecha | Ejecutado por |
|---|---|---|---|
| 1 — Ciclo completo e2e | ✅ PASS | 2026-06-13 | Jeyson Jimenez (Claude Code) |
| 2 — Matar contenedor mid-job | ✅ PASS | 2026-06-13 | Jeyson Jimenez (Claude Code) |
| 3 — Restart idempotente | ✅ PASS | 2026-06-13 | Jeyson Jimenez (Claude Code) |
| 4 — Heartbeat con execute bloqueado | ✅ PASS | 2026-06-13 | Jeyson Jimenez (Claude Code) |

**Fase 3 lista para cerrar:** [x] SÍ — todos los criterios en verde.
