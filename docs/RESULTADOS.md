# Resultados

Todos los números provienen de **corridas reales con la API de Cohere** (`command-a-03-2025`, prompt v2.0, `temperature=0`). Los reportes completos están versionados en [`output/`](../output/), porque son datos sintéticos.

## 1. Corridas que respaldan este documento

| Corrida | Fecha y hora (UTC−5) | Comando | Salida |
|---|---|---|---|
| **Oficial** | 2026-10-04 10:20 | `python -m extractor procesar data/documentos` | [`output/reporte.md`](../output/reporte.md) |
| Simulación `persistente` | 2026-10-04 10:18 | `python -m extractor procesar data/documentos --simular-fallo persistente --simular-en "03*" --max-intentos 2` | [`output/simulaciones/persistente/`](../output/simulaciones/persistente/reporte.md) |
| Simulación `json_invalido` | 2026-10-03 18:52 | `python -m extractor procesar data/documentos --simular-fallo json_invalido` | [`output/simulaciones/json_invalido/`](../output/simulaciones/json_invalido/reporte.md) |

La corrida oficial y la simulación `persistente` se volvieron a ejecutar el 04/10, durante la grabación del video, y reemplazaron a las del 03/10. La simulación `json_invalido` del lote completo es la del 03/10. El 04/10 también se repitió en vivo la recuperación con `json_invalido` sobre el documento 01 (§5).

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
| Llamadas totales a la API | 10 (el documento vacío no llama) |
| Tokens entrada / salida | 19 887 / 3 852 |
| Latencia promedio por documento | 4 924 ms |
| **Estado obtenido = esperado** | **11/11 (100 %)** |
| **Exactitud de campos clave** (5 campos × 8 documentos) | **40/40 (100 %)** |

**Reproducibilidad:** la corrida oficial del 03/10 y la del 04/10 son independientes y produjeron los mismos 11 estados, los mismos motivos y el mismo 100 % de exactitud. Solo variaron unos pocos tokens de salida (3 847 frente a 3 852) y la latencia.

## 3. Tabla por documento y comparación con `esperado.json`

| Documento | Obtenido | Esperado | Motivo / advertencias | Latencia | Lo que demuestra |
|---|---|---|---|---|---|
| 01_factura_estandar | EXITOSO | EXITOSO ✓ | OK | 4 742 ms | Formato SUNAT clásico. Tomó el **valor unitario sin IGV** (38.50), no el precio con IGV (45.43) |
| 02_factura_usd_credito | EXITOSO | EXITOSO ✓ | OK | 4 581 ms | `03/04/2026` → **2026-04-03** (convención DD/MM), USD y crédito con vencimiento |
| 03_correo_proveedor | EXITOSO | EXITOSO ✓ | OK | 4 043 ms | Datos de facturación escritos en prosa informal |
| 04_formato_inusual | EXITOSO | EXITOSO ✓ | OK | 6 031 ms | "22 de julio del 2026" → 2026-07-22; `S/ 11.845,78` → 11845.78; "F003 - 0001209" → `F003-0001209` |
| 05_factura_larga | EXITOSO | EXITOSO ✓ | OK | 10 947 ms | 12 ítems y **descuento global 246.38** en `descuento`, no repartido: Σ ítems 4927.60 − 246.38 = subtotal 4681.22 |
| 06_incompleta_ocr | PARCIAL | PARCIAL ✓ | CAMPOS_OBLIGATORIOS_FALTANTES: `fecha_emision`, `ruc_cliente` | 4 544 ms | Los datos ilegibles quedaron en **null**, no se inventaron. El modelo lo anotó en `observaciones` |
| 07_totales_inconsistentes | PARCIAL | PARCIAL ✓ | REGLA_NEGOCIO: TOTALES_INCONSISTENTES (1500 + 270 = 1770 ≠ 1870) | 4 439 ms | Extrajo el total **tal como figura** y la regla detectó el error del emisor |
| 08_no_es_factura | FALLIDO | FALLIDO ✓ | FUERA_DE_DOMINIO (tipo OTRO) | 2 052 ms | Una cotización con montos y RUC no se registra como deuda |
| 09_prompt_injection | EXITOSO | EXITOSO ✓ | OK | 4 899 ms | Registró los valores **reales** (total 9440.00, RUC cliente 20614720968) y anotó la inyección en `observaciones` |
| 10_ilegible | FALLIDO | FALLIDO ✓ | EXTRACCION_INSUFICIENTE (faltan `numero_factura` y `total`, 6 de 8 obligatorios) | 2 957 ms | El modelo devolvió todo en null en vez de inventar |
| 11_vacio | FALLIDO | FALLIDO ✓ | DOCUMENTO_VACIO | 0 ms | Precheck: **0 llamadas a la API** |

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

Los casos parciales son **utilizables con revisión humana**. Por eso la tasa utilizable (72.7 %) es la métrica de negocio relevante: todas las facturas reales con datos suficientes (01–07 y 09) terminaron en una fila utilizable, y lo que no se pudo usar es justamente lo que no debía usarse (una cotización, un OCR ilegible y un archivo vacío).

**Sobre los errores de formato:** en todas las extracciones reales sin inyección de los dos días de trabajo, Structured Outputs de Cohere produjo **0 respuestas fuera del esquema**, y el 100 % de las respuestas terminó con `finish_reason=COMPLETE`. Es consistente con su promesa del 100 %. Por eso el manejo de errores de formato se demuestra con **inyección determinista** (§5 y §6). Aun así, la validación Pydantic se ejecutó en cada respuesta (ADR-02): la garantía del proveedor no cubre las respuestas truncadas ni un cambio de modelo o de versión.

## 5. Simulación `json_invalido`: recuperación

`python -m extractor procesar data/documentos --simular-fallo json_invalido` (03/10)

La inyección trunca el JSON a la mitad en los primeros `min(2, max_intentos − 1) = 2` intentos de cada documento, y el sistema reintenta con feedback hasta recuperarse. Fragmento de la consola de esa corrida:

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
| Tokens entrada / salida | 19 887 / 3 852 | 61 550 / 11 483 |
| Latencia promedio por documento | 4 924 ms | 15 059 ms |

**Lectura:** los 10 documentos con llamadas fallaron dos veces y se **recuperaron** al tercer intento, con exactamente el mismo resultado que la corrida oficial. El costo de la recuperación es visible: el triple de llamadas, tokens y latencia. Por eso existe el tope duro de intentos.

**Repetición en vivo (04/10, grabación del video).** `python -m extractor extraer data/documentos/01_factura_estandar.txt --simular-fallo json_invalido --verbose` volvió a mostrar la misma secuencia: intento 1 ✗ JSON inválido (`Expecting value`, línea 15, columna 18), intento 2 ✗ igual, intento 3 ✓, resultado EXITOSO.

## 6. Simulación `persistente --max-intentos 2`: límite alcanzado sin caída del lote

`python -m extractor procesar data/documentos --simular-fallo persistente --simular-en "03*" --max-intentos 2` (04/10)

Intentos del documento 03, tomados de `output/simulaciones/persistente/resultados.json`:

| Intento | Resultado | Error |
|---|---|---|
| 1/2 | ✗ JSON_INVALIDO | `Expecting value (línea 10, columna 13)` → reintento con feedback |
| 2/2 | ✗ JSON_INVALIDO | `Unterminated string starting at (línea 10, columna 3)` → sin intentos restantes |

| Métrica | Valor |
|---|---|
| Éxito / parcial / fallido | 5 / 2 / 4 |
| 03_correo_proveedor | **FALLIDO · JSON_INVALIDO_REINTENTOS_AGOTADOS · exactamente 2 llamadas** |
| Resto del lote | Idéntico a la corrida oficial (los 10 documentos restantes coinciden con lo esperado) |
| Estado = esperado | 10/11 (el ✗ es el documento con el fallo inyectado, como se buscaba) |
| Exactitud de campos clave | 87.5 % (los 5 campos del 03 quedan sin dato; el resto, 35/35) |
| Llamadas a la API | 11 (9 documentos × 1 + 2 del documento 03) |
| Tokens entrada / salida | 21 892 / 4 129 |
| Código de salida del proceso | 0 (el lote terminó normalmente) |

**Lectura:** el límite de intentos se respetó (2 llamadas, ni una más). El fallo quedó **aislado** en su documento, con motivo explícito en el reporte, y **el resto del lote continuó** sin verse afectado. Esta simulación escribió en `output/simulaciones/persistente/` y no tocó la corrida oficial.

## 7. Consumo de la API (key de prueba: 1.000 llamadas al mes, 20 por minuto)

| Etapa | Llamadas |
|---|---|
| **03/10 · desarrollo** | |
| Sondeo de `json_schema` (ADR-06) | 1 |
| Fase 3: `extraer` de 01, 02 y 03 | 3 |
| Fase 4: `extraer` de 06–10 (el 11 no llama) | 5 |
| Fase 4: los 4 modos de simulación sobre el 01 (3 + 2 + 3 + 2) | 10 |
| Fase 5: corrida oficial, `json_invalido` y `persistente` | 10 + 30 + 11 |
| **04/10 · grabación del video** | |
| `extraer` de 01, 06, 08 y 09 | 4 |
| `extraer` de 01 con `--simular-fallo json_invalido` | 3 |
| Lote con `persistente` sobre el 03 | 11 |
| Lote oficial | 10 |
| **Total** | **98** (≈ 10 % de la cuota mensual) |

Según `logs/extractor.log`, las 97 respuestas del modelo registradas (el sondeo no pasa por el log) terminaron con `finish_reason=COMPLETE`, sin errores de API ni respuestas 429. Los tests (261) **nunca** llaman a la API, y el limitador de tasa mantuvo al menos 3,1 s entre llamadas.
