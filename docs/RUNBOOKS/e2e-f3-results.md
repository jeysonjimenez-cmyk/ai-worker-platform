# e2e F3 — Registro de ejecución

> Fecha: 2026-06-13  
> Ejecutado por: Jeyson Jimenez  
> Resultado final: **✅ PASS — todos los criterios en verde**

---

## 1. Estado inicial

- Rama activa: `feature/f3`
- VPS (`100.106.192.45`): API corriendo con código F2 (commit `9eb2d81`)
- ialab (`100.103.55.110`): sin contenedor `worker-echo`, solo Node Agent en systemd
- Cambios F3 locales: sin committear (workers/, api/, deploy/)

---

## 2. Problemas encontrados y correcciones aplicadas

### 2.1 API bound a `127.0.0.1` — ialab no puede alcanzarla

**Síntoma:** El prerequisito del runbook (`curl http://100.106.192.45:8081/healthz` desde ialab) fallaría porque `deploy/vps/docker-compose.yml` publicaba el puerto solo en loopback.

**Cambio en `deploy/vps/docker-compose.yml`:**
```diff
- - "127.0.0.1:8081:8080"
+ # Tailscale IP — accessible from ialab workers, not exposed to internet
+ - "100.106.192.45:8081:8080"
```

**Cambio en `deploy/vps/deploy.sh`** (el healthcheck también usaba localhost):
```diff
- if curl -sf http://localhost:8081/healthz > /dev/null 2>&1; then
+ if curl -sf http://100.106.192.45:8081/healthz > /dev/null 2>&1; then
```

**Cambio en `docs/PORTS.md`:**
```diff
- | `127.0.0.1:8081` | 8080 | API Go | No — localhost / proxy reverso |
+ | `100.106.192.45:8081` | 8080 | API Go | No — Tailscale únicamente (ialab y dev) |
```

### 2.2 Claim crashea con 500 — `capabilities.services` nil

**Síntoma:** El worker enviaba `WORKER_CAPABILITIES={"echo": true}` (sin el array `services`). La API cargaba esas capabilities, `caps.Services` quedaba nil, `json.Marshal(nil)` producía `null`, y el SQL fallaba:

```
ERROR: cannot extract elements from a scalar (SQLSTATE 22023)
```

La query afectada en `api/internal/workers/store.go`:
```sql
AND service = ANY(
  SELECT jsonb_array_elements_text($1::jsonb)
)
```

Cuando `$1` es `null`, PostgreSQL lanza el error porque `jsonb_array_elements_text` no acepta escalares.

**Corrección en `api/internal/workers/store.go`:**
```diff
  var caps capabilities
  json.Unmarshal(w.Capabilities, &caps)

+ if len(caps.Services) == 0 {
+     return nil, nil
+ }
+
  // Build services filter as a JSON array for the query.
  servicesJSON, _ := json.Marshal(caps.Services)
```

**Corrección en `~/.config/ai-platform/worker-echo.env` en ialab:**
```diff
- WORKER_CAPABILITIES={"echo": true}
+ WORKER_CAPABILITIES={"services": ["echo"], "echo": true}
```

**También hay que actualizar `deploy/ialab/worker-echo.env.example`** con el valor correcto (pendiente committear).

### 2.3 `docker compose restart` no re-lee `env_file`

**Descubrimiento:** `docker compose restart` reutiliza la configuración del contenedor anterior sin re-leer los archivos de entorno. Para aplicar cambios en `env_file` hay que usar `docker compose up -d` (que recrea el contenedor).

No hay cambio de código aquí — es comportamiento esperado de Docker Compose, documentado como nota operativa.

### 2.4 Ubicación del `env_file` — `/etc/ai-platform/` requiere root

**Problema:** El usuario `cracksonj` en ialab no puede escribir en `/etc/ai-platform/` sin contraseña de sudo.

**Cambio en `deploy/ialab/docker-compose.yml`:**
```diff
  env_file:
-   - path: /etc/ai-platform/worker-echo.env
-     required: false
+   # Primary location (legacy): /etc/ai-platform/worker-echo.env (root-owned)
+   # Fallback location: ~/.config/ai-platform/worker-echo.env (user-writable)
+   - path: /etc/ai-platform/worker-echo.env
+     required: false
+   - path: ${HOME}/.config/ai-platform/worker-echo.env
+     required: false
```

El env se crea en `~/.config/ai-platform/worker-echo.env` (accesible sin sudo). Docker Compose expande `${HOME}` en tiempo de ejecución del host.

---

## 3. Secuencia de comandos ejecutados

### Prerequisitos

```bash
# Verificar conectividad al VPS
curl -sf http://100.106.192.45:8081/healthz && echo "VPS OK"
# → no alcanzable (API bound a 127.0.0.1 — se corrige en §2.1)

# Ver contenedores en VPS
ssh ubuntu@vps-15a6511a "docker ps --format 'table {{.Names}}\t{{.Status}}'"

# Ver workers registrados en DB
ssh ubuntu@vps-15a6511a "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c 'SELECT id, status, capabilities FROM workers;'"

# Deploy F3 al VPS (con correcciones §2.1)
make deploy

# Confirmar acceso Tailscale
curl -s http://100.106.192.45:8081/healthz
# → {"status":"ok"}
```

### Crear app y registrar worker

```bash
# Crear app en DB para obtener X-App-Key
APP_KEY=$(openssl rand -hex 32)
ssh ubuntu@vps-15a6511a "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c \"INSERT INTO apps (id, name, api_key, active) \
      VALUES ('app-e2e', 'e2e test', '$APP_KEY', true) ON CONFLICT DO NOTHING;\""
# APP_KEY=********************************

# Registrar worker-echo via Admin API
ADMIN_KEY="****************************************************************"
WORKER_KEY=$(openssl rand -hex 32)
curl -s -X POST http://100.106.192.45:8081/workers/register \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -d "{\"id\":\"w-echo-ialab\",\"hostname\":\"ialab\",\"api_key\":\"$WORKER_KEY\",\
       \"capabilities\":{\"services\":[\"echo\"],\"echo\":true}}" | jq .
# WORKER_KEY=****************************************************************
```

### Setup en ialab

```bash
# Crear env con capabilities correctas (con services array)
ssh cracksonj@100.103.55.110 "mkdir -p ~/.config/ai-platform && cat > ~/.config/ai-platform/worker-echo.env << 'ENVEOF'
WORKER_API_URL=http://100.106.192.45:8081
WORKER_KEY=****************************************************************
WORKER_ADMIN_KEY=****************************************************************
WORKER_ID=w-echo-ialab
WORKER_CAPABILITIES={\"services\": [\"echo\"], \"echo\": true}
ENVEOF"

# Sync repo a ialab
rsync -az --delete \
  --exclude '.git' --exclude '.env' --exclude 'workers/.venv' --exclude 'dashboard/node_modules' \
  /home/cracksonj/PROJECTS/ai-worker-platform/ cracksonj@100.103.55.110:~/ai-worker-platform/

# Construir imagen en ialab
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml build"

# Arrancar worker-echo
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo"
```

### Escenario 1 — Ciclo completo e2e

```bash
# Verificar registro en logs
ssh cracksonj@100.103.55.110 "docker compose -f ~/ai-worker-platform/deploy/ialab/docker-compose.yml \
  logs worker-echo 2>&1 | grep registered"
# → registered worker w-echo-ialab on ialab

# Crear job de echo
APP_KEY="********************************"
JOB_ID=$(curl -s -X POST http://100.106.192.45:8081/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"echo","payload":{"text":"hola mundo"}}' | jq -r .id)

# Sondear hasta done
until curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID \
  -H "X-App-Key: $APP_KEY" | jq -e '.status == "done"' > /dev/null; do sleep 2; done
curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq '{status,result}'
# → {"status":"done","result":{"echo":{"text":"hola mundo"}}}

# Verificar job_logs en DB
ssh ubuntu@vps-15a6511a "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c \"SELECT level, message, created_at FROM job_logs WHERE job_id = '$JOB_ID' ORDER BY created_at;\""
# → 2 filas: "echo worker: processing job ..." y "echo worker: done"
```

**Resultado:** ✅ PASS

### Escenario 2 — Matar contenedor mid-job

```bash
# Crear job
JOB_ID=$(curl -s -X POST http://100.106.192.45:8081/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"echo","payload":{"text":"mid-job test"}}' | jq -r .id)

# Matar el contenedor inmediatamente
ssh cracksonj@100.103.55.110 "docker compose -f ~/ai-worker-platform/deploy/ialab/docker-compose.yml kill worker-echo"

# Verificar que queda running
curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq '{status}'
# → {"status":"running"}

# Esperar a que vuelva a pending (midiendo tiempo)
START=$(date +%s)
until curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID \
  -H "X-App-Key: $APP_KEY" | jq -e '.status == "pending"' > /dev/null; do sleep 5; done
echo "volvió a pending en $(($(date +%s) - START))s"
# → volvió a pending en 32s

# Rearrancar el worker
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo"

# Verificar que retoma y completa el job
until curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID \
  -H "X-App-Key: $APP_KEY" | jq -e '.status == "done"' > /dev/null; do sleep 3; done
curl -s http://100.106.192.45:8081/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq '{status,result}'
# → {"status":"done","result":{"echo":{"text":"mid-job test"}}}
```

**Resultado:** ✅ PASS — delta real 32s (límite 90s)

### Escenario 3 — Restart idempotente

```bash
WORKER_ID="w-echo-ialab"

# Estado antes del restart
ssh ubuntu@vps-15a6511a "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c \"SELECT id, status, registered_at FROM workers WHERE id = '$WORKER_ID';\""
# → w-echo-ialab | online | 2026-06-13 07:02:29.650513+00

# Restart (up -d recrea el contenedor aplicando el env)
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo"
sleep 5

# Estado después — misma fila, sin duplicados
ssh ubuntu@vps-15a6511a "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c \"SELECT id, status, registered_at FROM workers WHERE id = '$WORKER_ID';
       SELECT count(*) as total FROM workers WHERE id = '$WORKER_ID';\""
# → w-echo-ialab | online | 2026-06-13 07:02:29.650513+00   (misma fila)
# → total: 1
```

**Resultado:** ✅ PASS — count = 1, `registered_at` invariante

### Escenario 4 — Heartbeat con execute() bloqueado

```bash
# Modificar worker_echo/__init__.py temporalmente para simular job largo
# Añadir: import time y time.sleep(30) dentro de execute()
# (se revirtió tras el test)

# Rebuild en ialab
rsync -az --exclude '.git' --exclude 'workers/.venv' \
  /home/cracksonj/PROJECTS/ai-worker-platform/workers/ cracksonj@100.103.55.110:~/ai-worker-platform/workers/
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml build worker-echo && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo"

# Crear job largo
JOB_ID=$(curl -s -X POST http://100.106.192.45:8081/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"echo","payload":{"test":"heartbeat_sleep"}}' | jq -r .id)

# Esperar a que esté running y monitorear heartbeats durante 35s
# (execute() bloquea 30s con sleep)
ssh cracksonj@100.103.55.110 "docker compose -f ~/ai-worker-platform/deploy/ialab/docker-compose.yml \
  logs worker-echo 2>&1 | grep heartbeat | tail -8"
# → heartbeats a las 07:20:53, 07:21:04, 07:21:14, 07:21:24, 07:21:34, 07:21:45, 07:21:55, 07:22:05
#   cadencia real: ~10s durante el sleep(30)

# Verificar status del worker en DB
ssh ubuntu@vps-15a6511a "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c \"SELECT id, status, last_heartbeat FROM workers WHERE id = 'w-echo-ialab';\""
# → w-echo-ialab | online | 2026-06-13 07:22:15

# Revertir sleep(30) y rebuild final
rsync -az --exclude '.git' --exclude 'workers/.venv' \
  /home/cracksonj/PROJECTS/ai-worker-platform/workers/ cracksonj@100.103.55.110:~/ai-worker-platform/workers/
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml build worker-echo && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-echo"
```

**Resultado:** ✅ PASS — cadencia real ~10s, worker permanece online

---

## 4. Archivos modificados

| Archivo | Cambio |
|---|---|
| `deploy/vps/docker-compose.yml` | Puerto API: `127.0.0.1:8081` → `100.106.192.45:8081` |
| `deploy/vps/deploy.sh` | Healthcheck usa IP Tailscale en vez de localhost |
| `deploy/ialab/docker-compose.yml` | Añadir fallback env_file en `~/.config/ai-platform/` |
| `api/internal/workers/store.go` | Guarda defensiva si `caps.Services` es nil en el claim |
| `docs/PORTS.md` | Actualizar binding del puerto de la API |
| `docs/BACKLOG/f3.md` | Marcar criterios T3.10 y T3.12 como completados |
| `docs/RUNBOOKS/e2e-f3.md` | Rellenar tablas de resultados |

### Configuración creada en ialab (fuera del repo)

```
~/.config/ai-platform/worker-echo.env
```

```env
WORKER_API_URL=http://100.106.192.45:8081
WORKER_KEY=****************************************************************
WORKER_ADMIN_KEY=****************************************************************
WORKER_ID=w-echo-ialab
WORKER_CAPABILITIES={"services": ["echo"], "echo": true}
```

### Datos creados en DB del VPS

| Tabla | id | Nota |
|---|---|---|
| `apps` | `app-e2e` | API key para los tests (`X-App-Key`) |
| `workers` | `w-echo-ialab` | Registrado con capabilities `{"services":["echo"],"echo":true}` |

---

## 5. Resultado final

| Escenario | Resultado | Timestamp (UTC) |
|---|---|---|
| 1 — Ciclo completo e2e | ✅ PASS | 2026-06-13 07:14 |
| 2 — Matar contenedor mid-job | ✅ PASS | 2026-06-13 07:15 |
| 3 — Restart idempotente | ✅ PASS | 2026-06-13 07:16 |
| 4 — Heartbeat con execute bloqueado | ✅ PASS | 2026-06-13 07:22 |

**Fase 3 lista para cerrar. Todos los criterios en verde.**
