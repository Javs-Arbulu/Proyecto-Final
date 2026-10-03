"""Tests de métricas, comparación con esperado.json y archivos de reporte."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from extractor.report import (
    COLUMNAS_CSV,
    calcular_metricas,
    campo_coincide,
    carpeta_salida,
    construir_metadatos,
    escribir_reportes,
)
from extractor.schema import (
    Advertencia,
    CodigoAdvertencia,
    CodigoMotivo,
    EstadoExtraccion,
    Intento,
    ResultadoDocumento,
)


def _intento(numero: int, ok: bool = True) -> Intento:
    return Intento(numero=numero, ok=ok, tokens_entrada=1000, tokens_salida=100, latencia_ms=500)


def _resultados() -> list[ResultadoDocumento]:
    return [
        ResultadoDocumento(
            archivo="01.txt",
            estado=EstadoExtraccion.EXITOSO,
            motivos=[CodigoMotivo.OK],
            detalle="ok",
            datos={
                "numero_factura": "F001-00000001",
                "fecha_emision": "2026-09-15",
                "ruc_emisor": "20615487303",
                "moneda": "PEN",
                "total": "2209.55",
            },
            intentos=[_intento(1, False), _intento(2)],
            llamadas_api=2,
            tokens_entrada_total=2000,
            tokens_salida_total=200,
            latencia_ms_total=1000,
        ),
        ResultadoDocumento(
            archivo="07.txt",
            estado=EstadoExtraccion.PARCIAL,
            motivos=[CodigoMotivo.REGLA_NEGOCIO],
            detalle="requiere revisión humana",
            advertencias=[
                Advertencia(codigo=CodigoAdvertencia.TOTALES_INCONSISTENTES, mensaje="no cuadra")
            ],
            datos={
                "numero_factura": "F001-932",
                "fecha_emision": "2026-09-02",
                "ruc_emisor": "20497351808",
                "moneda": "PEN",
                "total": 1870.0,
            },
            intentos=[_intento(1)],
            llamadas_api=1,
            tokens_entrada_total=1000,
            tokens_salida_total=100,
            latencia_ms_total=500,
        ),
        ResultadoDocumento(
            archivo="11.txt",
            estado=EstadoExtraccion.FALLIDO,
            motivos=[CodigoMotivo.DOCUMENTO_VACIO],
            detalle="vacío",
        ),
    ]


ESPERADO = {
    "01.txt": {
        "estado": "EXITOSO",
        "motivos_aceptables": ["OK"],
        "campos": {
            "numero_factura": "F001-00000001",
            "fecha_emision": "2026-09-15",
            "ruc_emisor": "20615487303",
            "moneda": "PEN",
            "total": 2209.55,
        },
    },
    "07.txt": {
        "estado": "PARCIAL",
        "motivos_aceptables": ["REGLA_NEGOCIO"],
        "campos": {
            "numero_factura": "F001-00000932",
            "fecha_emision": "2026-09-03",
            "ruc_emisor": "20497351808",
            "moneda": "PEN",
            "total": 1870.0,
        },
    },
    "11.txt": {"estado": "EXITOSO", "motivos_aceptables": ["OK"], "campos": None},
}


def test_metricas_basicas() -> None:
    m = calcular_metricas(_resultados())
    assert (m["n_documentos"], m["exitosos"], m["parciales"], m["fallidos"]) == (3, 1, 1, 1)
    assert m["pct_exito"] == 33.3
    assert m["tasa_utilizable_pct"] == 66.7
    assert m["intentos_promedio"] == 1.5  # solo documentos con llamadas
    assert m["docs_con_reintento"] == 1
    assert m["llamadas_api_totales"] == 3
    assert (m["tokens_entrada_totales"], m["tokens_salida_totales"]) == (3000, 300)
    assert m["latencia_promedio_ms"] == 750
    assert m["motivos_por_codigo"] == {"REGLA_NEGOCIO": 1, "DOCUMENTO_VACIO": 1}
    assert m["advertencias_por_codigo"] == {"TOTALES_INCONSISTENTES": 1}
    assert m["comparacion_esperado"] is None


def test_comparacion_con_esperado() -> None:
    comp = calcular_metricas(_resultados(), ESPERADO, 0.05)["comparacion_esperado"]
    assert comp["documentos_comparados"] == 3
    assert comp["estado_coincide"] == 2  # 11.txt esperado EXITOSO pero FALLIDO
    exactitud = comp["exactitud_por_campo"]
    assert exactitud["numero_factura"] == {"aciertos": 2, "total": 2, "pct": 100.0}
    assert exactitud["fecha_emision"] == {"aciertos": 1, "total": 2, "pct": 50.0}
    assert exactitud["total"]["aciertos"] == 2
    assert comp["pct_exactitud_campos"] == 90.0


def test_campo_coincide_reglas() -> None:
    assert campo_coincide("total", 1870.0, "1870.04", 0.05)
    assert not campo_coincide("total", 1870.0, 1871.0, 0.05)
    assert campo_coincide("numero_factura", "F003-0001209", "F003-1209", 0.05)
    assert campo_coincide("fecha_emision", None, None, 0.05)
    assert not campo_coincide("fecha_emision", None, "2026-01-01", 0.05)  # inventado
    assert not campo_coincide("ruc_emisor", "20615487303", None, 0.05)


def test_escribe_json_csv_y_md(tmp_path: Path) -> None:
    resultados = _resultados()
    metricas = calcular_metricas(resultados, ESPERADO, 0.05)
    metadatos = construir_metadatos("command-a-03-2025", "2.0", {"max_intentos": 3}, None, False, 3)
    rutas = escribir_reportes(tmp_path, resultados, metricas, metadatos)
    assert [r.name for r in rutas] == ["resultados.json", "reporte.csv", "reporte.md"]
    contenido = json.loads(rutas[0].read_text(encoding="utf-8"))
    assert contenido["metadatos"]["proveedor"] == "cohere"
    assert contenido["metadatos"]["modelo"] == "command-a-03-2025"
    assert contenido["metricas"]["llamadas_api_totales"] == 3
    assert len(contenido["documentos"]) == 3
    with rutas[1].open(encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    assert tuple(filas[0]) == COLUMNAS_CSV
    assert filas[1]["advertencias"] == "TOTALES_INCONSISTENTES"
    assert filas[2]["total"] == ""
    markdown = rutas[2].read_text(encoding="utf-8")
    assert "Llamadas totales a la API | 3" in markdown
    assert "| 07.txt | **PARCIAL** | PARCIAL ✓ |" in markdown


def test_metadatos_no_incluyen_secretos() -> None:
    metadatos = construir_metadatos("m", "2.0", {"modelo": "m"}, None, False, 0)
    assert "api_key" not in json.dumps(metadatos)


def test_corrida_oficial_escribe_en_la_carpeta_base() -> None:
    assert carpeta_salida(Path("output"), None) == Path("output")


def test_simulacion_nunca_escribe_sobre_la_corrida_oficial() -> None:
    for modo in ("json_invalido", "tipo_incorrecto", "persistente", "sin_json"):
        destino = carpeta_salida(Path("output"), modo)
        assert destino == Path("output") / "simulaciones" / modo
        assert destino != Path("output")
