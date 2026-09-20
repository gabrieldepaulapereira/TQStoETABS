"""Regra: viga nao se sobrepoe a parede (feedback da importacao).

Trecho de viga paralelo a uma linha de eixo de parede, dentro da faixa de meia espessura da
parede e sobreposto a ela em planta, e removido: ou ha pilar (shell) ou ha viga, nunca os dois.
As partes da viga fora da parede permanecem e passam a terminar no no da parede (ponta da
linha de eixo). Uma viga pode virar varias vigas (V1, V1.2, V1.3 ...), uma por trecho
continuo restante. Bordos de laje apoiados nos trechos removidos passam a apoiar na parede.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import ChangeRecord, DiagnosticCollector, Source
from ..domain.elements import (AxisSegment, Beam, BeamSegment, BeamSupport, ColumnKind, EdgeSupport, Node,
                               NodeRole, Provenance, Slab, SlabEdge, SupportKind)
from ..domain.geometry import Point, distance_point_to_segment
from ..domain.model import StructuralModel
from .common import StepResult, fmt, fmt_pt, round_to

RULE = "beam-wall-overlap"


def _unit(a: Point, b: Point):
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else None


def _overlaps(a: Point, b: Point, seg: AxisSegment, tol: float) -> tuple[float, float] | None:
    """Intervalo [t0, t1] (parametros ao longo de a->b, em m) em que o trecho fica dentro da
    faixa da parede `seg` (paralelo, |perp| <= t/2 + tol), ou None."""
    u = _unit(a, b)
    v = _unit(seg.start, seg.end)
    if u is None or v is None or abs(u[0] * v[1] - u[1] * v[0]) > 1e-6:
        return None
    perp = abs((a.x - seg.start.x) * v[1] - (a.y - seg.start.y) * v[0])
    if perp > seg.thickness / 2 + tol:
        return None
    length = a.distance_to(b)
    t_s = (seg.start.x - a.x) * u[0] + (seg.start.y - a.y) * u[1]
    t_e = (seg.end.x - a.x) * u[0] + (seg.end.y - a.y) * u[1]
    lo, hi = max(0.0, min(t_s, t_e)), min(length, max(t_s, t_e))
    if hi - lo <= tol:
        return None
    return lo, hi


def trim_beams_over_walls(model: StructuralModel, config: Config) -> StepResult:
    tolc = config.tolerances
    tol = tolc.node_merge
    dec = tolc.rounding_decimals
    diag = DiagnosticCollector()
    changes: list[ChangeRecord] = []
    nodes = dict(model.nodes)
    beams: dict[str, Beam] = {}
    story = next(iter(model.stories))
    z = model.stories[story].elevation or 0.0
    next_id = max((int("".join(ch for ch in n if ch.isdigit()) or 0) for n in nodes), default=0) + 1
    walls = [(c.id, s) for c in model.columns.values() if c.kind_hint == ColumnKind.WALL for s in c.axes]
    removed_total = 0.0
    split_beams = 0

    def node_at(p: Point, wall_id: str, beam_id: str) -> str:
        nonlocal next_id
        for n in nodes.values():
            if n.is_structural and n.point.distance_to(p) <= tol:
                if NodeRole.BEAM_SUPPORT not in n.roles:
                    nodes[n.id] = replace(n, roles=n.roles | {NodeRole.BEAM_AXIS, NodeRole.BEAM_SUPPORT})
                return n.id
        nid = f"N{next_id}"
        next_id += 1
        nodes[nid] = Node(nid, round_to(p.x, dec), round_to(p.y, dec), z, story,
                          frozenset({NodeRole.BEAM_AXIS, NodeRole.BEAM_SUPPORT}),
                          Provenance("engine", nid, {"created_for": f"{beam_id} em {wall_id}"}))
        return nid

    for bid, beam in model.beams.items():
        # pedacos da viga: lista de (no_inicio, no_fim, segmento) apos remover sobreposicoes
        pieces: list[list[tuple[str, str, BeamSegment]]] = [[]]
        removed_here = 0.0
        for seg in beam.segments:
            a_id, b_id = seg.start_node_id, seg.end_node_id
            a, b = nodes[a_id].point, nodes[b_id].point
            length = a.distance_to(b)
            cuts: list[tuple[float, float, str, AxisSegment]] = []
            for wid, ws in walls:
                ov = _overlaps(a, b, ws, tol)
                if ov:
                    cuts.append((ov[0], ov[1], wid, ws))
            if not cuts:
                pieces[-1].append((a_id, b_id, seg))
                continue
            cuts.sort()
            cursor = 0.0
            cur_start = a_id
            u = _unit(a, b)
            for lo, hi, wid, ws in cuts:
                if lo > cursor + tol:
                    # parte antes da parede: termina no no da parede (ponta da linha de eixo mais proxima de lo)
                    end_pt = min((ws.start, ws.end), key=lambda p: abs((p.x - a.x) * u[0] + (p.y - a.y) * u[1] - lo))
                    end_id = node_at(end_pt, wid, bid)
                    if cur_start != end_id:
                        pieces[-1].append((cur_start, end_id, replace(seg, start_node_id=cur_start, end_node_id=end_id)))
                removed_here += max(0.0, hi - max(lo, cursor))
                if hi < length - tol:
                    start_pt = min((ws.start, ws.end), key=lambda p: abs((p.x - a.x) * u[0] + (p.y - a.y) * u[1] - hi))
                    cur_start = node_at(start_pt, wid, bid)
                    if pieces[-1]:
                        pieces.append([])
                else:
                    cur_start = None
                    if pieces[-1]:
                        pieces.append([])
                cursor = max(cursor, hi)
            if cur_start is not None and cursor < length - tol:
                pieces[-1].append((cur_start, b_id, replace(seg, start_node_id=cur_start, end_node_id=b_id)))
        pieces = [p for p in pieces if p]
        if removed_here <= tol:
            beams[bid] = beam
            continue
        removed_total += removed_here
        split_beams += 1
        for k, piece in enumerate(pieces):
            new_id = bid if k == 0 else f"{bid}.{k + 1}"
            axis = [piece[0][0]] + [p[1] for p in piece]
            segments = tuple(p[2] for p in piece)
            old_sup = {s.node_id: s for s in beam.supports}
            supports = []
            for nid in axis:
                if nid in old_sup:
                    supports.append(old_sup[nid])
                else:
                    wall_id = next((wid for wid, ws in walls
                                    if distance_point_to_segment(nodes[nid].point, ws.start, ws.end) <= tol), None)
                    supports.append(BeamSupport(nid, SupportKind.COLUMN if wall_id else SupportKind.FREE, wall_id))
            beams[new_id] = replace(beam, id=new_id, name=new_id, axis=tuple(axis), segments=segments,
                                    supports=tuple(supports))
        changes.append(ChangeRecord(bid, "axis", f"{len(beam.segments)} trechos, {fmt(sum(nodes[s.start_node_id].point.distance_to(nodes[s.end_node_id].point) for s in beam.segments))} m",
                                    f"{len(pieces)} pedaco(s): {', '.join(bid if k == 0 else f'{bid}.{k + 1}' for k in range(len(pieces)))}",
                                    f"Trecho(s) sobre parede removido(s) ({fmt(removed_here)} m); viga para no no da parede",
                                    RULE, "trim_beams_over_walls"))
        diag.info("OVL-I-BEAM-TRIMMED", f"{bid}: {fmt(removed_here)} m sobre parede removidos; "
                  f"{len(pieces)} pedaco(s) restante(s)" if pieces else f"{bid}: totalmente sobre parede; removida",
                  Source.ENGINE, refs=(bid,), action="beam stopped at wall node")

    # apoios viga-viga (AV/RV) que referenciam uma viga dividida: apontar para o pedaco certo
    def piece_containing(ref: str, nid: str) -> str | None:
        p = nodes[nid].point
        for nb in beams.values():
            if nb.id == ref or nb.id.startswith(ref + "."):
                pts = [nodes[n].point for n in nb.axis]
                if nid in nb.axis or any(distance_point_to_segment(p, pts[i], pts[i + 1]) <= tol
                                         for i in range(len(pts) - 1)):
                    return nb.id
        return None

    for bid, b in list(beams.items()):
        new_sup = []
        touched = False
        for sup in b.supports:
            if sup.kind in (SupportKind.ON_BEAM, SupportKind.RECEIVES) and sup.ref_id and sup.ref_id not in beams                     or (sup.kind in (SupportKind.ON_BEAM, SupportKind.RECEIVES) and sup.ref_id in beams
                        and sup.node_id not in beams[sup.ref_id].axis):
                target = piece_containing(sup.ref_id, sup.node_id)
                if target and target != sup.ref_id:
                    new_sup.append(BeamSupport(sup.node_id, sup.kind, target))
                    touched = True
                    continue
                if target is None:
                    wall_id = next((wid for wid, ws in walls
                                    if distance_point_to_segment(nodes[sup.node_id].point, ws.start, ws.end) <= tol), None)
                    new_sup.append(BeamSupport(sup.node_id, SupportKind.COLUMN if wall_id else SupportKind.FREE, wall_id))
                    touched = True
                    continue
            new_sup.append(sup)
        if touched:
            beams[bid] = replace(b, supports=tuple(new_sup))

    # bordos de laje apoiados em vigas divididas: subdividir nos nos das pontas das paredes e
    # reclassificar cada sub-bordo (pedaco de viga ou parede)
    trimmed_ids = {c.element_id for c in changes}
    structural_pts = [(n.id, n.point) for n in nodes.values() if n.is_structural]

    def classify(p_id: str, q_id: str, ref: str) -> SlabEdge:
        p, q = nodes[p_id].point, nodes[q_id].point
        for nb in beams.values():
            if nb.id == ref or nb.id.startswith(ref + "."):
                pts = [nodes[n].point for n in nb.axis]
                if all(any(distance_point_to_segment(x, pts[i], pts[i + 1]) <= tol for i in range(len(pts) - 1)) for x in (p, q)):
                    return SlabEdge(p_id, q_id, EdgeSupport.BEAM, nb.id)
        for wid, ws in walls:
            if (distance_point_to_segment(p, ws.start, ws.end) <= ws.thickness / 2 + tol
                    and distance_point_to_segment(q, ws.start, ws.end) <= ws.thickness / 2 + tol):
                return SlabEdge(p_id, q_id, EdgeSupport.COLUMN, wid)
        return SlabEdge(p_id, q_id, EdgeSupport.UNKNOWN, None)

    slabs: dict[str, Slab] = {}
    for sid, slab in model.slabs.items():
        edges: list[SlabEdge] = []
        for e in slab.edges:
            if e.support != EdgeSupport.BEAM or e.ref_id not in trimmed_ids:
                edges.append(e)
                continue
            p, q = nodes[e.start_node_id].point, nodes[e.end_node_id].point
            u = _unit(p, q)
            length = p.distance_to(q)
            inner: list[tuple[float, str]] = []
            if u is not None:
                for nid, pt in structural_pts:
                    if nid in (e.start_node_id, e.end_node_id):
                        continue
                    t = (pt.x - p.x) * u[0] + (pt.y - p.y) * u[1]
                    perp = abs((pt.x - p.x) * u[1] - (pt.y - p.y) * u[0])
                    if perp <= tol and tol < t < length - tol:
                        inner.append((t, nid))
            chain = [e.start_node_id] + [nid for _, nid in sorted(inner)] + [e.end_node_id]
            for a_id, b_id in zip(chain, chain[1:]):
                edges.append(classify(a_id, b_id, e.ref_id))
        slabs[sid] = replace(slab, edges=tuple(edges))

    # nos que ficaram sem viga e sem laje/pilar: orfaos
    referenced: set[str] = set()
    for b in beams.values():
        referenced.update(b.axis)
    for s in slabs.values():
        referenced.update(e.start_node_id for e in s.edges)
    for c in model.columns.values():
        referenced.add(c.reference_node_id)
    for nid, n in list(nodes.items()):
        if nid not in referenced and n.is_structural:
            nodes[nid] = replace(n, roles=frozenset({NodeRole.ORPHAN}))

    if split_beams:
        diag.info("OVL-I-SUMMARY", f"{split_beams} viga(s) com trechos sobre paredes; {fmt(removed_total)} m removidos",
                  Source.ENGINE)
    new_model = replace(model, nodes=nodes, beams=beams, slabs=slabs,
                        diagnostics=model.diagnostics + diag.as_tuple(), changes=model.changes + tuple(changes))
    return StepResult("trim_beams_over_walls", new_model, tuple(changes), diag.as_tuple(),
                      {"beams_trimmed": split_beams, "removed_length": removed_total})
