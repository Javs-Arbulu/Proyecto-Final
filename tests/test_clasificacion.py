"""Clasificación EXITOSO / PARCIAL / FALLIDO y precedencia de reglas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from extractor.config import Settings
from extractor.schema import CodigoAdvertencia, CodigoMotivo, EstadoExtraccion, FacturaExtraida
from extractor.validation import evaluar, extraer_con_reintentos

from .conftest import TEXTO_FACTURA, FakeClienteLLM, factura_valida, respuesta_json

HOY = date(2026, 10, 3)


def _evaluar(settings: Settings, texto: str = TEXTO_FACTURA, **cambios: object):
    factura = FacturaExtraida.model_validate(factura_valida(**cambios))
    return evaluar(factura, texto, settings, hoy=HOY)


def test_factura_completa_y_coherente_es_exitosa_con_decimal(settings: Settings) -> None:
    evaluacion = _evaluar(settings)
    assert evaluacion.estado is EstadoExtraccion.EXITOSO
    assert evaluacion.motivos == [CodigoMotivo.OK]
    assert evaluacion.datos is not None and evaluacion.datos["total"] == "118.00"
    assert Decimal(evaluacion.datos["igv"]) == Decimal("18.00")


def test_faltan_obligatorios_no_criticos_es_parcial_con_una_sola_llamada(
    settings: Settings,
) -> None:
    """Los campos faltantes NO se reintentan: si el dato no está, reintentar solo gasta."""
    datos = factura_valida(ruc_cliente=None, fecha_emision=None, fecha_vencimiento=None)
    datos["condicion_pago"] = "CONTADO"
    fake = FakeClienteLLM([respuesta_json(datos)])
    extraccion = extraer_con_reintentos(TEXTO_FACTURA, "06.txt", fake, settings)
    assert extraccion.factura is not None
    evaluacion = evaluar(extraccion.factura, TEXTO_FACTURA, settings, hoy=HOY)
    assert len(fake.llamadas) == 1
    assert evaluacion.estado is EstadoExtraccion.PARCIAL
    assert evaluacion.motivos == [CodigoMotivo.CAMPOS_OBLIGATORIOS_FALTANTES]
    assert evaluacion.campos_faltantes == ["fecha_emision", "ruc_cliente"]


def test_advertencia_de_negocio_es_parcial_con_regla_negocio(settings: Settings) -> None:
    texto = TEXTO_FACTURA.replace("118.00", "218.00")
    evaluacion = _evaluar(settings, texto, total=218.0)
    assert evaluacion.estado is EstadoExtraccion.PARCIAL
    assert evaluacion.motivos == [CodigoMotivo.REGLA_NEGOCIO]
    assert [a.codigo for a in evaluacion.advertencias] == [CodigoAdvertencia.TOTALES_INCONSISTENTES]
    assert "requiere revisión humana" in evaluacion.detalle


def test_faltantes_y_advertencias_juntos_listan_ambos_motivos(settings: Settings) -> None:
    evaluacion = _evaluar(settings, razon_social_emisor=None, igv=10.0, total=110.0)
    assert evaluacion.motivos == [
        CodigoMotivo.CAMPOS_OBLIGATORIOS_FALTANTES,
        CodigoMotivo.REGLA_NEGOCIO,
    ]


def test_tipo_otro_es_fallido_fuera_de_dominio(settings: Settings) -> None:
    evaluacion = _evaluar(settings, tipo_documento="OTRO")
    assert evaluacion.estado is EstadoExtraccion.FALLIDO
    assert evaluacion.motivos == [CodigoMotivo.FUERA_DE_DOMINIO]
    assert "OTRO" in evaluacion.detalle


def test_tipo_null_y_boleta_son_fuera_de_dominio(settings: Settings) -> None:
    for tipo in (None, "BOLETA", "NOTA_CREDITO"):
        assert _evaluar(settings, tipo_documento=tipo).motivos == [CodigoMotivo.FUERA_DE_DOMINIO]


def test_fuera_de_dominio_tiene_precedencia_sobre_faltantes(settings: Settings) -> None:
    nulos = dict.fromkeys(["numero_factura", "total", "ruc_emisor", "moneda"])
    evaluacion = _evaluar(settings, tipo_documento="OTRO", **nulos)
    assert evaluacion.motivos == [CodigoMotivo.FUERA_DE_DOMINIO]


def test_falta_campo_critico_es_fallido(settings: Settings) -> None:
    for critico in ("numero_factura", "total"):
        evaluacion = _evaluar(settings, **{critico: None})
        assert evaluacion.estado is EstadoExtraccion.FALLIDO
        assert evaluacion.motivos == [CodigoMotivo.EXTRACCION_INSUFICIENTE]
        assert critico in evaluacion.campos_faltantes
        assert evaluacion.datos is not None  # el reporte igual muestra lo obtenido


def test_mas_de_la_mitad_de_obligatorios_faltantes_es_fallido(settings: Settings) -> None:
    nulos = dict.fromkeys(
        ["fecha_emision", "ruc_emisor", "razon_social_emisor", "ruc_cliente", "moneda"]
    )
    evaluacion = _evaluar(settings, fecha_vencimiento=None, condicion_pago="CONTADO", **nulos)
    assert evaluacion.estado is EstadoExtraccion.FALLIDO
    assert evaluacion.motivos == [CodigoMotivo.EXTRACCION_INSUFICIENTE]


def test_exactamente_la_mitad_de_obligatorios_faltantes_es_parcial(settings: Settings) -> None:
    nulos = dict.fromkeys(["fecha_emision", "ruc_emisor", "razon_social_emisor", "ruc_cliente"])
    evaluacion = _evaluar(settings, fecha_vencimiento=None, condicion_pago="CONTADO", **nulos)
    assert evaluacion.estado is EstadoExtraccion.PARCIAL


def test_texto_vacio_cuenta_como_faltante(settings: Settings) -> None:
    evaluacion = _evaluar(settings, razon_social_emisor="   ")
    assert evaluacion.campos_faltantes == ["razon_social_emisor"]


def test_si_falla_el_contrato_estricto_queda_parcial(settings: Settings) -> None:
    """Defensa en profundidad: ítem sin descripción pasa las reglas pero no FacturaValidada."""
    item = {"descripcion": "  ", "cantidad": 2, "precio_unitario": 50.0, "importe": 100.0}
    evaluacion = _evaluar(settings, items=[item])
    assert evaluacion.estado is EstadoExtraccion.PARCIAL
    assert evaluacion.motivos == [CodigoMotivo.REGLA_NEGOCIO]
    assert "contrato estricto" in evaluacion.detalle
