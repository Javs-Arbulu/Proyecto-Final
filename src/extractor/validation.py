"""Parseo, validación y reintentos de formato: el corazón del proyecto.

Orden de cada intento (ver :func:`parsear_respuesta` y :func:`extraer_con_reintentos`):

1. Llamar al cliente.
2. Revisar ``finish_reason`` ANTES de parsear: una respuesta cortada por
   ``MAX_TOKENS`` puede verse como un JSON válido pero incompleto.
3. ``json.loads``; si falla, fallback secundario que busca el primer objeto JSON
   balanceado del texto (nunca es la estrategia principal).
4. ``FacturaExtraida.model_validate``: la validación Pydantic se ejecuta SIEMPRE,
   aunque el proveedor prometa cumplir el esquema el 100 % de las veces.

Solo se reintenta lo ESTRUCTURAL (formato y tipos), porque es corregible. Los
campos faltantes no se reintentan: si el dato no está en la fuente, reintentar
solo gasta tokens.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from extractor.config import TOPE_MAX_TOKENS, Settings
from extractor.extraction import SYSTEM_PROMPT, construir_mensaje_usuario, esquema_para_llm
from extractor.llm_client import FINALIZACIONES_NORMALES, ClienteLLM, ErrorAPI, RespuestaLLM
from extractor.schema import CodigoMotivo, FacturaExtraida, Intento, TipoError

logger = logging.getLogger(__name__)

MAX_ERRORES_FEEDBACK = 8
MAX_CHARS_VALOR = 60
MAX_CHARS_DETALLE = 300

MOTIVO_POR_ERROR: dict[TipoError, CodigoMotivo] = {
    TipoError.JSON_INVALIDO: CodigoMotivo.JSON_INVALIDO_REINTENTOS_AGOTADOS,
    TipoError.ESQUEMA_INVALIDO: CodigoMotivo.ESQUEMA_INVALIDO_REINTENTOS_AGOTADOS,
    TipoError.RESPUESTA_TRUNCADA: CodigoMotivo.RESPUESTA_TRUNCADA,
    TipoError.FINALIZACION_INESPERADA: CodigoMotivo.ERROR_API,
}

NOMBRE_ERROR: dict[TipoError, str] = {
    TipoError.JSON_INVALIDO: "JSON inválido",
    TipoError.ESQUEMA_INVALIDO: "esquema inválido",
    TipoError.RESPUESTA_TRUNCADA: "respuesta truncada",
    TipoError.FINALIZACION_INESPERADA: "finalización inesperada",
}

# ---------------------------------------------------------------------------
# Parseo de una respuesta
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoParseo:
    """Resultado de verificar una respuesta del modelo."""

    factura: FacturaExtraida | None
    tipo_error: TipoError | None = None
    errores: list[str] = field(default_factory=list)
    uso_fallback: bool = False

    @property
    def ok(self) -> bool:
        """True si la respuesta produjo un ``FacturaExtraida`` válido."""
        return self.factura is not None


def truncar(texto: str, maximo: int) -> str:
    """Recorta un texto largo agregando una elipsis."""
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _fin_de_objeto(texto: str, inicio: int) -> int | None:
    """Posición de la llave que cierra el objeto que abre en ``inicio`` (respeta strings)."""
    profundidad, en_string, escapado = 0, False, False
    for posicion in range(inicio, len(texto)):
        caracter = texto[posicion]
        if en_string:
            if escapado:
                escapado = False
            elif caracter == "\\":
                escapado = True
            elif caracter == '"':
                en_string = False
        elif caracter == '"':
            en_string = True
        elif caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad -= 1
            if profundidad == 0:
                return posicion
    return None


def extraer_primer_objeto_json(texto: str) -> str | None:
    """Primer objeto JSON de nivel superior, balanceado y parseable, dentro del texto.

    Si una llave ``{`` nunca se cierra, todo lo que sigue está anidado en ella y no
    hay objeto de nivel superior: así un JSON cortado a la mitad no se "rescata"
    devolviendo uno de sus ítems internos.
    """
    inicio = texto.find("{")
    while inicio != -1:
        fin = _fin_de_objeto(texto, inicio)
        if fin is None:
            return None
        candidato = texto[inicio : fin + 1]
        try:
            if isinstance(json.loads(candidato), dict):
                return candidato
        except json.JSONDecodeError:
            pass
        inicio = texto.find("{", fin + 1)
    return None


def formatear_errores_validacion(error: ValidationError) -> list[str]:
    """Errores de Pydantic en formato compacto: campo, problema y valor recibido."""
    lineas = []
    for detalle in error.errors(include_url=False)[:MAX_ERRORES_FEEDBACK]:
        campo = ".".join(str(parte) for parte in detalle["loc"]) or "(objeto raíz)"
        if detalle["type"] == "missing":
            lineas.append(f"{campo}: falta la clave (inclúyela; usa null si no hay dato)")
            continue
        recibido = truncar(repr(detalle.get("input")), MAX_CHARS_VALOR)
        lineas.append(f"{campo}: {detalle['msg']} (recibido: {recibido})")
    restantes = error.error_count() - MAX_ERRORES_FEEDBACK
    if restantes > 0:
        lineas.append(f"... y {restantes} errores más")
    return lineas


def _error(tipo: TipoError, mensaje: str, uso_fallback: bool = False) -> ResultadoParseo:
    return ResultadoParseo(None, tipo, [mensaje], uso_fallback)


def _revisar_finalizacion(finish_reason: str | None) -> ResultadoParseo | None:
    """Paso 2: ``finish_reason`` se revisa ANTES de parsear."""
    if finish_reason == "MAX_TOKENS":
        return _error(
            TipoError.RESPUESTA_TRUNCADA,
            "la respuesta se cortó por MAX_TOKENS; el objeto puede estar incompleto. "
            "Genera el objeto completo y usa observaciones breves.",
        )
    if finish_reason not in FINALIZACIONES_NORMALES:
        return _error(
            TipoError.FINALIZACION_INESPERADA,
            f"la generación terminó con finish_reason={finish_reason}",
        )
    return None


def _cargar_json(texto: str) -> tuple[object | None, ResultadoParseo | None, bool]:
    """Paso 3: ``json.loads`` con fallback al primer objeto balanceado."""
    try:
        return json.loads(texto), None, False
    except json.JSONDecodeError as error:
        candidato = extraer_primer_objeto_json(texto)
        if candidato is None:
            detalle = f"JSON inválido: {error.msg} (línea {error.lineno}, columna {error.colno})"
            return None, _error(TipoError.JSON_INVALIDO, detalle, uso_fallback=True), True
        return json.loads(candidato), None, True


def parsear_respuesta(respuesta: RespuestaLLM) -> ResultadoParseo:
    """Verifica una respuesta: finish_reason → JSON (con fallback) → esquema."""
    error_finalizacion = _revisar_finalizacion(respuesta.finish_reason)
    if error_finalizacion is not None:
        return error_finalizacion
    datos, error_json, uso_fallback = _cargar_json(respuesta.texto_crudo)
    if error_json is not None:
        return error_json
    if not isinstance(datos, dict):
        return _error(
            TipoError.ESQUEMA_INVALIDO,
            f"se esperaba un objeto JSON y se recibió {type(datos).__name__}",
            uso_fallback,
        )
    try:
        factura = FacturaExtraida.model_validate(datos)
    except ValidationError as error:
        return ResultadoParseo(
            None, TipoError.ESQUEMA_INVALIDO, formatear_errores_validacion(error), uso_fallback
        )
    return ResultadoParseo(factura, uso_fallback=uso_fallback)


# ---------------------------------------------------------------------------
# Reintentos de formato
# ---------------------------------------------------------------------------

Notificador = Callable[[Intento, int, bool], None]


@dataclass
class ResultadoExtraccion:
    """Resultado de la etapa de extracción (antes de las reglas de negocio)."""

    factura: FacturaExtraida | None
    intentos: list[Intento]
    motivo_fallo: CodigoMotivo | None = None
    detalle: str = ""


def describir_intento(intento: Intento, max_intentos: int, se_reintentara: bool) -> str:
    """Línea legible de un intento, p. ej. ``intento 1/3 ✗ JSON inválido: ... → reintentando``."""
    cabecera = f"intento {intento.numero}/{max_intentos}"
    if intento.ok:
        extra = " (vía fallback)" if intento.uso_fallback else ""
        return f"{cabecera} ✓ JSON válido según el esquema{extra}"
    nombre = NOMBRE_ERROR[intento.tipo_error] if intento.tipo_error else "error"
    sufijo = " → reintentando con feedback" if se_reintentara else " → sin intentos restantes"
    return f"{cabecera} ✗ {nombre}: {truncar(intento.detalle_error or '', 120)}{sufijo}"


def _registrar_intento(
    numero: int, respuesta: RespuestaLLM, parseo: ResultadoParseo, max_tokens: int
) -> Intento:
    return Intento(
        numero=numero,
        ok=parseo.ok,
        tipo_error=parseo.tipo_error,
        detalle_error=truncar("; ".join(parseo.errores), MAX_CHARS_DETALLE) or None,
        tokens_entrada=respuesta.tokens_entrada,
        tokens_salida=respuesta.tokens_salida,
        latencia_ms=respuesta.latencia_ms,
        uso_fallback=parseo.uso_fallback,
        finish_reason=respuesta.finish_reason,
        max_tokens=max_tokens,
    )


def extraer_con_reintentos(
    texto: str,
    nombre: str,
    cliente: ClienteLLM,
    settings: Settings,
    notificar: Notificador | None = None,
) -> ResultadoExtraccion:
    """Extrae con como máximo ``settings.max_intentos`` llamadas de formato.

    Es un bucle ``for`` acotado (nunca ``while True``). ``ErrorAutenticacion`` se
    propaga al pipeline (circuit breaker); ``ErrorAPI`` termina el documento con
    ERROR_API sin consumir más intentos de formato.
    """
    logger.debug("texto del documento %s:\n%s", nombre, texto)
    esquema = esquema_para_llm()
    intentos: list[Intento] = []
    errores_previos: list[str] | None = None
    max_tokens = settings.max_tokens
    ultimo_error = TipoError.JSON_INVALIDO
    for numero in range(1, settings.max_intentos + 1):
        mensaje = construir_mensaje_usuario(texto, nombre, errores_previos)
        try:
            respuesta = cliente.extraer(SYSTEM_PROMPT, mensaje, esquema, max_tokens)
        except ErrorAPI as error:
            return ResultadoExtraccion(None, intentos, CodigoMotivo.ERROR_API, str(error))
        parseo = parsear_respuesta(respuesta)
        intento = _registrar_intento(numero, respuesta, parseo, max_tokens)
        intentos.append(intento)
        logger.info("%s: %s", nombre, describir_intento(intento, settings.max_intentos, False))
        if notificar is not None:
            notificar(
                intento, settings.max_intentos, not parseo.ok and numero < settings.max_intentos
            )
        if parseo.factura is not None:
            return ResultadoExtraccion(parseo.factura, intentos)
        ultimo_error = parseo.tipo_error or TipoError.JSON_INVALIDO
        errores_previos = parseo.errores
        if ultimo_error is TipoError.RESPUESTA_TRUNCADA:
            max_tokens = min(max_tokens * 2, TOPE_MAX_TOKENS)
    detalle = (
        f"{settings.max_intentos} intento(s) agotado(s); último error "
        f"{NOMBRE_ERROR[ultimo_error]}: {intentos[-1].detalle_error}"
    )
    return ResultadoExtraccion(None, intentos, MOTIVO_POR_ERROR[ultimo_error], detalle)
