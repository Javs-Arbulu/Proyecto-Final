**PROYECTO FINAL · OPCIÓN 3 de 10**

**Extractor de Datos Estructurados desde Documentos**

*Structured output como técnica de confiabilidad, no de formato*

Fundamentos de Arquitectura LLM · Proyecto Final

**1. Contexto y motivación**

Uno de los usos empresariales más comunes de los LLMs no es conversar, sino convertir texto desordenado (un correo, un contrato, una factura escaneada y transcrita) en datos que un sistema pueda procesar: una fila en una base de datos, un campo en un CRM, una línea en una hoja de cálculo contable. La diferencia entre un extractor de juguete y uno de producción no está en si el LLM «entiende» el texto —casi siempre lo hace— sino en qué pasa cuando el LLM devuelve algo que no calza exactamente con el esquema esperado.

Este proyecto pone al estudiante frente a ese problema real: el modelo, tarde o temprano, va a devolver un campo vacío cuando no debería estarlo, un formato de fecha distinto al esperado, o directamente un JSON mal formado. La calidad del proyecto se mide en cómo se maneja ese momento, no en si la demo feliz funciona.

**2. Objetivos de aprendizaje**

- Usar structured output / function calling como mecanismo de confiabilidad de un sistema, no simplemente para «que la respuesta salga en JSON».

- Diseñar un esquema de datos explícito y validarlo programáticamente (no confiar «a ojo» en que el modelo cumplió el formato).

- Construir un mecanismo de manejo de errores para cuando la extracción falla parcial o totalmente.

**3. Planteamiento del problema**

Construir un pipeline que reciba documentos de texto libre de un dominio elegido por el estudiante (contratos, facturas, correos de soporte, historiales clínicos ficticios, CVs, etc.) y devuelva, para cada uno, un objeto JSON estructurado y validado con los campos relevantes de ese dominio, reportando explícitamente qué documentos no pudieron procesarse correctamente y por qué.

**4. Alcance funcional (qué debe hacer el sistema)**

- Definición explícita de un esquema de datos (con Pydantic u otra herramienta de validación) para el dominio elegido, con al menos 6 campos, incluyendo al menos uno numérico, uno de fecha, y uno categórico/enumerado.

- Uso de structured output o function calling del proveedor elegido para forzar al modelo a devolver datos en ese esquema.

- Validación programática de la salida del modelo contra el esquema, independiente de si el proveedor «promete» cumplirlo.

- Procesamiento de un lote de al menos 5 documentos de ejemplo distintos (pueden ser sintéticos, generados por el propio estudiante, siempre que sean realistas).

- Un reporte final que muestre, por documento, si la extracción fue exitosa, parcialmente exitosa (algunos campos faltantes) o fallida, con el motivo.

**5. Requisitos no funcionales**

- El sistema no debe lanzar una excepción no controlada y detener todo el lote si un solo documento falla —debe continuar con los siguientes y reportar el fallo aislado.

- Debe existir al menos un mecanismo de reintento (con límite máximo de intentos) para el caso en que el modelo devuelva un JSON inválido.

**6. Arquitectura sugerida**

- Un módulo de esquema (clases Pydantic o equivalente) separado de la lógica de llamada al modelo.

- Un módulo de extracción que use la capacidad de structured output/function calling nativa del proveedor (no parsear texto libre con regex como única estrategia).

- Un módulo de validación/reintento que intercepte fallos de parseo antes de que lleguen al resultado final.

- Un reporte de salida (CSV, JSON, o una vista simple en consola/web) que resuma el resultado del lote completo.

**7. Plan de trabajo sugerido (5 fases)**

1.  Fase 1 — Definir el dominio y el esquema de datos exacto, incluyendo qué campos son obligatorios y cuáles opcionales.

2.  Fase 2 — Preparar el conjunto de documentos de prueba, incluyendo deliberadamente 1 o 2 «difíciles» (ambiguos, incompletos, con formato inusual) para poner a prueba el manejo de errores.

3.  Fase 3 — Implementar la extracción básica con structured output y validarla contra los documentos «fáciles».

4.  Fase 4 — Implementar el manejo de errores, reintentos y el reporte de fallos usando los documentos «difíciles» diseñados en la Fase 2.

5.  Fase 5 — Correr el lote completo y medir la tasa de éxito de extracción.

**8. Entregables**

- Repositorio con el esquema de datos, el pipeline de extracción y los documentos de prueba usados.

- Reporte de resultados del lote completo (tasa de éxito, casos parciales, casos fallidos con motivo).

- Video explicativo de máximo 30 minutos.

**9. Rúbrica de evaluación (100 puntos)**

| **Criterio**                                                                  | **Peso**    | **Qué se evalúa**                                                                                                                                            |
|-------------------------------------------------------------------------------|-------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Funcionalidad y código**                                                    | **30 pts**  | El pipeline procesa el lote completo sin caerse; el esquema está bien definido y cubre casos realistas del dominio elegido.                                  |
| **Seguridad y arquitectura**                                                  | **20 pts**  | Manejo seguro de credenciales; separación clara entre esquema, extracción y validación.                                                                      |
| **Extracción validada con manejo robusto de errores (entregable específico)** | **25 pts**  | Se demuestra manejo real de al menos un caso de fallo (JSON inválido, campo faltante, etc.), no solo el camino feliz; existe lógica de reintento con límite. |
| **Documentación**                                                             | **10 pts**  | El esquema y las decisiones de validación están documentadas y justificadas.                                                                                 |
| **Video (máx. 30 min)**                                                       | **15 pts**  | Debe mostrarse en vivo un documento que falle y cómo el sistema lo maneja, no solo los casos exitosos.                                                       |
| **Total**                                                                     | **100 pts** | *Calificación máxima del proyecto*                                                                                                                           |

*La distribución de pesos sigue el estándar del curso: funcionalidad/código 30 pts, seguridad y arquitectura 20 pts, entregable específico del proyecto 25 pts, documentación 10 pts, video explicativo 15 pts.*

**10. Guía para el video (máx. 30 minutos)**

El video debe responder explícitamente estas preguntas, en cualquier orden, mostrando la aplicación funcionando en vivo:

1.  ¿Qué dominio eligieron y por qué el esquema de datos definido tiene sentido para ese dominio?

2.  Muestren en vivo un documento fácil siendo extraído correctamente.

3.  Muestren en vivo un documento difícil (de los diseñados a propósito) y cómo el sistema reacciona ante el fallo.

4.  ¿Cuál fue la tasa de éxito final del lote completo, y qué patrón comparten los casos que fallaron?

**11. Errores comunes a evitar**

- Confiar en que el structured output del proveedor «siempre» cumple el esquema y omitir la validación programática —en producción, eventualmente falla.

- Probar únicamente con documentos fáciles y nunca diseñar deliberadamente un caso difícil, lo que oculta el verdadero comportamiento del sistema bajo estrés.

- Dejar que un reintento sin límite entre en un ciclo infinito consumiendo tokens indefinidamente ante un documento imposible de extraer.

**12. Definición de "terminado" (checklist final)**

- El esquema de datos está formalmente definido y validado programáticamente (no solo «confiado»).

- Existe evidencia de al menos un caso de fallo manejado correctamente sin caída del sistema.

- El reporte final distingue explícitamente entre éxito total, éxito parcial y fallo por documento.

BSG · Fundamentos de Arquitectura LLM · Proyecto Final
