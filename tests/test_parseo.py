"""Tests de parsear_respuesta: stop_reason, fallback, JSON y esquema."""

from __future__ import annotations

import json

import pytest

from extractor.schema import TipoError
from extractor.validation import extraer_primer_objeto_json, parsear_respuesta

from .conftest import factura_valida, respuesta_texto, respuesta_tool


def test_respuesta_valida_produce_factura() -> None:
    resultado = parsear_respuesta(respuesta_tool(factura_valida()))
    assert resultado.ok
    assert resultado.factura is not None and resultado.factura.total == 118.0
    assert resultado.uso_fallback is False


def test_max_tokens_es_truncada_aunque_el_json_sea_valido() -> None:
    resultado = parsear_respuesta(respuesta_tool(factura_valida(), stop_reason="max_tokens"))
    assert not resultado.ok
    assert resultado.tipo_error is TipoError.RESPUESTA_TRUNCADA


def test_json_cortado_es_json_invalido() -> None:
    texto = json.dumps(factura_valida())
    resultado = parsear_respuesta(respuesta_texto(texto[: len(texto) // 2]))
    assert resultado.tipo_error is TipoError.JSON_INVALIDO
    assert "JSON inválido" in resultado.errores[0]


def test_tipo_incorrecto_es_esquema_invalido_con_errores_compactos() -> None:
    datos = factura_valida(total="mil doscientos soles", fecha_emision="15 de marzo")
    resultado = parsear_respuesta(respuesta_tool(datos))
    assert resultado.tipo_error is TipoError.ESQUEMA_INVALIDO
    campos = {error.split(":")[0] for error in resultado.errores}
    assert campos == {"total", "fecha_emision"}
    assert any("mil doscientos soles" in error for error in resultado.errores)


def test_json_que_no_es_objeto_es_esquema_invalido() -> None:
    resultado = parsear_respuesta(respuesta_texto("[1, 2, 3]"))
    assert resultado.tipo_error is TipoError.ESQUEMA_INVALIDO


def test_fallback_rescata_json_embebido_en_prosa() -> None:
    prosa = "Claro, aquí están los datos: " + json.dumps(factura_valida()) + " ¡Saludos!"
    resultado = parsear_respuesta(respuesta_texto(prosa, hubo_tool_use=False))
    assert resultado.ok
    assert resultado.uso_fallback is True


def test_fallback_sin_json_es_json_invalido() -> None:
    resultado = parsear_respuesta(respuesta_texto("No puedo ayudarte.", hubo_tool_use=False))
    assert resultado.tipo_error is TipoError.JSON_INVALIDO
    assert resultado.uso_fallback is True


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ('prefijo {"a": {"b": 1}} sufijo {"c": 2}', '{"a": {"b": 1}}'),
        ('{"llave": "texto con } y { dentro"} fin', '{"llave": "texto con } y { dentro"}'),
        ('{"escape": "comilla \\" y }"}', '{"escape": "comilla \\" y }"}'),
        ("{ sin cerrar", None),
        ("sin llaves", None),
    ],
)
def test_extraer_primer_objeto_json_balanceado(texto: str, esperado: str | None) -> None:
    assert extraer_primer_objeto_json(texto) == esperado
