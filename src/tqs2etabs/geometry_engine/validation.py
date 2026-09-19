"""Validacao do modelo normalizado (ARCHITECTURE.md secao 13): geometria, alinhamento,
conectividade e integridade. Nao altera o modelo; so produz diagnosticos."""

from __future__ import annotations

import math
from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import DiagnosticCollector, Level, Source
from ..domain.elements import ColumnKind, EdgeSupport, SupportKind
from ..domain.geometry import (Point, distance_point_to_segment, polygon_area_signed,
                               polyline_length)
from ..domain.model import StructuralModel
from .common import StepResult, fmt


def _seg_intersection(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Intersecao propria (interior de ambos) dos segmentos ab e cd."""
    ux, uy = b.x - a.x, b.y - a.y
    vx, vy = d.x - c.x, d.y - c.y
    den = ux * vy - uy * vx
    if abs(den) < 1e-12:
        return None
    wx, wy = c.x - a.x, c.y - a.y
    t = (wx * vy - wy * vx) / den
    s = (wx * uy - wy * ux) / den
    eps = 1e-6
    if eps < t < 1 - eps and eps < s < 1 - eps:
        return Point(a.x + ux * t, a.y + uy * t)
    return None


def validate_model(model: StructuralModel, original: StructuralModel, config: Config,
                   extended: dict[str, float] | None = None) -> StepResult:
    tol = config.tolerances
    diag = DiagnosticCollector()
    extended = extended or {}
    V = Source.VALIDATION

    # ------------------------------------------------------------ integridade
    for b in model.beams.values():
        missing = [n for n in b.axis if n not in model.nodes]
        if missing:
            diag.error("INT-E-BEAM-NODE", f"{b.id}: nos inexistentes {missing}", V, refs=(b.id,))
        if len(b.segments) != max(len(b.axis) - 1, 0):
            diag.error("INT-E-BEAM-SEGMENTS", f"{b.id}: {len(b.segments)} trechos para {len(b.axis)} nos", V, refs=(b.id,))
        for s in b.supports:
            if s.node_id not in model.nodes:
                diag.error("INT-E-SUPPORT-NODE", f"{b.id}: apoio em no inexistente {s.node_id}", V, refs=(b.id,))
            if s.kind == SupportKind.COLUMN and s.ref_id not in model.columns:
                diag.error("INT-E-SUPPORT-COLUMN", f"{b.id}: apoio em pilar inexistente {s.ref_id}", V, refs=(b.id,))
            if s.kind in (SupportKind.ON_BEAM, SupportKind.RECEIVES):
                other = model.beams.get(s.ref_id or "")
                if other is None:
                    diag.error("INT-E-SUPPORT-BEAM", f"{b.id}: apoio em viga inexistente {s.ref_id}", V, refs=(b.id,))
                elif s.node_id not in other.axis:
                    diag.error("CON-E-BEAM-BEAM", f"{b.id}: no {s.node_id} nao pertence ao eixo de {s.ref_id}", V,
                               refs=(b.id, s.ref_id or "?"))
    for c in model.columns.values():
        if c.reference_node_id not in model.nodes:
            diag.error("INT-E-COLUMN-NODE", f"{c.id}: no de referencia inexistente", V, refs=(c.id,))
        if c.area <= 1e-9:
            diag.error("INT-E-COLUMN-AREA", f"{c.id}: secao sem area", V, refs=(c.id,))
    for s in model.slabs.values():
        for e in s.edges:
            if e.start_node_id not in model.nodes or e.end_node_id not in model.nodes:
                diag.error("INT-E-SLAB-NODE", f"{s.id}: bordo com no inexistente", V, refs=(s.id,))

    if diag.count(Level.ERROR):
        return _finish(model, diag)

    # -------------------------------------------------------------- geometria
    structural = model.structural_nodes()
    for i, a in enumerate(structural):
        for b in structural[i + 1:]:
            if a.point.distance_to(b.point) <= tol.node_merge:
                diag.error("GEO-E-DUP-NODE", f"Nos {a.id} e {b.id} coincidem apos normalizacao", V, refs=(a.id, b.id))

    for b in model.beams.values():
        ob = original.beams.get(b.id)
        for k, seg in enumerate(b.segments):
            p, q = model.node(seg.start_node_id).point, model.node(seg.end_node_id).point
            length = p.distance_to(q)
            if length <= tol.node_merge:
                diag.error("GEO-E-ZERO-LENGTH", f"{b.id} trecho {k + 1}: comprimento {fmt(length)} m", V, refs=(b.id,))
                continue
            if ob and len(ob.segments) == len(b.segments):
                oseg = ob.segments[k]
                op, oq = original.node(oseg.start_node_id).point, original.node(oseg.end_node_id).point
                olen = op.distance_to(oq)
                allowed = tol.length_change_warning + extended.get(seg.start_node_id, 0.0) + extended.get(seg.end_node_id, 0.0)
                if abs(length - olen) > allowed:
                    diag.warning("GEO-W-LENGTH-CHANGED", f"{b.id} trecho {k + 1}: comprimento {fmt(olen)} -> {fmt(length)} m "
                                 f"(variacao {fmt(abs(length - olen))} m > {fmt(allowed)})", V, refs=(b.id,))
        if ob:
            l0 = polyline_length([original.node(n).point for n in ob.axis])
            l1 = polyline_length([model.node(n).point for n in b.axis])
            allowed = tol.length_change_warning + sum(extended.get(n, 0.0) for n in (b.axis[0], b.axis[-1]))
            if abs(l1 - l0) > allowed:
                diag.warning("GEO-W-BEAM-LENGTH", f"{b.id}: comprimento total {fmt(l0)} -> {fmt(l1)} m", V, refs=(b.id,))

    for s in model.slabs.values():
        pts = [model.node(e.start_node_id).point for e in s.edges]
        if len(pts) < 3:
            diag.error("GEO-E-SLAB-OPEN", f"{s.id}: menos de 3 vertices", V, refs=(s.id,))
            continue
        for e in s.edges:
            if model.node(e.start_node_id).point.distance_to(model.node(e.end_node_id).point) <= tol.node_merge:
                diag.error("GEO-E-SLAB-EDGE", f"{s.id}: bordo {e.start_node_id}-{e.end_node_id} degenerado", V, refs=(s.id,))
        # anel fechado: o fim de cada bordo e o inicio do proximo
        for e, nxt in zip(s.edges, s.edges[1:] + s.edges[:1]):
            if e.end_node_id != nxt.start_node_id:
                diag.error("GEO-E-SLAB-RING", f"{s.id}: contorno nao encadeado em {e.end_node_id}", V, refs=(s.id,))
        area = polygon_area_signed(pts)
        if abs(area) < 1e-6:
            diag.error("GEO-E-SLAB-AREA", f"{s.id}: area nula", V, refs=(s.id,))
        elif area < 0:
            diag.info("GEO-I-SLAB-CW", f"{s.id}: contorno horario (sera invertido na exportacao)", V, refs=(s.id,))
        ref = s.tqs_attrs.get("area_cm2")
        if ref and abs(area) < ref * 1e-4 * 0.9:
            diag.warning("GEO-W-SLAB-AREA", f"{s.id}: area do poligono {fmt(abs(area))} m2 menor que a area "
                         f"liquida do TQS {fmt(ref * 1e-4)} m2", V, refs=(s.id,))

    # ------------------------------------------------------------- alinhamento
    for b in model.beams.values():
        for sup in b.supports:
            if sup.kind != SupportKind.COLUMN:
                continue
            col = model.columns[sup.ref_id]
            p = model.node(sup.node_id).point
            if col.kind_hint == ColumnKind.WALL and col.axes:
                d = min(distance_point_to_segment(p, s.start, s.end) for s in col.axes)
            elif col.kind_hint == ColumnKind.WALL:
                continue
            else:
                d = p.distance_to(col.centroid)
            if d > tol.node_merge:
                diag.warning("ALN-W-END-OFF-AXIS", f"{b.id} no {sup.node_id}: a {fmt(d)} m do eixo de {col.id}", V,
                             refs=(b.id, col.id), distance=d)
    for i, g in enumerate(model.grids):
        for h in model.grids[i + 1:]:
            if g.direction == h.direction and abs(g.coordinate - h.coordinate) <= tol.coordinate_cluster:
                diag.error("ALN-E-GRID-DUP", f"Grids {g.label} e {h.label} duplicados", V, refs=(g.label, h.label))

    # ----------------------------------------------------------- conectividade
    node_beams: dict[str, set[str]] = {}
    for b in model.beams.values():
        for n in b.axis:
            node_beams.setdefault(n, set()).add(b.id)
    for b in model.beams.values():
        for end in (b.axis[0], b.axis[-1]):
            kinds = {s.kind for s in b.supports if s.node_id == end}
            shared = len(node_beams.get(end, set())) > 1
            if kinds <= {SupportKind.FREE} and not shared:
                diag.warning("CON-W-FREE-END", f"{b.id}: extremidade {end} sem apoio e sem outra viga", V, refs=(b.id,))
    walls = [c for c in model.columns.values() if c.kind_hint == ColumnKind.WALL and c.axes]
    for b in model.beams.values():
        axis_pts = [model.node(n).point for n in b.axis]
        for k in range(len(axis_pts) - 1):
            a, c = axis_pts[k], axis_pts[k + 1]
            for col in walls:
                for seg in col.axes:
                    x = _seg_intersection(a, c, seg.start, seg.end)
                    if x is None:
                        continue
                    if not any(x.distance_to(p) <= tol.node_merge for p in axis_pts):
                        diag.warning("CON-W-BEAM-CROSSES-WALL", f"{b.id} trecho {k + 1} atravessa o eixo de {col.id} em "
                                     f"({fmt(x.x)}, {fmt(x.y)}) sem no", V, refs=(b.id, col.id))
    for s in model.slabs.values():
        for e in s.edges:
            if e.support == EdgeSupport.BEAM:
                ob = model.beams.get(e.ref_id or "")
                ok = False
                if ob is not None:
                    pts = [model.node(n).point for n in ob.axis]
                    def on_beam(p: Point) -> bool:
                        return any(distance_point_to_segment(p, pts[i], pts[i + 1]) <= tol.node_merge
                                   for i in range(len(pts) - 1))
                    ok = on_beam(model.node(e.start_node_id).point) and on_beam(model.node(e.end_node_id).point)
                if not ok:
                    diag.error("CON-E-SLAB-EDGE-BEAM", f"{s.id}: bordo {e.start_node_id}-{e.end_node_id} nao esta sobre {e.ref_id}",
                               V, refs=(s.id, e.ref_id or "?"))
            elif e.support == EdgeSupport.UNKNOWN:
                diag.warning("CON-W-SLAB-EDGE-UNKNOWN", f"{s.id}: bordo {e.start_node_id}-{e.end_node_id} sem apoio identificado",
                             V, refs=(s.id,))
    for col in (model.columns.values() if (model.beams or model.slabs) else ()):
        touching = [b.id for b in model.beams.values() if any(s.kind == SupportKind.COLUMN and s.ref_id == col.id for s in b.supports)]
        slabs_on = [s.id for s in model.slabs.values() if any(e.support == EdgeSupport.COLUMN and e.ref_id == col.id for e in s.edges)]
        if not touching and not slabs_on:
            diag.warning("CON-W-ISOLATED-COLUMN", f"{col.id}: nenhuma viga ou laje apoiada", V, refs=(col.id,))

    return _finish(model, diag)


def _finish(model: StructuralModel, diag: DiagnosticCollector) -> StepResult:
    counts = {lv.value: diag.count(lv) for lv in Level}
    diag.info("VAL-I-SUMMARY", f"Validacao: {counts['ERROR']} erros, {counts['WARNING']} avisos", Source.VALIDATION)
    new_model = replace(model, diagnostics=model.diagnostics + diag.as_tuple())
    return StepResult("validate_model", new_model, (), diag.as_tuple(), counts)
