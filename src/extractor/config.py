"""Configuración de la aplicación (pydantic-settings).

La API key se maneja como ``SecretStr``: ni ``repr`` ni ``str`` la exponen, y
nunca se imprime ni se loguea. Si falta, la aplicación falla rápido con un
mensaje claro y sin traceback (ver :func:`cargar_settings`).
"""

from __future__ import annotations

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODELO_POR_DEFECTO = "claude-haiku-4-5-20251001"
MAX_INTENTOS_MIN = 1
MAX_INTENTOS_MAX = 5
TOPE_MAX_TOKENS = 8192
PLACEHOLDER_API_KEY = "tu_key_aqui"


class ConfiguracionInvalida(Exception):
    """Error de configuración con un mensaje apto para mostrar (sin secretos)."""


class Settings(BaseSettings):
    """Parámetros del extractor, leídos de variables de entorno o de ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr
    modelo: str = MODELO_POR_DEFECTO
    max_intentos: int = 3
    max_tokens: int = Field(default=2048, ge=1, le=TOPE_MAX_TOKENS)
    timeout_s: float = Field(default=60, gt=0)
    max_chars_documento: int = Field(default=20000, ge=1)
    tolerancia_montos: float = Field(default=0.05, ge=0)

    @field_validator("anthropic_api_key")
    @classmethod
    def _key_no_vacia(cls, valor: SecretStr) -> SecretStr:
        """Rechaza una key vacía o el placeholder de ``.env.example``."""
        secreto = valor.get_secret_value().strip()
        if not secreto or secreto == PLACEHOLDER_API_KEY:
            raise ValueError("la API key está vacía o es el placeholder de .env.example")
        return valor

    @field_validator("max_intentos")
    @classmethod
    def _rango_intentos(cls, valor: int) -> int:
        """Tope duro contra bucles y costo descontrolado: 1 a 5 intentos."""
        if not MAX_INTENTOS_MIN <= valor <= MAX_INTENTOS_MAX:
            raise ValueError(
                f"max_intentos debe estar entre {MAX_INTENTOS_MIN} y {MAX_INTENTOS_MAX}"
            )
        return valor

    def resumen_publico(self) -> dict[str, object]:
        """Configuración sin secretos, apta para reportes y logs."""
        return self.model_dump(exclude={"anthropic_api_key"})


def _mensaje_seguro(error: ValidationError) -> str:
    """Arma un mensaje con campo y problema, sin incluir valores recibidos."""
    lineas = []
    for detalle in error.errors(include_input=False, include_url=False):
        campo = ".".join(str(parte) for parte in detalle["loc"]) or "configuración"
        if campo == "anthropic_api_key":
            campo = "ANTHROPIC_API_KEY"
        lineas.append(f"- {campo}: {detalle['msg']}")
    return "Configuración inválida:\n" + "\n".join(lineas)


def cargar_settings(**overrides: object) -> Settings:
    """Carga la configuración; traduce errores a :class:`ConfiguracionInvalida`."""
    try:
        return Settings(**overrides)
    except ValidationError as error:
        raise ConfiguracionInvalida(_mensaje_seguro(error)) from None
