# Reporte del lote

- Fecha: 2026-10-03T18:52:50-05:00
- Proveedor / modelo: cohere / `command-a-03-2025`
- Prompt: v2.0
- Simulación: {'modo': 'json_invalido', 'patron': None}
- Interrumpido: no

## Métricas

| Métrica | Valor |
|---|---|
| Documentos | 11 |
| Éxito total | 6 (54.5 %) |
| Éxito parcial | 2 (18.2 %) |
| Fallido | 3 (27.3 %) |
| Tasa de extracción utilizable | 72.7 % |
| Intentos promedio (docs con llamadas) | 3.0 |
| Documentos con reintento | 10 |
| Llamadas totales a la API | 30 |
| Tokens entrada / salida | 61550 / 11483 |
| Latencia promedio por documento | 15059 ms |
| Estado coincide con esperado | 11/11 (100.0 %) |
| Exactitud de campos clave | 100.0 % |

## Resultados por documento

| Archivo | Estado | Esperado | Motivos | Faltantes | Advertencias | Intentos | Tokens | Latencia (ms) |
|---|---|---|---|---|---|---|---|---|
| 01_factura_estandar.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 3 | 7518 | 14954 |
| 02_factura_usd_credito.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 3 | 7199 | 16474 |
| 03_correo_proveedor.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 3 | 6782 | 11075 |
| 04_formato_inusual.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 3 | 7240 | 17589 |
| 05_factura_larga.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 3 | 10528 | 31354 |
| 06_incompleta_ocr.txt | **PARCIAL** | PARCIAL ✓ | CAMPOS_OBLIGATORIOS_FALTANTES | fecha_emision, ruc_cliente | — | 3 | 6906 | 14795 |
| 07_totales_inconsistentes.txt | **PARCIAL** | PARCIAL ✓ | REGLA_NEGOCIO | — | TOTALES_INCONSISTENTES | 3 | 6744 | 14214 |
| 08_no_es_factura.txt | **FALLIDO** | FALLIDO ✓ | FUERA_DE_DOMINIO | — | — | 3 | 6447 | 7520 |
| 09_prompt_injection.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 3 | 7064 | 14956 |
| 10_ilegible.txt | **FALLIDO** | FALLIDO ✓ | EXTRACCION_INSUFICIENTE | fecha_emision, moneda, numero_factura, razon_social_emisor, ruc_cliente, total | — | 3 | 6605 | 7655 |
| 11_vacio.txt | **FALLIDO** | FALLIDO ✓ | DOCUMENTO_VACIO | — | — | 0 | 0 | 0 |

## Detalle por documento

- **01_factura_estandar.txt**: factura válida y lista para registrar
- **02_factura_usd_credito.txt**: factura válida y lista para registrar
- **03_correo_proveedor.txt**: factura válida y lista para registrar
- **04_formato_inusual.txt**: factura válida y lista para registrar
- **05_factura_larga.txt**: factura válida y lista para registrar
- **06_incompleta_ocr.txt**: requiere revisión humana: faltan ['fecha_emision', 'ruc_cliente']
- **07_totales_inconsistentes.txt**: requiere revisión humana: advertencias ['TOTALES_INCONSISTENTES']
- **08_no_es_factura.txt**: el documento no es una factura (tipo_documento detectado: OTRO)
- **09_prompt_injection.txt**: factura válida y lista para registrar
- **10_ilegible.txt**: faltan campos críticos ['numero_factura', 'total']
- **11_vacio.txt**: documento vacío; no se llamó a la API

## Patrón de los fallos y advertencias

- Motivo `CAMPOS_OBLIGATORIOS_FALTANTES`: 1
- Motivo `REGLA_NEGOCIO`: 1
- Motivo `FUERA_DE_DOMINIO`: 1
- Motivo `EXTRACCION_INSUFICIENTE`: 1
- Motivo `DOCUMENTO_VACIO`: 1
- Advertencia `TOTALES_INCONSISTENTES`: 1

## Exactitud por campo clave

| Campo | Aciertos | % |
|---|---|---|
| numero_factura | 8/8 | 100.0 |
| fecha_emision | 8/8 | 100.0 |
| ruc_emisor | 8/8 | 100.0 |
| moneda | 8/8 | 100.0 |
| total | 8/8 | 100.0 |
