"""Fixtures compartidas. Ningún test llama a la API real: se usa ``FakeClienteLLM``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from extractor.config import Settings
from extractor.llm_client import RespuestaLLM

KEY_FALSA = "clave-de-prueba-no-real-123"

TEXTO_FACTURA = """\
PROVEEDOR FICTICIO S.A.C.   RUC 20512345670
FACTURA ELECTRÓNICA F001-00012345
Fecha de emisión: 15/03/2026   Vence: 14/04/2026   Crédito 30 días
Cliente: CLIENTE FICTICIO S.A.   RUC 20498765431
Moneda: Soles
2 x Servicio de soporte  V.Unit 50.00  Importe 100.00
Op. gravada S/ 100.00   IGV S/ 18.00   Total S/ 118.00
"""


def factura_valida(**cambios: Any) -> dict[str, Any]:
    """Diccionario de una factura coherente con ``TEXTO_FACTURA``."""
    datos: dict[str, Any] = {
        "tipo_documento": "FACTURA",
        "numero_factura": "F001-00012345",
        "fecha_emision": "2026-03-15",
        "fecha_vencimiento": "2026-04-14",
        "ruc_emisor": "20512345670",
        "razon_social_emisor": "PROVEEDOR FICTICIO S.A.C.",
        "ruc_cliente": "20498765431",
        "razon_social_cliente": "CLIENTE FICTICIO S.A.",
        "moneda": "PEN",
        "condicion_pago": "CREDITO",
        "items": [
            {
                "descripcion": "Servicio de soporte",
                "cantidad": 2,
                "precio_unitario": 50.0,
                "importe": 100.0,
            }
        ],
        "descuento": None,
        "subtotal": 100.0,
        "igv": 18.0,
        "total": 118.0,
        "observaciones": None,
    }
    datos.update(cambios)
    return datos


def respuesta_tool(datos: dict[str, Any], stop_reason: str = "tool_use") -> RespuestaLLM:
    """Respuesta con bloque tool_use (camino normal)."""
    return RespuestaLLM(
        texto_crudo=json.dumps(datos, ensure_ascii=False),
        hubo_tool_use=True,
        stop_reason=stop_reason,
        tokens_entrada=1000,
        tokens_salida=200,
        latencia_ms=50,
    )


def respuesta_texto(texto: str, hubo_tool_use: bool = True) -> RespuestaLLM:
    """Respuesta con texto crudo arbitrario (para simular JSON roto o prosa)."""
    return RespuestaLLM(
        texto_crudo=texto,
        hubo_tool_use=hubo_tool_use,
        stop_reason="tool_use" if hubo_tool_use else "end_turn",
        tokens_entrada=1000,
        tokens_salida=150,
        latencia_ms=40,
    )


@dataclass
class Llamada:
    """Registro de una llamada al fake."""

    system: str
    mensaje_usuario: str
    herramienta: dict[str, Any]
    max_tokens: int


@dataclass
class FakeClienteLLM:
    """Cliente guionado: devuelve (o lanza) los elementos de ``guion`` en orden.

    Si el guion se agota, repite el último elemento. Registra cada llamada para
    poder verificar cuántas veces se llamó y con qué mensaje.
    """

    guion: list[RespuestaLLM | Exception]
    llamadas: list[Llamada] = field(default_factory=list)

    def extraer(
        self,
        system: str,
        mensaje_usuario: str,
        herramienta: dict[str, Any],
        max_tokens: int,
    ) -> RespuestaLLM:
        self.llamadas.append(Llamada(system, mensaje_usuario, herramienta, max_tokens))
        indice = min(len(self.llamadas), len(self.guion)) - 1
        elemento = self.guion[indice]
        if isinstance(elemento, Exception):
            raise elemento
        return elemento


@pytest.fixture(autouse=True)
def _aislar_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que los tests lean la key real o la configuración del desarrollador."""
    for variable in (
        "ANTHROPIC_API_KEY",
        "MODELO",
        "MAX_INTENTOS",
        "MAX_TOKENS",
        "TIMEOUT_S",
        "MAX_CHARS_DOCUMENTO",
        "TOLERANCIA_MONTOS",
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def settings() -> Settings:
    """Settings de prueba: sin leer .env y con una key falsa."""
    return Settings(_env_file=None, anthropic_api_key=KEY_FALSA)
