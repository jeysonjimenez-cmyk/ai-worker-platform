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
| `systemctl status node-agent` → active tras reinicio | ✅ PASS | 2026-06-13 04:34:59 UTC | Agente volvió ~2 min después del reboot (04:33:04 UTC) |
| Filas en `worker_metrics` con timestamps post-reinicio | ✅ PASS | 2026-06-13 04:34:59 UTC | 5 filas con cadencia de 10s confirmada |

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
| Agente no crasheó durante los 7 min (sin reinicios en journald) | ✅ PASS | 2026-06-13 04:38–04:45 UTC | Backoff progresivo: 5s→10s→20s→40s→80s→160s→300s |
| Lote enviado al reconectar (gap visible en `worker_metrics`) | ✅ PASS | 2026-06-13 04:47:29 UTC | Gap de ~5 min, lote de muestras insertado al reconectar |
| `recorded_at` del lote = timestamps de captura, no de llegada | ✅ PASS | 2026-06-13 04:47:29 UTC | Timestamps cubren el periodo de caída con intervalos reales |
| Cadencia volvió a ~10s tras el lote | ✅ PASS | 2026-06-13 04:47:39 UTC | Delta de 10s confirmado en las filas posteriores |

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
| Warning de deriva aparece si delta > margen configurado | ✅ PASS | 2026-06-13 04:37:03 UTC | delta_mb=926 > margin_mb=512 → WARN en logs |
| Silencio total si delta ≤ margen | ✅ N/A | — | No hay escenario sin deriva: SO/drivers consumen ~1.7 GB |

---

## Resultado final

| Escenario | Resultado | Fecha | Ejecutado por |
|---|---|---|---|
| 1 — Reinicio ialab | ✅ PASS | 2026-06-13 | Jeyson Jimenez |
| 2 — Caída VPS 7 min | ✅ PASS | 2026-06-13 | Jeyson Jimenez |
| 3 — Alerta deriva ledger | ✅ PASS | 2026-06-13 | Jeyson Jimenez |

**Fase 2 lista para cerrar:** ✅ SÍ
