"""Tests de la inyección de fallos determinista."""

from __future__ import annotations

import pytest

from extractor.config import Settings
from extractor.fault_injection import PROSA_SIN_JSON, ClienteConFallas, ModoFallo
from extractor.schema import CodigoMotivo, TipoError
from extractor.validation import extraer_con_reintentos

from .conftest import TEXTO_FACTURA, FakeClienteLLM, factura_valida, respuesta_json


def _ejecutar(settings: Settings, modo: ModoFallo, max_intentos: int):
    real = FakeClienteLLM([respuesta_json(factura_valida())])
    ajustes = settings.model_copy(update={"max_intentos": max_intentos})
    cliente = ClienteConFallas(real, modo, max_intentos)
    resultado = extraer_con_reintentos(TEXTO_FACTURA, "doc.txt", cliente, ajustes)
    return resultado, real


@pytest.mark.parametrize(("max_intentos", "corrompidos"), [(2, 1), (3, 2), (5, 2)])
def test_json_invalido_se_recupera(settings: Settings, max_intentos: int, corrompidos: int) -> None:
    """Corrompe min(2, max_intentos - 1) intentos: también se recupera con 2."""
    resultado, real = _ejecutar(settings, ModoFallo.JSON_INVALIDO, max_intentos)
    assert resultado.factura is not None
    assert len(resultado.intentos) == corrompidos + 1 == len(real.llamadas)
    assert all(i.tipo_error is TipoError.JSON_INVALIDO for i in resultado.intentos[:-1])
    assert resultado.intentos[-1].ok


def test_json_invalido_con_un_solo_intento_no_corrompe(settings: Settings) -> None:
    resultado, _ = _ejecutar(settings, ModoFallo.JSON_INVALIDO, 1)
    assert resultado.factura is not None


@pytest.mark.parametrize("max_intentos", [2, 3])
def test_persistente_agota_los_intentos(settings: Settings, max_intentos: int) -> None:
    resultado, real = _ejecutar(settings, ModoFallo.PERSISTENTE, max_intentos)
    assert resultado.factura is None
    assert resultado.motivo_fallo is CodigoMotivo.JSON_INVALIDO_REINTENTOS_AGOTADOS
    assert len(real.llamadas) == max_intentos  # llama al cliente real en cada intento


def test_tipo_incorrecto_produce_error_de_esquema_y_se_recupera(settings: Settings) -> None:
    resultado, real = _ejecutar(settings, ModoFallo.TIPO_INCORRECTO, 3)
    assert resultado.factura is not None
    primero = resultado.intentos[0]
    assert primero.tipo_error is TipoError.ESQUEMA_INVALIDO
    assert "mil doscientos soles" in (primero.detalle_error or "")
    assert "<errores_intento_anterior>" in real.llamadas[1].mensaje_usuario


def test_sin_json_el_fallback_falla_y_se_reintenta(settings: Settings) -> None:
    resultado, real = _ejecutar(settings, ModoFallo.SIN_JSON, 3)
    primero, segundo = resultado.intentos
    assert primero.uso_fallback is True
    assert primero.tipo_error is TipoError.JSON_INVALIDO
    assert segundo.ok
    assert len(real.llamadas) == 2
    assert "{" not in PROSA_SIN_JSON


def test_cada_instancia_cuenta_desde_cero(settings: Settings) -> None:
    """El pipeline crea un ClienteConFallas por documento."""
    for _ in range(2):
        resultado, _real = _ejecutar(settings, ModoFallo.JSON_INVALIDO, 3)
        assert len(resultado.intentos) == 3
