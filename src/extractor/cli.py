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
from extractor.extraction import PROMPT_VERSION, esquema_para_llm, problemas_compatibilidad_cohere
from extractor.fault_injection import ModoFallo
from extractor.llm_client import ClienteCohere, ClienteLLM, ErrorAutenticacion
from extractor.pipeline import Simulacion, procesar_aislado, procesar_lote
from extractor.report import (
    COLOR_ESTADO,
    calcular_metricas,
    cargar_esperado,
    carpeta_salida,
    construir_metadatos,
    escribir_reportes,
    mostrar_en_consola,
)
from extractor.schema import Intento, ResultadoDocumento
from extractor.validation import describir_intento

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
RUTA_ESPERADO = Path("data") / "esperado.json"


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


def _simulacion(modo: ModoFallo | None, patron: str | None) -> Simulacion | None:
    if modo is None:
        if patron is not None:
            consola.print("[bold red]--simular-en requiere --simular-fallo[/]")
            raise typer.Exit(code=2)
        return None
    destino = f" (solo {patron})" if patron else ""
    consola.print(f"[bold magenta][SIMULACIÓN: {modo.value}]{destino}[/]")
    return Simulacion(modo, patron)


def _imprimir_intento(intento: Intento, max_intentos: int, se_reintentara: bool) -> None:
    color = "green" if intento.ok else ("yellow" if se_reintentara else "red")
    consola.print(f"  [{color}]{describir_intento(intento, max_intentos, se_reintentara)}[/]")


def _imprimir_resultado(resultado: ResultadoDocumento) -> None:
    color = COLOR_ESTADO[resultado.estado]
    motivos = ", ".join(m.value for m in resultado.motivos)
    consola.print(f"[bold {color}]{resultado.estado.value}[/] · {motivos} · {resultado.detalle}")
    if resultado.campos_faltantes:
        consola.print(f"  campos faltantes: {', '.join(resultado.campos_faltantes)}")
    for advertencia in resultado.advertencias:
        consola.print(f"  [yellow]⚠ {advertencia.codigo.value}[/]: {advertencia.mensaje}")
    consola.print(
        f"  [dim]llamadas a la API: {resultado.llamadas_api} · tokens "
        f"{resultado.tokens_entrada_total}/{resultado.tokens_salida_total} · "
        f"{resultado.latencia_ms_total} ms[/]"
    )


OpcionSimular = Annotated[
    ModoFallo | None,
    typer.Option("--simular-fallo", help="Inyecta un fallo determinista (nunca por defecto)."),
]
OpcionVerbose = Annotated[bool, typer.Option("--verbose", help="Log en nivel DEBUG.")]


@app.command()
def procesar(
    carpeta: Annotated[Path, typer.Argument(help="Carpeta con documentos .txt.")],
    max_intentos: Annotated[
        int | None, typer.Option("--max-intentos", help="Total de llamadas de formato (1-5).")
    ] = None,
    simular_fallo: OpcionSimular = None,
    simular_en: Annotated[
        str | None, typer.Option("--simular-en", help="Patrón glob de documentos a simular.")
    ] = None,
    salida: Annotated[Path, typer.Option("--salida", help="Carpeta de reportes.")] = Path("output"),
    verbose: OpcionVerbose = False,
) -> None:
    """Procesa un lote de documentos y genera los reportes."""
    configurar_logging(verbose)
    settings = _settings_o_salir(max_intentos=max_intentos)
    if not carpeta.is_dir():
        consola.print(f"[bold red]No existe la carpeta {carpeta}[/]")
        raise typer.Exit(code=2)
    simulacion = _simulacion(simular_fallo, simular_en)
    consola.print(
        f"Procesando [bold]{carpeta}[/] · cohere / {settings.modelo} · "
        f"max_intentos={settings.max_intentos}"
    )
    lote = procesar_lote(
        carpeta,
        crear_cliente(settings),
        settings,
        simulacion,
        consola,
        al_terminar_documento=lambda r: consola.print(
            f"[{COLOR_ESTADO[r.estado]}]● {r.archivo}: {r.estado.value}[/] "
            f"({', '.join(m.value for m in r.motivos)})"
        ),
    )
    metricas = calcular_metricas(
        lote.resultados, cargar_esperado(RUTA_ESPERADO), settings.tolerancia_montos
    )
    metadatos = construir_metadatos(
        settings.modelo,
        PROMPT_VERSION,
        settings.resumen_publico(),
        {"modo": simulacion.modo.value, "patron": simulacion.patron} if simulacion else None,
        lote.interrumpido,
        lote.n_archivos,
    )
    destino = carpeta_salida(salida, simulacion.modo.value if simulacion else None)
    rutas = escribir_reportes(destino, lote.resultados, metricas, metadatos)
    mostrar_en_consola(consola, lote.resultados, metricas)
    consola.print(f"Reportes: {', '.join(str(ruta) for ruta in rutas)}")
    if lote.interrumpido:
        consola.print("[bold yellow]Lote interrumpido: se guardó el reporte parcial.[/]")
        raise typer.Exit(code=130)
    if lote.circuito_abierto:
        consola.print("[bold red]Credenciales inválidas: circuit breaker abierto.[/]")
        raise typer.Exit(code=1)


@app.command()
def extraer(
    ruta: Annotated[Path, typer.Argument(help="Archivo .txt a procesar.")],
    simular_fallo: OpcionSimular = None,
    verbose: OpcionVerbose = False,
) -> None:
    """Extrae un documento individual mostrando cada intento (pensado para la demo)."""
    configurar_logging(verbose)
    settings = _settings_o_salir()
    if not ruta.is_file():
        consola.print(f"[bold red]No existe el archivo {ruta}[/]")
        raise typer.Exit(code=2)
    simulacion = _simulacion(simular_fallo, None)
    consola.print(f"[bold]{ruta.name}[/] · cohere / {settings.modelo}")
    try:
        resultado = procesar_aislado(
            ruta, crear_cliente(settings), settings, simulacion, _imprimir_intento
        )
    except ErrorAutenticacion as error:
        consola.print(f"[bold red]ErrorAutenticacion: {error}[/]")
        raise typer.Exit(code=1) from None
    if resultado.datos is not None:
        consola.print(Syntax(json.dumps(resultado.datos, ensure_ascii=False, indent=2), "json"))
    _imprimir_resultado(resultado)


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
