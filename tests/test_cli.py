"""CLI de punta a punta con un cliente falso (sin red ni key real)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from extractor import cli
from extractor.config import Settings, cargar_settings

from .conftest import KEY_FALSA, TEXTO_FACTURA, FakePorDocumento, factura_valida, respuesta_json

runner = CliRunner()


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakePorDocumento:
    """Settings de prueba (sin leer .env), cliente falso y logs en tmp."""
    fake = FakePorDocumento({}, por_defecto=respuesta_json(factura_valida()))

    def settings_de_prueba(**overrides: object) -> Settings:
        return cargar_settings(
            _env_file=None, cohere_api_key=KEY_FALSA, intervalo_min_s=0, **overrides
        )

    monkeypatch.setattr(cli, "cargar_settings", settings_de_prueba)
    monkeypatch.setattr(cli, "crear_cliente", lambda _settings: fake)
    monkeypatch.setattr(cli, "CARPETA_LOGS", tmp_path / "logs")
    return fake


def _documentos(tmp_path: Path) -> Path:
    carpeta = tmp_path / "docs"
    carpeta.mkdir()
    (carpeta / "01_ok.txt").write_text(TEXTO_FACTURA, encoding="utf-8")
    (carpeta / "03_otro.txt").write_text(TEXTO_FACTURA, encoding="utf-8")
    (carpeta / "11_vacio.txt").write_text("", encoding="utf-8")
    return carpeta


def test_procesar_genera_los_tres_reportes(entorno: FakePorDocumento, tmp_path: Path) -> None:
    salida = tmp_path / "output"
    resultado = runner.invoke(
        cli.app, ["procesar", str(_documentos(tmp_path)), "--salida", str(salida)]
    )
    assert resultado.exit_code == 0, resultado.output
    assert {p.name for p in salida.iterdir()} == {"resultados.json", "reporte.csv", "reporte.md"}
    datos = json.loads((salida / "resultados.json").read_text(encoding="utf-8"))
    assert datos["metadatos"]["proveedor"] == "cohere"
    assert datos["metricas"]["llamadas_api_totales"] == 2  # el vacío no llama
    assert "Métricas del lote" in resultado.output


def test_procesar_con_simulacion_escribe_en_simulaciones(
    entorno: FakePorDocumento, tmp_path: Path
) -> None:
    salida = tmp_path / "output"
    argumentos = ["procesar", str(_documentos(tmp_path)), "--salida", str(salida)]
    argumentos += ["--simular-fallo", "persistente", "--simular-en", "03*", "--max-intentos", "2"]
    resultado = runner.invoke(cli.app, argumentos)
    assert resultado.exit_code == 0, resultado.output
    assert "[SIMULACIÓN: persistente]" in resultado.output
    destino = salida / "simulaciones" / "persistente"
    assert (destino / "resultados.json").is_file()
    assert not (salida / "resultados.json").exists()  # nunca pisa la corrida oficial
    datos = json.loads((destino / "resultados.json").read_text(encoding="utf-8"))
    simulado = next(d for d in datos["documentos"] if d["archivo"] == "03_otro.txt")
    assert simulado["estado"] == "FALLIDO"
    assert len(simulado["intentos"]) == 2


def test_max_intentos_fuera_de_rango_sale_controlado(
    entorno: FakePorDocumento, tmp_path: Path
) -> None:
    resultado = runner.invoke(cli.app, ["procesar", str(tmp_path), "--max-intentos", "6"])
    assert resultado.exit_code == 2
    assert "MAX_INTENTOS" in resultado.output


def test_simular_en_sin_simular_fallo_es_error(entorno: FakePorDocumento, tmp_path: Path) -> None:
    resultado = runner.invoke(cli.app, ["procesar", str(tmp_path), "--simular-en", "03*"])
    assert resultado.exit_code == 2


def test_extraer_documento_vacio_no_llama_a_la_api(
    entorno: FakePorDocumento, tmp_path: Path
) -> None:
    ruta = _documentos(tmp_path) / "11_vacio.txt"
    resultado = runner.invoke(cli.app, ["extraer", str(ruta)])
    assert resultado.exit_code == 0
    assert "DOCUMENTO_VACIO" in resultado.output
    assert entorno.llamadas == []


def test_extraer_con_simulacion_muestra_reintento_y_recuperacion(
    entorno: FakePorDocumento, tmp_path: Path
) -> None:
    ruta = _documentos(tmp_path) / "01_ok.txt"
    resultado = runner.invoke(cli.app, ["extraer", str(ruta), "--simular-fallo", "json_invalido"])
    assert resultado.exit_code == 0, resultado.output
    assert "intento 1/3 ✗ JSON inválido" in resultado.output
    assert "intento 3/3 ✓" in resultado.output
    assert "EXITOSO" in resultado.output


def test_sin_key_sale_con_mensaje_claro_y_sin_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "cargar_settings", lambda **kw: cargar_settings(_env_file=None, **kw))
    monkeypatch.setattr(cli, "CARPETA_LOGS", tmp_path / "logs")
    resultado = runner.invoke(cli.app, ["procesar", str(tmp_path)])
    assert resultado.exit_code == 2
    assert "COHERE_API_KEY" in resultado.output
    assert "Traceback" not in resultado.output
