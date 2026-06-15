Usando el archivo IMPLEMENTATION_PLAN como única fuente de verdad, genera el backlog detallado exclusivamente para la Fase 4.

Reglas:

No modifiques la arquitectura.
No modifiques el plan de implementación.
No agregues nuevas tecnologías.
No diseñes fases futuras.
No crees tareas relacionadas con fases posteriores.
Limítate estrictamente al alcance definido para la Fase 4.

Para cada tarea necesito:

Título corto.
Descripción.
Dependencias.
Estimación de esfuerzo (1–4 horas por tarea).
Criterios de aceptación verificables.
Resultado esperado.
Tipo de tarea:
Infraestructura
DevOps
Desarrollo
Seguridad
Documentación

Organiza las tareas en orden cronológico de ejecución.

No quiero una nueva arquitectura ni recomendaciones estratégicas.

Quiero únicamente el backlog ejecutable de la Fase 24 para que pueda comenzar a trabajar inmediatamente.

ten en cuenta las RETROSPECTIVES, son tips de cosas que hemos analizado, valida si es buen momento o no de hacer las mejoras que dicen las restrospectives



--------------------------------------


Implementa T4.4, T4.5

Contexto:
- Arquitectura congelada.
- IMPLEMENTATION_PLAN es la fuente de verdad.
- BACKLOG 4 es la fuente de verdad para la tarea.
- No implementes ninguna otra tarea.
- Incluye tests.
- Mantén el estilo existente del repositorio.
- Marca lo que vas implementando del backlog

Al finalizar:
- Resume los cambios realizados.
- Enumera los archivos modificados.
- Indica riesgos o deuda técnica detectada.
y ponlo en el changelog 




------------------------------------------


Hacer retrospectiva F3

Responder estas preguntas:

1. ¿Qué tomó más tiempo del esperado?

2. ¿Qué fue más difícil?

3. ¿Qué automatizaría antes de F4?

4. ¿Qué documentación faltó?

5. ¿Qué aprendí sobre Tailscale,
   Docker o la operación real?

6. ¿Qué me sorprendió del comportamiento
   del sistema distribuido?


   agrega la reto a la carpeta  RETROSPECTIVES









   --
   
1. Los audios reales son peores que los de prueba.

2. El servidor de archivos fue más complejo de lo esperado.

3. Los timeouts teóricos no sirven.

4. La VRAM real difiere mucho del cálculo inicial.

5. Faster-whisper tiene sus propias sorpresas