"""Modelos Pydantic, enums y constantes de campos.

Este módulo NO importa nada del proyecto (verificado por test AST).

Esquema doble:

* :class:`FacturaExtraida` es el contrato con el LLM y es PERMISIVO. Valida solo
  estructura y tipos (tipos de dato, enums y fechas ISO), sin restricciones de
  valor. Todos los campos son obligatorios-nullable: la clave siempre está y
  ``None`` significa "no aparece en el documento". Todo lo que falla aquí es
  corregible con un reintento.
* :class:`FacturaValidada` es el contrato de negocio y es ESTRICTO: representa la
  fila lista para la base de datos (obligatorios no nulos, montos ``Decimal``,
  RUC y número con formato SUNAT).

Las restricciones de valor (positividad, formatos y coherencia entre campos) se
evalúan como reglas de negocio en ``validation.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

PATRON_RUC = r"^(10|15|17|20)\d{9}$"
PATRON_NUMERO_FACTURA = r"^[FBE][A-Z0-9]{3}-\d{1,8}$"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TipoDocumento(StrEnum):
    """Tipo de comprobante detectado."""

    FACTURA = "FACTURA"
    BOLETA = "BOLETA"
    NOTA_CREDITO = "NOTA_CREDITO"
    OTRO = "OTRO"


class Moneda(StrEnum):
    """Moneda del comprobante (ISO 4217)."""

    PEN = "PEN"
    USD = "USD"


class CondicionPago(StrEnum):
    """Forma de pago declarada en el comprobante."""

    CONTADO = "CONTADO"
    CREDITO = "CREDITO"


class EstadoExtraccion(StrEnum):
    """Resultado final de un documento."""

    EXITOSO = "EXITOSO"
    PARCIAL = "PARCIAL"
    FALLIDO = "FALLIDO"


class CodigoMotivo(StrEnum):
    """Motivo del estado de un documento."""

    OK = "OK"
    CAMPOS_OBLIGATORIOS_FALTANTES = "CAMPOS_OBLIGATORIOS_FALTANTES"
    REGLA_NEGOCIO = "REGLA_NEGOCIO"
    JSON_INVALIDO_REINTENTOS_AGOTADOS = "JSON_INVALIDO_REINTENTOS_AGOTADOS"
    ESQUEMA_INVALIDO_REINTENTOS_AGOTADOS = "ESQUEMA_INVALIDO_REINTENTOS_AGOTADOS"
    RESPUESTA_TRUNCADA = "RESPUESTA_TRUNCADA"
    FUERA_DE_DOMINIO = "FUERA_DE_DOMINIO"
    EXTRACCION_INSUFICIENTE = "EXTRACCION_INSUFICIENTE"
    DOCUMENTO_VACIO = "DOCUMENTO_VACIO"
    DOCUMENTO_DEMASIADO_LARGO = "DOCUMENTO_DEMASIADO_LARGO"
    ERROR_API = "ERROR_API"
    ERROR_INESPERADO = "ERROR_INESPERADO"


class CodigoAdvertencia(StrEnum):
    """Incumplimientos de reglas de negocio (producen PARCIAL, no FALLIDO)."""

    RUC_FORMATO = "RUC_FORMATO"
    NUMERO_FORMATO = "NUMERO_FORMATO"
    FECHAS_INCOHERENTES = "FECHAS_INCOHERENTES"
    FECHA_FUERA_DE_RANGO = "FECHA_FUERA_DE_RANGO"
    TOTALES_INCONSISTENTES = "TOTALES_INCONSISTENTES"
    IGV_NO_18 = "IGV_NO_18"
    ITEMS_NO_CUADRAN = "ITEMS_NO_CUADRAN"
    CREDITO_SIN_VENCIMIENTO = "CREDITO_SIN_VENCIMIENTO"
    MONTO_NO_POSITIVO = "MONTO_NO_POSITIVO"
    POSIBLE_ALUCINACION = "POSIBLE_ALUCINACION"


class TipoError(StrEnum):
    """Errores estructurales de un intento (todos son reintentables)."""

    JSON_INVALIDO = "JSON_INVALIDO"
    ESQUEMA_INVALIDO = "ESQUEMA_INVALIDO"
    RESPUESTA_TRUNCADA = "RESPUESTA_TRUNCADA"
    FINALIZACION_INESPERADA = "FINALIZACION_INESPERADA"


# ---------------------------------------------------------------------------
# Contrato con el LLM (PERMISIVO)
# ---------------------------------------------------------------------------


class ItemFactura(BaseModel):
    """Línea de detalle de la factura, tal como aparece en el documento."""

    model_config = ConfigDict(extra="forbid")

    descripcion: str = Field(
        description="Descripción del bien o servicio tal como figura en la línea de detalle.",
    )
    cantidad: float | None = Field(
        description="Cantidad de unidades de la línea. null si no figura.",
    )
    precio_unitario: float | None = Field(
        description=(
            "Valor unitario SIN IGV. Si el documento muestra a la vez 'valor unitario' "
            "(sin IGV) y 'precio unitario' (con IGV), usa el valor unitario sin IGV. "
            "Número con punto decimal, sin símbolo de moneda. null si no figura."
        ),
    )
    importe: float | None = Field(
        description=(
            "Valor de venta de la línea sin IGV (normalmente cantidad x valor unitario), "
            "tal como figura en el documento. null si no figura."
        ),
    )


class FacturaExtraida(BaseModel):
    """Datos de una factura SUNAT extraídos por el LLM.

    Contrato permisivo: todos los campos son obligatorios-nullable. La clave debe
    estar siempre (Cohere exige al menos un ``required`` por objeto y así el
    esquema es explícito), pero el valor puede ser ``None``, que significa "no
    aparece o es ilegible en el documento". Así se evita forzar al modelo a
    inventar valores.
    """

    model_config = ConfigDict(
        extra="forbid",
        title="FacturaExtraida",
        json_schema_extra={
            "description": (
                "Datos de una factura electrónica peruana (SUNAT). Usa null para "
                "cualquier dato que no aparezca o sea ilegible en el documento."
            )
        },
    )

    tipo_documento: TipoDocumento | None = Field(
        description=(
            "Tipo de comprobante: FACTURA (factura electrónica), BOLETA (boleta de venta), "
            "NOTA_CREDITO, u OTRO para cualquier documento que no sea un comprobante de pago "
            "(cotización, proforma, orden de compra, guía, etc.)."
        ),
    )
    numero_factura: str | None = Field(
        description=(
            "Número del comprobante en formato serie-correlativo, p. ej. F001-00012345: "
            "serie de 4 caracteres (empieza con F, B o E) y correlativo numérico. "
            "Cópialo tal como figura. null si no aparece."
        ),
    )
    fecha_emision: date | None = Field(
        description=(
            "Fecha de emisión en formato ISO AAAA-MM-DD. El documento usa la convención "
            "peruana DD/MM/AAAA: 03/04/2026 es el 3 de abril de 2026. null si no aparece."
        ),
    )
    fecha_vencimiento: date | None = Field(
        description=(
            "Fecha de vencimiento del pago en formato ISO AAAA-MM-DD (convención DD/MM/AAAA "
            "en el documento). null si no aparece."
        ),
    )
    ruc_emisor: str | None = Field(
        description=(
            "RUC del emisor (proveedor): 11 dígitos, solo números, sin espacios ni guiones. "
            "null si no aparece o es ilegible; nunca lo completes ni lo adivines."
        ),
    )
    razon_social_emisor: str | None = Field(
        description="Razón social del emisor (proveedor que emite la factura).",
    )
    ruc_cliente: str | None = Field(
        description=(
            "RUC del cliente (adquirente): 11 dígitos, solo números. null si no aparece o es "
            "ilegible; nunca lo completes ni lo adivines."
        ),
    )
    razon_social_cliente: str | None = Field(
        description="Razón social del cliente (adquirente o 'Señor(es)').",
    )
    moneda: Moneda | None = Field(
        description="Moneda: PEN para S/, soles o PEN; USD para US$, $, dólares o USD.",
    )
    condicion_pago: CondicionPago | None = Field(
        description=(
            "Forma de pago: CONTADO o CREDITO (incluye 'crédito a N días' o pago en cuotas). "
            "null si no aparece."
        ),
    )
    items: list[ItemFactura] | None = Field(
        description="Líneas de detalle de la factura, en el orden del documento.",
    )
    descuento: float | None = Field(
        description=(
            "Descuento GLOBAL en monto (no porcentaje), si el documento lo indica. "
            "No lo repartas entre los ítems. null si no hay descuento global."
        ),
    )
    subtotal: float | None = Field(
        description=(
            "Base imponible sin IGV ('Op. Gravada', 'Valor de venta' o 'Subtotal'), "
            "después de descuentos. Número sin símbolo ni separador de miles."
        ),
    )
    igv: float | None = Field(
        description="Monto del IGV (impuesto general a las ventas) tal como figura.",
    )
    total: float | None = Field(
        description=(
            "Importe total del comprobante tal como figura, aunque no cuadre con el subtotal "
            "y el IGV. Número con punto decimal, sin símbolo ni separador de miles."
        ),
    )
    observaciones: str | None = Field(
        description=(
            "Notas breves sobre ambigüedades, datos ilegibles o texto sospechoso ignorado. "
            "null si no hay nada que observar."
        ),
    )


# ---------------------------------------------------------------------------
# Constantes de clasificación de campos
# ---------------------------------------------------------------------------

CAMPOS_CRITICOS: frozenset[str] = frozenset({"numero_factura", "total"})
CAMPOS_OBLIGATORIOS: frozenset[str] = frozenset(
    {
        "tipo_documento",
        "numero_factura",
        "fecha_emision",
        "ruc_emisor",
        "razon_social_emisor",
        "ruc_cliente",
        "moneda",
        "total",
    }
)
CAMPOS_OPCIONALES: frozenset[str] = frozenset(FacturaExtraida.model_fields) - CAMPOS_OBLIGATORIOS
CAMPOS_CLAVE: tuple[str, ...] = ("numero_factura", "fecha_emision", "ruc_emisor", "moneda", "total")


# ---------------------------------------------------------------------------
# Contrato de negocio (ESTRICTO)
# ---------------------------------------------------------------------------

CENTIMOS = Decimal("0.01")


def _a_decimal(valor: Any) -> Any:
    """Convierte floats a Decimal vía ``str`` para no arrastrar error binario."""
    if isinstance(valor, float):
        return Decimal(str(valor))
    return valor


def _cuantizar(valor: Decimal) -> Decimal:
    """Redondea a 2 decimales (céntimos) con redondeo comercial."""
    return valor.quantize(CENTIMOS, rounding=ROUND_HALF_UP)


Monto = Annotated[Decimal, BeforeValidator(_a_decimal), AfterValidator(_cuantizar)]
Cantidad = Annotated[Decimal, BeforeValidator(_a_decimal)]
Ruc = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PATRON_RUC)]
NumeroFactura = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=PATRON_NUMERO_FACTURA)
]
TextoNoVacio = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ItemValidado(BaseModel):
    """Línea de detalle con montos en Decimal."""

    model_config = ConfigDict(extra="forbid")

    descripcion: TextoNoVacio
    cantidad: Cantidad | None = None
    precio_unitario: Monto | None = None
    importe: Monto | None = None


class FacturaValidada(BaseModel):
    """Fila lista para la base de datos: obligatorios no nulos y formatos SUNAT."""

    model_config = ConfigDict(extra="forbid")

    tipo_documento: TipoDocumento
    numero_factura: NumeroFactura
    fecha_emision: date
    fecha_vencimiento: date | None = None
    ruc_emisor: Ruc
    razon_social_emisor: TextoNoVacio
    ruc_cliente: Ruc
    razon_social_cliente: str | None = None
    moneda: Moneda
    condicion_pago: CondicionPago | None = None
    items: list[ItemValidado] = Field(default_factory=list)
    descuento: Monto | None = None
    subtotal: Monto | None = None
    igv: Monto | None = None
    total: Annotated[Monto, Field(gt=0)]
    observaciones: str | None = None


# ---------------------------------------------------------------------------
# Modelos de resultado
# ---------------------------------------------------------------------------


class Advertencia(BaseModel):
    """Incumplimiento de una regla de negocio."""

    codigo: CodigoAdvertencia
    mensaje: str


class Intento(BaseModel):
    """Registro de una llamada de formato al modelo."""

    numero: int
    ok: bool
    tipo_error: TipoError | None = None
    detalle_error: str | None = None
    tokens_entrada: int = 0
    tokens_salida: int = 0
    latencia_ms: int = 0
    uso_fallback: bool = False
    finish_reason: str | None = None
    max_tokens: int | None = None


class ResultadoDocumento(BaseModel):
    """Resultado completo de un documento, tal como se reporta."""

    archivo: str
    estado: EstadoExtraccion
    motivos: list[CodigoMotivo]
    detalle: str
    campos_faltantes: list[str] = Field(default_factory=list)
    advertencias: list[Advertencia] = Field(default_factory=list)
    datos: dict[str, Any] | None = None
    intentos: list[Intento] = Field(default_factory=list)
    tokens_entrada_total: int = 0
    tokens_salida_total: int = 0
    latencia_ms_total: int = 0
    simulacion: str | None = None
