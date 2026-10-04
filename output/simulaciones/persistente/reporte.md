# Reporte del lote

- Fecha: 2026-10-04T10:18:41-05:00
- Proveedor / modelo: cohere / `command-a-03-2025`
- Prompt: v2.0
- Simulación: {'modo': 'persistente', 'patron': '03*'}
- Interrumpido: no

## Métricas

| Métrica | Valor |
|---|---|
| Documentos | 11 |
| Éxito total | 5 (45.5 %) |
| Éxito parcial | 2 (18.2 %) |
| Fallido | 4 (36.4 %) |
| Tasa de extracción utilizable | 63.6 % |
| Intentos promedio (docs con llamadas) | 1.1 |
| Documentos con reintento | 1 |
| Llamadas totales a la API | 11 |
| Tokens entrada / salida | 21892 / 4129 |
| Latencia promedio por documento | 5551 ms |
| Estado coincide con esperado | 10/11 (90.9 %) |
| Exactitud de campos clave | 87.5 % |

## Resultados por documento

| Archivo | Estado | Esperado | Motivos | Faltantes | Advertencias | Intentos | Tokens | Latencia (ms) |
|---|---|---|---|---|---|---|---|---|
| 01_factura_estandar.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 1 | 2444 | 5500 |
| 02_factura_usd_credito.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 1 | 2337 | 6006 |
| 03_correo_proveedor.txt | **FALLIDO** | EXITOSO ✗ | JSON_INVALIDO_REINTENTOS_AGOTADOS | — | — | 2 | 4493 | 7264 |
| 04_formato_inusual.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 1 | 2340 | 5573 |
| 05_factura_larga.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 1 | 3448 | 11232 |
| 06_incompleta_ocr.txt | **PARCIAL** | PARCIAL ✓ | CAMPOS_OBLIGATORIOS_FALTANTES | fecha_emision, ruc_cliente | — | 1 | 2247 | 4381 |
| 07_totales_inconsistentes.txt | **PARCIAL** | PARCIAL ✓ | REGLA_NEGOCIO | — | TOTALES_INCONSISTENTES | 1 | 2196 | 7018 |
| 08_no_es_factura.txt | **FALLIDO** | FALLIDO ✓ | FUERA_DE_DOMINIO | — | — | 1 | 2079 | 2021 |
| 09_prompt_injection.txt | **EXITOSO** | EXITOSO ✓ | OK | — | — | 1 | 2302 | 4467 |
| 10_ilegible.txt | **FALLIDO** | FALLIDO ✓ | EXTRACCION_INSUFICIENTE | fecha_emision, moneda, numero_factura, razon_social_emisor, ruc_cliente, total | — | 1 | 2135 | 2046 |
| 11_vacio.txt | **FALLIDO** | FALLIDO ✓ | DOCUMENTO_VACIO | — | — | 0 | 0 | 0 |

## Detalle por documento

- **01_factura_estandar.txt**: factura válida y lista para registrar
- **02_factura_usd_credito.txt**: factura válida y lista para registrar
- **03_correo_proveedor.txt**: 2 intento(s) agotado(s); último error JSON inválido: JSON inválido: Unterminated string starting at (línea 10, columna 3)
- **04_formato_inusual.txt**: factura válida y lista para registrar
- **05_factura_larga.txt**: factura válida y lista para registrar
- **06_incompleta_ocr.txt**: requiere revisión humana: faltan ['fecha_emision', 'ruc_cliente']
- **07_totales_inconsistentes.txt**: requiere revisión humana: advertencias ['TOTALES_INCONSISTENTES']
- **08_no_es_factura.txt**: el documento no es una factura (tipo_documento detectado: OTRO)
- **09_prompt_injection.txt**: factura válida y lista para registrar
- **10_ilegible.txt**: faltan campos críticos ['numero_factura', 'total']
- **11_vacio.txt**: documento vacío; no se llamó a la API

## Patrón de los fallos y advertencias

- Motivo `JSON_INVALIDO_REINTENTOS_AGOTADOS`: 1
- Motivo `CAMPOS_OBLIGATORIOS_FALTANTES`: 1
- Motivo `REGLA_NEGOCIO`: 1
- Motivo `FUERA_DE_DOMINIO`: 1
- Motivo `EXTRACCION_INSUFICIENTE`: 1
- Motivo `DOCUMENTO_VACIO`: 1
- Advertencia `TOTALES_INCONSISTENTES`: 1

## Exactitud por campo clave

| Campo | Aciertos | % |
|---|---|---|
| numero_factura | 7/8 | 87.5 |
| fecha_emision | 7/8 | 87.5 |
| ruc_emisor | 7/8 | 87.5 |
| moneda | 7/8 | 87.5 |
| total | 7/8 | 87.5 |
