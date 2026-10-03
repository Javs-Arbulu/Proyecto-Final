"""Interfaz de línea de comandos (typer)."""

from __future__ import annotations

import typer
from rich.console import Console

from extractor.config import ConfiguracionInvalida, Settings, cargar_settings

# pretty_exceptions_show_locals=False: un traceback "bonito" con variables locales
# podría exponer la API key. Nunca se muestran locales.
app = typer.Typer(
    help="Extractor de datos estructurados desde facturas SUNAT.",
    pretty_exceptions_show_locals=False,
    no_args_is_help=True,
    add_completion=False,
)
consola = Console()


def _settings_o_salir(**overrides: object) -> Settings:
    """Carga la configuración o sale de forma controlada (sin traceback ni secretos)."""
    try:
        return cargar_settings(**overrides)
    except ConfiguracionInvalida as error:
        consola.print(f"[bold red]{error}[/]")
        consola.print("Copia [bold].env.example[/] a [bold].env[/] y coloca tu ANTHROPIC_API_KEY.")
        raise typer.Exit(code=2) from None


@app.command()
def procesar() -> None:
    """Procesa un lote de documentos (pendiente: Fase 4)."""
    _settings_o_salir()
    consola.print("[yellow]Comando aún no implementado.[/]")


@app.command()
def extraer() -> None:
    """Extrae un documento individual (pendiente: Fase 3)."""
    _settings_o_salir()
    consola.print("[yellow]Comando aún no implementado.[/]")


@app.command()
def esquema() -> None:
    """Imprime el JSON Schema enviado al modelo (pendiente: Fase 1)."""
    consola.print("[yellow]Comando aún no implementado.[/]")
