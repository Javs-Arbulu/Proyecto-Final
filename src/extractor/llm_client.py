"""Cliente LLM. Es el ÚNICO módulo que importa el SDK ``anthropic`` (verificado por test AST).

El resto del proyecto depende del protocolo :class:`ClienteLLM`, nunca del SDK.

Separación de reintentos:

* **Transporte** (429, 5xx, timeouts, errores de conexión): los maneja el SDK,
  configurado explícitamente con ``max_retries=2`` y ``timeout``.
* **Formato** (JSON inválido, esquema inválido, respuesta truncada): los maneja
  ``validation.py`` a mano, con feedback al modelo.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from pydantic import SecretStr

logger = logging.getLogger(__name__)

MAX_REINTENTOS_TRANSPORTE = 2


@dataclass(frozen=True)
class RespuestaLLM:
    """Respuesta normalizada e independiente del proveedor."""

    texto_crudo: str
    hubo_tool_use: bool
    stop_reason: str | None
    tokens_entrada: int
    tokens_salida: int
    latencia_ms: int


class ClienteLLM(Protocol):
    """Contrato mínimo que necesita la capa de validación."""

    def extraer(
        self,
        system: str,
        mensaje_usuario: str,
        herramienta: dict[str, Any],
        max_tokens: int,
    ) -> RespuestaLLM:
        """Envía una conversación de un turno y fuerza el uso de ``herramienta``."""
        ...


class ErrorLLM(Exception):
    """Base de los errores del cliente LLM (mensajes sin secretos)."""


class ErrorAutenticacion(ErrorLLM):
    """Credenciales inválidas o sin permiso: activa el circuit breaker del lote."""


class ErrorAPI(ErrorLLM):
    """Error de API no recuperable tras los reintentos de transporte del SDK."""


def _texto_crudo(contenido: list[Any]) -> tuple[str, bool]:
    """Serializa el bloque tool_use; si no lo hay, concatena el texto plano.

    La capa de validación parsea este texto exactamente como lo haría con
    cualquier proveedor, así que el camino de "JSON inválido" es real.
    """
    for bloque in contenido:
        if bloque.type == "tool_use":
            return json.dumps(bloque.input, ensure_ascii=False), True
    texto = "".join(bloque.text for bloque in contenido if bloque.type == "text")
    return texto, False


class ClienteAnthropic:
    """Implementación de :class:`ClienteLLM` con el SDK oficial de Anthropic."""

    def __init__(self, api_key: SecretStr, modelo: str, timeout_s: float) -> None:
        self._modelo = modelo
        self._cliente = anthropic.Anthropic(
            api_key=api_key.get_secret_value(),
            timeout=timeout_s,
            max_retries=MAX_REINTENTOS_TRANSPORTE,
        )

    def extraer(
        self,
        system: str,
        mensaje_usuario: str,
        herramienta: dict[str, Any],
        max_tokens: int,
    ) -> RespuestaLLM:
        """Llama a Messages API con tool use forzado y ``temperature=0``."""
        inicio = time.perf_counter()
        try:
            respuesta = self._cliente.messages.create(
                model=self._modelo,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": mensaje_usuario}],
                tools=[herramienta],
                tool_choice={"type": "tool", "name": herramienta["name"]},
                # El SDK 1.x ya no acepta `temperature` como argumento; la API sí lo
                # acepta para Haiku 4.5, así que viaja en el cuerpo de la petición.
                extra_body={"temperature": 0},
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            raise ErrorAutenticacion(
                f"credenciales inválidas o sin permiso (HTTP {error.status_code})"
            ) from None
        except anthropic.APITimeoutError:
            raise ErrorAPI("timeout de la API tras los reintentos de transporte") from None
        except anthropic.APIConnectionError:
            raise ErrorAPI("error de conexión con la API tras los reintentos") from None
        except anthropic.RateLimitError:
            raise ErrorAPI("límite de tasa (HTTP 429) tras los reintentos de transporte") from None
        except anthropic.APIStatusError as error:
            raise ErrorAPI(f"HTTP {error.status_code}: {_mensaje_api(error)}") from None
        latencia_ms = int((time.perf_counter() - inicio) * 1000)
        texto, hubo_tool_use = _texto_crudo(respuesta.content)
        logger.info(
            "respuesta modelo=%s stop_reason=%s tool_use=%s tokens=%s/%s latencia_ms=%s",
            self._modelo,
            respuesta.stop_reason,
            hubo_tool_use,
            respuesta.usage.input_tokens,
            respuesta.usage.output_tokens,
            latencia_ms,
        )
        return RespuestaLLM(
            texto_crudo=texto,
            hubo_tool_use=hubo_tool_use,
            stop_reason=respuesta.stop_reason,
            tokens_entrada=respuesta.usage.input_tokens,
            tokens_salida=respuesta.usage.output_tokens,
            latencia_ms=latencia_ms,
        )


def _mensaje_api(error: anthropic.APIStatusError) -> str:
    """Mensaje de error de la API, truncado. Nunca incluye headers ni la key."""
    cuerpo = error.body if isinstance(error.body, dict) else {}
    detalle = cuerpo.get("error", {}) if isinstance(cuerpo.get("error"), dict) else {}
    mensaje = str(detalle.get("message") or error.message)
    return mensaje[:300]
