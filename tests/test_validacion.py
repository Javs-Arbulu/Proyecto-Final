"""Tests de parseo y reintentos de formato (sin llamadas reales a la API)."""

from __future__ import annotations

import json

import pytest

from extractor.config import Settings
from extractor.llm_client import ErrorAPI, ErrorAutenticacion
from extractor.schema import CodigoMotivo, TipoError
from extractor.validation import (
    describir_intento,
    extraer_con_reintentos,
    extraer_primer_objeto_json,
    parsear_respuesta,
)

from .conftest import TEXTO_FACTURA, FakeClienteLLM, factura_valida, respuesta_json, respuesta_texto

JSON_VALIDO = json.dumps(factura_valida())
JSON_CORTADO = JSON_VALIDO[: len(JSON_VALIDO) // 2]


# --- parsear_respuesta -----------------------------------------------------------------


def test_respuesta_valida_produce_factura() -> None:
    resultado = parsear_respuesta(respuesta_json(factura_valida()))
    assert resultado.ok
    assert resultado.factura is not None and resultado.factura.total == 118.0
    assert resultado.uso_fallback is False


def test_max_tokens_es_truncada_aunque_el_json_parezca_valido() -> None:
    resultado = parsear_respuesta(respuesta_json(factura_valida(), finish_reason="MAX_TOKENS"))
    assert not resultado.ok
    assert resultado.tipo_error is TipoError.RESPUESTA_TRUNCADA


@pytest.mark.parametrize("finish_reason", ["ERROR", "TIMEOUT", "TOOL_CALL", None])
def test_finish_reason_inesperado_es_error_reintentable(finish_reason: str | None) -> None:
    resultado = parsear_respuesta(respuesta_json(factura_valida(), finish_reason=finish_reason))
    assert resultado.tipo_error is TipoError.FINALIZACION_INESPERADA
    assert str(finish_reason) in resultado.errores[0]


def test_stop_sequence_es_finalizacion_normal() -> None:
    assert parsear_respuesta(respuesta_json(factura_valida(), finish_reason="STOP_SEQUENCE")).ok


def test_json_cortado_es_json_invalido_y_el_fallback_no_rescata_un_item() -> None:
    resultado = parsear_respuesta(respuesta_texto(JSON_CORTADO))
    assert resultado.tipo_error is TipoError.JSON_INVALIDO
    assert resultado.uso_fallback is True
    assert "JSON inválido" in resultado.errores[0]


def test_tipo_incorrecto_es_esquema_invalido_con_errores_compactos() -> None:
    datos = factura_valida(total="mil doscientos soles", fecha_emision="15 de marzo")
    resultado = parsear_respuesta(respuesta_json(datos))
    assert resultado.tipo_error is TipoError.ESQUEMA_INVALIDO
    assert {error.split(":")[0] for error in resultado.errores} == {"total", "fecha_emision"}
    assert any("mil doscientos soles" in error for error in resultado.errores)


def test_clave_ausente_es_esquema_invalido() -> None:
    datos = factura_valida()
    del datos["moneda"]
    resultado = parsear_respuesta(respuesta_json(datos))
    assert resultado.tipo_error is TipoError.ESQUEMA_INVALIDO
    assert resultado.errores == ["moneda: falta la clave (inclúyela; usa null si no hay dato)"]


def test_json_que_no_es_objeto_es_esquema_invalido() -> None:
    assert parsear_respuesta(respuesta_texto("[1, 2, 3]")).tipo_error is TipoError.ESQUEMA_INVALIDO


def test_fallback_rescata_json_embebido_en_prosa() -> None:
    prosa = "Claro, aquí están los datos: " + JSON_VALIDO + " ¡Saludos!"
    resultado = parsear_respuesta(respuesta_texto(prosa))
    assert resultado.ok
    assert resultado.uso_fallback is True


def test_prosa_sin_json_es_json_invalido_tras_el_fallback() -> None:
    resultado = parsear_respuesta(respuesta_texto("No puedo ayudarte con eso."))
    assert resultado.tipo_error is TipoError.JSON_INVALIDO
    assert resultado.uso_fallback is True
    assert resultado.errores[0].startswith("la respuesta no contiene ningún objeto JSON")


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ('prefijo {"a": {"b": 1}} sufijo {"c": 2}', '{"a": {"b": 1}}'),
        ('{"llave": "texto con } y { dentro"} fin', '{"llave": "texto con } y { dentro"}'),
        ('{"escape": "comilla \\" y }"}', '{"escape": "comilla \\" y }"}'),
        ('usa {llaves} así: {"ok": true}', '{"ok": true}'),
        ('{"a": [{"b": 1}, {"c": ', None),
        ("sin llaves", None),
    ],
)
def test_extraer_primer_objeto_json_balanceado(texto: str, esperado: str | None) -> None:
    assert extraer_primer_objeto_json(texto) == esperado


# --- extraer_con_reintentos --------------------------------------------------------------


def _con_intentos(settings: Settings, n: int) -> Settings:
    return settings.model_copy(update={"max_intentos": n})


def test_invalido_invalido_valido_produce_exito_en_3_intentos(settings: Settings) -> None:
    fake = FakeClienteLLM(
        [respuesta_texto(JSON_CORTADO), respuesta_texto("nada"), respuesta_json(factura_valida())]
    )
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)
    assert resultado.factura is not None
    assert len(resultado.intentos) == 3 == len(fake.llamadas)
    assert [i.ok for i in resultado.intentos] == [False, False, True]


@pytest.mark.parametrize("max_intentos", [1, 2, 3, 5])
def test_siempre_invalido_falla_tras_exactamente_max_intentos(
    settings: Settings, max_intentos: int
) -> None:
    """Prueba de que no hay bucle infinito: el fake se llama EXACTAMENTE N veces."""
    fake = FakeClienteLLM([respuesta_texto(JSON_CORTADO)])
    resultado = extraer_con_reintentos(
        TEXTO_FACTURA, "doc.txt", fake, _con_intentos(settings, max_intentos)
    )
    assert resultado.factura is None
    assert len(fake.llamadas) == max_intentos
    assert resultado.motivo_fallo is CodigoMotivo.JSON_INVALIDO_REINTENTOS_AGOTADOS


def test_esquema_siempre_invalido_agota_con_su_codigo(settings: Settings) -> None:
    fake = FakeClienteLLM([respuesta_json(factura_valida(total="mucho"))])
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)
    assert resultado.motivo_fallo is CodigoMotivo.ESQUEMA_INVALIDO_REINTENTOS_AGOTADOS


def test_segundo_mensaje_contiene_el_error_del_primero(settings: Settings) -> None:
    fake = FakeClienteLLM(
        [
            respuesta_json(factura_valida(total="mil doscientos soles")),
            respuesta_json(factura_valida()),
        ]
    )
    extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)
    primero, segundo = fake.llamadas[0].mensaje_usuario, fake.llamadas[1].mensaje_usuario
    assert "<errores_intento_anterior>" not in primero
    bloque = segundo.split("<errores_intento_anterior>")[1]
    assert "total:" in bloque and "mil doscientos soles" in bloque
    assert segundo.startswith('<documento nombre="doc.txt">')  # conversación desde cero


def test_max_tokens_es_truncada_y_el_siguiente_intento_duplica_hasta_8192(
    settings: Settings,
) -> None:
    fake = FakeClienteLLM([respuesta_json(factura_valida(), finish_reason="MAX_TOKENS")])
    ajustes = settings.model_copy(update={"max_tokens": 3000, "max_intentos": 4})
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, ajustes)
    assert [llamada.max_tokens for llamada in fake.llamadas] == [3000, 6000, 8192, 8192]
    assert resultado.motivo_fallo is CodigoMotivo.RESPUESTA_TRUNCADA


def test_truncada_y_luego_completa_se_recupera(settings: Settings) -> None:
    fake = FakeClienteLLM(
        [
            respuesta_json(factura_valida(), finish_reason="MAX_TOKENS"),
            respuesta_json(factura_valida()),
        ]
    )
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)
    assert resultado.factura is not None
    assert fake.llamadas[1].max_tokens == 2 * settings.max_tokens


def test_error_api_termina_sin_consumir_intentos_de_formato(settings: Settings) -> None:
    fake = FakeClienteLLM([ErrorAPI("HTTP 400: esquema rechazado")])
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)
    assert resultado.motivo_fallo is CodigoMotivo.ERROR_API
    assert len(fake.llamadas) == 1
    assert resultado.intentos == []


def test_error_autenticacion_se_propaga(settings: Settings) -> None:
    fake = FakeClienteLLM([ErrorAutenticacion("HTTP 401")])
    with pytest.raises(ErrorAutenticacion):
        extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)


def test_intentos_registran_tokens_latencia_y_fallback(settings: Settings) -> None:
    prosa = "Aquí va: " + JSON_VALIDO
    fake = FakeClienteLLM([respuesta_texto(prosa)])
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", fake, settings)
    intento = resultado.intentos[0]
    assert intento.ok and intento.uso_fallback
    assert (intento.tokens_entrada, intento.latencia_ms, intento.finish_reason) == (
        1000,
        40,
        "COMPLETE",
    )


def test_notificador_y_descripcion_de_cada_intento(settings: Settings) -> None:
    lineas: list[str] = []
    fake = FakeClienteLLM([respuesta_texto(JSON_CORTADO), respuesta_json(factura_valida())])
    extraer_con_reintentos(
        TEXTO_FACTURA,
        "doc.txt",
        fake,
        settings,
        notificar=lambda i, n, r: lineas.append(describir_intento(i, n, r)),
    )
    assert lineas[0].startswith("intento 1/3 ✗ JSON inválido: Unterminated string")
    assert "JSON inválido: JSON inválido" not in lineas[0]
    assert lineas[0].endswith("→ reintentando con feedback")
    assert lineas[1] == "intento 2/3 ✓ JSON válido según el esquema"
