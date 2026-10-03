"""Tests de seguridad y arquitectura (evidencia para la rúbrica de 20 pts)."""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PAQUETE = RAIZ / "src" / "extractor"
# Se construye por concatenación para que este archivo no se detecte a sí mismo.
PREFIJO_KEY = "sk-" + "ant-"


def _archivos_del_repo() -> list[Path]:
    """Archivos versionados o versionables (respeta .gitignore)."""
    if shutil.which("git") is None:
        pytest.skip("git no está disponible")
    salida = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [RAIZ / linea for linea in salida.splitlines() if (RAIZ / linea).is_file()]


def _imports_de(modulo: Path) -> set[str]:
    """Nombres de módulos importados por un archivo Python (vía AST)."""
    arbol = ast.parse(modulo.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level > 0:
                nombres.add("." * nodo.level + (nodo.module or ""))
            elif nodo.module:
                nombres.add(nodo.module)
    return nombres


def test_env_esta_en_gitignore() -> None:
    lineas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lineas


def test_git_ignora_env() -> None:
    if shutil.which("git") is None:
        pytest.skip("git no está disponible")
    resultado = subprocess.run(["git", "check-ignore", ".env"], cwd=RAIZ, capture_output=True)
    assert resultado.returncode == 0, ".env NO está ignorado por git"


def test_env_example_existe_y_no_tiene_keys() -> None:
    ejemplo = RAIZ / ".env.example"
    assert ejemplo.is_file()
    contenido = ejemplo.read_text(encoding="utf-8")
    assert PREFIJO_KEY not in contenido
    assert "ANTHROPIC_API_KEY=tu_key_aqui" in contenido


def test_ningun_archivo_del_repo_contiene_una_key() -> None:
    con_key = []
    for archivo in _archivos_del_repo():
        try:
            if PREFIJO_KEY in archivo.read_text(encoding="utf-8", errors="ignore"):
                con_key.append(str(archivo.relative_to(RAIZ)))
        except OSError:
            continue
    assert con_key == [], f"Posible API key versionada en: {con_key}"


def test_env_no_es_versionable() -> None:
    nombres = {archivo.relative_to(RAIZ).as_posix() for archivo in _archivos_del_repo()}
    assert ".env" not in nombres


def test_solo_llm_client_importa_anthropic() -> None:
    infractores = []
    for modulo in PAQUETE.glob("*.py"):
        importa = any(n == "anthropic" or n.startswith("anthropic.") for n in _imports_de(modulo))
        if importa and modulo.name != "llm_client.py":
            infractores.append(modulo.name)
    assert infractores == [], f"Importan el SDK sin ser llm_client.py: {infractores}"


def test_schema_no_importa_nada_del_proyecto() -> None:
    imports = _imports_de(PAQUETE / "schema.py") if (PAQUETE / "schema.py").exists() else set()
    del_proyecto = {n for n in imports if n.startswith(".") or n.split(".")[0] == "extractor"}
    assert del_proyecto == set(), f"schema.py depende del proyecto: {del_proyecto}"
