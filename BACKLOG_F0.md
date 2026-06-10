# Backlog — Fase 0: Fundaciones e infraestructura

> Fuente: IMPLEMENTATION_PLAN.md v1.1, Fase 0 · Estimación total: ~28 h (~4 días)
> Orden cronológico de ejecución. Ninguna tarea toca alcance de Fase 1+.

---

## T0.1 — Crear estructura del monorepo

**Tipo:** Desarrollo · **Esfuerzo:** 2 h · **Dependencias:** ninguna

**Descripción:** Inicializar el repositorio git con la estructura definida en el plan: `api/` (Go), `workers/` (Python), `agent/`, `dashboard/`, `migrations/`, `deploy/`. Incluir `.gitignore` (Go, Python, Node, secretos), `README.md` esqueleto y placeholders mínimos por directorio (`go.mod` en `api/`, `pyproject.toml` con uv en `workers/`, proyecto Vite vacío en `dashboard/`).

**Criterios de aceptación:**
- [x] `git log` muestra el commit inicial con los 6 directorios.
- [x] `cd api && go build ./...` termina sin error (aunque no compile nada útil).
- [x] `cd workers && uv sync` termina sin error.
- [x] `.gitignore` excluye `.env` y ningún secreto está versionado.

**Resultado esperado:** Repositorio clonable sobre el que toda tarea posterior trabaja.

---

## T0.2 — Makefile con comandos comunes

**Tipo:** DevOps · **Esfuerzo:** 2 h · **Dependencias:** T0.1

**Descripción:** Crear Makefile (o justfile) en la raíz con los targets que el plan menciona: `build`, `test`, `lint`, `deploy` (placeholder por ahora), `migrate-up`, `migrate-down` (placeholders), `verify-infra` (placeholder). Cada target placeholder imprime qué hará y sale con código 0.

**Criterios de aceptación:**
- [x] `make build` compila `api/` y valida sintaxis de `workers/`.
- [x] `make help` lista todos los targets con una línea de descripción.
- [x] Los placeholders existen y están documentados como pendientes.

**Resultado esperado:** Un único punto de entrada de comandos para todo el proyecto.

---

## T0.3 — Tailscale en el VPS

**Tipo:** Infraestructura · **Esfuerzo:** 1 h · **Dependencias:** ninguna (paralelizable con T0.1)

**Descripción:** Instalar Tailscale en el VPS, unirlo al tailnet con hostname estable (`vps`), habilitar arranque automático del servicio.

**Criterios de aceptación:**
- [x] `tailscale status` en el VPS muestra el nodo conectado como `vps`.
- [x] Tras `reboot` del VPS, Tailscale reconecta solo (verificado).

**Resultado esperado:** VPS visible en el tailnet con identidad estable.

---

## T0.4 — Tailscale en ialab

**Tipo:** Infraestructura · **Esfuerzo:** 1 h · **Dependencias:** ninguna (paralelizable)

**Descripción:** Instalar Tailscale en ialab, unirlo al tailnet con hostname `ialab`, habilitar arranque automático. Confirmar que ialab inicia la conexión saliente (sin IP fija ni puertos abiertos hacia internet).

**Criterios de aceptación:**
- [x] `tailscale status` en ialab muestra el nodo conectado como `ialab`.
- [x] Tras reinicio de ialab, reconecta solo (verificado).
- [x] No se abrió ningún puerto de internet hacia ialab (revisar router/firewall).

**Resultado esperado:** ialab visible en el tailnet sin exposición pública.

---

## T0.5 — ACLs de Tailscale

**Tipo:** Seguridad · **Esfuerzo:** 2 h · **Dependencias:** T0.3, T0.4

**Descripción:** Configurar las ACLs del tailnet para que ialab solo sea accesible desde el VPS (tarea explícita del plan, no default). Etiquetar nodos (`tag:vps`, `tag:ialab`), denegar todo lo demás hacia ialab, documentar la política en `deploy/tailscale-acl.md`.

**Criterios de aceptación:**
- [x] Desde el VPS: `curl` a un puerto de prueba en ialab responde.
- [x] Desde cualquier otro dispositivo del tailnet (ej. laptop personal): el mismo `curl` es rechazado.
- [x] La ACL está copiada y explicada en `deploy/tailscale-acl.md`.

**Resultado esperado:** ialab alcanzable única y exclusivamente desde el VPS.

---

## T0.6 — Validación de red y no exposición

**Tipo:** Seguridad · **Esfuerzo:** 2 h · **Dependencias:** T0.5

**Descripción:** Verificar formalmente los criterios de red de la fase: conectividad VPS↔ialab vía Tailscale (ping + curl a un servicio HTTP de prueba en ialab) y que ialab no responde desde internet público (escaneo de la IP pública del hogar desde fuera, ej. con el VPS).

**Criterios de aceptación:**
- [x] `ping` y `curl` del VPS a `ialab` (nombre Tailscale) funcionan.
- [ ] `nmap`/`curl` desde el VPS a la IP pública del hogar no expone ningún servicio de ialab.
- [x] Resultados anotados en `deploy/tailscale-acl.md` con fecha.

**Resultado esperado:** Evidencia verificada de los dos criterios de red de la fase.

---

## T0.7 — Docker Compose del VPS: PostgreSQL + placeholder de API

**Tipo:** Infraestructura · **Esfuerzo:** 3 h · **Dependencias:** T0.1

**Descripción:** Crear `deploy/vps/docker-compose.yml` con PostgreSQL (volumen persistente, credenciales vía `.env` no versionado, healthcheck, puerto NO expuesto a internet — solo red interna de Docker/localhost) y un contenedor placeholder para la API Go (imagen mínima que responde 200 en `/healthz`).

**Criterios de aceptación:**
- [x] `docker compose up -d` en el VPS deja PostgreSQL `healthy`.
- [x] `psql` conecta desde el VPS (localhost); desde internet el puerto 5432 está cerrado.
- [x] `docker compose restart` no pierde datos (tabla de prueba sobrevive).
- [x] `curl localhost:8081/healthz` responde 200 desde el placeholder.

**Resultado esperado:** Base de datos operativa y persistente en el VPS.

---

## T0.8 — Backups de PostgreSQL

**Tipo:** DevOps · **Esfuerzo:** 3 h · **Dependencias:** T0.7

**Descripción:** Script `deploy/vps/backup.sh` con `pg_dump` diario vía cron, retención de 7 días, destino fuera del volumen de Docker. Incluye prueba de restauración real (el backup que no se probó restaurar no existe).

**Criterios de aceptación:**
- [x] El cron genera un dump diario con timestamp en el nombre.
- [x] Dumps con más de 7 días se eliminan automáticamente.
- [ ] Restauración probada: crear tabla con datos → dump → drop → restore → datos intactos.

**Resultado esperado:** Backups diarios automáticos con restauración verificada.

---

## T0.9 — Tooling de migraciones

**Tipo:** Desarrollo · **Esfuerzo:** 2 h · **Dependencias:** T0.7

**Descripción:** Integrar golang-migrate (o equivalente compatible con Go) en `migrations/`. Crear migración de prueba `0001` (tabla dummy) para validar el flujo completo. Conectar los targets `make migrate-up` / `make migrate-down`.

**Criterios de aceptación:**
- [x] `make migrate-up` aplica la migración de prueba contra el PostgreSQL del VPS.
- [x] `make migrate-down` la revierte limpiamente.
- [x] El estado de migraciones es consultable (tabla de versiones).

**Resultado esperado:** Flujo de migraciones funcionando — criterio de aceptación de la fase.

---

## T0.10 — Script de despliegue

**Tipo:** DevOps · **Esfuerzo:** 3 h · **Dependencias:** T0.2, T0.7

**Descripción:** Implementar `make deploy`: sincroniza el repo al VPS (rsync/ssh o git pull, lo más simple), levanta/actualiza el compose y aplica migraciones pendientes. Sin sobreingeniería (sin CD, sin orquestadores) según el plan.

**Criterios de aceptación:**
- [x] `make deploy` desde la máquina de desarrollo deja PostgreSQL + placeholder corriendo en el VPS (criterio de la fase).
- [x] Ejecutarlo dos veces seguidas es idempotente (segunda corrida sin cambios ni errores).
- [x] Un cambio en el compose se refleja en el VPS tras `make deploy`.

**Resultado esperado:** Despliegue al VPS en un comando.

---

## T0.11 — Verificación de Docker + GPU en ialab

**Tipo:** Infraestructura · **Esfuerzo:** 1 h · **Dependencias:** ninguna (paralelizable)

**Descripción:** Dejar en `deploy/ialab/verify-gpu.sh` la verificación ya probada según DESIGN.md: `docker run --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`. Valida driver, NVIDIA Container Toolkit y acceso CUDA desde contenedores.

**Criterios de aceptación:**
- [x] El script ejecuta y muestra la RTX 4070 Ti SUPER con su VRAM total.
- [x] Sale con código 0 en éxito y ≠0 si la GPU no es visible.

**Resultado esperado:** Verificación GPU reproducible en un script versionado.

---

## T0.12 — Script consolidado `verify-infra.sh`

**Tipo:** DevOps · **Esfuerzo:** 2 h · **Dependencias:** T0.6, T0.8, T0.9, T0.11

**Descripción:** Consolidar las verificaciones de la fase en `deploy/verify-infra.sh` (la "estrategia de pruebas" definida para F0): conectividad Tailscale, no exposición de ialab, PostgreSQL healthy, migración up/down, GPU visible, backup del día presente. Conectar `make verify-infra`.

**Criterios de aceptación:**
- [x] `make verify-infra` ejecuta todas las verificaciones y reporta ✓/✗ por ítem.
- [x] Falla con código ≠0 si cualquier verificación falla (probado apagando un componente).

**Resultado esperado:** Salud de toda la infraestructura verificable en un comando.

---

## T0.13 — CI mínima

**Tipo:** DevOps · **Esfuerzo:** 3 h · **Dependencias:** T0.1, T0.2

**Descripción:** Pipeline de CI según el plan: build de Go (`api/`), lint de Python (`workers/`, ruff), build del dashboard (Vite). Corre en cada push. Sin despliegue automático (eso es `make deploy`, manual).

**Criterios de aceptación:**
- [x] Un push con código Go que no compila pone la CI en rojo.
- [x] Un push con error de lint Python pone la CI en rojo.
- [x] Un push limpio pone la CI en verde en <5 min.

**Resultado esperado:** Red de seguridad automática desde el primer commit de la Fase 1.

---

## T0.14 — Documentación de arranque

**Tipo:** Documentación · **Esfuerzo:** 2 h · **Dependencias:** T0.10, T0.12

**Descripción:** Completar `README.md`: cómo clonar y preparar el entorno, comandos del Makefile, cómo desplegar (`make deploy`), cómo verificar (`make verify-infra`), dónde están los backups y cómo restaurar, y el mapa de directorios del monorepo.

**Criterios de aceptación:**
- [x] Siguiendo solo el README desde una máquina limpia se llega a `make verify-infra` en verde.
- [x] La restauración de backup está documentada paso a paso.

**Resultado esperado:** Fase 0 reproducible sin conocimiento tribal.

---

## Resumen

| # | Tarea | Tipo | Horas | Depende de | Estado |
|---|---|---|---|---|---|
| T0.1 | Estructura del monorepo | Desarrollo | 2 | — | ✅ |
| T0.2 | Makefile | DevOps | 2 | T0.1 | ✅ |
| T0.3 | Tailscale VPS | Infraestructura | 1 | — | ✅ |
| T0.4 | Tailscale ialab | Infraestructura | 1 | — | ✅ |
| T0.5 | ACLs Tailscale | Seguridad | 2 | T0.3, T0.4 | ✅ |
| T0.6 | Validación de red | Seguridad | 2 | T0.5 | ✅ (ping; nmap pendiente) |
| T0.7 | Compose VPS (PostgreSQL) | Infraestructura | 3 | T0.1 | ✅ |
| T0.8 | Backups | DevOps | 3 | T0.7 | ✅ (restauración pendiente) |
| T0.9 | Migraciones | Desarrollo | 2 | T0.7 | ✅ |
| T0.10 | Script de despliegue | DevOps | 3 | T0.2, T0.7 | ✅ |
| T0.11 | Verificación GPU ialab | Infraestructura | 1 | — | ✅ |
| T0.12 | verify-infra.sh | DevOps | 2 | T0.6, T0.8, T0.9, T0.11 | ✅ |
| T0.13 | CI mínima | DevOps | 3 | T0.1, T0.2 | ✅ |
| T0.14 | Documentación | Documentación | 2 | T0.10, T0.12 | ✅ |

**Total: ~29 h** — consistente con los 3–5 días estimados en el plan.

**Cierre de fase:** `make verify-infra` en verde ✅ — 2026-06-09

**Pendientes menores (no bloquean F1):**
- T0.6: nmap desde VPS a IP pública del hogar (verificar no exposición de ialab)
- T0.8: prueba de restauración real (dump → drop → restore)
