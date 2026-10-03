"""Procesamiento del lote con aislamiento de fallos.

Garantías:

* Cada documento va envuelto en su propio try/except: el lote NUNCA se detiene
  por un documento. Un error inesperado queda como FALLIDO con ERROR_INESPERADO
  (tipo y mensaje en el reporte; el traceback solo en ``logs/extractor.log``).
* Prechecks sin llamar a la API: documento vacío o demasiado largo.
* Circuit breaker: ante credenciales inválidas, el resto de documentos se marca
  FALLIDO con ERROR_API sin llamar a la API.
* Ctrl+C: se devuelve el resultado parcial para guardar el reporte.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

from extractor.config import Settings
from extractor.fault_injection import ClienteConFallas, ModoFallo
from extractor.llm_client import ClienteLLM, ErrorAutenticacion
from extractor.schema import CodigoMotivo, EstadoExtraccion, Intento, ResultadoDocumento
from extractor.validation import (
    Evaluacion,
    Notificador,
    ResultadoExtraccion,
    describir_intento,
    evaluar,
    extraer_con_reintentos,
    truncar,
)

logger = logging.getLogger(__name__)

DETALLE_CIRCUITO = "credenciales inválidas; no se llamó a la API"


@dataclass(frozen=True)
class Simulacion:
    """Configuración de la inyección de fallos (solo con ``--simular-fallo``)."""

    modo: ModoFallo
    patron: str | None = None

    def aplica(self, nombre: str) -> bool:
        """True si el documento coincide con ``--simular-en`` (o si no hay patrón)."""
        return self.patron is None or fnmatch(nombre, self.patron)


@dataclass
class ResultadoLote:
    """Resultados del lote y cómo terminó."""

    resultados: list[ResultadoDocumento] = field(default_factory=list)
    n_archivos: int = 0
    interrumpido: bool = False
    circuito_abierto: bool = False


def leer_documento(ruta: Path) -> str:
    """Lee en UTF-8 con fallback a latin-1 (que decodifica cualquier byte)."""
    try:
        return ruta.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("%s no es UTF-8 válido; se lee como latin-1", ruta.name)
        return ruta.read_text(encoding="latin-1")


def precheck(texto: str, settings: Settings) -> tuple[CodigoMotivo, str] | None:
    """Rechaza sin llamar a la API los documentos vacíos o demasiado largos."""
    if not texto.strip():
        return CodigoMotivo.DOCUMENTO_VACIO, "documento vacío; no se llamó a la API"
    if len(texto) > settings.max_chars_documento:
        detalle = (
            f"{len(texto)} caracteres > máximo {settings.max_chars_documento}; no se llamó a la API"
        )
        return CodigoMotivo.DOCUMENTO_DEMASIADO_LARGO, detalle
    return None


def resultado_fallido(
    archivo: str,
    motivo: CodigoMotivo,
    detalle: str,
    intentos: list[Intento] | None = None,
    llamadas_api: int = 0,
    simulacion: str | None = None,
) -> ResultadoDocumento:
    """ResultadoDocumento FALLIDO sin datos extraídos."""
    intentos = intentos or []
    return ResultadoDocumento(
        archivo=archivo,
        estado=EstadoExtraccion.FALLIDO,
        motivos=[motivo],
        detalle=detalle,
        intentos=intentos,
        llamadas_api=llamadas_api,
        tokens_entrada_total=sum(i.tokens_entrada for i in intentos),
        tokens_salida_total=sum(i.tokens_salida for i in intentos),
        latencia_ms_total=sum(i.latencia_ms for i in intentos),
        simulacion=simulacion,
    )


def construir_resultado(
    archivo: str,
    extraccion: ResultadoExtraccion,
    evaluacion: Evaluacion,
    simulacion: str | None,
) -> ResultadoDocumento:
    """Combina la extracción (intentos, tokens) con la evaluación (estado, datos)."""
    intentos = extraccion.intentos
    return ResultadoDocumento(
        archivo=archivo,
        estado=evaluacion.estado,
        motivos=evaluacion.motivos,
        detalle=evaluacion.detalle,
        campos_faltantes=evaluacion.campos_faltantes,
        advertencias=evaluacion.advertencias,
        datos=evaluacion.datos,
        intentos=intentos,
        llamadas_api=extraccion.llamadas_api,
        tokens_entrada_total=sum(i.tokens_entrada for i in intentos),
        tokens_salida_total=sum(i.tokens_salida for i in intentos),
        latencia_ms_total=sum(i.latencia_ms for i in intentos),
        simulacion=simulacion,
    )


def procesar_documento(
    ruta: Path,
    cliente: ClienteLLM,
    settings: Settings,
    simulacion: Simulacion | None = None,
    notificar: Notificador | None = None,
) -> ResultadoDocumento:
    """Lee, aplica prechecks, extrae con reintentos y evalúa un documento.

    Puede lanzar ``ErrorAutenticacion`` (circuit breaker) o errores inesperados;
    :func:`procesar_aislado` los convierte en resultados.
    """
    texto = leer_documento(ruta)
    rechazo = precheck(texto, settings)
    if rechazo is not None:
        return resultado_fallido(ruta.name, *rechazo)
    marca = None
    if simulacion is not None and simulacion.aplica(ruta.name):
        # Instancia nueva por documento: el conteo de intentos empieza en 1.
        cliente = ClienteConFallas(cliente, simulacion.modo, settings.max_intentos)
        marca = simulacion.modo.value
    extraccion = extraer_con_reintentos(texto, ruta.name, cliente, settings, notificar)
    if extraccion.factura is None:
        motivo = extraccion.motivo_fallo or CodigoMotivo.ERROR_INESPERADO
        return resultado_fallido(
            ruta.name,
            motivo,
            extraccion.detalle,
            extraccion.intentos,
            extraccion.llamadas_api,
            marca,
        )
    evaluacion = evaluar(extraccion.factura, texto, settings)
    return construir_resultado(ruta.name, extraccion, evaluacion, marca)


def procesar_aislado(
    ruta: Path,
    cliente: ClienteLLM,
    settings: Settings,
    simulacion: Simulacion | None = None,
    notificar: Notificador | None = None,
) -> ResultadoDocumento:
    """:func:`procesar_documento` con aislamiento: nada salvo la autenticación escapa."""
    try:
        return procesar_documento(ruta, cliente, settings, simulacion, notificar)
    except ErrorAutenticacion:
        raise
    except Exception as error:  # aislamiento de fallos: el lote nunca se detiene
        logger.exception("error inesperado procesando %s", ruta.name)
        detalle = truncar(f"{type(error).__name__}: {error}", 300)
        return resultado_fallido(ruta.name, CodigoMotivo.ERROR_INESPERADO, detalle)


def _notificador_lote(consola: Console | None, nombre: str) -> Notificador | None:
    """Imprime cada intento con el nombre del documento (para ver reintentos en vivo)."""
    if consola is None:
        return None

    def notificar(intento: Intento, max_intentos: int, se_reintentara: bool) -> None:
        color = "green" if intento.ok else ("yellow" if se_reintentara else "red")
        linea = describir_intento(intento, max_intentos, se_reintentara)
        consola.print(f"  [dim]{nombre}[/] [{color}]{linea}[/]")

    return notificar


def procesar_lote(
    carpeta: Path,
    cliente: ClienteLLM,
    settings: Settings,
    simulacion: Simulacion | None = None,
    consola: Console | None = None,
    al_terminar_documento: Callable[[ResultadoDocumento], None] | None = None,
) -> ResultadoLote:
    """Procesa los ``.txt`` de ``carpeta`` en orden alfabético con aislamiento de fallos."""
    archivos = sorted(carpeta.glob("*.txt"))
    lote = ResultadoLote(n_archivos=len(archivos))
    progreso = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=consola,
        disable=consola is None,
        transient=True,
    )
    with progreso:
        tarea = progreso.add_task("Procesando", total=len(archivos))
        try:
            for ruta in archivos:
                progreso.update(tarea, description=f"Procesando {ruta.name}")
                if lote.circuito_abierto:
                    resultado = resultado_fallido(
                        ruta.name, CodigoMotivo.ERROR_API, DETALLE_CIRCUITO
                    )
                else:
                    resultado = _procesar_con_circuito(
                        ruta, cliente, settings, simulacion, consola, lote
                    )
                lote.resultados.append(resultado)
                if al_terminar_documento is not None:
                    al_terminar_documento(resultado)
                progreso.advance(tarea)
        except KeyboardInterrupt:
            lote.interrumpido = True
            logger.warning(
                "lote interrumpido por el usuario tras %d documentos", len(lote.resultados)
            )
    return lote


def _procesar_con_circuito(
    ruta: Path,
    cliente: ClienteLLM,
    settings: Settings,
    simulacion: Simulacion | None,
    consola: Console | None,
    lote: ResultadoLote,
) -> ResultadoDocumento:
    """Procesa un documento y abre el circuito si las credenciales son inválidas."""
    try:
        return procesar_aislado(
            ruta, cliente, settings, simulacion, _notificador_lote(consola, ruta.name)
        )
    except ErrorAutenticacion as error:
        lote.circuito_abierto = True
        logger.error("circuit breaker abierto: %s", error)
        return resultado_fallido(ruta.name, CodigoMotivo.ERROR_API, str(error), llamadas_api=1)
