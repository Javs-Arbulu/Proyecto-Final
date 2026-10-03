"""Tests del esquema: JSON Schema válido, extra=forbid, enums y contrato estricto."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from extractor.schema import (
    CAMPOS_CLAVE,
    CAMPOS_CRITICOS,
    CAMPOS_OBLIGATORIOS,
    CAMPOS_OPCIONALES,
    FacturaExtraida,
    FacturaValidada,
    TipoDocumento,
)


@pytest.fixture
def datos_validos() -> dict[str, Any]:
    return {
        "tipo_documento": "FACTURA",
        "numero_factura": "F001-00012345",
        "fecha_emision": "2026-03-15",
        "fecha_vencimiento": "2026-04-14",
        "ruc_emisor": "20512345678",
        "razon_social_emisor": "Proveedor Ficticio S.A.C.",
        "ruc_cliente": "20498765432",
        "razon_social_cliente": "Cliente Ficticio S.A.",
        "moneda": "PEN",
        "condicion_pago": "CREDITO",
        "items": [
            {"descripcion": "Servicio", "cantidad": 2, "precio_unitario": 50.0, "importe": 100}
        ],
        "descuento": None,
        "subtotal": 100.0,
        "igv": 18.0,
        "total": 118.0,
        "observaciones": None,
    }


# --- JSON Schema ----------------------------------------------------------------


def test_json_schema_generado_es_valido() -> None:
    Draft202012Validator.check_schema(FacturaExtraida.model_json_schema())


def test_instancia_valida_cumple_json_schema_y_pydantic(datos_validos: dict[str, Any]) -> None:
    Draft202012Validator(FacturaExtraida.model_json_schema()).validate(datos_validos)
    assert FacturaExtraida.model_validate(datos_validos).total == 118.0


def test_esquema_cubre_requisitos_minimos_del_enunciado() -> None:
    """Al menos 6 campos, con uno numérico, uno de fecha y uno enumerado."""
    esquema = FacturaExtraida.model_json_schema()
    propiedades = esquema["properties"]
    texto = str(propiedades)
    assert len(propiedades) >= 6
    assert "'type': 'number'" in texto
    assert "'format': 'date'" in texto
    assert all("enum" in esquema["$defs"][nombre] for nombre in ("TipoDocumento", "Moneda"))


def test_json_schema_prohibe_campos_extra() -> None:
    esquema = FacturaExtraida.model_json_schema()
    assert esquema["additionalProperties"] is False
    assert esquema["$defs"]["ItemFactura"]["additionalProperties"] is False


def test_cada_campo_tiene_descripcion() -> None:
    propiedades = FacturaExtraida.model_json_schema()["properties"]
    sin_descripcion = [campo for campo, defn in propiedades.items() if not defn.get("description")]
    assert sin_descripcion == []


# --- Contrato permisivo -----------------------------------------------------------


def test_todos_los_campos_son_opcionales() -> None:
    vacia = FacturaExtraida.model_validate({})
    assert all(valor is None for valor in vacia.model_dump().values())


def test_extra_forbid_rechaza_campos_inventados(datos_validos: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FacturaExtraida.model_validate({**datos_validos, "campo_inventado": "x"})


def test_extra_forbid_aplica_tambien_a_items(datos_validos: dict[str, Any]) -> None:
    datos_validos["items"][0]["descuento_linea"] = 5
    with pytest.raises(ValidationError):
        FacturaExtraida.model_validate(datos_validos)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("tipo_documento", "TICKET"),
        ("tipo_documento", "factura"),
        ("moneda", "EUR"),
        ("moneda", "S/"),
        ("condicion_pago", "A_PLAZOS"),
    ],
)
def test_enums_rechazan_valores_fuera_del_conjunto(
    datos_validos: dict[str, Any], campo: str, valor: str
) -> None:
    with pytest.raises(ValidationError):
        FacturaExtraida.model_validate({**datos_validos, campo: valor})


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("total", "mil doscientos soles"),
        ("subtotal", "S/ 1,234.50"),
        ("fecha_emision", "15 de marzo"),
        ("fecha_emision", "2026-13-01"),
        ("items", "tres ítems"),
    ],
)
def test_tipos_incorrectos_se_rechazan(
    datos_validos: dict[str, Any], campo: str, valor: str
) -> None:
    with pytest.raises(ValidationError):
        FacturaExtraida.model_validate({**datos_validos, campo: valor})


def test_item_requiere_descripcion() -> None:
    with pytest.raises(ValidationError):
        FacturaExtraida.model_validate({"items": [{"cantidad": 1}]})


# --- Constantes de campos --------------------------------------------------------------


def test_clasificacion_de_campos_es_coherente() -> None:
    campos = set(FacturaExtraida.model_fields)
    assert CAMPOS_CRITICOS <= CAMPOS_OBLIGATORIOS <= campos
    assert CAMPOS_OBLIGATORIOS | CAMPOS_OPCIONALES == campos
    assert CAMPOS_OBLIGATORIOS.isdisjoint(CAMPOS_OPCIONALES)
    assert set(CAMPOS_CLAVE) <= campos
    assert CAMPOS_CRITICOS == {"numero_factura", "total"}
    assert len(CAMPOS_OBLIGATORIOS) == 8


# --- Contrato estricto ---------------------------------------------------------------


def test_factura_validada_cuantiza_montos_a_decimal(datos_validos: dict[str, Any]) -> None:
    validada = FacturaValidada.model_validate({**datos_validos, "total": 118.005, "igv": 18.1})
    assert validada.total == Decimal("118.01")
    assert validada.igv == Decimal("18.10")
    assert isinstance(validada.subtotal, Decimal)


@pytest.mark.parametrize("ruc", ["2051234567", "30512345678", "20-51234567", "99999999999"])
def test_factura_validada_rechaza_ruc_invalido(datos_validos: dict[str, Any], ruc: str) -> None:
    with pytest.raises(ValidationError):
        FacturaValidada.model_validate({**datos_validos, "ruc_emisor": ruc})


@pytest.mark.parametrize("numero", ["001-123", "F01-123", "F001-", "F001-123456789", "X001-1"])
def test_factura_validada_rechaza_numero_invalido(
    datos_validos: dict[str, Any], numero: str
) -> None:
    with pytest.raises(ValidationError):
        FacturaValidada.model_validate({**datos_validos, "numero_factura": numero})


@pytest.mark.parametrize("campo", sorted(CAMPOS_OBLIGATORIOS))
def test_factura_validada_no_acepta_obligatorios_nulos(
    datos_validos: dict[str, Any], campo: str
) -> None:
    with pytest.raises(ValidationError):
        FacturaValidada.model_validate({**datos_validos, campo: None})


def test_factura_validada_exige_total_positivo(datos_validos: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FacturaValidada.model_validate({**datos_validos, "total": 0})


def test_factura_validada_acepta_datos_correctos(datos_validos: dict[str, Any]) -> None:
    validada = FacturaValidada.model_validate(datos_validos)
    assert validada.tipo_documento is TipoDocumento.FACTURA
    assert validada.model_dump(mode="json")["total"] == "118.00"
