# Checklist e2e — Fase 4: worker-whisper + archivos

> Ejecutar contra hardware real antes de cerrar la Fase 4.
> Regla del plan: no avanzar con criterios en rojo.
> Audio objetivo: ≥1 hora de duración.

## Prerrequisitos

- La API del VPS está corriendo con los cambios de F4 desplegados (`make deploy`).
- `worker-whisper` y `file-server` están construidos en ialab (`docker compose build`).
- El archivo de entorno del worker existe: `~/.config/ai-platform/worker-whisper.env` (ver plantilla `deploy/ialab/worker-whisper.env.example`).
- `FILE_SERVER_URL=http://100.103.55.110:8001` está configurado en `deploy/vps/.env`.
- Tailscale activo en VPS e ialab, conectividad verificada:

```bash
# Desde ialab
curl -sf http://100.106.192.45:8081/healthz && echo "ok"
```

---

## Escenario 0 — Arranque de los servicios

```bash
# En ialab — arrancar worker-whisper y file-server
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-whisper file-server"

# Verificar que el worker se registró
ssh cracksonj@100.103.55.110 "docker compose -f ~/ai-worker-platform/deploy/ialab/docker-compose.yml \
  logs worker-whisper 2>&1 | grep registered"

# Verificar que el servidor de archivos escucha en la IP Tailscale (no 0.0.0.0 ni 127.0.0.1)
ssh cracksonj@100.103.55.110 "ss -tlnp | grep 8001"
# Esperado: 100.103.55.110:8001

# Verificar acceso desde el VPS vía Tailscale
curl -sf http://100.103.55.110:8001/ && echo "file-server accesible desde VPS"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| `worker-whisper` registrado y `online` en DB | [ ] | | |
| `file-server` escucha en `100.103.55.110:8001` (no 0.0.0.0) | [ ] | | |
| `file-server` accesible desde VPS vía Tailscale | [ ] | | |

---

## Escenario 1 — Audio corto (smoke test)

**Objetivo:** validar el ciclo completo con audio corto antes del audio de 1 hora.

```bash
APP_KEY="<tu_app_key>"
API="http://100.106.192.45:8081"

# Crear job de transcripción con audio corto (≤5 min)
JOB_ID=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"https://<url_audio_corto>"}}' | jq -r .id)
echo "job_id: $JOB_ID"

# Sondear hasta done (≤2 min para audio corto)
until curl -s $API/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" \
    | jq -e '.status == "done"' > /dev/null; do
  curl -s $API/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq '{status,progress}'
  sleep 10
done

# Verificar resultado y descargar VTT
curl -s $API/ai/jobs/$JOB_ID -H "X-App-Key: $APP_KEY" | jq '{status,result}'
curl -s "$API/ai/jobs/$JOB_ID/files/output.vtt" -H "X-App-Key: $APP_KEY" | head -5
# Esperado: WEBVTT seguido de los segmentos

# Verificar que los 3 archivos están en job_files (DB del VPS)
source ~/ai-worker-platform/deploy/vps/.env
docker exec vps-postgres-1 psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT filename, size_bytes FROM job_files WHERE job_id = '$JOB_ID' ORDER BY filename;"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job → `done` con archivos VTT/SRT/JSON en `job_files` | [ ] | | |
| VTT descargable vía proxy con contenido válido (`WEBVTT`) | [ ] | | |
| Progreso avanzó de forma realista (no saltó 0→100) | [ ] | | |

---

## Escenario 2 — Audio de 1 hora

**Objetivo:** criterio principal de F4 — transcripción de 1 hora sin falso timeout de heartbeat, progreso realista, memoria estable.

```bash
# Crear job con audio de 1 hora
JOB_LONG=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"https://<url_audio_1hora>"}}' | jq -r .id)
echo "job_long: $JOB_LONG"

# Monitorear progreso en tiempo real (en otra terminal)
watch -n 30 "curl -s $API/ai/jobs/$JOB_LONG -H 'X-App-Key: $APP_KEY' | jq '{status,progress}'"

# Verificar que el worker no cae a offline durante el job largo (en otra terminal)
watch -n 60 "source ~/ai-worker-platform/deploy/vps/.env && \
  docker exec vps-postgres-1 psql -U \$POSTGRES_USER -d \$POSTGRES_DB \
  -c \"SELECT id, status, last_heartbeat FROM workers WHERE id = 'w-whisper-ialab';\""

# Verificar consumo de VRAM en ialab durante el job
ssh cracksonj@100.103.55.110 \
  "nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader"

# Al completar: descargar VTT y verificar tamaño
curl -s "$API/ai/jobs/$JOB_LONG/files/output.vtt" \
  -H "X-App-Key: $APP_KEY" -o output_1h.vtt
wc -l output_1h.vtt   # debe tener cientos de líneas
head -20 output_1h.vtt
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job 1h → `done` con VTT descargable vía proxy | [ ] | | |
| Heartbeat no disparó timeout falso durante el job | [ ] | | |
| Progreso avanzó de forma realista durante todo el job | [ ] | | |
| Memoria GPU estable (sin leak) durante el job | [ ] | | |

---

## Escenario 3 — Concurrencia 1 (dos jobs en serie)

**Objetivo:** dos jobs de transcripción encolados se procesan en serie; nunca dos en `running` simultáneamente.

```bash
# Encolar dos jobs casi simultáneamente
JOB_A=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"https://<url_audio_corto_a>"}}' | jq -r .id)

JOB_B=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"https://<url_audio_corto_b>"}}' | jq -r .id)

echo "A: $JOB_A   B: $JOB_B"

# Monitorear estados (uno debe estar pending mientras el otro está running)
watch -n 5 "curl -s $API/ai/jobs/$JOB_A -H 'X-App-Key: $APP_KEY' | jq -r '.status' && \
  curl -s $API/ai/jobs/$JOB_B -H 'X-App-Key: $APP_KEY' | jq -r '.status'"

# Verificar que el ledger de VRAM refleja un solo job a la vez
source ~/ai-worker-platform/deploy/vps/.env
docker exec vps-postgres-1 psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT id, vram_total_mb, vram_reserved_mb, vram_total_mb - vram_reserved_mb AS free_mb FROM gpus;"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Dos jobs encolados se procesan en serie | [ ] | | |
| Nunca dos jobs en `running` simultáneamente | [ ] | | |
| Ledger libera VRAM correctamente entre jobs | [ ] | | |

---

## Escenario 4 — Caos: apagar ialab mid-job

**Objetivo:** job en curso con ialab apagado vuelve a `pending` y se reprocesa solo al rearrancar, sin intervención.

```bash
# 1. Crear un job largo
JOB_CHAOS=$(curl -s -X POST $API/ai/jobs \
  -H "Content-Type: application/json" \
  -H "X-App-Key: $APP_KEY" \
  -d '{"service":"transcription","payload":{"audio_url":"https://<url_audio_1hora>"}}' | jq -r .id)

# 2. Esperar a que esté en running
until curl -s $API/ai/jobs/$JOB_CHAOS \
  -H "X-App-Key: $APP_KEY" | jq -e '.status == "running"' > /dev/null; do sleep 5; done
echo "job en running — apagando ialab..."

# 3. Apagar ialab
ssh cracksonj@100.103.55.110 "sudo poweroff"

# 4. Medir tiempo hasta que el job vuelva a pending (debe ser ≤90s por heartbeat timeout)
START=$(date +%s)
until curl -s $API/ai/jobs/$JOB_CHAOS \
  -H "X-App-Key: $APP_KEY" | jq -e '.status == "pending"' > /dev/null; do sleep 5; done
echo "volvió a pending en $(($(date +%s) - START))s"

# 5. Encender ialab y arrancar los servicios
# (arrancar ialab físicamente o vía IPMI/WoL)
ssh cracksonj@100.103.55.110 "cd ~/ai-worker-platform && \
  docker compose -f deploy/ialab/docker-compose.yml up -d worker-whisper file-server"

# 6. Verificar que el job se reprocesa solo hasta done
until curl -s $API/ai/jobs/$JOB_CHAOS \
  -H "X-App-Key: $APP_KEY" | jq -e '.status == "done"' > /dev/null; do sleep 10; done
curl -s $API/ai/jobs/$JOB_CHAOS -H "X-App-Key: $APP_KEY" | jq '{status}'
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Job vuelve a `pending` en ≤90s tras apagar ialab | [ ] | | |
| Al rearrancar ialab, el job se reprocesa solo | [ ] | | |
| Job completa sin intervención manual | [ ] | | |

---

## Escenario 5 — Archivo con ialab apagado → 503

**Objetivo:** con ialab offline, la API devuelve 503 con mensaje claro, no 500 ni timeout.

```bash
# Con ialab apagado (o file-server parado):
# docker compose stop file-server  # para simular sin apagar ialab

JOB_PREV="<job_id_de_un_job_anterior_done>"

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "$API/ai/jobs/$JOB_PREV/files/output.vtt" \
  -H "X-App-Key: $APP_KEY")
echo "HTTP: $HTTP_CODE"
# Esperado: 503

# Verificar mensaje del body
curl -s "$API/ai/jobs/$JOB_PREV/files/output.vtt" -H "X-App-Key: $APP_KEY"
# Esperado: {"error":"file server unavailable"} o similar
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Con file-server apagado → 503 (no 500 ni timeout) | [ ] | | |
| Mensaje del body es claro | [ ] | | |

---

## Resultado final

| Escenario | Resultado | Fecha | Ejecutado por |
|---|---|---|---|
| 0 — Arranque de servicios | [ ] | | |
| 1 — Audio corto (smoke test) | [ ] | | |
| 2 — Audio 1 hora | [ ] | | |
| 3 — Concurrencia 1 (dos jobs en serie) | [ ] | | |
| 4 — Caos: apagar ialab mid-job | [ ] | | |
| 5 — Archivo con ialab apagado → 503 | [ ] | | |

**Fase 4 lista para cerrar:** [ ] SÍ — todos los criterios en verde.
