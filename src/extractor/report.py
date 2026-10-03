"""Reportes del lote: JSON completo, CSV, Markdown y vista en consola.

Las métricas responden las preguntas del enunciado: tasa de éxito, casos
parciales y fallidos con su motivo, y "qué patrón comparten los fallos"
(agrupación por código). Si existe ``data/esperado.json``, además se compara
el estado obtenido y los campos clave contra lo esperado.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from extractor.schema import CAMPOS_CLAVE, EstadoExtraccion, ResultadoDocumento

CARPETA_SIMULACIONES = "simulaciones"
COLOR_ESTADO = {
    EstadoExtraccion.EXITOSO: "green",
    EstadoExtraccion.PARCIAL: "yellow",
    EstadoExtraccion.FALLIDO: "red",
}
COLUMNAS_CSV = (
    "archivo",
    "estado",
    "motivos",
    "campos_faltantes",
    "advertencias",
    "n_intentos",
    "tokens_entrada",
    "tokens_salida",
    "latencia_ms",
    "simulacion",
    *CAMPOS_CLAVE,
)


def carpeta_salida(base: Path, modo_simulacion: str | None) -> Path:
    """Carpeta de salida: la oficial o ``<base>/simulaciones/<modo>/``.

    Las corridas con ``--simular-fallo`` NUNCA escriben sobre la corrida oficial.
    """
    if modo_simulacion is None:
        return base
    return base / CARPETA_SIMULACIONES / modo_simulacion


def cargar_esperado(ruta: Path) -> dict[str, Any] | None:
    """Lee ``esperado.json`` (sección ``documentos``) si existe."""
    if not ruta.is_file():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))["documentos"]


# ---------------------------------------------------------------------------
# Comparación con esperado.json
# ---------------------------------------------------------------------------


def _normalizar_numero(valor: Any) -> Any:
    """``F003-0001209`` y ``F003-1209`` son el mismo comprobante."""
    if not isinstance(valor, str):
        return valor
    partes = re.fullmatch(r"\s*([A-Z0-9]{4})\s*-\s*0*(\d+)\s*", valor.upper())
    return f"{partes.group(1)}-{int(partes.group(2))}" if partes else valor.strip().upper()


def campo_coincide(campo: str, esperado: Any, obtenido: Any, tolerancia: float) -> bool:
    """Compara un campo clave; ``null`` esperado exige ``null`` obtenido (no inventar)."""
    if esperado is None or obtenido is None:
        return esperado is None and obtenido is None
    if campo == "total":
        return abs(float(esperado) - float(obtenido)) <= tolerancia
    if campo == "numero_factura":
        return _normalizar_numero(esperado) == _normalizar_numero(obtenido)
    return str(esperado).strip() == str(obtenido).strip()


def comparar_documento(
    resultado: ResultadoDocumento, esperado: dict[str, Any], tolerancia: float
) -> dict[str, Any]:
    """Compara estado, motivo y campos clave de un documento con lo esperado."""
    comparacion: dict[str, Any] = {
        "estado_esperado": esperado["estado"],
        "coincide_estado": resultado.estado.value == esperado["estado"],
        "coincide_motivo": any(
            m.value in esperado["motivos_aceptables"] for m in resultado.motivos
        ),
        "campos": None,
    }
    if esperado.get("campos"):
        datos = resultado.datos or {}
        comparacion["campos"] = {
            campo: {
                "esperado": esperado["campos"][campo],
                "obtenido": datos.get(campo),
                "ok": campo_coincide(
                    campo, esperado["campos"][campo], datos.get(campo), tolerancia
                ),
            }
            for campo in CAMPOS_CLAVE
        }
    return comparacion


def comparar_lote(
    resultados: list[ResultadoDocumento], esperado: dict[str, Any], tolerancia: float
) -> dict[str, Any]:
    """Coincidencia de estado y exactitud por campo clave en todo el lote."""
    por_documento = {
        r.archivo: comparar_documento(r, esperado[r.archivo], tolerancia)
        for r in resultados
        if r.archivo in esperado
    }
    n = len(por_documento)
    coinciden = sum(c["coincide_estado"] for c in por_documento.values())
    exactitud: dict[str, dict[str, Any]] = {}
    for campo in CAMPOS_CLAVE:
        evaluados = [c["campos"][campo]["ok"] for c in por_documento.values() if c["campos"]]
        exactitud[campo] = {
            "aciertos": sum(evaluados),
            "total": len(evaluados),
            "pct": round(100 * sum(evaluados) / len(evaluados), 1) if evaluados else None,
        }
    total_campos = sum(e["total"] for e in exactitud.values())
    aciertos = sum(e["aciertos"] for e in exactitud.values())
    return {
        "documentos_comparados": n,
        "estado_coincide": coinciden,
        "pct_estado_coincide": round(100 * coinciden / n, 1) if n else None,
        "motivo_coincide": sum(c["coincide_motivo"] for c in por_documento.values()),
        "exactitud_por_campo": exactitud,
        "pct_exactitud_campos": round(100 * aciertos / total_campos, 1) if total_campos else None,
        "por_documento": por_documento,
    }


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------


def _pct(parte: int, total: int) -> float:
    return round(100 * parte / total, 1) if total else 0.0


def calcular_metricas(
    resultados: list[ResultadoDocumento],
    esperado: dict[str, Any] | None = None,
    tolerancia: float = 0.05,
) -> dict[str, Any]:
    """Métricas del lote (y comparación con lo esperado, si se entrega)."""
    n = len(resultados)
    conteo = Counter(r.estado for r in resultados)
    con_llamadas = [r for r in resultados if r.intentos]
    motivos = Counter(m.value for r in resultados for m in r.motivos if m.value != "OK")
    advertencias = Counter(a.codigo.value for r in resultados for a in r.advertencias)
    exitosos = conteo[EstadoExtraccion.EXITOSO]
    parciales = conteo[EstadoExtraccion.PARCIAL]
    fallidos = conteo[EstadoExtraccion.FALLIDO]
    return {
        "n_documentos": n,
        "exitosos": exitosos,
        "parciales": parciales,
        "fallidos": fallidos,
        "pct_exito": _pct(exitosos, n),
        "pct_parcial": _pct(parciales, n),
        "pct_fallido": _pct(fallidos, n),
        "tasa_utilizable_pct": _pct(exitosos + parciales, n),
        "intentos_promedio": (
            round(sum(len(r.intentos) for r in con_llamadas) / len(con_llamadas), 2)
            if con_llamadas
            else 0.0
        ),
        "docs_con_reintento": sum(len(r.intentos) > 1 for r in resultados),
        "llamadas_api_totales": sum(r.llamadas_api for r in resultados),
        "tokens_entrada_totales": sum(r.tokens_entrada_total for r in resultados),
        "tokens_salida_totales": sum(r.tokens_salida_total for r in resultados),
        "latencia_promedio_ms": (
            round(sum(r.latencia_ms_total for r in con_llamadas) / len(con_llamadas))
            if con_llamadas
            else 0
        ),
        "motivos_por_codigo": dict(motivos.most_common()),
        "advertencias_por_codigo": dict(advertencias.most_common()),
        "comparacion_esperado": (
            comparar_lote(resultados, esperado, tolerancia) if esperado else None
        ),
    }


def construir_metadatos(
    modelo: str,
    prompt_version: str,
    configuracion: dict[str, Any],
    simulacion: dict[str, Any] | None,
    interrumpido: bool,
    n_archivos: int,
) -> dict[str, Any]:
    """Metadatos de la corrida (sin secretos)."""
    return {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "proveedor": "cohere",
        "modelo": modelo,
        "prompt_version": prompt_version,
        "configuracion": configuracion,
        "simulacion": simulacion,
        "interrumpido": interrumpido,
        "archivos_en_carpeta": n_archivos,
    }


# ---------------------------------------------------------------------------
# Escritura de archivos
# ---------------------------------------------------------------------------


def _fila_csv(resultado: ResultadoDocumento) -> dict[str, Any]:
    datos = resultado.datos or {}
    return {
        "archivo": resultado.archivo,
        "estado": resultado.estado.value,
        "motivos": ";".join(m.value for m in resultado.motivos),
        "campos_faltantes": ";".join(resultado.campos_faltantes),
        "advertencias": ";".join(a.codigo.value for a in resultado.advertencias),
        "n_intentos": len(resultado.intentos),
        "tokens_entrada": resultado.tokens_entrada_total,
        "tokens_salida": resultado.tokens_salida_total,
        "latencia_ms": resultado.latencia_ms_total,
        "simulacion": resultado.simulacion or "",
        **{campo: "" if datos.get(campo) is None else datos[campo] for campo in CAMPOS_CLAVE},
    }


def escribir_json(ruta: Path, resultados, metricas, metadatos) -> None:
    """``resultados.json``: metadatos, métricas y cada ResultadoDocumento completo."""
    contenido = {
        "metadatos": metadatos,
        "metricas": metricas,
        "documentos": [r.model_dump(mode="json") for r in resultados],
    }
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")


def escribir_csv(ruta: Path, resultados: list[ResultadoDocumento]) -> None:
    """``reporte.csv``: una fila por documento."""
    with ruta.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS_CSV)
        escritor.writeheader()
        escritor.writerows(_fila_csv(r) for r in resultados)


def _celda(texto: str) -> str:
    return texto.replace("|", "\\|").replace("\n", " ")


def generar_markdown(resultados, metricas, metadatos) -> str:
    """``reporte.md``: resumen de métricas y tabla por documento."""
    comparacion = metricas["comparacion_esperado"] or {}
    por_doc = comparacion.get("por_documento", {})
    lineas = [
        "# Reporte del lote",
        "",
        f"- Fecha: {metadatos['timestamp']}",
        f"- Proveedor / modelo: {metadatos['proveedor']} / `{metadatos['modelo']}`",
        f"- Prompt: v{metadatos['prompt_version']}",
        f"- Simulación: {metadatos['simulacion'] or 'no (corrida real)'}",
        f"- Interrumpido: {'sí' if metadatos['interrumpido'] else 'no'}",
        "",
        "## Métricas",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Documentos | {metricas['n_documentos']} |",
        f"| Éxito total | {metricas['exitosos']} ({metricas['pct_exito']} %) |",
        f"| Éxito parcial | {metricas['parciales']} ({metricas['pct_parcial']} %) |",
        f"| Fallido | {metricas['fallidos']} ({metricas['pct_fallido']} %) |",
        f"| Tasa de extracción utilizable | {metricas['tasa_utilizable_pct']} % |",
        f"| Intentos promedio (docs con llamadas) | {metricas['intentos_promedio']} |",
        f"| Documentos con reintento | {metricas['docs_con_reintento']} |",
        f"| Llamadas totales a la API | {metricas['llamadas_api_totales']} |",
        f"| Tokens entrada / salida | {metricas['tokens_entrada_totales']} / "
        f"{metricas['tokens_salida_totales']} |",
        f"| Latencia promedio por documento | {metricas['latencia_promedio_ms']} ms |",
    ]
    if comparacion:
        lineas += [
            f"| Estado coincide con esperado | {comparacion['estado_coincide']}/"
            f"{comparacion['documentos_comparados']} ({comparacion['pct_estado_coincide']} %) |",
            f"| Exactitud de campos clave | {comparacion['pct_exactitud_campos']} % |",
        ]
    lineas += ["", "## Resultados por documento", ""]
    lineas += [
        "| Archivo | Estado | Esperado | Motivos | Faltantes | Advertencias | Intentos | Tokens | "
        "Latencia (ms) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in resultados:
        comp = por_doc.get(r.archivo)
        esperado = (
            f"{comp['estado_esperado']} {'✓' if comp['coincide_estado'] else '✗'}" if comp else "—"
        )
        lineas.append(
            f"| {r.archivo} | **{r.estado.value}** | {esperado} | "
            f"{', '.join(m.value for m in r.motivos)} | {', '.join(r.campos_faltantes) or '—'} | "
            f"{', '.join(a.codigo.value for a in r.advertencias) or '—'} | {len(r.intentos)} | "
            f"{r.tokens_entrada_total + r.tokens_salida_total} | {r.latencia_ms_total} |"
        )
    lineas += ["", "## Detalle por documento", ""]
    lineas += [f"- **{r.archivo}**: {_celda(r.detalle)}" for r in resultados]
    lineas += ["", "## Patrón de los fallos y advertencias", ""]
    lineas += [f"- Motivo `{c}`: {n}" for c, n in metricas["motivos_por_codigo"].items()] or ["- —"]
    lineas += [f"- Advertencia `{c}`: {n}" for c, n in metricas["advertencias_por_codigo"].items()]
    if comparacion:
        lineas += [
            "",
            "## Exactitud por campo clave",
            "",
            "| Campo | Aciertos | % |",
            "|---|---|---|",
        ]
        lineas += [
            f"| {campo} | {e['aciertos']}/{e['total']} | {e['pct']} |"
            for campo, e in comparacion["exactitud_por_campo"].items()
        ]
    return "\n".join(lineas) + "\n"


def escribir_reportes(
    carpeta: Path, resultados: list[ResultadoDocumento], metricas, metadatos
) -> list[Path]:
    """Escribe ``resultados.json``, ``reporte.csv`` y ``reporte.md`` en ``carpeta``."""
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = [carpeta / "resultados.json", carpeta / "reporte.csv", carpeta / "reporte.md"]
    escribir_json(rutas[0], resultados, metricas, metadatos)
    escribir_csv(rutas[1], resultados)
    rutas[2].write_text(generar_markdown(resultados, metricas, metadatos), encoding="utf-8")
    return rutas


# ---------------------------------------------------------------------------
# Consola
# ---------------------------------------------------------------------------


def mostrar_en_consola(consola: Console, resultados, metricas) -> None:
    """Tabla coloreada por documento y panel de métricas."""
    comparacion = metricas["comparacion_esperado"] or {}
    por_doc = comparacion.get("por_documento", {})
    tabla = Table(title="Resultados por documento", show_lines=False)
    for columna in (
        "Archivo",
        "Estado",
        "Esperado",
        "Motivos",
        "Faltantes / advertencias",
        "Int.",
        "Tokens",
    ):
        tabla.add_column(columna)
    for r in resultados:
        color = COLOR_ESTADO[r.estado]
        comp = por_doc.get(r.archivo)
        esperado = (
            f"{comp['estado_esperado']} {'✓' if comp['coincide_estado'] else '✗'}" if comp else "—"
        )
        notas = ", ".join([*r.campos_faltantes, *(a.codigo.value for a in r.advertencias)]) or "—"
        tabla.add_row(
            r.archivo,
            f"[bold {color}]{r.estado.value}[/]",
            esperado,
            ", ".join(m.value for m in r.motivos),
            notas,
            str(len(r.intentos)),
            str(r.tokens_entrada_total + r.tokens_salida_total),
        )
    consola.print(tabla)
    lineas = [
        f"Documentos: {metricas['n_documentos']}",
        f"[green]Éxito total: {metricas['exitosos']} ({metricas['pct_exito']} %)[/]   "
        f"[yellow]Parcial: {metricas['parciales']} ({metricas['pct_parcial']} %)[/]   "
        f"[red]Fallido: {metricas['fallidos']} ({metricas['pct_fallido']} %)[/]",
        f"Tasa de extracción utilizable: {metricas['tasa_utilizable_pct']} %",
        f"Intentos promedio: {metricas['intentos_promedio']} · docs con reintento: "
        f"{metricas['docs_con_reintento']} · llamadas a la API: {metricas['llamadas_api_totales']}",
        f"Tokens: {metricas['tokens_entrada_totales']} entrada / "
        f"{metricas['tokens_salida_totales']} salida · latencia promedio: "
        f"{metricas['latencia_promedio_ms']} ms",
        f"Motivos: {metricas['motivos_por_codigo'] or '—'}",
        f"Advertencias: {metricas['advertencias_por_codigo'] or '—'}",
    ]
    if comparacion:
        lineas.append(
            f"Vs. esperado: estado {comparacion['estado_coincide']}/"
            f"{comparacion['documentos_comparados']} · exactitud de campos clave "
            f"{comparacion['pct_exactitud_campos']} %"
        )
    consola.print(Panel("\n".join(lineas), title="Métricas del lote"))
