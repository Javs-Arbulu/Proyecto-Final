"""Inyección de fallos determinista para demostrar el manejo de errores.

Structured Outputs rara vez falla en una demo, así que :class:`ClienteConFallas`
envuelve al cliente real (decorador de :class:`ClienteLLM`), lo llama de verdad y
corrompe su salida según el modo. Se activa SOLO con ``--simular-fallo``; nunca
está activo por defecto. Se crea una instancia por documento, así que el conteo
de intentos empieza en 1 para cada documento.
"""

from __future__ import annotations

import json
from dataclasses import replace
from enum import StrEnum
from typing import Any

from extractor.llm_client import ClienteLLM, RespuestaLLM

PROSA_SIN_JSON = (
    "Revisé el documento y parece una factura de un proveedor. Lamentablemente no puedo "
    "registrar los datos en este momento; por favor, intente más tarde."
)


class ModoFallo(StrEnum):
    """Modos de simulación disponibles."""

    JSON_INVALIDO = "json_invalido"
    TIPO_INCORRECTO = "tipo_incorrecto"
    PERSISTENTE = "persistente"
    SIN_JSON = "sin_json"


def _truncar_a_la_mitad(respuesta: RespuestaLLM) -> RespuestaLLM:
    """JSON cortado a la mitad (simula una salida mal formada)."""
    texto = respuesta.texto_crudo
    return replace(respuesta, texto_crudo=texto[: len(texto) // 2])


def _tipos_incorrectos(respuesta: RespuestaLLM) -> RespuestaLLM:
    """Reemplaza ``total`` y ``fecha_emision`` por texto libre (tipos inválidos)."""
    try:
        datos = json.loads(respuesta.texto_crudo)
    except json.JSONDecodeError:
        return respuesta
    if not isinstance(datos, dict):
        return respuesta
    datos.update(total="mil doscientos soles", fecha_emision="15 de marzo")
    return replace(respuesta, texto_crudo=json.dumps(datos, ensure_ascii=False))


class ClienteConFallas:
    """Decorador de :class:`ClienteLLM` que corrompe la salida de forma determinista.

    | Modo              | Qué hace                                                     |
    |-------------------|--------------------------------------------------------------|
    | ``json_invalido`` | Trunca el JSON en los primeros ``min(2, max_intentos - 1)``  |
    |                   | intentos y después deja pasar la respuesta real: RECUPERACIÓN |
    | ``tipo_incorrecto`` | En el 1.er intento, ``total`` y ``fecha_emision`` en texto  |
    | ``persistente``   | Trunca SIEMPRE: demuestra el LÍMITE de intentos              |
    | ``sin_json``      | En el 1.er intento devuelve prosa sin JSON: el fallback      |
    |                   | falla y se reintenta                                          |
    """

    def __init__(self, interno: ClienteLLM, modo: ModoFallo, max_intentos: int) -> None:
        self._interno = interno
        self._modo = ModoFallo(modo)
        self._max_intentos = max_intentos
        self.llamadas = 0

    def _corromper(self, respuesta: RespuestaLLM) -> RespuestaLLM:
        """Aplica la corrupción que corresponde al modo y al número de intento."""
        n = self.llamadas
        if self._modo is ModoFallo.JSON_INVALIDO and n <= min(2, self._max_intentos - 1):
            return _truncar_a_la_mitad(respuesta)
        if self._modo is ModoFallo.TIPO_INCORRECTO and n == 1:
            return _tipos_incorrectos(respuesta)
        if self._modo is ModoFallo.PERSISTENTE:
            return _truncar_a_la_mitad(respuesta)
        if self._modo is ModoFallo.SIN_JSON and n == 1:
            return replace(respuesta, texto_crudo=PROSA_SIN_JSON)
        return respuesta

    def extraer(
        self,
        system: str,
        mensaje_usuario: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> RespuestaLLM:
        """Llama al cliente real y corrompe su salida según el modo."""
        self.llamadas += 1
        respuesta = self._interno.extraer(system, mensaje_usuario, schema, max_tokens)
        return self._corromper(respuesta)
