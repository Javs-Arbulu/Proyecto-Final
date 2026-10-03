"""Parseo y validación de la respuesta del modelo.

Orden de verificación de cada respuesta (ver :func:`parsear_respuesta`):

1. ``stop_reason`` ANTES de parsear: un tool_use cortado por ``max_tokens`` puede
   verse como un objeto válido pero incompleto.
2. Si no hubo tool_use: fallback secundario que busca el primer objeto JSON
   balanceado en el texto (nunca es la estrategia principal).
3. ``json.loads``.
4. ``FacturaExtraida.model_validate``: la validación Pydantic se ejecuta SIEMPRE,
   aunque el proveedor "prometa" cumplir el esquema.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from extractor.extraction import NOMBRE_HERRAMIENTA
from extractor.llm_client import RespuestaLLM
from extractor.schema import FacturaExtraida, TipoError

logger = logging.getLogger(__name__)

MAX_ERRORES_FEEDBACK = 8
MAX_CHARS_VALOR = 60
MAX_CHARS_DETALLE = 300


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


def extraer_primer_objeto_json(texto: str) -> str | None:
    """Devuelve el primer objeto ``{...}`` balanceado del texto, respetando strings."""
    inicio = texto.find("{")
    while inicio != -1:
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
                    return texto[inicio : posicion + 1]
        inicio = texto.find("{", inicio + 1)
    return None


def formatear_errores_validacion(error: ValidationError) -> list[str]:
    """Errores de Pydantic en formato compacto: campo, problema y valor recibido."""
    lineas = []
    for detalle in error.errors(include_url=False)[:MAX_ERRORES_FEEDBACK]:
        campo = ".".join(str(parte) for parte in detalle["loc"]) or "(objeto raíz)"
        recibido = truncar(repr(detalle.get("input")), MAX_CHARS_VALOR)
        lineas.append(f"{campo}: {detalle['msg']} (recibido: {recibido})")
    restantes = error.error_count() - MAX_ERRORES_FEEDBACK
    if restantes > 0:
        lineas.append(f"... y {restantes} errores más")
    return lineas


def _error(tipo: TipoError, mensaje: str, uso_fallback: bool = False) -> ResultadoParseo:
    return ResultadoParseo(None, tipo, [mensaje], uso_fallback)


def parsear_respuesta(respuesta: RespuestaLLM) -> ResultadoParseo:
    """Verifica una respuesta en el orden stop_reason → fallback → JSON → esquema."""
    if respuesta.stop_reason == "max_tokens":
        return _error(
            TipoError.RESPUESTA_TRUNCADA,
            "la respuesta se cortó por max_tokens; el objeto puede estar incompleto. "
            "Devuelve el objeto completo y usa observaciones breves.",
        )
    texto, uso_fallback = respuesta.texto_crudo, False
    if not respuesta.hubo_tool_use:
        uso_fallback = True
        candidato = extraer_primer_objeto_json(texto)
        if candidato is None:
            return _error(
                TipoError.JSON_INVALIDO,
                f"no se llamó a la herramienta {NOMBRE_HERRAMIENTA} y la respuesta no "
                "contiene un objeto JSON",
                uso_fallback,
            )
        texto = candidato
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as error:
        return _error(
            TipoError.JSON_INVALIDO,
            f"JSON inválido: {error.msg} (línea {error.lineno}, columna {error.colno})",
            uso_fallback,
        )
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
