"""Regra: derivar as linhas de eixo dos pilares (ARCHITECTURE.md 10.4).

- Pilar-frame (kind COLUMN): eixo = centroide (sem AxisSegment).
- Parede retangular sem laminas: 1 segmento pela linha media do lado maior.
- Parede com LAMINAS: 1 segmento por lamina; laminas colineares contiguas (ou
  separadas por furo <= wall_opening_merge_max) sao unidas; nos cantos, o segmento
  que termina na face da lamina perpendicular e prolongado ate a linha media dela e
  a lamina atravessada e dividida no encontro (ver join_corners), para que os
  paineis de parede compartilhem uma aresta.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import DiagnosticCollector, Source
from ..domain.elements import AxisSegment, Column, ColumnKind, RectSection
from ..domain.geometry import Point, Polygon, open_polygon
from ..domain.model import StructuralModel
from .common import StepResult, fmt, fmt_pt

_EPS = 1e-6


def lamina_axis(poly: Polygon) -> AxisSegment | None:
    """Linha media do lado maior de um retangulo (qualquer rotacao)."""
    pts = open_polygon(poly, tol=1e-9)
    if len(pts) != 4:
        return None
    e0 = pts[0].distance_to(pts[1])
    e1 = pts[1].distance_to(pts[2])
    if e0 >= e1:   # lados longos: p0-p1 e p2-p3 ; lados curtos: p1-p2 e p3-p0
        a = _mid(pts[3], pts[0])
        b = _mid(pts[1], pts[2])
        return AxisSegment(a, b, round(e1, 6))
    a = _mid(pts[0], pts[1])
    b = _mid(pts[2], pts[3])
    return AxisSegment(a, b, round(e0, 6))


def _mid(p: Point, q: Point) -> Point:
    return Point((p.x + q.x) / 2, (p.y + q.y) / 2)


def _unit(seg: AxisSegment) -> tuple[float, float]:
    dx, dy = seg.end.x - seg.start.x, seg.end.y - seg.start.y
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 0 else (1.0, 0.0)


def _collinear(a: AxisSegment, b: AxisSegment, tol: float) -> bool:
    ux, uy = _unit(a)
    vx, vy = _unit(b)
    if abs(ux * vy - uy * vx) > 1e-6:
        return False
    # distancia perpendicular de b.start a linha de a
    wx, wy = b.start.x - a.start.x, b.start.y - a.start.y
    return abs(wx * uy - wy * ux) <= tol


def merge_collinear(segments: list[AxisSegment], gap_max: float, tol: float) -> tuple[list[AxisSegment], list[float]]:
    """Une segmentos colineares contiguos ou separados por vao <= gap_max. Devolve os vaos unidos."""
    segs = list(segments)
    gaps: list[float] = []
    changed = True
    while changed:
        changed = False
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a, b = segs[i], segs[j]
                if not _collinear(a, b, tol):
                    continue
                ux, uy = _unit(a)
                # projecoes sobre a linha de a
                def proj(p: Point) -> float:
                    return (p.x - a.start.x) * ux + (p.y - a.start.y) * uy
                ia = sorted((proj(a.start), proj(a.end)))
                ib = sorted((proj(b.start), proj(b.end)))
                gap = max(ib[0] - ia[1], ia[0] - ib[1])
                if gap > gap_max + tol:
                    continue
                if gap > tol:
                    gaps.append(gap)
                lo, hi = min(ia[0], ib[0]), max(ia[1], ib[1])
                merged = AxisSegment(Point(a.start.x + ux * lo, a.start.y + uy * lo),
                                     Point(a.start.x + ux * hi, a.start.y + uy * hi),
                                     max(a.thickness, b.thickness))
                segs = [s for k, s in enumerate(segs) if k not in (i, j)] + [merged]
                changed = True
                break
            if changed:
                break
    return segs, gaps


def join_corners(segments: list[AxisSegment], tol: float) -> list[AxisSegment]:
    """Encontro de laminas perpendiculares (cantos e T):

    1. a extremidade que para na face da lamina perpendicular e prolongada ate a linha
       media dela (a alma do U passa a alcancar a linha media dos bracos);
    2. a lamina atravessada no seu interior e dividida no ponto de encontro (o braco do
       U vira [ponta, alma] + [alma, face externa]); nada e recuado, para que o toco que
       representa o canto continue existindo como painel.
    """
    # passo 1: prolongar extremidades
    extended: list[AxisSegment] = []
    for i, s in enumerate(segments):
        new_start, new_end = s.start, s.end
        u = _unit(s)
        for j, t in enumerate(segments):
            if i == j:
                continue
            v = _unit(t)
            if abs(u[0] * v[1] - u[1] * v[0]) < 1e-6:
                continue
            inter = _intersect_lines(s.start, u, t.start, v)
            if inter is None or not _within_extent(inter, t, s.thickness / 2 + tol):
                continue
            reach = s.thickness / 2 + t.thickness / 2 + tol
            ps = (inter.x - s.start.x) * u[0] + (inter.y - s.start.y) * u[1]
            if ps < 0 and new_start.distance_to(inter) <= reach:
                new_start = inter
            elif ps > s.length and new_end.distance_to(inter) <= reach:
                new_end = inter
        extended.append(AxisSegment(new_start, new_end, s.thickness))
    # passo 2: dividir no interior
    result: list[AxisSegment] = []
    for i, s in enumerate(extended):
        u = _unit(s)
        cuts: list[float] = []
        for j, t in enumerate(extended):
            if i == j:
                continue
            v = _unit(t)
            if abs(u[0] * v[1] - u[1] * v[0]) < 1e-6:
                continue
            inter = _intersect_lines(s.start, u, t.start, v)
            if inter is None or not _within_extent(inter, t, s.thickness / 2 + tol):
                continue
            ps = (inter.x - s.start.x) * u[0] + (inter.y - s.start.y) * u[1]
            if tol < ps < s.length - tol:
                cuts.append(ps)
        params = [0.0] + sorted(set(round(c, 9) for c in cuts)) + [s.length]
        for a, b in zip(params, params[1:]):
            if b - a > tol:
                result.append(AxisSegment(Point(s.start.x + u[0] * a, s.start.y + u[1] * a),
                                          Point(s.start.x + u[0] * b, s.start.y + u[1] * b), s.thickness))
    return result


def _within_extent(p: Point, seg: AxisSegment, slack: float) -> bool:
    v = _unit(seg)
    t = (p.x - seg.start.x) * v[0] + (p.y - seg.start.y) * v[1]
    return -slack <= t <= seg.length + slack


def _intersect_lines(p: Point, u: tuple[float, float], q: Point, v: tuple[float, float]) -> Point | None:
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) < 1e-12:
        return None
    wx, wy = q.x - p.x, q.y - p.y
    t = (wx * v[1] - wy * v[0]) / den
    return Point(p.x + u[0] * t, p.y + u[1] * t)


def derive_column_axes(model: StructuralModel, config: Config) -> StepResult:
    diag = DiagnosticCollector()
    tol = config.tolerances.coordinate_cluster
    columns: dict[str, Column] = {}
    n_segments = 0
    for cid, col in model.columns.items():
        if col.kind_hint == ColumnKind.COLUMN:
            columns[cid] = replace(col, axes=())
            continue
        if col.laminas:
            segs = [s for s in (lamina_axis(l) for l in col.laminas) if s is not None]
            if len(segs) != len(col.laminas):
                diag.warning("AXES-W-LAMINA", f"Pilar {cid}: {len(col.laminas) - len(segs)} laminas nao retangulares ignoradas",
                             Source.ENGINE, refs=(cid,))
            segs, gaps = merge_collinear(segs, config.policy.wall_opening_merge_max, tol)
            for g in gaps:
                diag.info("AXES-I-OPENING-MERGED", f"Pilar {cid}: laminas unidas atraves de vao de {fmt(g)} m (furo)",
                          Source.ENGINE, refs=(cid,), gap=g)
            segs = join_corners(segs, tol)
        elif isinstance(col.section, RectSection):
            sec = col.section
            seg = lamina_axis(sec.polygon)
            segs = [seg] if seg else []
        else:
            diag.error("AXES-E-NO-LAMINAS", f"Pilar {cid} poligonal sem LAMINAS: eixo indeterminado",
                       Source.ENGINE, refs=(cid,))
            columns[cid] = col
            continue
        n_segments += len(segs)
        columns[cid] = replace(col, axes=tuple(segs))
        diag.info("AXES-I-COLUMN", f"Pilar {cid}: {len(segs)} linha(s) de eixo: " +
                  "; ".join(f"{fmt_pt(s.start)}-{fmt_pt(s.end)} e={fmt(s.thickness)}" for s in segs),
                  Source.ENGINE, refs=(cid,))
    new_model = replace(model, columns=columns, diagnostics=model.diagnostics + diag.as_tuple())
    return StepResult("derive_column_axes", new_model, (), diag.as_tuple(),
                      {"wall_segments": n_segments,
                       "walls": sum(1 for c in columns.values() if c.kind_hint == ColumnKind.WALL),
                       "frame_columns": sum(1 for c in columns.values() if c.kind_hint == ColumnKind.COLUMN)})
