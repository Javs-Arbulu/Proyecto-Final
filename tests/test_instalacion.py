"""El proyecto es instalable (layout src/, instalación editable) y ejecutable con -m."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_pyproject_usa_layout_src_y_versiones_fijadas() -> None:
    proyecto = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    assert proyecto["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/extractor"]
    dependencias = proyecto["project"]["dependencies"]
    dependencias += proyecto["project"]["optional-dependencies"]["dev"]
    assert all("==" in dependencia for dependencia in dependencias)
    assert any(d.startswith("cohere==") for d in dependencias)


def test_paquete_instalado_en_modo_editable() -> None:
    direct_url = distribution("extractor-facturas").read_text("direct_url.json")
    assert direct_url is not None
    assert json.loads(direct_url).get("dir_info", {}).get("editable") is True


def test_python_m_extractor_funciona_desde_la_raiz() -> None:
    salida = subprocess.run(
        [sys.executable, "-m", "extractor", "--help"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for comando in ("procesar", "extraer", "esquema"):
        assert comando in salida


def test_comando_esquema_confirma_compatibilidad_con_cohere() -> None:
    resultado = subprocess.run(
        [sys.executable, "-m", "extractor", "esquema"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    assert resultado.returncode == 0
    assert "Compatible con Structured Outputs de Cohere" in resultado.stdout
