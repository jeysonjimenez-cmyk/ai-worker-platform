# Checklist de resiliencia — Fase 2

> Ejecutar contra el sistema real antes de cerrar la Fase 2.
> Regla del plan: no avanzar con criterios en rojo.

## Prerrequisitos

- El agente está corriendo en ialab (`systemctl status node-agent` → active)
- El VPS tiene el compose activo (`docker compose ps` → api y postgres up)
- `worker_metrics` recibe filas cada ~10s (verificar antes de empezar)

---

## Escenario 1 — Reinicio completo de ialab

**Objetivo:** el agente vuelve solo tras un reinicio del nodo sin intervención manual.

```bash
# En ialab — anotar timestamp antes
date -u

# Reiniciar el nodo
sudo reboot
```

Esperar ~2 min. Desde el VPS:

```bash
# Verificar que el agente volvió a reportar
source ~/ai-worker-platform/deploy/vps/.env
psql -h localhost -p 5433 -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT recorded_at, cpu_pct FROM worker_metrics ORDER BY recorded_at DESC LIMIT 5;"
```

La última fila debe tener `recorded_at` dentro de los últimos 30s.

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| `systemctl status node-agent` → active tras reinicio | ☐ PASS / ☐ FAIL | | |
| Filas en `worker_metrics` con timestamps post-reinicio | ☐ PASS / ☐ FAIL | | |

---

## Escenario 2 — Caída del VPS de 5 minutos

**Objetivo:** el agente no crashea, acumula en buffer, y al reconectar envía el lote con los timestamps de captura originales.

```bash
# En el VPS — anotar timestamp exacto de inicio de caída
date -u
OUTAGE_START=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Parar el compose
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml stop api
```

Esperar 5 minutos. Luego:

```bash
# Reiniciar la API
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml start api

# Anotar timestamp de recuperación
OUTAGE_END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "Outage: $OUTAGE_START → $OUTAGE_END"
```

Verificar el patrón de gap + lote en la base de datos:

```sql
-- Rows around the outage window (replace timestamps)
SELECT recorded_at, cpu_pct,
       recorded_at - LAG(recorded_at) OVER (ORDER BY recorded_at) AS delta
FROM worker_metrics
WHERE worker_id = 'w-ialab'
  AND recorded_at BETWEEN '<OUTAGE_START - 1min>' AND '<OUTAGE_END + 2min>'
ORDER BY recorded_at;
```

Resultado esperado:
- Filas con `delta` ≈ 10s antes del gap
- Una fila con `delta` grande (≈ duración de la caída) — el primer sample del lote
- Filas con `delta` ≈ 10s después (cadencia normal)
- Los `recorded_at` del lote coinciden con los timestamps de captura, **no** con el momento de inserción

```sql
-- Confirmar que la cadencia post-caída volvió a 10s
SELECT recorded_at, cpu_pct
FROM worker_metrics
WHERE worker_id = 'w-ialab' AND recorded_at > '<OUTAGE_END>'
ORDER BY recorded_at
LIMIT 10;
```

Verificar que el agente siguió vivo durante la caída (no reinició systemd):

```bash
# En ialab
journalctl -u node-agent --since "<OUTAGE_START>" --until "<OUTAGE_END>" | grep -E "ERROR|flush|retry|buffer"
```

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Agente no crasheó durante los 5 min (sin reinicios en journald) | ☐ PASS / ☐ FAIL | | |
| Lote enviado al reconectar (gap visible en `worker_metrics`) | ☐ PASS / ☐ FAIL | | |
| `recorded_at` del lote = timestamps de captura, no de llegada | ☐ PASS / ☐ FAIL | | |
| Cadencia volvió a ~10s tras el lote | ☐ PASS / ☐ FAIL | | |

---

## Escenario 3 — Verificación de deriva ledger (log warnings)

**Objetivo:** confirmar que si hay deriva entre VRAM real y el ledger, aparece el warning en logs de la API.

```bash
# Desde el VPS — buscar warnings de deriva en los últimos logs
docker compose -f ~/ai-worker-platform/deploy/vps/docker-compose.yml logs api | grep -i "vram drift"
```

Si no hay jobs en vuelo, el ledger `vram_reserved_mb` es 0, y la drift depende del uso del SO/drivers.
Si la deriva supera 512 MB (default de `VRAM_DRIFT_MARGIN_MB`), debe aparecer el warning.

| Criterio | Resultado | Timestamp | Notas |
|---|---|---|---|
| Warning de deriva aparece si delta > margen configurado | ☐ PASS / ☐ N/A | | |
| Silencio total si delta ≤ margen | ☐ PASS / ☐ FAIL | | |

---

## Resultado final

| Escenario | Resultado | Fecha | Ejecutado por |
|---|---|---|---|
| 1 — Reinicio ialab | ☐ PASS / ☐ FAIL | | |
| 2 — Caída VPS 5 min | ☐ PASS / ☐ FAIL | | |
| 3 — Alerta deriva ledger | ☐ PASS / ☐ N/A | | |

**Fase 2 lista para cerrar:** ☐ SÍ / ☐ NO (criterios en rojo pendientes: _____)
