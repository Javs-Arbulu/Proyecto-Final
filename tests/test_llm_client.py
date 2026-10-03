"""Tests de ClienteAnthropic sin red: se reemplaza ``messages.create`` por un doble."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import TextBlock, ToolUseBlock
from pydantic import SecretStr

from extractor.extraction import construir_herramienta
from extractor.llm_client import (
    MAX_REINTENTOS_TRANSPORTE,
    ClienteAnthropic,
    ErrorAPI,
    ErrorAutenticacion,
)

from .conftest import KEY_FALSA

PETICION = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _cliente(monkeypatch: pytest.MonkeyPatch, efecto: Any) -> tuple[ClienteAnthropic, list]:
    cliente = ClienteAnthropic(SecretStr(KEY_FALSA), "claude-haiku-4-5-20251001", 60)
    capturas: list[dict[str, Any]] = []

    def falso_create(**kwargs: Any) -> Any:
        capturas.append(kwargs)
        if isinstance(efecto, Exception):
            raise efecto
        return efecto

    monkeypatch.setattr(cliente._cliente.messages, "create", falso_create)
    return cliente, capturas


def _mensaje(contenido: list[Any], stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(
        content=contenido,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


def test_configura_reintentos_de_transporte_y_timeout() -> None:
    cliente = ClienteAnthropic(SecretStr(KEY_FALSA), "modelo", 42)
    assert cliente._cliente.max_retries == MAX_REINTENTOS_TRANSPORTE == 2
    assert cliente._cliente.timeout == 42


def test_fuerza_la_herramienta_con_temperature_cero(monkeypatch: pytest.MonkeyPatch) -> None:
    bloque = ToolUseBlock(id="t1", input={"total": 10.5}, name="registrar_factura", type="tool_use")
    cliente, capturas = _cliente(monkeypatch, _mensaje([bloque]))
    cliente.extraer("sistema", "doc", construir_herramienta(), 2048)
    peticion = capturas[0]
    assert peticion["tool_choice"] == {"type": "tool", "name": "registrar_factura"}
    assert peticion["extra_body"] == {"temperature": 0}
    assert peticion["max_tokens"] == 2048
    assert peticion["messages"] == [{"role": "user", "content": "doc"}]


def test_texto_crudo_serializa_el_bloque_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    bloque = ToolUseBlock(id="t1", input={"razon": "Ñandú S.A.C."}, name="x", type="tool_use")
    cliente, _ = _cliente(monkeypatch, _mensaje([bloque]))
    respuesta = cliente.extraer("s", "m", construir_herramienta(), 100)
    assert respuesta.hubo_tool_use is True
    assert respuesta.texto_crudo == '{"razon": "Ñandú S.A.C."}'
    assert (respuesta.tokens_entrada, respuesta.tokens_salida) == (1200, 300)
    assert respuesta.stop_reason == "tool_use"


def test_sin_tool_use_devuelve_el_texto_plano(monkeypatch: pytest.MonkeyPatch) -> None:
    bloques = [TextBlock(text="No puedo ", type="text"), TextBlock(text="hacerlo.", type="text")]
    cliente, _ = _cliente(monkeypatch, _mensaje(bloques, "end_turn"))
    respuesta = cliente.extraer("s", "m", construir_herramienta(), 100)
    assert respuesta.hubo_tool_use is False
    assert respuesta.texto_crudo == "No puedo hacerlo."


def test_401_se_mapea_a_error_autenticacion_sin_exponer_la_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=PETICION), body=None
    )
    cliente, _ = _cliente(monkeypatch, error)
    with pytest.raises(ErrorAutenticacion) as capturado:
        cliente.extraer("s", "m", construir_herramienta(), 100)
    assert KEY_FALSA not in str(capturado.value)
    assert capturado.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APITimeoutError(request=PETICION),
        anthropic.APIConnectionError(request=PETICION),
        anthropic.RateLimitError(
            "lento", response=httpx2.Response(429, request=PETICION), body=None
        ),
        anthropic.BadRequestError(
            "malo",
            response=httpx2.Response(400, request=PETICION),
            body={"error": {"message": "tools.0.input_schema: invalid"}},
        ),
    ],
)
def test_otros_errores_se_mapean_a_error_api(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    cliente, _ = _cliente(monkeypatch, error)
    with pytest.raises(ErrorAPI):
        cliente.extraer("s", "m", construir_herramienta(), 100)
