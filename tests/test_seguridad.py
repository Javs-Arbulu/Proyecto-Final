"""Tests de seguridad y arquitectura (evidencia para la rúbrica de 20 pts)."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

RAIZ = Path(__file__).resolve().parent.parent
PAQUETE = RAIZ / "src" / "extractor"
PLACEHOLDER = "tu_key_aqui"
# Se captura al importar el módulo, antes de que el fixture autouse limpie el entorno.
KEY_DEL_ENTORNO = os.environ.get("COHERE_API_KEY")
ASIGNACION_KEY = re.compile(r"COHERE_API_KEY\s*[=:]\s*[\"']?([^\s\"'#]*)")


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


def _leer(archivo: Path) -> str:
    try:
        return archivo.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _keys_reales() -> list[str]:
    """Valores reales de COHERE_API_KEY (en .env o en el entorno). Nunca se imprimen."""
    candidatos = [KEY_DEL_ENTORNO]
    env = RAIZ / ".env"
    if env.is_file():
        candidatos.append(dotenv_values(env).get("COHERE_API_KEY"))
    return [k for k in candidatos if k and k.strip() and k.strip() != PLACEHOLDER]


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


# --- Credenciales -------------------------------------------------------------------


def test_env_esta_en_gitignore() -> None:
    lineas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lineas


def test_git_ignora_env() -> None:
    if shutil.which("git") is None:
        pytest.skip("git no está disponible")
    resultado = subprocess.run(["git", "check-ignore", ".env"], cwd=RAIZ, capture_output=True)
    assert resultado.returncode == 0, ".env NO está ignorado por git"


def test_env_no_es_versionable() -> None:
    nombres = {archivo.relative_to(RAIZ).as_posix() for archivo in _archivos_del_repo()}
    assert ".env" not in nombres


def test_env_example_existe_con_placeholders() -> None:
    contenido = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "COHERE_API_KEY=" + PLACEHOLDER in contenido
    for variable in ("MODELO=command-a-03-2025", "MAX_INTENTOS=3", "INTERVALO_MIN_S=3.1"):
        assert variable in contenido


def test_la_key_real_no_aparece_en_ningun_archivo_del_repo() -> None:
    """Las keys de Cohere no tienen prefijo reconocible: se busca el valor real."""
    keys = _keys_reales()
    if not keys:
        pytest.skip("no hay .env ni COHERE_API_KEY en el entorno")
    con_key = [
        archivo.relative_to(RAIZ).as_posix()
        for archivo in _archivos_del_repo()
        if any(key in _leer(archivo) for key in keys)
    ]
    # El mensaje solo lista nombres de archivo: nunca incluye la key.
    assert con_key == [], f"La key real aparece en: {con_key}"


def test_ningun_archivo_versionado_asigna_otra_key() -> None:
    asignaciones = [
        archivo.relative_to(RAIZ).as_posix()
        for archivo in _archivos_del_repo()
        for valor in ASIGNACION_KEY.findall(_leer(archivo))
        if valor not in ("", PLACEHOLDER)
    ]
    assert asignaciones == [], (
        f"COHERE_API_KEY con un valor distinto del placeholder en: {asignaciones}"
    )


# --- Arquitectura -------------------------------------------------------------------


def test_solo_llm_client_importa_cohere() -> None:
    infractores = []
    for modulo in PAQUETE.glob("*.py"):
        importa = any(n == "cohere" or n.startswith("cohere.") for n in _imports_de(modulo))
        if importa and modulo.name != "llm_client.py":
            infractores.append(modulo.name)
    assert infractores == [], f"Importan el SDK sin ser llm_client.py: {infractores}"
    assert "cohere" in _imports_de(PAQUETE / "llm_client.py")


def test_schema_no_importa_nada_del_proyecto() -> None:
    imports = _imports_de(PAQUETE / "schema.py")
    del_proyecto = {n for n in imports if n.startswith(".") or n.split(".")[0] == "extractor"}
    assert del_proyecto == set(), f"schema.py depende del proyecto: {del_proyecto}"
