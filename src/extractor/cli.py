"""Interfaz de línea de comandos (typer)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax

from extractor.config import ConfiguracionInvalida, Settings, cargar_settings
from extractor.extraction import (
    SYSTEM_PROMPT,
    construir_herramienta,
    construir_mensaje_usuario,
    motivos_incompatibilidad_strict,
)
from extractor.llm_client import ClienteAnthropic, ClienteLLM, ErrorAPI, ErrorAutenticacion
from extractor.validation import parsear_respuesta

# pretty_exceptions_show_locals=False: un traceback "bonito" con variables locales
# podría exponer la API key. Nunca se muestran locales.
app = typer.Typer(
    help="Extractor de datos estructurados desde facturas SUNAT.",
    pretty_exceptions_show_locals=False,
    no_args_is_help=True,
    add_completion=False,
)
consola = Console()
CARPETA_LOGS = Path("logs")


def configurar_logging(verbose: bool = False) -> None:
    """Logs a ``logs/extractor.log`` (gitignored). El texto del documento solo en DEBUG."""
    CARPETA_LOGS.mkdir(exist_ok=True)
    manejador = logging.FileHandler(CARPETA_LOGS / "extractor.log", encoding="utf-8")
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.handlers = [manejador]
    raiz.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Las librerías HTTP podrían registrar detalles de la petición en DEBUG: se silencian.
    for ruidoso in ("anthropic", "httpx", "httpx2", "httpcore"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def _settings_o_salir(**overrides: object) -> Settings:
    """Carga la configuración o sale de forma controlada (sin traceback ni secretos)."""
    try:
        return cargar_settings(**{k: v for k, v in overrides.items() if v is not None})
    except ConfiguracionInvalida as error:
        consola.print(f"[bold red]{error}[/]")
        consola.print("Copia [bold].env.example[/] a [bold].env[/] y coloca tu ANTHROPIC_API_KEY.")
        raise typer.Exit(code=2) from None


def crear_cliente(settings: Settings) -> ClienteLLM:
    """Fábrica del cliente real (los tests la reemplazan por un fake)."""
    return ClienteAnthropic(settings.anthropic_api_key, settings.modelo, settings.timeout_s)


@app.command()
def procesar() -> None:
    """Procesa un lote de documentos (pendiente: Fase 4)."""
    _settings_o_salir()
    consola.print("[yellow]Comando aún no implementado.[/]")


@app.command()
def extraer(
    ruta: Annotated[Path, typer.Argument(help="Archivo .txt a procesar.")],
    verbose: Annotated[bool, typer.Option("--verbose", help="Log en nivel DEBUG.")] = False,
) -> None:
    """Extrae un documento individual (versión básica de la Fase 3: un intento)."""
    configurar_logging(verbose)
    settings = _settings_o_salir()
    texto = ruta.read_text(encoding="utf-8")
    cliente = crear_cliente(settings)
    try:
        respuesta = cliente.extraer(
            SYSTEM_PROMPT,
            construir_mensaje_usuario(texto, ruta.name),
            construir_herramienta(),
            settings.max_tokens,
        )
    except (ErrorAutenticacion, ErrorAPI) as error:
        consola.print(f"[bold red]{type(error).__name__}: {error}[/]")
        raise typer.Exit(code=1) from None
    consola.print(
        f"stop_reason={respuesta.stop_reason} tool_use={respuesta.hubo_tool_use} "
        f"tokens={respuesta.tokens_entrada}/{respuesta.tokens_salida} "
        f"latencia={respuesta.latencia_ms} ms"
    )
    resultado = parsear_respuesta(respuesta)
    if resultado.factura is None:
        consola.print(f"[bold red]✗ {resultado.tipo_error}[/]: {resultado.errores}")
        raise typer.Exit(code=1)
    datos = resultado.factura.model_dump(mode="json")
    consola.print(Syntax(json.dumps(datos, ensure_ascii=False, indent=2), "json"))
    consola.print("[bold green]✓ JSON válido según FacturaExtraida[/]")


@app.command()
def esquema() -> None:
    """Imprime el JSON Schema que se envía al modelo como input_schema de la herramienta."""
    herramienta = construir_herramienta()
    texto = json.dumps(herramienta["input_schema"], ensure_ascii=False, indent=2)
    consola.print(Syntax(texto, "json"))
    consola.print(f"Herramienta: [bold]{herramienta['name']}[/] (tool_choice forzado)")
    motivos = motivos_incompatibilidad_strict(herramienta["input_schema"])
    if motivos:
        consola.print(
            "strict: [yellow]desactivado[/]. El esquema supera los límites documentados "
            f"de structured outputs: {'; '.join(motivos)}. La validación Pydantic se ejecuta "
            "siempre."
        )
    else:
        consola.print("strict: [green]activado[/]")
