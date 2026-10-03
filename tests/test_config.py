"""Tests de configuración: rangos, fail-fast y no exposición de la key."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractor.config import ConfiguracionInvalida, Settings, cargar_settings

from .conftest import KEY_FALSA


def _settings(**cambios: object) -> Settings:
    return Settings(_env_file=None, cohere_api_key=KEY_FALSA, **cambios)


def test_valores_por_defecto() -> None:
    settings = _settings()
    assert settings.modelo == "command-a-03-2025"
    assert settings.max_intentos == 3
    assert settings.max_tokens == 2048
    assert settings.timeout_s == 60
    assert settings.max_chars_documento == 20000
    assert settings.tolerancia_montos == 0.05
    assert settings.intervalo_min_s == 3.1


def test_lee_la_key_de_la_variable_cohere_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COHERE_API_KEY", KEY_FALSA)
    settings = Settings(_env_file=None)
    assert settings.cohere_api_key.get_secret_value() == KEY_FALSA


@pytest.mark.parametrize("valor", [0, 6, 100])
def test_rechaza_max_intentos_fuera_de_rango(valor: int) -> None:
    with pytest.raises(ValidationError):
        _settings(max_intentos=valor)


@pytest.mark.parametrize("valor", [1, 5])
def test_acepta_max_intentos_en_los_extremos(valor: int) -> None:
    assert _settings(max_intentos=valor).max_intentos == valor


def test_rechaza_max_tokens_sobre_el_tope() -> None:
    with pytest.raises(ValidationError):
        _settings(max_tokens=9000)


def test_rechaza_modelo_sin_structured_outputs() -> None:
    with pytest.raises(ValidationError, match="no soporta Structured Outputs"):
        _settings(modelo="command-r7b-12-2024")


def test_acepta_modelo_alternativo() -> None:
    assert _settings(modelo="command-a-plus-05-2026").modelo == "command-a-plus-05-2026"


def test_falta_key_falla_con_mensaje_claro_sin_valores() -> None:
    with pytest.raises(ConfiguracionInvalida) as error:
        cargar_settings(_env_file=None)
    assert "COHERE_API_KEY" in str(error.value)
    assert "Traceback" not in str(error.value)


def test_placeholder_de_env_example_se_rechaza() -> None:
    with pytest.raises(ConfiguracionInvalida):
        cargar_settings(_env_file=None, cohere_api_key="tu_key_aqui")


def test_error_de_rango_no_expone_la_key() -> None:
    with pytest.raises(ConfiguracionInvalida) as error:
        cargar_settings(_env_file=None, cohere_api_key=KEY_FALSA, max_intentos=9)
    assert KEY_FALSA not in str(error.value)


def test_repr_y_str_no_exponen_la_key() -> None:
    settings = _settings()
    assert KEY_FALSA not in repr(settings)
    assert KEY_FALSA not in str(settings)
    assert KEY_FALSA not in str(settings.resumen_publico())
    assert "cohere_api_key" not in settings.resumen_publico()
