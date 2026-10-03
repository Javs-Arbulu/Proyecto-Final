"""Pipeline: aislamiento de fallos, prechecks, circuit breaker y simulación acotada."""

from __future__ import annotations

from pathlib import Path

import pytest

from extractor.config import Settings
from extractor.fault_injection import ModoFallo
from extractor.llm_client import ErrorAPI, ErrorAutenticacion
from extractor.pipeline import (
    DETALLE_CIRCUITO,
    Simulacion,
    leer_documento,
    procesar_aislado,
    procesar_lote,
)
from extractor.schema import CodigoMotivo, EstadoExtraccion

from .conftest import TEXTO_FACTURA, FakePorDocumento, factura_valida, respuesta_json

VALIDA = respuesta_json(factura_valida())


def _carpeta(tmp_path: Path, nombres: list[str], contenido: str = TEXTO_FACTURA) -> Path:
    for nombre in nombres:
        (tmp_path / nombre).write_text(contenido, encoding="utf-8")
    return tmp_path


def test_runtime_error_no_detiene_el_lote(tmp_path: Path, settings: Settings) -> None:
    carpeta = _carpeta(tmp_path, ["01.txt", "02.txt", "03.txt"])
    fake = FakePorDocumento({"02": RuntimeError("se cayó el parser")}, por_defecto=VALIDA)
    lote = procesar_lote(carpeta, fake, settings)
    estados = {r.archivo: r for r in lote.resultados}
    assert [r.archivo for r in lote.resultados] == ["01.txt", "02.txt", "03.txt"]
    assert estados["01.txt"].estado is EstadoExtraccion.EXITOSO
    assert estados["03.txt"].estado is EstadoExtraccion.EXITOSO
    fallido = estados["02.txt"]
    assert fallido.estado is EstadoExtraccion.FALLIDO
    assert fallido.motivos == [CodigoMotivo.ERROR_INESPERADO]
    assert fallido.detalle == "RuntimeError: se cayó el parser"
    assert "Traceback" not in fallido.detalle  # el traceback solo va al log


def test_documento_vacio_no_llama_a_la_api(tmp_path: Path, settings: Settings) -> None:
    (tmp_path / "11_vacio.txt").write_text("", encoding="utf-8")
    (tmp_path / "12_espacios.txt").write_text("  \n\t ", encoding="utf-8")
    fake = FakePorDocumento({})
    lote = procesar_lote(tmp_path, fake, settings)
    assert fake.llamadas == []
    assert all(r.motivos == [CodigoMotivo.DOCUMENTO_VACIO] for r in lote.resultados)
    assert all(r.llamadas_api == 0 for r in lote.resultados)


def test_documento_demasiado_largo_no_llama_a_la_api(tmp_path: Path, settings: Settings) -> None:
    (tmp_path / "largo.txt").write_text("x" * 101, encoding="utf-8")
    fake = FakePorDocumento({})
    ajustes = settings.model_copy(update={"max_chars_documento": 100})
    resultado = procesar_lote(tmp_path, fake, ajustes).resultados[0]
    assert fake.llamadas == []
    assert resultado.motivos == [CodigoMotivo.DOCUMENTO_DEMASIADO_LARGO]


def test_circuit_breaker_ante_error_de_autenticacion(tmp_path: Path, settings: Settings) -> None:
    carpeta = _carpeta(tmp_path, ["01.txt", "02.txt", "03.txt", "04.txt"])
    fake = FakePorDocumento({"02": ErrorAutenticacion("HTTP 401")}, por_defecto=VALIDA)
    lote = procesar_lote(carpeta, fake, settings)
    assert lote.circuito_abierto is True
    assert fake.llamadas == ["01.txt", "02.txt"]  # 03 y 04 no llaman a la API
    assert lote.resultados[0].estado is EstadoExtraccion.EXITOSO
    for resultado in lote.resultados[1:]:
        assert resultado.estado is EstadoExtraccion.FALLIDO
        assert resultado.motivos == [CodigoMotivo.ERROR_API]
    assert [r.detalle for r in lote.resultados[2:]] == [DETALLE_CIRCUITO] * 2


def test_error_api_queda_aislado_en_su_documento(tmp_path: Path, settings: Settings) -> None:
    carpeta = _carpeta(tmp_path, ["01.txt", "02.txt"])
    fake = FakePorDocumento({"01": ErrorAPI("HTTP 500 tras reintentos")}, por_defecto=VALIDA)
    lote = procesar_lote(carpeta, fake, settings)
    assert lote.resultados[0].motivos == [CodigoMotivo.ERROR_API]
    assert lote.resultados[0].llamadas_api == 1
    assert lote.resultados[1].estado is EstadoExtraccion.EXITOSO


def test_ctrl_c_devuelve_resultado_parcial(tmp_path: Path, settings: Settings) -> None:
    carpeta = _carpeta(tmp_path, ["01.txt", "02.txt", "03.txt"])
    fake = FakePorDocumento({"02": KeyboardInterrupt()}, por_defecto=VALIDA)
    lote = procesar_lote(carpeta, fake, settings)
    assert lote.interrumpido is True
    assert [r.archivo for r in lote.resultados] == ["01.txt"]
    assert lote.n_archivos == 3


def test_simulacion_acotada_por_glob(tmp_path: Path, settings: Settings) -> None:
    carpeta = _carpeta(tmp_path, ["01_a.txt", "03_b.txt", "05_c.txt"])
    fake = FakePorDocumento({}, por_defecto=VALIDA)
    ajustes = settings.model_copy(update={"max_intentos": 2})
    simulacion = Simulacion(ModoFallo.PERSISTENTE, "03*")
    lote = procesar_lote(carpeta, fake, ajustes, simulacion)
    por_nombre = {r.archivo: r for r in lote.resultados}
    simulado = por_nombre["03_b.txt"]
    assert simulado.estado is EstadoExtraccion.FALLIDO
    assert simulado.motivos == [CodigoMotivo.JSON_INVALIDO_REINTENTOS_AGOTADOS]
    assert simulado.simulacion == "persistente"
    assert len(simulado.intentos) == 2  # límite alcanzado
    for nombre in ("01_a.txt", "05_c.txt"):  # el resto del lote sigue normal
        assert por_nombre[nombre].estado is EstadoExtraccion.EXITOSO
        assert por_nombre[nombre].simulacion is None


def test_documento_latin1_se_lee_con_fallback(tmp_path: Path) -> None:
    ruta = tmp_path / "latin.txt"
    ruta.write_bytes("Señor(es): ÑANDÚ S.A.C.".encode("latin-1"))
    assert leer_documento(ruta) == "Señor(es): ÑANDÚ S.A.C."


def test_procesar_aislado_agrega_tokens_y_llamadas(tmp_path: Path, settings: Settings) -> None:
    ruta = _carpeta(tmp_path, ["01.txt"]) / "01.txt"
    resultado = procesar_aislado(ruta, FakePorDocumento({}, por_defecto=VALIDA), settings)
    assert resultado.estado is EstadoExtraccion.EXITOSO
    assert (resultado.llamadas_api, resultado.tokens_entrada_total) == (1, 1000)


def test_error_autenticacion_se_propaga_desde_procesar_aislado(
    tmp_path: Path, settings: Settings
) -> None:
    ruta = _carpeta(tmp_path, ["01.txt"]) / "01.txt"
    with pytest.raises(ErrorAutenticacion):
        procesar_aislado(ruta, FakePorDocumento({"01": ErrorAutenticacion("401")}), settings)
