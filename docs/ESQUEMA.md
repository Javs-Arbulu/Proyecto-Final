# Esquema de datos: factura electrónica SUNAT

Fuente de verdad: [`src/extractor/schema.py`](../src/extractor/schema.py). El JSON Schema que recibe el modelo se puede ver con:

```bash
python -m extractor esquema
```

## 1. Por qué este dominio y este esquema

Las facturas electrónicas peruanas (SUNAT) son un caso real de **cuentas por pagar** y de **integración con ERP**: el área contable recibe PDFs, transcripciones OCR y correos de proveedores, y alguien tiene que convertir ese texto en una fila contable. Este dominio es bueno para demostrar confiabilidad por tres razones:

1. **Sus campos tienen reglas verificables.** El RUC tiene 11 dígitos con prefijo conocido, el número sigue el patrón serie-correlativo, el IGV es 18 % y se cumple `subtotal + IGV = total`. Eso permite detectar errores (del modelo o del emisor) sin intervención humana.
2. **Los formatos varían mucho.** Hay fechas DD/MM/AAAA o escritas en texto, montos como `S/ 1.234,50` o `S/ 1,234.50`, y monedas escritas como `S/`, `soles`, `US$` o `dólares`.
3. **El costo de un error es alto.** Un RUC inventado o un total equivocado termina en un pago incorrecto. Por eso el contrato prohíbe inventar datos (`null` significa "no está en el documento") y separa lo que se puede reintentar de lo que necesita revisión humana.

## 2. Esquema doble

| | `FacturaExtraida` (contrato con el LLM) | `FacturaValidada` (contrato de negocio) |
|---|---|---|
| Rol | Lo que el modelo debe devolver por la herramienta `registrar_factura` | La fila lista para la base de datos |
| Rigidez | **Permisivo:** valida solo estructura y tipos | **Estricto:** obligatorios no nulos y formatos SUNAT |
| Campos | Todos opcionales, `None` significa "no aparece en el documento" | Los 8 obligatorios no aceptan `null` |
| Montos | `float` (lo que produce un LLM en JSON) | `Decimal` cuantizado a 2 decimales (`ROUND_HALF_UP`) |
| RUC / número | `str` libre | `^(10\|15\|17\|20)\d{9}$` / `^[FBE][A-Z0-9]{3}-\d{1,8}$` |
| Campos extra | Prohibidos (`extra="forbid"`) | Prohibidos (`extra="forbid"`) |
| Si falla | Error **estructural**: se **reintenta** con feedback | No debería fallar (ver §4); si falla, queda **PARCIAL** |

**Por qué el contrato permisivo valida solo estructura y tipos:** todo lo que falla en `FacturaExtraida` (JSON mal formado, `"total": "mil doscientos soles"`, `"fecha_emision": "15 de marzo"`, un enum fuera de rango o un campo inventado) **se puede corregir con un reintento**, porque el dato correcto está en el documento y el modelo solo lo expresó mal. En cambio, un RUC de 10 dígitos o un total que no cuadra **no se arreglan reintentando**: o el documento trae ese valor (error del emisor) o el dato no está. Si estas restricciones de valor estuvieran en el contrato permisivo, el sistema gastaría tokens reintentando algo que no tiene arreglo, o empujaría al modelo a "corregir" el documento, es decir, a alucinar. Por eso esas restricciones se evalúan como **reglas de negocio** en `validation.py`, que producen advertencias y el estado PARCIAL.

**Por qué todos los campos aceptan `null`:** si un campo fuera obligatorio en el contrato con el LLM, el modelo tendría que rellenarlo aunque el documento no lo traiga, y eso es una alucinación forzada. Con `null` explícito, la ausencia de un dato se vuelve información verificable ("falta `ruc_cliente`") en vez de un valor inventado.

## 3. Campos

Leyenda: **C** = crítico (si falta, FALLIDO), **O** = obligatorio (si falta, PARCIAL), **op** = opcional.

| Campo | Tipo (LLM → negocio) | C / O / op | Validación | Justificación de negocio |
|---|---|---|---|---|
| `tipo_documento` | enum `FACTURA`, `BOLETA`, `NOTA_CREDITO`, `OTRO` | O | Enum. Si no es `FACTURA`, el documento es FALLIDO con `FUERA_DE_DOMINIO` | Solo las facturas generan crédito fiscal y entran al flujo de cuentas por pagar. Una cotización con montos y RUC no debe registrarse como deuda |
| `numero_factura` | `str` → `NumeroFactura` | **C** | Patrón `^[FBE][A-Z0-9]{3}-\d{1,8}$` (regla `NUMERO_FORMATO`) y anclaje literal en el texto (`POSIBLE_ALUCINACION`) | Es la clave única del comprobante para conciliar y evitar pagos duplicados. Sin él, el registro no se puede rastrear |
| `fecha_emision` | `date` ISO | O | Fecha ISO (tipo). No futura ni anterior al año 2000 (`FECHA_FUERA_DE_RANGO`) | Define el periodo tributario y el plazo de pago |
| `fecha_vencimiento` | `date` ISO | op | Debe ser mayor o igual que la emisión (`FECHAS_INCOHERENTES`). Puede ser futura | Programación de tesorería. Es obligatoria en la práctica si la venta es a crédito (`CREDITO_SIN_VENCIMIENTO`) |
| `ruc_emisor` | `str` → `Ruc` | O | `^(10\|15\|17\|20)\d{9}$` (`RUC_FORMATO`) y anclaje literal (`POSIBLE_ALUCINACION`) | Identifica al proveedor al que se le paga. Un RUC equivocado significa pagarle a otro |
| `razon_social_emisor` | `str` | O | Texto no vacío | Validación cruzada humana del proveedor |
| `ruc_cliente` | `str` → `Ruc` | O | Igual que `ruc_emisor` | Confirma que la factura está emitida a nuestra empresa (requisito para el crédito fiscal) |
| `razon_social_cliente` | `str` | op | — | Informativo. El RUC es el identificador formal |
| `moneda` | enum `PEN`, `USD` | O | Enum | Sin moneda el monto es ambiguo: USD 4,495.80 no es lo mismo que S/ 4,495.80 |
| `condicion_pago` | enum `CONTADO`, `CREDITO` | op | Enum. Si es `CREDITO` y no hay vencimiento, se advierte | Flujo de caja |
| `items[]` | lista de `ItemFactura` | op | `descripcion` obligatoria. `extra="forbid"` | Detalle para control de compras. Permite verificar el subtotal (`ITEMS_NO_CUADRAN`) |
| `items[].cantidad` | `float` → `Decimal` | op | Mayor que 0 (`MONTO_NO_POSITIVO`) | — |
| `items[].precio_unitario` | `float` → `Decimal` | op | Mayor o igual que 0 (`MONTO_NO_POSITIVO`). Es el valor **sin IGV** | El formato SUNAT distingue el valor unitario (sin IGV) del precio unitario (con IGV) |
| `items[].importe` | `float` → `Decimal` | op | Si falta, se calcula como `cantidad × precio_unitario` | Valor de venta de la línea sin IGV |
| `descuento` | `float` → `Decimal` | op | Se resta en `ITEMS_NO_CUADRAN` | Descuento **global**. Repartirlo entre los ítems falsearía el detalle |
| `subtotal` | `float` → `Decimal` | op | Mayor que 0. `subtotal + igv ≈ total`. `igv ≈ 18 % × subtotal` | Base imponible (operación gravada) |
| `igv` | `float` → `Decimal` | op | `IGV_NO_18` (solo advertencia, puede haber operaciones exoneradas) | Crédito fiscal recuperable |
| `total` | `float` → `Decimal` | **C** | Mayor que 0 (`MONTO_NO_POSITIVO`). Cuadre con subtotal e IGV (`TOTALES_INCONSISTENTES`) | Es el monto a pagar. Sin él, el registro no sirve para cuentas por pagar |
| `observaciones` | `str` | op | — | El modelo anota ambigüedades o texto sospechoso ignorado (por ejemplo, un intento de prompt injection) |

Constantes en el código: `CAMPOS_CRITICOS`, `CAMPOS_OBLIGATORIOS`, `CAMPOS_OPCIONALES` y `CAMPOS_CLAVE` (los 5 campos que se comparan contra `data/esperado.json`).

## 4. Umbrales de clasificación

La etapa `evaluar()` se ejecuta **después** de que la extracción produjo un `FacturaExtraida` válido y **no reintenta**. Precedencia:

| # | Condición | Estado | Motivo |
|---|---|---|---|
| 1 | `tipo_documento` distinto de `FACTURA` (incluido `null`) | FALLIDO | `FUERA_DE_DOMINIO` (el detalle indica el tipo detectado) |
| 2 | Falta algún **crítico** (`numero_factura`, `total`), o faltan **más del 50 %** de los obligatorios (5 o más de 8) | FALLIDO | `EXTRACCION_INSUFICIENTE` (el reporte igual muestra lo obtenido) |
| 3 | Faltan obligatorios no críticos **o** hay advertencias de reglas de negocio | PARCIAL ("requiere revisión humana") | `CAMPOS_OBLIGATORIOS_FALTANTES` y/o `REGLA_NEGOCIO` |
| 4 | Ninguna de las anteriores y `FacturaValidada` se construye | EXITOSO | `OK` |
| 4b | `FacturaValidada` fallara de todos modos (defensa en profundidad) | PARCIAL | `REGLA_NEGOCIO` con el detalle |

Justificación de los umbrales:

- **Críticos implican FALLIDO.** Sin número no hay identidad del comprobante (no se puede conciliar ni detectar duplicados). Sin total no hay obligación de pago que registrar. Una fila así no le sirve a nadie, ni siquiera para revisión.
- **Más del 50 % de obligatorios faltantes implica FALLIDO.** Si falta la mayoría, el documento es prácticamente ilegible o no es lo que parece. Revisarlo a mano cuesta lo mismo que digitarlo desde cero, así que no tiene sentido presentarlo como "casi listo".
- **Pocos faltantes o advertencias implican PARCIAL.** El registro es **utilizable con revisión humana**: el operador completa uno o dos campos o confirma una inconsistencia del emisor. Por eso la métrica "tasa de extracción utilizable" suma EXITOSO y PARCIAL.
- **Los faltantes no se reintentan.** Si el dato no está en la fuente, reintentar solo gasta tokens o, peor, presiona al modelo a inventarlo. Solo se reintenta lo estructural (formato y tipos).

## 5. Reglas de negocio (advertencias)

Cada regla se aplica solo si sus campos están presentes. Todas generan una **advertencia** (estado PARCIAL), nunca un reintento. `tol = TOLERANCIA_MONTOS` (por defecto 0.05).

| Código | Condición |
|---|---|
| `RUC_FORMATO` | Algún RUC no cumple `^(10\|15\|17\|20)\d{9}$` |
| `NUMERO_FORMATO` | `numero_factura` no cumple `^[FBE][A-Z0-9]{3}-\d{1,8}$` |
| `FECHAS_INCOHERENTES` | `fecha_vencimiento < fecha_emision` |
| `FECHA_FUERA_DE_RANGO` | `fecha_emision` es futura o anterior al año 2000 |
| `TOTALES_INCONSISTENTES` | `abs(subtotal + igv - total) > tol` |
| `IGV_NO_18` | `abs(igv - 0.18 × subtotal) > max(tol, 0.005 × subtotal)` |
| `ITEMS_NO_CUADRAN` | `abs(Σ importes - descuento - subtotal) > max(tol, 0.005 × subtotal)`. Se omite si algún ítem no permite calcular su importe |
| `CREDITO_SIN_VENCIMIENTO` | `condicion_pago == CREDITO` y no hay `fecha_vencimiento` |
| `MONTO_NO_POSITIVO` | `total` o `subtotal` ≤ 0, o un ítem con cantidad ≤ 0 o precio < 0 |
| `POSIBLE_ALUCINACION` | `numero_factura` o algún RUC no aparece literalmente en el texto (normalizando espacios, guiones y ceros a la izquierda del correlativo) |

## 6. Datos de prueba y RUCs ficticios

Los RUCs de `data/documentos/` tienen formato válido (prefijo 20 y 11 dígitos), pero su **dígito verificador SUNAT (módulo 11) es deliberadamente incorrecto**. Así se garantiza que no corresponden a ningún contribuyente real. El sistema no valida el dígito verificador porque la especificación del proyecto solo exige el patrón.
