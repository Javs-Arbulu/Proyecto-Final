"""Reportes del lote: JSON completo, CSV, Markdown y vista en consola."""

from __future__ import annotations

from pathlib import Path

CARPETA_SIMULACIONES = "simulaciones"


def carpeta_salida(base: Path, modo_simulacion: str | None) -> Path:
    """Carpeta de salida: la oficial o ``<base>/simulaciones/<modo>/``.

    Las corridas con ``--simular-fallo`` NUNCA escriben sobre la corrida oficial.
    """
    if modo_simulacion is None:
        return base
    return base / CARPETA_SIMULACIONES / modo_simulacion
