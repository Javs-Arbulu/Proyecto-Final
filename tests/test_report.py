"""Tests de los reportes."""

from __future__ import annotations

from pathlib import Path

from extractor.report import carpeta_salida


def test_corrida_oficial_escribe_en_la_carpeta_base() -> None:
    assert carpeta_salida(Path("output"), None) == Path("output")


def test_simulacion_nunca_escribe_sobre_la_corrida_oficial() -> None:
    for modo in ("json_invalido", "tipo_incorrecto", "persistente", "sin_json"):
        destino = carpeta_salida(Path("output"), modo)
        assert destino == Path("output") / "simulaciones" / modo
        assert destino != Path("output")
