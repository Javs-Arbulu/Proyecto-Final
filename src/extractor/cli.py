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
from extractor.extraction import esquema_para_llm, problemas_compatibilidad_cohere
from extractor.fault_injection import ClienteConFallas, ModoFallo
from extractor.llm_client import ClienteCohere, ClienteLLM, ErrorAutenticacion
from extractor.schema import EstadoExtraccion, Intento
from extractor.validation import Evaluacion, describir_intento, evaluar, extraer_con_reintentos

# pretty_exceptions_show_locals=False: un traceback "bonito" con variables locales
# podría exponer la API key. Nunca se muestran locales.
app = typer.Typer(
    help="Extractor de datos estructurados desde facturas SUNAT (Cohere Structured Outputs).",
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
    for ruidoso in ("cohere", "httpx", "httpcore"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def _settings_o_salir(**overrides: object) -> Settings:
    """Carga la configuración o sale de forma controlada (sin traceback ni secretos)."""
    try:
        return cargar_settings(**{k: v for k, v in overrides.items() if v is not None})
    except ConfiguracionInvalida as error:
        consola.print(f"[bold red]{error}[/]")
        consola.print("Copia [bold].env.example[/] a [bold].env[/] y coloca tu COHERE_API_KEY.")
        raise typer.Exit(code=2) from None


def crear_cliente(settings: Settings) -> ClienteLLM:
    """Fábrica del cliente real (los tests la reemplazan por un fake)."""
    return ClienteCohere(
        settings.cohere_api_key, settings.modelo, settings.timeout_s, settings.intervalo_min_s
    )


COLOR_ESTADO = {
    EstadoExtraccion.EXITOSO: "green",
    EstadoExtraccion.PARCIAL: "yellow",
    EstadoExtraccion.FALLIDO: "red",
}


def _imprimir_evaluacion(evaluacion: Evaluacion) -> None:
    color = COLOR_ESTADO[evaluacion.estado]
    motivos = ", ".join(m.value for m in evaluacion.motivos)
    consola.print(f"[bold {color}]{evaluacion.estado.value}[/] · {motivos} · {evaluacion.detalle}")
    if evaluacion.campos_faltantes:
        consola.print(f"  campos faltantes: {', '.join(evaluacion.campos_faltantes)}")
    for advertencia in evaluacion.advertencias:
        consola.print(f"  [yellow]⚠ {advertencia.codigo.value}[/]: {advertencia.mensaje}")


def _imprimir_intento(intento: Intento, max_intentos: int, se_reintentara: bool) -> None:
    color = "green" if intento.ok else ("yellow" if se_reintentara else "red")
    consola.print(f"  [{color}]{describir_intento(intento, max_intentos, se_reintentara)}[/]")


@app.command()
def procesar() -> None:
    """Procesa un lote de documentos (pendiente: Fase 4)."""
    _settings_o_salir()
    consola.print("[yellow]Comando aún no implementado.[/]")


@app.command()
def extraer(
    ruta: Annotated[Path, typer.Argument(help="Archivo .txt a procesar.")],
    simular_fallo: Annotated[
        ModoFallo | None, typer.Option("--simular-fallo", help="Inyecta un fallo determinista.")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="Log en nivel DEBUG.")] = False,
) -> None:
    """Extrae un documento individual mostrando cada intento."""
    configurar_logging(verbose)
    settings = _settings_o_salir()
    texto = ruta.read_text(encoding="utf-8")
    cliente = crear_cliente(settings)
    if simular_fallo is not None:
        consola.print(f"[bold magenta][SIMULACIÓN: {simular_fallo.value}][/]")
        cliente = ClienteConFallas(cliente, simular_fallo, settings.max_intentos)
    consola.print(f"[bold]{ruta.name}[/] · modelo {settings.modelo}")
    try:
        resultado = extraer_con_reintentos(
            texto, ruta.name, cliente, settings, notificar=_imprimir_intento
        )
    except ErrorAutenticacion as error:
        consola.print(f"[bold red]ErrorAutenticacion: {error}[/]")
        raise typer.Exit(code=1) from None
    if resultado.factura is None:
        consola.print(f"[bold red]FALLIDO · {resultado.motivo_fallo}[/]: {resultado.detalle}")
        raise typer.Exit(code=1)
    evaluacion = evaluar(resultado.factura, texto, settings)
    consola.print(Syntax(json.dumps(evaluacion.datos, ensure_ascii=False, indent=2), "json"))
    _imprimir_evaluacion(evaluacion)


@app.command()
def esquema() -> None:
    """Imprime el JSON Schema que se envía a Cohere en ``response_format``."""
    esquema_json = esquema_para_llm()
    consola.print(Syntax(json.dumps(esquema_json, ensure_ascii=False, indent=2), "json"))
    problemas = problemas_compatibilidad_cohere(esquema_json)
    if problemas:
        consola.print(f"[bold red]Incompatible con Structured Outputs de Cohere:[/] {problemas}")
        raise typer.Exit(code=1)
    consola.print(
        "[green]Compatible con Structured Outputs de Cohere[/] "
        '(response_format={"type": "json_object", "json_schema": ...}). '
        "La validación Pydantic se ejecuta siempre."
    )
