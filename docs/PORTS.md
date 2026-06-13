# Mapa de puertos

## VPS

| Puerto (host) | Puerto (contenedor) | Servicio | Expuesto a internet |
|---|---|---|---|
| `127.0.0.1:5433` | 5432 | PostgreSQL | No — localhost únicamente |
| `100.106.192.45:8081` | 8080 | API Go | No — Tailscale únicamente (ialab y dev) |
| `127.0.0.1:3000` | 3000 | Dashboard (React) | No — localhost / proxy reverso |

Los servicios del VPS no publican puertos a `0.0.0.0`. Todo acceso externo pasa por un proxy reverso (nginx/caddy) con TLS, o por Tailscale.

## IA Lab

| Puerto | Servicio | Accesible desde |
|---|---|---|
| `9100` | Endpoint local `/metrics` del Node Agent | VPS y workers locales (Tailscale) |
| `8001` | Servidor de archivos de jobs | VPS únicamente (Tailscale) |

ialab no expone ningún puerto a internet. La regla de Tailscale ACL lo garantiza.

Los **workers de inferencia** (F3+, p. ej. `worker-echo`) corren en Docker con `network_mode: host`
y **no publican puertos propios**: solo abren conexiones salientes a la API del VPS y al `/metrics`
local, ambos vía Tailscale.

## Puertos reservados para fases futuras

| Puerto | Servicio planificado | Fase |
|---|---|---|
| — | worker-anthropic (proceso, sin puerto propio) | F9 |
| — | worker-openrouter (proceso, sin puerto propio) | F9 |

## Notas

- PostgreSQL usa `5433` en el host del VPS (no `5432`) porque el `5432` estaba ocupado.
- La API Go usa `8081` en el host del VPS (no `8080`) porque el `8080` estaba ocupado.
- El `DATABASE_URL` en `.env` apunta a `localhost:5433`.
- `make migrate-up` usa ese `DATABASE_URL` directamente desde la máquina de desarrollo a través de Tailscale.
