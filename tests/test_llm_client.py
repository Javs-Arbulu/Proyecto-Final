"""Tests de ClienteCohere sin red: transporte HTTP simulado y reloj inyectado."""

from __future__ import annotations

import json
from typing import Any

import cohere
import httpx
import pytest
from pydantic import SecretStr

from extractor.extraction import esquema_para_llm
from extractor.llm_client import (
    MAX_REINTENTOS_TRANSPORTE,
    ClienteCohere,
    ErrorAPI,
    ErrorAutenticacion,
    LimitadorTasa,
)

from .conftest import KEY_FALSA


class RelojFalso:
    """Reloj controlable: ``dormir`` avanza el tiempo sin esperar de verdad."""

    def __init__(self) -> None:
        self.ahora = 1000.0
        self.esperas: list[float] = []

    def reloj(self) -> float:
        return self.ahora

    def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += segundos


def _respuesta_http(texto: str = '{"total": 10.5}', finish: str = "COMPLETE", usage=None) -> dict:
    return {
        "id": "r1",
        "finish_reason": finish,
        "message": {"role": "assistant", "content": [{"type": "text", "text": texto}]},
        "usage": usage
        if usage is not None
        else {
            "billed_units": {"input_tokens": 1200, "output_tokens": 300},
            "tokens": {"input_tokens": 1500, "output_tokens": 310},
        },
    }


def _cliente_http(manejador: Any, intervalo: float = 0) -> ClienteCohere:
    """ClienteCohere con un transporte httpx falso (sin red)."""
    cliente = ClienteCohere(SecretStr(KEY_FALSA), "command-a-03-2025", 60, intervalo)
    cliente._cliente = cohere.ClientV2(
        api_key=KEY_FALSA,
        httpx_client=httpx.Client(transport=httpx.MockTransport(manejador)),
        log_warning_experimental_features=False,
    )
    return cliente


def test_envia_structured_outputs_json_schema_y_temperature_cero() -> None:
    capturas: list[dict] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        capturas.append(json.loads(peticion.content))
        return httpx.Response(200, json=_respuesta_http())

    esquema = esquema_para_llm()
    _cliente_http(manejador).extraer("sistema", "documento", esquema, 2048)
    cuerpo = capturas[0]
    assert cuerpo["model"] == "command-a-03-2025"
    assert cuerpo["response_format"] == {"type": "json_object", "json_schema": esquema}
    assert cuerpo["temperature"] == 0
    assert cuerpo["max_tokens"] == 2048
    assert cuerpo["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "documento"},
    ]


def test_configura_reintentos_de_transporte_y_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = ClienteCohere(SecretStr(KEY_FALSA), "command-a-03-2025", 42.5, 0)
    capturas: list[dict] = []

    def falso_chat(**kwargs: Any) -> Any:
        capturas.append(kwargs)
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(cliente._cliente, "chat", falso_chat)
    with pytest.raises(ErrorAPI):
        cliente.extraer("s", "m", {}, 100)
    opciones = capturas[0]["request_options"]
    assert opciones == {"max_retries": MAX_REINTENTOS_TRANSPORTE, "timeout_in_seconds": 43}


def test_texto_crudo_y_tokens_facturados() -> None:
    respuesta = _cliente_http(lambda _: httpx.Response(200, json=_respuesta_http())).extraer(
        "s", "m", {}, 100
    )
    assert respuesta.texto_crudo == '{"total": 10.5}'
    assert respuesta.finish_reason == "COMPLETE"
    assert (respuesta.tokens_entrada, respuesta.tokens_salida) == (1200, 300)


def test_tokens_usan_usage_tokens_si_faltan_billed_units() -> None:
    uso = {"tokens": {"input_tokens": 1500, "output_tokens": 310}}
    respuesta = _cliente_http(
        lambda _: httpx.Response(200, json=_respuesta_http(usage=uso))
    ).extraer("s", "m", {}, 100)
    assert (respuesta.tokens_entrada, respuesta.tokens_salida) == (1500, 310)


def test_finish_reason_max_tokens_se_propaga() -> None:
    respuesta = _cliente_http(
        lambda _: httpx.Response(200, json=_respuesta_http('{"total": 1', "MAX_TOKENS"))
    ).extraer("s", "m", {}, 100)
    assert respuesta.finish_reason == "MAX_TOKENS"


@pytest.mark.parametrize("estado", [401, 403])
def test_401_y_403_son_error_autenticacion_sin_exponer_la_key(estado: int) -> None:
    cliente = _cliente_http(lambda _: httpx.Response(estado, json={"message": "invalid api token"}))
    with pytest.raises(ErrorAutenticacion) as capturado:
        cliente.extraer("s", "m", {}, 100)
    assert KEY_FALSA not in str(capturado.value)
    assert capturado.value.__suppress_context__ is True


def test_400_es_error_api_con_el_mensaje_del_cuerpo() -> None:
    cliente = _cliente_http(
        lambda _: httpx.Response(400, json={"message": "invalid json_schema: unsupported"})
    )
    with pytest.raises(ErrorAPI, match="HTTP 400: invalid json_schema"):
        cliente.extraer("s", "m", {}, 100)


@pytest.mark.parametrize("estado", [429, 500, 503])
def test_429_y_5xx_son_error_api_tras_los_reintentos_del_sdk(
    estado: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _segundos: None)  # backoff del SDK sin esperar
    llamadas: list[int] = []

    def manejador(_peticion: httpx.Request) -> httpx.Response:
        llamadas.append(1)
        return httpx.Response(estado, json={"message": "ocupado"})

    with pytest.raises(ErrorAPI):
        _cliente_http(manejador).extraer("s", "m", {}, 100)
    assert len(llamadas) == 1 + MAX_REINTENTOS_TRANSPORTE  # el SDK reintentó el transporte


def test_timeout_es_error_api() -> None:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=peticion)

    with pytest.raises(ErrorAPI, match="timeout"):
        _cliente_http(manejador).extraer("s", "m", {}, 100)


# --- Throttling -----------------------------------------------------------------------


def test_limitador_respeta_el_intervalo_minimo_con_reloj_inyectado() -> None:
    reloj = RelojFalso()
    limitador = LimitadorTasa(3.1, reloj=reloj.reloj, dormir=reloj.dormir)
    assert limitador.esperar_turno() == 0  # la primera llamada no espera
    reloj.ahora += 1.0
    assert limitador.esperar_turno() == pytest.approx(2.1)
    reloj.ahora += 5.0
    assert limitador.esperar_turno() == 0  # ya pasó más que el intervalo
    assert reloj.esperas == [pytest.approx(2.1)]


def test_cliente_aplica_el_throttling_entre_llamadas() -> None:
    reloj = RelojFalso()
    cliente = _cliente_http(lambda _: httpx.Response(200, json=_respuesta_http()))
    cliente._limitador = LimitadorTasa(3.1, reloj=reloj.reloj, dormir=reloj.dormir)
    for _ in range(3):
        cliente.extraer("s", "m", {}, 100)
    assert reloj.esperas == [pytest.approx(3.1), pytest.approx(3.1)]
