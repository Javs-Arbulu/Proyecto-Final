"""Parseo, validación y reintentos de formato: el corazón del proyecto.

Orden de cada intento (ver :func:`parsear_respuesta` y :func:`extraer_con_reintentos`):

1. Llamar al cliente.
2. Revisar ``finish_reason`` ANTES de parsear: una respuesta cortada por
   ``MAX_TOKENS`` puede verse como un JSON válido pero incompleto.
3. ``json.loads``; si falla, fallback secundario que busca el primer objeto JSON
   balanceado del texto (nunca es la estrategia principal).
4. ``FacturaExtraida.model_validate``: la validación Pydantic se ejecuta SIEMPRE,
   aunque el proveedor prometa cumplir el esquema el 100 % de las veces.

Solo se reintenta lo ESTRUCTURAL (formato y tipos), porque es corregible. Los
campos faltantes no se reintentan: si el dato no está en la fuente, reintentar
solo gasta tokens.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from extractor.config import TOPE_MAX_TOKENS, Settings
from extractor.extraction import SYSTEM_PROMPT, construir_mensaje_usuario, esquema_para_llm
from extractor.llm_client import FINALIZACIONES_NORMALES, ClienteLLM, ErrorAPI, RespuestaLLM
from extractor.schema import (
    CAMPOS_CRITICOS,
    CAMPOS_OBLIGATORIOS,
    PATRON_NUMERO_FACTURA,
    PATRON_RUC,
    Advertencia,
    CodigoAdvertencia,
    CodigoMotivo,
    CondicionPago,
    EstadoExtraccion,
    FacturaExtraida,
    FacturaValidada,
    Intento,
    TipoDocumento,
    TipoError,
)

logger = logging.getLogger(__name__)

MAX_ERRORES_FEEDBACK = 8
MAX_CHARS_VALOR = 60
MAX_CHARS_DETALLE = 300

MOTIVO_POR_ERROR: dict[TipoError, CodigoMotivo] = {
    TipoError.JSON_INVALIDO: CodigoMotivo.JSON_INVALIDO_REINTENTOS_AGOTADOS,
    TipoError.ESQUEMA_INVALIDO: CodigoMotivo.ESQUEMA_INVALIDO_REINTENTOS_AGOTADOS,
    TipoError.RESPUESTA_TRUNCADA: CodigoMotivo.RESPUESTA_TRUNCADA,
    TipoError.FINALIZACION_INESPERADA: CodigoMotivo.ERROR_API,
}

NOMBRE_ERROR: dict[TipoError, str] = {
    TipoError.JSON_INVALIDO: "JSON inválido",
    TipoError.ESQUEMA_INVALIDO: "esquema inválido",
    TipoError.RESPUESTA_TRUNCADA: "respuesta truncada",
    TipoError.FINALIZACION_INESPERADA: "finalización inesperada",
}

# ---------------------------------------------------------------------------
# Parseo de una respuesta
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoParseo:
    """Resultado de verificar una respuesta del modelo."""

    factura: FacturaExtraida | None
    tipo_error: TipoError | None = None
    errores: list[str] = field(default_factory=list)
    uso_fallback: bool = False

    @property
    def ok(self) -> bool:
        """True si la respuesta produjo un ``FacturaExtraida`` válido."""
        return self.factura is not None


def truncar(texto: str, maximo: int) -> str:
    """Recorta un texto largo agregando una elipsis."""
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _fin_de_objeto(texto: str, inicio: int) -> int | None:
    """Posición de la llave que cierra el objeto que abre en ``inicio`` (respeta strings)."""
    profundidad, en_string, escapado = 0, False, False
    for posicion in range(inicio, len(texto)):
        caracter = texto[posicion]
        if en_string:
            if escapado:
                escapado = False
            elif caracter == "\\":
                escapado = True
            elif caracter == '"':
                en_string = False
        elif caracter == '"':
            en_string = True
        elif caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad -= 1
            if profundidad == 0:
                return posicion
    return None


def extraer_primer_objeto_json(texto: str) -> str | None:
    """Primer objeto JSON de nivel superior, balanceado y parseable, dentro del texto.

    Si una llave ``{`` nunca se cierra, todo lo que sigue está anidado en ella y no
    hay objeto de nivel superior: así un JSON cortado a la mitad no se "rescata"
    devolviendo uno de sus ítems internos.
    """
    inicio = texto.find("{")
    while inicio != -1:
        fin = _fin_de_objeto(texto, inicio)
        if fin is None:
            return None
        candidato = texto[inicio : fin + 1]
        try:
            if isinstance(json.loads(candidato), dict):
                return candidato
        except json.JSONDecodeError:
            pass
        inicio = texto.find("{", fin + 1)
    return None


def formatear_errores_validacion(error: ValidationError) -> list[str]:
    """Errores de Pydantic en formato compacto: campo, problema y valor recibido."""
    lineas = []
    for detalle in error.errors(include_url=False)[:MAX_ERRORES_FEEDBACK]:
        campo = ".".join(str(parte) for parte in detalle["loc"]) or "(objeto raíz)"
        if detalle["type"] == "missing":
            lineas.append(f"{campo}: falta la clave (inclúyela; usa null si no hay dato)")
            continue
        recibido = truncar(repr(detalle.get("input")), MAX_CHARS_VALOR)
        lineas.append(f"{campo}: {detalle['msg']} (recibido: {recibido})")
    restantes = error.error_count() - MAX_ERRORES_FEEDBACK
    if restantes > 0:
        lineas.append(f"... y {restantes} errores más")
    return lineas


def _error(tipo: TipoError, mensaje: str, uso_fallback: bool = False) -> ResultadoParseo:
    return ResultadoParseo(None, tipo, [mensaje], uso_fallback)


def _revisar_finalizacion(finish_reason: str | None) -> ResultadoParseo | None:
    """Paso 2: ``finish_reason`` se revisa ANTES de parsear."""
    if finish_reason == "MAX_TOKENS":
        return _error(
            TipoError.RESPUESTA_TRUNCADA,
            "la respuesta se cortó por MAX_TOKENS; el objeto puede estar incompleto. "
            "Genera el objeto completo y usa observaciones breves.",
        )
    if finish_reason not in FINALIZACIONES_NORMALES:
        return _error(
            TipoError.FINALIZACION_INESPERADA,
            f"la generación terminó con finish_reason={finish_reason}",
        )
    return None


def _cargar_json(texto: str) -> tuple[object | None, ResultadoParseo | None, bool]:
    """Paso 3: ``json.loads`` con fallback al primer objeto balanceado."""
    try:
        return json.loads(texto), None, False
    except json.JSONDecodeError as error:
        candidato = extraer_primer_objeto_json(texto)
        if candidato is None:
            if "{" not in texto:
                muestra = truncar(texto.strip(), MAX_CHARS_VALOR)
                detalle = f"la respuesta no contiene ningún objeto JSON (texto: {muestra!r})"
            else:
                detalle = (
                    f"JSON inválido: {error.msg} (línea {error.lineno}, columna {error.colno})"
                )
            return None, _error(TipoError.JSON_INVALIDO, detalle, uso_fallback=True), True
        return json.loads(candidato), None, True


def parsear_respuesta(respuesta: RespuestaLLM) -> ResultadoParseo:
    """Verifica una respuesta: finish_reason → JSON (con fallback) → esquema."""
    error_finalizacion = _revisar_finalizacion(respuesta.finish_reason)
    if error_finalizacion is not None:
        return error_finalizacion
    datos, error_json, uso_fallback = _cargar_json(respuesta.texto_crudo)
    if error_json is not None:
        return error_json
    if not isinstance(datos, dict):
        return _error(
            TipoError.ESQUEMA_INVALIDO,
            f"se esperaba un objeto JSON y se recibió {type(datos).__name__}",
            uso_fallback,
        )
    try:
        factura = FacturaExtraida.model_validate(datos)
    except ValidationError as error:
        return ResultadoParseo(
            None, TipoError.ESQUEMA_INVALIDO, formatear_errores_validacion(error), uso_fallback
        )
    return ResultadoParseo(factura, uso_fallback=uso_fallback)


# ---------------------------------------------------------------------------
# Reintentos de formato
# ---------------------------------------------------------------------------

Notificador = Callable[[Intento, int, bool], None]


@dataclass
class ResultadoExtraccion:
    """Resultado de la etapa de extracción (antes de las reglas de negocio)."""

    factura: FacturaExtraida | None
    intentos: list[Intento]
    motivo_fallo: CodigoMotivo | None = None
    detalle: str = ""
    llamadas_api: int = 0


def describir_intento(intento: Intento, max_intentos: int, se_reintentara: bool) -> str:
    """Línea legible de un intento, p. ej. ``intento 1/3 ✗ JSON inválido: ... → reintentando``."""
    cabecera = f"intento {intento.numero}/{max_intentos}"
    if intento.ok:
        extra = " (vía fallback)" if intento.uso_fallback else ""
        return f"{cabecera} ✓ JSON válido según el esquema{extra}"
    nombre = NOMBRE_ERROR[intento.tipo_error] if intento.tipo_error else "error"
    detalle = (intento.detalle_error or "").removeprefix(f"{nombre}: ")
    sufijo = " → reintentando con feedback" if se_reintentara else " → sin intentos restantes"
    return f"{cabecera} ✗ {nombre}: {truncar(detalle, 120)}{sufijo}"


def _registrar_intento(
    numero: int, respuesta: RespuestaLLM, parseo: ResultadoParseo, max_tokens: int
) -> Intento:
    return Intento(
        numero=numero,
        ok=parseo.ok,
        tipo_error=parseo.tipo_error,
        detalle_error=truncar("; ".join(parseo.errores), MAX_CHARS_DETALLE) or None,
        tokens_entrada=respuesta.tokens_entrada,
        tokens_salida=respuesta.tokens_salida,
        latencia_ms=respuesta.latencia_ms,
        uso_fallback=parseo.uso_fallback,
        finish_reason=respuesta.finish_reason,
        max_tokens=max_tokens,
    )


def extraer_con_reintentos(
    texto: str,
    nombre: str,
    cliente: ClienteLLM,
    settings: Settings,
    notificar: Notificador | None = None,
) -> ResultadoExtraccion:
    """Extrae con como máximo ``settings.max_intentos`` llamadas de formato.

    Es un bucle ``for`` acotado (nunca ``while True``). ``ErrorAutenticacion`` se
    propaga al pipeline (circuit breaker); ``ErrorAPI`` termina el documento con
    ERROR_API sin consumir más intentos de formato.
    """
    logger.debug("texto del documento %s:\n%s", nombre, texto)
    esquema = esquema_para_llm()
    intentos: list[Intento] = []
    errores_previos: list[str] | None = None
    max_tokens = settings.max_tokens
    ultimo_error = TipoError.JSON_INVALIDO
    for numero in range(1, settings.max_intentos + 1):
        mensaje = construir_mensaje_usuario(texto, nombre, errores_previos)
        try:
            respuesta = cliente.extraer(SYSTEM_PROMPT, mensaje, esquema, max_tokens)
        except ErrorAPI as error:
            # La llamada fallida también cuenta para el presupuesto de la API.
            return ResultadoExtraccion(
                None, intentos, CodigoMotivo.ERROR_API, str(error), llamadas_api=numero
            )
        parseo = parsear_respuesta(respuesta)
        intento = _registrar_intento(numero, respuesta, parseo, max_tokens)
        intentos.append(intento)
        logger.info("%s: %s", nombre, describir_intento(intento, settings.max_intentos, False))
        if notificar is not None:
            notificar(
                intento, settings.max_intentos, not parseo.ok and numero < settings.max_intentos
            )
        if parseo.factura is not None:
            return ResultadoExtraccion(parseo.factura, intentos, llamadas_api=numero)
        ultimo_error = parseo.tipo_error or TipoError.JSON_INVALIDO
        errores_previos = parseo.errores
        if ultimo_error is TipoError.RESPUESTA_TRUNCADA:
            max_tokens = min(max_tokens * 2, TOPE_MAX_TOKENS)
    detalle = (
        f"{settings.max_intentos} intento(s) agotado(s); último error "
        f"{NOMBRE_ERROR[ultimo_error]}: {intentos[-1].detalle_error}"
    )
    return ResultadoExtraccion(
        None, intentos, MOTIVO_POR_ERROR[ultimo_error], detalle, llamadas_api=len(intentos)
    )


# ---------------------------------------------------------------------------
# Reglas de negocio y clasificación (esta etapa NO reintenta)
# ---------------------------------------------------------------------------

TASA_IGV = 0.18
TOLERANCIA_RELATIVA = 0.005
UMBRAL_OBLIGATORIOS_FALTANTES = 0.5
ANIO_MINIMO = 2000
_RUC = re.compile(PATRON_RUC)
_NUMERO = re.compile(PATRON_NUMERO_FACTURA)
_SEPARADORES = re.compile(r"[\s\-–—.]")


@dataclass
class Evaluacion:
    """Resultado de aplicar reglas de negocio y clasificación a una extracción válida."""

    estado: EstadoExtraccion
    motivos: list[CodigoMotivo]
    detalle: str
    campos_faltantes: list[str] = field(default_factory=list)
    advertencias: list[Advertencia] = field(default_factory=list)
    datos: dict[str, Any] | None = None


def _presente(valor: object) -> bool:
    """Un campo está presente si no es None ni un texto vacío."""
    return valor is not None and not (isinstance(valor, str) and not valor.strip())


def campos_faltantes(factura: FacturaExtraida, campos: frozenset[str]) -> list[str]:
    """Campos de ``campos`` que no están presentes en la factura (orden alfabético)."""
    return sorted(campo for campo in campos if not _presente(getattr(factura, campo)))


def _tolerancia_relativa(subtotal: float, tolerancia: float) -> float:
    return max(tolerancia, TOLERANCIA_RELATIVA * abs(subtotal))


def _advertencia(codigo: CodigoAdvertencia, mensaje: str) -> list[Advertencia]:
    return [Advertencia(codigo=codigo, mensaje=mensaje)]


def regla_ruc_formato(factura: FacturaExtraida) -> list[Advertencia]:
    """RUC_FORMATO: algún RUC no cumple ``^(10|15|17|20)\\d{9}$``."""
    malos = [
        f"{campo}={valor!r}"
        for campo in ("ruc_emisor", "ruc_cliente")
        if _presente(valor := getattr(factura, campo)) and not _RUC.fullmatch(valor.strip())
    ]
    if not malos:
        return []
    return _advertencia(
        CodigoAdvertencia.RUC_FORMATO, f"RUC con formato inválido: {', '.join(malos)}"
    )


def regla_numero_formato(factura: FacturaExtraida) -> list[Advertencia]:
    """NUMERO_FORMATO: el número no cumple ``^[FBE][A-Z0-9]{3}-\\d{1,8}$``."""
    numero = factura.numero_factura
    if not _presente(numero) or _NUMERO.fullmatch(numero.strip()):
        return []
    return _advertencia(
        CodigoAdvertencia.NUMERO_FORMATO, f"número con formato inválido: {numero!r}"
    )


def regla_fechas_incoherentes(factura: FacturaExtraida) -> list[Advertencia]:
    """FECHAS_INCOHERENTES: el vencimiento es anterior a la emisión."""
    emision, vencimiento = factura.fecha_emision, factura.fecha_vencimiento
    if emision is None or vencimiento is None or vencimiento >= emision:
        return []
    return _advertencia(
        CodigoAdvertencia.FECHAS_INCOHERENTES,
        f"vencimiento {vencimiento} anterior a la emisión {emision}",
    )


def regla_fecha_fuera_de_rango(factura: FacturaExtraida, hoy: date) -> list[Advertencia]:
    """FECHA_FUERA_DE_RANGO: SOLO la emisión, si es futura o anterior a 2000."""
    emision = factura.fecha_emision
    if emision is None or ANIO_MINIMO <= emision.year and emision <= hoy:
        return []
    return _advertencia(
        CodigoAdvertencia.FECHA_FUERA_DE_RANGO,
        f"fecha de emisión {emision} futura o anterior a {ANIO_MINIMO}",
    )


def regla_totales(factura: FacturaExtraida, tolerancia: float) -> list[Advertencia]:
    """TOTALES_INCONSISTENTES: ``abs(subtotal + igv - total) > tolerancia``."""
    subtotal, igv, total = factura.subtotal, factura.igv, factura.total
    if subtotal is None or igv is None or total is None:
        return []
    diferencia = abs(subtotal + igv - total)
    if diferencia <= tolerancia:
        return []
    return _advertencia(
        CodigoAdvertencia.TOTALES_INCONSISTENTES,
        f"subtotal {subtotal:.2f} + IGV {igv:.2f} = {subtotal + igv:.2f} ≠ total {total:.2f}",
    )


def regla_igv(factura: FacturaExtraida, tolerancia: float) -> list[Advertencia]:
    """IGV_NO_18: ``abs(igv - 0.18 * subtotal) > max(tolerancia, 0.005 * subtotal)``."""
    subtotal, igv = factura.subtotal, factura.igv
    if subtotal is None or igv is None:
        return []
    esperado = TASA_IGV * subtotal
    if abs(igv - esperado) <= _tolerancia_relativa(subtotal, tolerancia):
        return []
    return _advertencia(
        CodigoAdvertencia.IGV_NO_18,
        f"IGV {igv:.2f} distinto del 18 % del subtotal ({esperado:.2f}); ¿operación exonerada?",
    )


def _importe_item(
    cantidad: float | None, precio: float | None, importe: float | None
) -> float | None:
    """Importe de la línea; si falta, ``cantidad * precio_unitario``; si no se puede, None."""
    if importe is not None:
        return importe
    if cantidad is not None and precio is not None:
        return cantidad * precio
    return None


def regla_items(factura: FacturaExtraida, tolerancia: float) -> list[Advertencia]:
    """ITEMS_NO_CUADRAN: la suma de ítems menos el descuento no cuadra con el subtotal.

    Fórmula: ``abs(suma_importes - (descuento or 0) - subtotal) > max(tol, 0.005 * subtotal)``.
    Se omite si no hay ítems o si algún ítem no permite calcular su importe.
    """
    if not factura.items or factura.subtotal is None:
        return []
    importes = [_importe_item(i.cantidad, i.precio_unitario, i.importe) for i in factura.items]
    if any(importe is None for importe in importes):
        return []
    suma = sum(importe for importe in importes if importe is not None)
    descuento = factura.descuento or 0
    diferencia = abs(suma - descuento - factura.subtotal)
    if diferencia <= _tolerancia_relativa(factura.subtotal, tolerancia):
        return []
    return _advertencia(
        CodigoAdvertencia.ITEMS_NO_CUADRAN,
        f"Σ ítems {suma:.2f} - descuento {descuento:.2f} ≠ subtotal {factura.subtotal:.2f}",
    )


def regla_credito_sin_vencimiento(factura: FacturaExtraida) -> list[Advertencia]:
    """CREDITO_SIN_VENCIMIENTO: venta a crédito sin fecha de vencimiento."""
    if factura.condicion_pago is not CondicionPago.CREDITO or factura.fecha_vencimiento:
        return []
    return _advertencia(
        CodigoAdvertencia.CREDITO_SIN_VENCIMIENTO, "condición CREDITO sin fecha de vencimiento"
    )


def regla_monto_no_positivo(factura: FacturaExtraida) -> list[Advertencia]:
    """MONTO_NO_POSITIVO: total o subtotal ≤ 0, o ítem con cantidad ≤ 0 o precio < 0."""
    problemas = [
        f"{campo}={valor}"
        for campo in ("total", "subtotal")
        if (valor := getattr(factura, campo)) is not None and valor <= 0
    ]
    for indice, item in enumerate(factura.items or [], start=1):
        if item.cantidad is not None and item.cantidad <= 0:
            problemas.append(f"ítem {indice} cantidad={item.cantidad}")
        if item.precio_unitario is not None and item.precio_unitario < 0:
            problemas.append(f"ítem {indice} precio_unitario={item.precio_unitario}")
    if not problemas:
        return []
    return _advertencia(CodigoAdvertencia.MONTO_NO_POSITIVO, "; ".join(problemas))


def _compactar(texto: str) -> str:
    return _SEPARADORES.sub("", texto).upper()


def numero_anclado(numero: str, texto: str) -> bool:
    """True si el número aparece en el texto.

    Normaliza espacios, guiones y ceros a la izquierda del correlativo.
    """
    partes = re.fullmatch(r"\s*([A-Z0-9]{4})\s*[-–—]?\s*0*(\d+)\s*", numero.upper())
    if partes is None:
        return _compactar(numero) in _compactar(texto)
    serie, correlativo = partes.groups()
    patron = rf"{re.escape(serie)}[\s\-–—]*0*{correlativo}(?!\d)"
    return re.search(patron, texto.upper()) is not None


def regla_anclaje(factura: FacturaExtraida, texto_original: str) -> list[Advertencia]:
    """POSIBLE_ALUCINACION: número o algún RUC no aparece literalmente en el texto."""
    no_anclados = []
    if _presente(factura.numero_factura) and not numero_anclado(
        factura.numero_factura, texto_original
    ):
        no_anclados.append(f"numero_factura={factura.numero_factura!r}")
    for campo in ("ruc_emisor", "ruc_cliente"):
        valor = getattr(factura, campo)
        if _presente(valor) and _compactar(valor) not in _compactar(texto_original):
            no_anclados.append(f"{campo}={valor!r}")
    if not no_anclados:
        return []
    return _advertencia(
        CodigoAdvertencia.POSIBLE_ALUCINACION,
        f"valores que no aparecen en el documento: {', '.join(no_anclados)}",
    )


def aplicar_reglas(
    factura: FacturaExtraida, texto_original: str, tolerancia: float, hoy: date
) -> list[Advertencia]:
    """Aplica todas las reglas de negocio; cada una solo si sus campos están presentes."""
    return [
        *regla_ruc_formato(factura),
        *regla_numero_formato(factura),
        *regla_fechas_incoherentes(factura),
        *regla_fecha_fuera_de_rango(factura, hoy),
        *regla_totales(factura, tolerancia),
        *regla_igv(factura, tolerancia),
        *regla_items(factura, tolerancia),
        *regla_credito_sin_vencimiento(factura),
        *regla_monto_no_positivo(factura),
        *regla_anclaje(factura, texto_original),
    ]


def evaluar(
    extraida: FacturaExtraida,
    texto_original: str,
    settings: Settings,
    hoy: date | None = None,
) -> Evaluacion:
    """Clasifica una extracción válida en EXITOSO, PARCIAL o FALLIDO (sin reintentar).

    Precedencia: fuera de dominio → extracción insuficiente → reglas de negocio →
    contrato estricto (:class:`FacturaValidada`).
    """
    datos = extraida.model_dump(mode="json")
    if extraida.tipo_documento is not TipoDocumento.FACTURA:
        tipo = extraida.tipo_documento.value if extraida.tipo_documento else "null"
        detalle = f"el documento no es una factura (tipo_documento detectado: {tipo})"
        return Evaluacion(
            EstadoExtraccion.FALLIDO, [CodigoMotivo.FUERA_DE_DOMINIO], detalle, datos=datos
        )
    faltantes = campos_faltantes(extraida, CAMPOS_OBLIGATORIOS)
    criticos = [campo for campo in faltantes if campo in CAMPOS_CRITICOS]
    if criticos or len(faltantes) > UMBRAL_OBLIGATORIOS_FALTANTES * len(CAMPOS_OBLIGATORIOS):
        detalle = (
            f"faltan campos críticos {criticos}"
            if criticos
            else f"faltan {len(faltantes)} de {len(CAMPOS_OBLIGATORIOS)} campos obligatorios"
        )
        return Evaluacion(
            EstadoExtraccion.FALLIDO,
            [CodigoMotivo.EXTRACCION_INSUFICIENTE],
            detalle,
            faltantes,
            datos=datos,
        )
    advertencias = aplicar_reglas(
        extraida, texto_original, settings.tolerancia_montos, hoy or date.today()
    )
    if faltantes or advertencias:
        motivos = [CodigoMotivo.CAMPOS_OBLIGATORIOS_FALTANTES] if faltantes else []
        motivos += [CodigoMotivo.REGLA_NEGOCIO] if advertencias else []
        partes = [f"faltan {faltantes}"] if faltantes else []
        partes += [f"advertencias {[a.codigo.value for a in advertencias]}"] if advertencias else []
        detalle = "requiere revisión humana: " + "; ".join(partes)
        return Evaluacion(
            EstadoExtraccion.PARCIAL, motivos, detalle, faltantes, advertencias, datos
        )
    try:
        validada = FacturaValidada.model_validate(extraida.model_dump())
    except ValidationError as error:
        detalle = "no cumple el contrato estricto: " + "; ".join(
            formatear_errores_validacion(error)
        )
        return Evaluacion(
            EstadoExtraccion.PARCIAL, [CodigoMotivo.REGLA_NEGOCIO], detalle, datos=datos
        )
    return Evaluacion(
        EstadoExtraccion.EXITOSO,
        [CodigoMotivo.OK],
        "factura válida y lista para registrar",
        datos=validada.model_dump(mode="json"),
    )
