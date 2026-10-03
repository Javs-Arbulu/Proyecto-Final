"""Fixtures compartidas. Ningún test llama a la API real."""

from __future__ import annotations

import pytest

from extractor.config import Settings

KEY_FALSA = "clave-de-prueba-no-real-123"


@pytest.fixture(autouse=True)
def _aislar_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que los tests lean la key real del entorno del desarrollador."""
    for variable in ("ANTHROPIC_API_KEY", "MODELO", "MAX_INTENTOS", "MAX_TOKENS"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def settings() -> Settings:
    """Settings de prueba: sin leer .env y con una key falsa."""
    return Settings(_env_file=None, anthropic_api_key=KEY_FALSA)
