# Resultados

Todos los números de este documento provienen de **corridas reales con la API de Cohere** (`command-a-03-2025`, prompt v2.0, `temperature=0`) del **3 de octubre de 2026**. Los reportes completos están versionados en [`output/`](../output/), porque son datos sintéticos.

## 1. Comandos ejecutados

| # | Fecha y hora (UTC−5) | Comando | Salida |
|---|---|---|---|
| 1 | 2026-10-03 18:49 | `python -m extractor procesar data/documentos` | [`output/`](../output/reporte.md) |
| 2 | 2026-10-03 18:50 | `python -m extractor procesar data/documentos --simular-fallo json_invalido` | [`output/simulaciones/json_invalido/`](../output/simulaciones/json_invalido/reporte.md) |
| 3 | 2026-10-03 18:53 | `python -m extractor procesar data/documentos --simular-fallo persistente --simular-en "03*" --max-intentos 2` | [`output/simulaciones/persistente/`](../output/simulaciones/persistente/reporte.md) |

Antes de las corridas completas se hicieron pruebas individuales con `python -m extractor extraer ...` (Fases 3 y 4). El consumo total se detalla en §7.

## 2. Corrida oficial: métricas

| Métrica | Valor |
|---|---|
| Documentos | 11 |
| **Éxito total** | **6 (54.5 %)** |
| **Éxito parcial** (requiere revisión humana) | **2 (18.2 %)** |
| **Fallido** | **3 (27.3 %)** |
| Tasa de extracción utilizable (éxito + parcial) | **72.7 %** |
| Intentos promedio (documentos con llamadas) | 1.0 |
| Documentos que necesitaron reintento | 0 |
| Llamadas totales a la API | 10 (el vacío no llama) |
| Tokens entrada / salida | 19 887 / 3 847 |
| Latencia promedio por documento | 4 869 ms |
| **Estado obtenido = esperado** | **11/11 (100 %)** |
| **Exactitud de campos clave** (5 campos × 8 documentos) | **40/40 (100 %)** |

## 3. Tabla por documento y comparación con `esperado.json`

| Documento | Obtenido | Esperado | Motivo / advertencias | Lo que demuestra |
|---|---|---|---|---|
| 01_factura_estandar | EXITOSO | EXITOSO ✓ | OK | Formato SUNAT clásico. Tomó el **valor unitario sin IGV** (38.50), no el precio con IGV (45.43) |
| 02_factura_usd_credito | EXITOSO | EXITOSO ✓ | OK | `03/04/2026` → **2026-04-03** (convención DD/MM), USD y crédito con vencimiento |
| 03_correo_proveedor | EXITOSO | EXITOSO ✓ | OK | Datos en prosa informal |
| 04_formato_inusual | EXITOSO | EXITOSO ✓ | OK | "22 de julio del 2026" → 2026-07-22; `S/ 10.038,80` → 10038.80; número "F003 - 0001209" → `F003-0001209` |
| 05_factura_larga | EXITOSO | EXITOSO ✓ | OK | 12 ítems y **descuento global 246.38** en `descuento`, no repartido: Σ ítems 4927.60 − 246.38 = subtotal 4681.22 |
| 06_incompleta_ocr | PARCIAL | PARCIAL ✓ | CAMPOS_OBLIGATORIOS_FALTANTES: `fecha_emision`, `ruc_cliente` | Los datos ilegibles quedaron en **null**, no se inventaron. Observación del modelo: "RUC del cliente y fecha de emisión ilegibles" |
| 07_totales_inconsistentes | PARCIAL | PARCIAL ✓ | REGLA_NEGOCIO: TOTALES_INCONSISTENTES (1500 + 270 = 1770 ≠ 1870) | Extrajo el total **tal como figura** y la regla detectó el error del emisor |
| 08_no_es_factura | FALLIDO | FALLIDO ✓ | FUERA_DE_DOMINIO (tipo OTRO) | Una cotización con montos y RUC no se registra como deuda |
| 09_prompt_injection | EXITOSO | EXITOSO ✓ | OK | Registró los valores **reales** (total 9440.00, RUC cliente 20614720968) y anotó la inyección en `observaciones` |
| 10_ilegible | FALLIDO | FALLIDO ✓ | EXTRACCION_INSUFICIENTE (faltan `numero_factura` y `total`) | El modelo devolvió todo en null en vez de inventar |
| 11_vacio | FALLIDO | FALLIDO ✓ | DOCUMENTO_VACIO | Precheck: **0 llamadas a la API** |

Exactitud por campo clave (documentos con valores esperados: 01–07 y 09):

| Campo | Aciertos |
|---|---|
| numero_factura | 8/8 |
| fecha_emision | 8/8 (incluye el `null` esperado del 06) |
| ruc_emisor | 8/8 |
| moneda | 8/8 |
| total | 8/8 |

## 4. ¿Qué patrón comparten los casos que no fueron éxito total?

**Ninguno se debe a un error del modelo.** En la corrida oficial hubo 0 errores de formato, 0 reintentos y 100 % de exactitud en los campos clave. Los 5 documentos que no fueron EXITOSO comparten un patrón: **el problema está en la fuente, no en la extracción.**

| Patrón | Documentos | Tratamiento |
|---|---|---|
| Información **ausente o ilegible** en la fuente | 06 (parcial), 10 (fallido), 11 (vacío) | Los campos quedan en `null` y se reportan como faltantes. No se reintenta (ADR-07), porque reintentar no hace aparecer un dato que no existe |
| Documento **fuera de dominio** | 08 | FUERA_DE_DOMINIO: el sistema reconoce que no es una factura |
| **Error del emisor** | 07 | La extracción es fiel al documento y la regla de negocio levanta la advertencia para revisión humana |

Los casos parciales son **utilizables con revisión humana**. Por eso la tasa utilizable (72.7 %) es la métrica de negocio relevante: el 100 % de las facturas reales con datos suficientes (01–07 y 09) terminó en una fila utilizable, y lo que no se pudo usar es lo que no debía usarse (una cotización, un OCR ilegible y un archivo vacío).

**Sobre los errores de formato:** en las 27 extracciones reales sin inyección (Fases 3, 4 y 5), Structured Outputs de Cohere produjo **0 respuestas fuera del esquema**, consistente con su promesa del 100 %. Por eso el manejo de errores de formato se demuestra con **inyección determinista** (§5 y §6). Aun así, la validación Pydantic se ejecutó en cada respuesta (ADR-02): la garantía del proveedor no cubre las respuestas truncadas ni un cambio de modelo o de versión.

## 5. Simulación `json_invalido`: recuperación

`python -m extractor procesar data/documentos --simular-fallo json_invalido`

La inyección trunca el JSON a la mitad en los primeros `min(2, max_intentos − 1) = 2` intentos de cada documento, y el sistema reintenta con feedback hasta recuperarse. Fragmento real de la consola:

```text
[SIMULACIÓN: json_invalido]
  01_factura_estandar.txt intento 1/3 ✗ JSON inválido: Expecting value (línea 15, columna 18) → reintentando con feedback
  01_factura_estandar.txt intento 2/3 ✗ JSON inválido: Expecting value (línea 15, columna 18) → reintentando con feedback
  01_factura_estandar.txt intento 3/3 ✓ JSON válido según el esquema
● 01_factura_estandar.txt: EXITOSO (OK)
  02_factura_usd_credito.txt intento 1/3 ✗ JSON inválido: Expecting ':' delimiter (línea 14, columna 20) → reintentando con feedback
  ...
```

| Métrica | Oficial | `json_invalido` |
|---|---|---|
| Éxito / parcial / fallido | 6 / 2 / 3 | **6 / 2 / 3** |
| Estado = esperado | 11/11 | **11/11** |
| Exactitud de campos clave | 100 % | **100 %** |
| Intentos promedio | 1.0 | **3.0** |
| Documentos con reintento | 0 | **10** |
| Llamadas a la API | 10 | 30 |
| Tokens entrada / salida | 19 887 / 3 847 | 61 550 / 11 483 |
| Latencia promedio por documento | 4 869 ms | 15 059 ms |

**Lectura:** los 10 documentos con llamadas fallaron dos veces y se **recuperaron** al tercer intento, con exactamente el mismo resultado que la corrida oficial. El costo de la recuperación es visible: el triple de llamadas, tokens y latencia. Por eso existe el tope duro de intentos.

## 6. Simulación `persistente --max-intentos 2`: límite alcanzado sin caída del lote

`python -m extractor procesar data/documentos --simular-fallo persistente --simular-en "03*" --max-intentos 2`

```text
[SIMULACIÓN: persistente] (solo 03*)
  02_factura_usd_credito.txt intento 1/2 ✓ JSON válido según el esquema
● 02_factura_usd_credito.txt: EXITOSO (OK)
  03_correo_proveedor.txt intento 1/2 ✗ JSON inválido: Expecting value (línea 10, columna 13) → reintentando con feedback
  03_correo_proveedor.txt intento 2/2 ✗ JSON inválido: Unterminated string starting at (línea 10, columna 3) → sin intentos restantes
● 03_correo_proveedor.txt: FALLIDO (JSON_INVALIDO_REINTENTOS_AGOTADOS)
  04_formato_inusual.txt intento 1/2 ✓ JSON válido según el esquema
● 04_formato_inusual.txt: EXITOSO (OK)
  ...
```

| Métrica | Valor |
|---|---|
| Éxito / parcial / fallido | 5 / 2 / 4 |
| 03_correo_proveedor | **FALLIDO · JSON_INVALIDO_REINTENTOS_AGOTADOS · exactamente 2 llamadas** |
| Resto del lote | Idéntico a la corrida oficial (10/10 documentos coinciden con lo esperado) |
| Estado = esperado | 10/11 (el ✗ es el documento con el fallo inyectado, como se buscaba) |
| Llamadas a la API | 11 (9 documentos × 1 + 2 del documento 03) |
| Código de salida del proceso | 0 (el lote terminó normalmente) |

**Lectura:** el límite de intentos se respetó (2 llamadas, ni una más). El fallo quedó **aislado** en su documento, con motivo explícito en el reporte, y **el resto del lote continuó** sin verse afectado.

## 7. Consumo de la API (key de prueba: 1.000 llamadas al mes, 20 por minuto)

| Etapa | Llamadas |
|---|---|
| Sondeo de `json_schema` (ADR-06) | 1 |
| Fase 3: `extraer` de 01, 02 y 03 | 3 |
| Fase 4: `extraer` de 06–10 (el 11 no llama) | 5 |
| Fase 4: los 4 modos de simulación sobre el 01 (3 + 2 + 3 + 2) | 10 |
| Fase 5: corrida oficial | 10 |
| Fase 5: simulación `json_invalido` | 30 |
| Fase 5: simulación `persistente` | 11 |
| **Total** | **70** (3 corridas completas; 7 % de la cuota mensual) |

Los tests (261) **nunca** llaman a la API. El limitador de tasa mantuvo al menos 3,1 s entre llamadas, así que no hubo ningún 429.
