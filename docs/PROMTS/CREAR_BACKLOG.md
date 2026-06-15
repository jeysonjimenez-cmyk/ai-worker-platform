

Usando como fuentes de verdad exclusivamente:

* IMPLEMENTATION_PLAN.md  (la fase en cuestión).
* DESIGN.md  (para no perder intención).

* Todas las RETROSPECTIVES completadas hasta la fase anterior.
* Cualquier documento oficial ya existente para la fase objetivo.

Genera el backlog detallado exclusivamente para la Fase .

Reglas:

* No modifiques la arquitectura.
* No modifiques el IMPLEMENTATION_PLAN.
* No agregues nuevas tecnologías.
* No rediseñes fases anteriores.
* No diseñes fases futuras.
* No crees tareas relacionadas con fases posteriores.
* Limítate estrictamente al alcance definido para la FaseX.

Tratamiento de las retrospectivas:

* Considera las retrospectivas únicamente como insumo operativo y fuente de riesgos reales observados durante la implementación.
* Evalúa cada mejora sugerida por las retrospectivas y determina si es el momento correcto para incorporarla.
* Solo incorpora una mejora derivada de retrospectivas si es estrictamente necesaria para cumplir el objetivo de la Fase X o si reduce un riesgo significativo que afectaría directamente la ejecución de esta fase.
* Si una mejora es valiosa pero puede diferirse sin comprometer la Fase X, indícalo explícitamente y no la conviertas en tarea del backlog.

Para cada tarea necesito:

* Título corto.
* Descripción.
* Dependencias.
* Estimación de esfuerzo (1–4 horas por tarea).
* Criterios de aceptación verificables.
* Resultado esperado.
* Tipo de tarea:

  * Infraestructura
  * DevOps
  * Desarrollo
  * Seguridad
  * Documentación

Organiza las tareas en orden cronológico de ejecución.

Antes de generar las tareas:

1. Resume en pocas líneas cuál es el objetivo concreto de la Fase X según el IMPLEMENTATION_PLAN.
2. Enumera los riesgos provenientes de las retrospectivas que impactan directamente esta fase.
3. Identifica cuáles de esos riesgos deben resolverse ahora y cuáles pueden posponerse.

No quiero una nueva arquitectura ni recomendaciones estratégicas.

Quiero únicamente el backlog ejecutable de la Fase X para que pueda comenzar a trabajar inmediatamente.
