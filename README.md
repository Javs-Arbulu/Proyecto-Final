# extractor-facturas

Extractor de datos estructurados desde **facturas electrónicas peruanas (SUNAT)** en texto libre (PDF transcrito, OCR o correos de proveedores), con **Structured Outputs de Cohere** (JSON Schema) y validación programática robusta. Es el proyecto final de *Fundamentos de Arquitectura LLM*, Opción 3.

> El structured output es una técnica de **confiabilidad**, no de formato. Este proyecto demuestra qué pasa cuando el modelo devuelve algo que no calza con el esquema. Cohere promete el 100 %, y aun así validamos.

*Documentación completa (diagramas, resultados y rúbrica): en construcción, se completa en la Fase 5.*

## Instalación

Requiere Python 3.11 o superior.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuración de la API key de Cohere

1. Crea una cuenta gratuita en [dashboard.cohere.com](https://dashboard.cohere.com) e inicia sesión.
2. Ve a **API Keys** ([dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys)) y copia la *Trial key* que se crea por defecto, o genera una nueva. La key de prueba es gratuita y admite 20 llamadas por minuto y 1.000 por mes.
3. Crea tu `.env` desde la plantilla y pega la key en `COHERE_API_KEY`:

```bash
cp .env.example .env
```

`.env` está en `.gitignore` y nunca se versiona. La aplicación respeta la cuota con un intervalo mínimo entre llamadas (`INTERVALO_MIN_S=3.1`).

## Uso

```bash
python -m extractor esquema
```

```bash
python -m extractor extraer data/documentos/01_factura_estandar.txt --verbose
```

```bash
pytest -q
```
