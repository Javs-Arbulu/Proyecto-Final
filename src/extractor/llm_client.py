"""Cliente LLM. Es el ÚNICO módulo que importa el SDK ``cohere`` (verificado por test AST).

El resto del proyecto depende del protocolo :class:`ClienteLLM`, nunca del SDK.

Separación de reintentos:

* **Transporte** (429, 408, 409, 5xx y errores de conexión): los maneja el SDK,
  configurado explícitamente con ``request_options={"max_retries": 2,
  "timeout_in_seconds": ...}``.
* **Formato** (JSON inválido, esquema inválido, respuesta truncada): los maneja
  ``validation.py`` a mano, con feedback al modelo.

Además, :class:`LimitadorTasa` garantiza un intervalo mínimo entre llamadas para
respetar la cuota de la key de prueba (20 llamadas por minuto).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import cohere
import httpx
from cohere.core.api_error import ApiError
from pydantic import SecretStr

logger = logging.getLogger(__name__)

MAX_REINTENTOS_TRANSPORTE = 2
FINALIZACIONES_NORMALES = frozenset({"COMPLETE", "STOP_SEQUENCE"})


@dataclass(frozen=True)
class RespuestaLLM:
    """Respuesta normalizada e independiente del proveedor."""

    texto_crudo: str
    finish_reason: str | None
    tokens_entrada: int
    tokens_salida: int
    latencia_ms: int


class ClienteLLM(Protocol):
    """Contrato mínimo que necesita la capa de validación."""

    def extraer(
        self,
        system: str,
        mensaje_usuario: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> RespuestaLLM:
        """Envía una conversación de un turno y exige un JSON que cumpla ``schema``."""
        ...


class ErrorLLM(Exception):
    """Base de los errores del cliente LLM (mensajes sin secretos)."""


class ErrorAutenticacion(ErrorLLM):
    """Credenciales inválidas o sin permiso: activa el circuit breaker del lote."""


class ErrorAPI(ErrorLLM):
    """Error de API no recuperable tras los reintentos de transporte del SDK."""


class LimitadorTasa:
    """Garantiza al menos ``intervalo_s`` segundos entre el inicio de dos llamadas.

    El reloj y la función de espera son inyectables para testearlo sin esperar.
    """

    def __init__(
        self,
        intervalo_s: float,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._intervalo_s = intervalo_s
        self._reloj = reloj
        self._dormir = dormir
        self._ultima: float | None = None

    def esperar_turno(self) -> float:
        """Duerme lo necesario antes de la próxima llamada; devuelve los segundos esperados."""
        espera = 0.0
        if self._ultima is not None:
            espera = max(0.0, self._intervalo_s - (self._reloj() - self._ultima))
            if espera > 0:
                logger.debug("throttling: esperando %.2f s", espera)
                self._dormir(espera)
        self._ultima = self._reloj()
        return espera


def _texto_crudo(respuesta: Any) -> str:
    """Concatena los bloques de texto de ``response.message.content``."""
    contenido = respuesta.message.content or []
    return "".join(bloque.text for bloque in contenido if getattr(bloque, "type", "") == "text")


def _tokens(respuesta: Any) -> tuple[int, int]:
    """Tokens facturados (``billed_units``); si faltan, los tokens totales."""
    uso = respuesta.usage
    for fuente in (getattr(uso, "billed_units", None), getattr(uso, "tokens", None)):
        if fuente is not None and fuente.input_tokens is not None:
            return int(fuente.input_tokens), int(fuente.output_tokens or 0)
    return 0, 0


def _mensaje_api(error: ApiError) -> str:
    """Mensaje del cuerpo del error, truncado. Nunca usa ``str(error)``: incluye headers."""
    cuerpo = error.body
    mensaje = cuerpo.get("message", cuerpo) if isinstance(cuerpo, dict) else cuerpo
    return str(mensaje)[:300]


def _mapear_error_api(error: ApiError) -> ErrorLLM:
    """401/403 → autenticación; 429, 5xx y el resto → ErrorAPI."""
    estado = error.status_code
    if estado in (401, 403):
        return ErrorAutenticacion(f"credenciales inválidas o sin permiso (HTTP {estado})")
    if estado == 429:
        return ErrorAPI("límite de tasa (HTTP 429) tras los reintentos de transporte")
    if estado is not None and estado >= 500:
        return ErrorAPI(f"error del servidor (HTTP {estado}) tras los reintentos de transporte")
    return ErrorAPI(f"HTTP {estado}: {_mensaje_api(error)}")


class ClienteCohere:
    """Implementación de :class:`ClienteLLM` con Structured Outputs de Cohere (Chat V2)."""

    def __init__(
        self,
        api_key: SecretStr,
        modelo: str,
        timeout_s: float,
        intervalo_min_s: float,
        limitador: LimitadorTasa | None = None,
    ) -> None:
        self._modelo = modelo
        self._timeout_s = timeout_s
        self._limitador = limitador or LimitadorTasa(intervalo_min_s)
        self._cliente = cohere.ClientV2(
            api_key=api_key.get_secret_value(),
            log_warning_experimental_features=False,
        )

    def extraer(
        self,
        system: str,
        mensaje_usuario: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> RespuestaLLM:
        """Llama a Chat V2 con ``response_format`` JSON Schema y ``temperature=0``."""
        self._limitador.esperar_turno()
        inicio = time.perf_counter()
        try:
            respuesta = self._cliente.chat(
                model=self._modelo,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": mensaje_usuario},
                ],
                response_format={"type": "json_object", "json_schema": schema},
                temperature=0,
                max_tokens=max_tokens,
                request_options={
                    "max_retries": MAX_REINTENTOS_TRANSPORTE,
                    "timeout_in_seconds": math.ceil(self._timeout_s),
                },
            )
        except ApiError as error:
            raise _mapear_error_api(error) from None
        except httpx.TimeoutException:
            raise ErrorAPI("timeout de la API") from None
        except httpx.HTTPError as error:
            raise ErrorAPI(f"error de transporte ({type(error).__name__})") from None
        latencia_ms = int((time.perf_counter() - inicio) * 1000)
        tokens_entrada, tokens_salida = _tokens(respuesta)
        logger.info(
            "respuesta modelo=%s finish_reason=%s tokens=%s/%s latencia_ms=%s",
            self._modelo,
            respuesta.finish_reason,
            tokens_entrada,
            tokens_salida,
            latencia_ms,
        )
        return RespuestaLLM(
            texto_crudo=_texto_crudo(respuesta),
            finish_reason=respuesta.finish_reason,
            tokens_entrada=tokens_entrada,
            tokens_salida=tokens_salida,
            latencia_ms=latencia_ms,
        )
