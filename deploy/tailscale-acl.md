# Tailscale ACLs — ai-worker-platform

## Objetivo

`ialab` solo debe ser alcanzable desde `vps`. Ningún otro nodo del tailnet (laptop, móvil, etc.) puede conectarse a ialab directamente.

## Flujo de tráfico

```
tus dispositivos → tag:vps   (deploy, admin SSH, dashboard)
tag:vps          → tag:ialab (proxy de archivos)
tag:ialab        → tag:vps   (workers pollan la API — imprescindible)
```

## Configuración (aplicar en https://login.tailscale.com/admin/acls)

Cambiar al "JSON editor" y reemplazar todo con:

```json
{
    "tagOwners": {
        "tag:vps":   ["autogroup:owner"],
        "tag:ialab": ["autogroup:owner"]
    },
    "grants": [
        {"src": ["autogroup:member"], "dst": ["tag:vps"],   "ip": ["*"]},
        {"src": ["tag:vps"],          "dst": ["tag:ialab"], "ip": ["*"]},
        {"src": ["tag:ialab"],        "dst": ["tag:vps"],   "ip": ["*"]}
    ],
    "ssh": [
        {
            "action": "check",
            "src":    ["autogroup:member"],
            "dst":    ["autogroup:self"],
            "users":  ["autogroup:nonroot", "root"]
        }
    ]
}
```

> La regla implícita es `deny` para todo lo que no esté listado.
> `autogroup:member` cubre tus dispositivos personales (laptop, móvil).
> **Resultado:** ialab NO es alcanzable desde tus dispositivos directamente — solo desde el VPS.

## Pasos de aplicación

1. En el panel, clic en **JSON editor**.
2. Reemplazar todo el contenido con el JSON de arriba.
3. Clic en **Save**.
4. En el **VPS**, ejecutar: `sudo tailscale up --advertise-tags=tag:vps --accept-risk=lose-ssh`
5. En **ialab**, ejecutar: `sudo tailscale up --advertise-tags=tag:ialab --accept-risk=lose-ssh`
6. Aprobar los tags en https://login.tailscale.com/admin/machines (columna "Tags")

## Verificación (T0.6)

Fecha de verificación: 2026-06-09

| Check | Comando | Resultado |
|---|---|---|
| VPS → ialab ping | `ping -c3 100.103.55.110` desde VPS | ✓ 81–136ms |
| celular → ialab | Tailscale app (pclinux/iOS) | ✓ ialab no aparece en la lista de nodos — inaccesible |
| VPS → ialab HTTP | pendiente (no hay servicio HTTP en ialab aún) | — |
| ialab no expuesto públicamente | pendiente | — |
