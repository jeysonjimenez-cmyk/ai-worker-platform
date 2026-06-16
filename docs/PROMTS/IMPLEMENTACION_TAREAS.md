Implementa las tareas que te mencione.

---

## Contexto antes de empezar

- La arquitectura está congelada — leer `DESIGN.md` antes de proponer cualquier componente nuevo.
- `IMPLEMENTATION_PLAN.md` es la fuente de verdad del plan de fases.
- El backlog de la fase activa (`docs/BACKLOG/f<N>.md`) es la fuente de verdad de la tarea. Leerlo completo antes de implementar.
- No implementes ninguna tarea fuera de la que se te pide. Sin features extra, sin refactors de oportunidad.
- Mantén el estilo existente del repositorio (nombres, estructura, convenciones de tests).
- Incluye tests que cubran los criterios de aceptación de la tarea.

---

## Durante la implementación

- Si un criterio de aceptación depende de otra tarea o de hardware real, no lo implementes — déjalo `[ ]` en el backlog y anótalo en el changelog.
- Si detectas deuda técnica o riesgo no relacionado con la tarea, menciónalo pero no lo toques.

---

## Al finalizar — actualizar dos archivos

### 1. Backlog `docs/BACKLOG/f<N>.md`

Marcar cada criterio de aceptación de la tarea según su estado real:

```
- [x]  cubierto por código o test en este commit
- [ ]  depende de una tarea posterior o requiere hardware real
```

Solo marcar `[x]` si el criterio es verificable con lo que se implementó ahora. Los criterios con "verificable en T4.X" o "audio largo real" se dejan `[ ]` hasta que esa tarea se cierre.

---

### 2. Changelog `docs/CHANGELOG/f<N>.md`

Agregar una sección antes de `## Resumen de tests`, con este esquema:

```markdown
## T<N>.<X> ✅ <título de la tarea>

**Fecha:** YYYY-MM-DD

### Archivos creados
- `ruta/archivo.ext` — qué hace y por qué existe.

### Archivos modificados
- `ruta/archivo.ext` — qué se cambió y qué resuelve.

### Configuración por env vars        ← solo si la tarea introduce variables de entorno
| Variable | Default | Descripción |
|---|---|---|
| `NOMBRE_VAR` | `valor` | para qué sirve |

### Notas                              ← solo si hay algo no obvio
- Decisiones de diseño relevantes.
- Criterios que quedaron [ ] y de qué dependen.
- Deuda técnica o riesgo detectado.
```

Añadir también la fila correspondiente en la tabla `## Resumen de tests`:

```markdown
| `ruta/test_nuevo.py` | N (T<fase>.<tarea>) | ✅ |
```

**Qué hace un buen changelog:**
- Archivos creados y modificados en secciones separadas.
- Cada línea de archivo explica qué hace, no solo su nombre.
- Si hay criterios `[ ]`, el "Por qué" está en `### Notas`.
- Las env vars tienen tabla con defaults.
- No repite lo que ya dice el código; documenta lo que no es obvio leyendo el diff.
