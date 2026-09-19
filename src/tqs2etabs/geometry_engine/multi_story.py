"""Regra multi-pavimento: eixo do pilar alinhado entre pavimentos (decisao do usuario).

Quando a secao de um pilar muda entre pavimentos (ex.: 50 -> 40 cm), o ETABS nao recebe a
excentricidade: as linhas de eixo do pilar em cada planta sao transladadas para coincidir
com as da planta de referencia (a mais baixa em que o pilar existe). A translacao e rigida
(dx, dy), aplicada a secao/laminas do pilar antes do motor geometrico da planta; vigas e
lajes dessa planta seguem depois pelas regras normais (extensao ate o eixo etc.).
"""

from __future__ import annotations

from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import ChangeRecord, DiagnosticCollector, Source
from ..domain.elements import AxisSegment, Column, ColumnKind, PolygonSection, RectSection
from ..domain.geometry import Point, Polygon
from ..domain.model import StructuralModel
from .column_axes import derive_column_axes
from .common import StepResult, fmt

RULE = "column-axis-continuity"
MAX_SHIFT = 0.60   # acima disto provavelmente nao e o mesmo pilar: aviso, sem translacao


def _shift_of(own: Column, ref: Column, tol: float) -> tuple[float, float] | None:
    """Translacao (dx, dy) que leva as linhas de eixo de `own` sobre as de `ref`."""
    if own.kind_hint != ColumnKind.WALL or ref.kind_hint != ColumnKind.WALL or not own.axes or not ref.axes:
        c0, c1 = own.centroid, ref.centroid
        return (c1.x - c0.x, c1.y - c0.y)
    dxs, dys = [], []
    for s in own.axes:
        if s.direction == "Y":
            cands = [r.start.x - s.start.x for r in ref.axes if r.direction == "Y"]
            if cands:
                dxs.append(min(cands, key=abs))
        elif s.direction == "X":
            cands = [r.start.y - s.start.y for r in ref.axes if r.direction == "X"]
            if cands:
                dys.append(min(cands, key=abs))
    if not dxs and not dys:
        c0, c1 = own.centroid, ref.centroid
        return (c1.x - c0.x, c1.y - c0.y)
    dx = sum(dxs) / len(dxs) if dxs else 0.0
    dy = sum(dys) / len(dys) if dys else 0.0
    return (dx, dy)


def _translate_polygon(poly: Polygon, dx: float, dy: float) -> Polygon:
    return tuple(Point(p.x + dx, p.y + dy) for p in poly)


def translate_column(col: Column, dx: float, dy: float) -> Column:
    sec = col.section
    if isinstance(sec, RectSection):
        sec = replace(sec, origin=Point(sec.origin.x + dx, sec.origin.y + dy))
    else:
        sec = PolygonSection(_translate_polygon(sec.outline, dx, dy))
    return replace(col, section=sec,
                   laminas=tuple(_translate_polygon(l, dx, dy) for l in col.laminas),
                   axes=tuple(AxisSegment(Point(s.start.x + dx, s.start.y + dy), Point(s.end.x + dx, s.end.y + dy), s.thickness)
                              for s in col.axes),
                   section_above=_translate_polygon(col.section_above, dx, dy) if col.section_above else None)


def align_columns_to_reference(model: StructuralModel, reference: dict[str, Column],
                               config: Config) -> StepResult:
    """`reference`: pilares (com axes ja derivados) da(s) planta(s) de referencia, por nome."""
    tol = config.tolerances.coordinate_cluster
    diag = DiagnosticCollector()
    changes: list[ChangeRecord] = []
    own_axes = derive_column_axes(model, config).model.columns
    columns = dict(model.columns)
    shifted = 0
    for cid, col in model.columns.items():
        ref = reference.get(cid)
        if ref is None:
            continue
        d = _shift_of(own_axes[cid], ref, tol)
        if d is None:
            continue
        dx, dy = d
        if abs(dx) <= tol and abs(dy) <= tol:
            continue
        if max(abs(dx), abs(dy)) > MAX_SHIFT:
            diag.warning("MULTI-W-COLUMN-FAR", f"Pilar {cid}: eixo a ({fmt(dx)}, {fmt(dy)}) m da referencia; "
                         "nao alinhado (verificar se e o mesmo pilar)", Source.ENGINE, refs=(cid,))
            continue
        columns[cid] = translate_column(col, dx, dy)
        shifted += 1
        changes.append(ChangeRecord(cid, "axis", f"({fmt(col.centroid.x)}, {fmt(col.centroid.y)})",
                                    f"({fmt(columns[cid].centroid.x)}, {fmt(columns[cid].centroid.y)})",
                                    f"Eixo do pilar alinhado ao pavimento de referencia (dx={fmt(dx)}, dy={fmt(dy)}); "
                                    "sem excentricidade entre lances", RULE, "align_columns_to_reference", tol))
    if shifted:
        diag.info("MULTI-I-ALIGNED", f"{shifted} pilar(es) transladado(s) para o eixo do pavimento de referencia",
                  Source.ENGINE)
    new_model = replace(model, columns=columns, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("align_columns_to_reference", new_model, tuple(changes), diag.as_tuple(), {"shifted": shifted})
