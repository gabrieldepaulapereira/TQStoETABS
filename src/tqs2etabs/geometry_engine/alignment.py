"""Regras de alinhamento viga x pilar (ARCHITECTURE.md 10.3). O pilar nunca se move.

(a) snap_beams_transverse: desloca a linha do eixo da viga sobre uma linha de eixo
    de parede paralela quando a distancia perpendicular <= beam_column_snap.
(b) extend_beam_ends: prolonga/recua a extremidade da viga, ao longo do seu proprio
    eixo, ate a linha de eixo do pilar de apoio (linha media da lamina para paredes;
    centroide para pilares-frame). Limite max_end_extension; acima disso -> ERROR.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import ChangeRecord, DiagnosticCollector, Level, Source
from ..domain.elements import AxisSegment, Beam, Column, ColumnKind, Node, SupportKind
from ..domain.geometry import Point
from ..domain.model import StructuralModel
from .common import StepResult, fmt, fmt_pt, round_to

RULE_TRANSVERSE = "column-axis-priority/transverse-snap"
RULE_EXTENSION = "column-axis-priority/end-extension"


def _dir(a: Point, b: Point) -> tuple[float, float] | None:
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return None
    return dx / n, dy / n


def _seg_dir(s: AxisSegment) -> tuple[float, float] | None:
    return _dir(s.start, s.end)


def _signed_perp(p: Point, origin: Point, u: tuple[float, float]) -> float:
    """Distancia perpendicular com sinal de p a linha (origin, u)."""
    wx, wy = p.x - origin.x, p.y - origin.y
    return wx * u[1] - wy * u[0]


def _proj(p: Point, origin: Point, u: tuple[float, float]) -> float:
    return (p.x - origin.x) * u[0] + (p.y - origin.y) * u[1]


def _intersect(p: Point, u, q: Point, v) -> Point | None:
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) < 1e-9:
        return None
    wx, wy = q.x - p.x, q.y - p.y
    t = (wx * v[1] - wy * v[0]) / den
    return Point(p.x + u[0] * t, p.y + u[1] * t)


# ------------------------------------------------------------ (a) transversal

def snap_beams_transverse(model: StructuralModel, config: Config) -> StepResult:
    tol = config.tolerances.beam_column_snap
    dec = config.tolerances.rounding_decimals
    diag = DiagnosticCollector()
    proposals: dict[str, list[tuple[Point, str, str]]] = {}   # node -> [(alvo, pilar, viga)]

    walls = [c for c in model.columns.values() if c.kind_hint == ColumnKind.WALL and c.axes]
    for beam in model.beams.values():
        for k in range(len(beam.axis) - 1):
            a_id, b_id = beam.axis[k], beam.axis[k + 1]
            a, b = model.node(a_id).point, model.node(b_id).point
            u = _dir(a, b)
            if u is None:
                continue
            for col in walls:
                for seg in col.axes:
                    v = _seg_dir(seg)
                    if v is None or abs(u[0] * v[1] - u[1] * v[0]) > 1e-6:
                        continue
                    d = _signed_perp(a, seg.start, v)
                    if abs(d) <= 1e-9 or abs(d) > tol:
                        continue
                    # sobreposicao das projecoes ao longo do eixo
                    pa, pb = sorted((_proj(a, seg.start, v), _proj(b, seg.start, v)))
                    if pb < -tol or pa > seg.length + tol:
                        continue
                    # deslocar perpendicularmente: p' = p - d * n, n = (v_y, -v_x)
                    for nid, p in ((a_id, a), (b_id, b)):
                        target = Point(round_to(p.x - d * v[1], dec), round_to(p.y + d * v[0], dec))
                        proposals.setdefault(nid, []).append((target, col.id, beam.id))

    nodes = dict(model.nodes)
    changes: list[ChangeRecord] = []
    aligned: dict[str, set[str]] = {}
    for nid, props in proposals.items():
        targets = {(round(t.x, 6), round(t.y, 6)) for t, _, _ in props}
        if len(targets) > 1:
            diag.warning("ALIGN-W-CONFLICT", f"No {nid}: alvos transversais conflitantes {sorted(targets)}; nao movido",
                         Source.ENGINE, refs=(nid,) + tuple({c for _, c, _ in props}))
            continue
        target, col_id, beam_id = props[0]
        n = nodes[nid]
        if abs(n.x - target.x) < 1e-9 and abs(n.y - target.y) < 1e-9:
            continue
        changes.append(ChangeRecord(nid, "xy", fmt_pt(n.point), fmt_pt(target),
                                    f"Viga {beam_id} alinhada ao eixo do pilar {col_id}", RULE_TRANSVERSE,
                                    "snap_beams_transverse", tol, col_id))
        nodes[nid] = replace(n, x=target.x, y=target.y)
        aligned.setdefault(beam_id, set()).add(col_id)
    for beam_id, cols in aligned.items():
        diag.info("ALIGN-I-TRANSVERSE", f"{beam_id} -> alinhada transversalmente a {', '.join(sorted(cols))}",
                  Source.ENGINE, refs=(beam_id,), action="beam moved onto column axis")
    new_model = replace(model, nodes=nodes, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("snap_beams_transverse", new_model, tuple(changes), diag.as_tuple(),
                      {"nodes_moved": len(changes), "beams_aligned": len(aligned)})


# ---------------------------------------------------------- (b) longitudinal

def _end_target(node: Point, u: tuple[float, float], col: Column, snap_tol: float,
                dec: int) -> tuple[Point, float, float] | None:
    """Alvo (ponto, distancia ao longo da viga, excentricidade) para uma extremidade apoiada em `col`."""
    best = None
    if col.kind_hint == ColumnKind.WALL and col.axes:
        for seg in col.axes:
            v = _seg_dir(seg)
            if v is None:
                continue
            cross = u[0] * v[1] - u[1] * v[0]
            if abs(cross) < 1e-6:                       # viga paralela/colinear ao eixo
                perp = abs(_signed_perp(node, seg.start, v))
                if perp > snap_tol:
                    continue
                t = min(max(_proj(node, seg.start, v), 0.0), seg.length)
                target = Point(seg.start.x + v[0] * t, seg.start.y + v[1] * t)
                ecc = perp
            else:
                target = _intersect(node, u, seg.start, v)
                if target is None:
                    continue
                t = _proj(target, seg.start, v)
                if t < -snap_tol or t > seg.length + snap_tol:
                    continue
                ecc = 0.0
            d = node.distance_to(target)
            if best is None or d < best[1]:
                best = (Point(round_to(target.x, dec), round_to(target.y, dec)), d, ecc)
        return best
    # pilar-frame: projecao do centroide sobre a linha da viga
    c = col.centroid
    t = _proj(c, node, u)
    target = Point(node.x + u[0] * t, node.y + u[1] * t)
    ecc = c.distance_to(target)
    return (Point(round_to(target.x, dec), round_to(target.y, dec)), abs(t), ecc)


def extend_beam_ends(model: StructuralModel, config: Config) -> StepResult:
    tolc = config.tolerances
    diag = DiagnosticCollector()
    proposals: dict[str, list[tuple[Point, float, str, str]]] = {}   # node -> [(alvo, d, pilar, viga)]

    for beam in model.beams.values():
        if len(beam.axis) < 2:
            continue
        axis = beam.axis
        for sup in beam.supports:
            if sup.kind != SupportKind.COLUMN or sup.node_id not in axis:
                continue
            col = model.columns.get(sup.ref_id or "")
            if col is None:
                diag.error("ALIGN-E-COLUMN-REF", f"{beam.id}: apoio em pilar inexistente {sup.ref_id}",
                           Source.ENGINE, refs=(beam.id,))
                continue
            node = model.node(sup.node_id).point
            k = axis.index(sup.node_id)
            # direcoes a testar: extremidade -> para fora; no interior -> ao longo dos dois trechos vizinhos
            neighbors = [axis[k - 1]] if k == len(axis) - 1 else ([axis[k + 1]] if k == 0 else [axis[k - 1], axis[k + 1]])
            res = None
            for nb in neighbors:
                u = _dir(model.node(nb).point, node) if k in (0, len(axis) - 1) else _dir(node, model.node(nb).point)
                if u is None:
                    continue
                cand = _end_target(node, u, col, tolc.beam_column_snap, tolc.rounding_decimals)
                if cand is not None and (res is None or cand[1] < res[1]):
                    res = cand
            if res is None:
                diag.warning("ALIGN-W-NO-AXIS", f"{beam.id} no {sup.node_id}: nenhuma linha de eixo de {col.id} "
                             "alcancavel na direcao da viga", Source.ENGINE, refs=(beam.id, col.id))
                continue
            target, d, ecc = res
            if ecc > tolc.beam_column_snap:
                diag.warning("ALIGN-W-ECCENTRIC", f"{beam.id} no {sup.node_id}: eixo da viga passa a {fmt(ecc)} m "
                             f"do eixo do pilar-frame {col.id} (junta nao conectada)", Source.ENGINE,
                             refs=(beam.id, col.id), eccentricity=ecc)
            if d > tolc.max_end_extension:
                diag.error("ALIGN-E-EXTENSION-EXCEEDS-MAX",
                           f"{beam.id} no {sup.node_id}: extensao {fmt(d)} m ate o eixo de {col.id} excede "
                           f"max_end_extension={tolc.max_end_extension}; nao corrigida", Source.ENGINE,
                           refs=(beam.id, col.id), distance=d)
                continue
            proposals.setdefault(sup.node_id, []).append((target, d, col.id, beam.id))

    nodes = dict(model.nodes)
    changes: list[ChangeRecord] = []
    extended: dict[str, float] = {}   # node -> distancia movida
    for nid, props in proposals.items():
        targets = {(t.x, t.y) for t, _, _, _ in props}
        if len(targets) > 1:
            diag.warning("ALIGN-W-CONFLICT", f"No {nid}: alvos de extensao conflitantes "
                         f"{[fmt_pt(t) for t, _, _, _ in props]}; nao movido",
                         Source.ENGINE, refs=(nid,) + tuple({b for _, _, _, b in props}))
            continue
        target, d, col_id, beam_id = props[0]
        n = nodes[nid]
        if n.point.distance_to(target) < 1e-9:
            continue
        beams = sorted({b for _, _, _, b in props})
        changes.append(ChangeRecord(nid, "xy", fmt_pt(n.point), fmt_pt(target),
                                    f"Extremidade de {'/'.join(beams)} levada ao eixo do pilar {col_id} "
                                    f"({fmt(d)} m)", RULE_EXTENSION, "extend_beam_ends",
                                    tolc.max_end_extension, col_id))
        nodes[nid] = replace(n, x=target.x, y=target.y)
        extended[nid] = n.point.distance_to(target)
        for b in beams:
            diag.info("ALIGN-I-EXTENDED", f"{b} -> extremidade {nid} estendida {fmt(d)} m ate o eixo de {col_id}",
                      Source.ENGINE, refs=(b, col_id), action="beam end moved to column axis", distance=d)
    new_model = replace(model, nodes=nodes, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("extend_beam_ends", new_model, tuple(changes), diag.as_tuple(),
                      {"nodes_moved": len(changes), "extended": extended,
                       "errors": sum(1 for d in diag.items if d.level == Level.ERROR)})


# ------------------------------------------------------ (c) ponta da parede

RULE_WALL_END = "column-axis-priority/wall-end-snap"


def _dist_to_segment(p: Point, seg: AxisSegment) -> float:
    ax, ay, bx, by = seg.start.x, seg.start.y, seg.end.x, seg.end.y
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    if ll == 0:
        return p.distance_to(seg.start)
    t = max(0.0, min(1.0, ((p.x - ax) * dx + (p.y - ay) * dy) / ll))
    return p.distance_to(Point(ax + t * dx, ay + t * dy))


def _wall_true_ends(col: Column, tol: float) -> list[Point]:
    """Extremidades reais das linhas de eixo (pontos que pertencem a um unico segmento)."""
    pts: list[Point] = []
    for s in col.axes:
        pts.extend((s.start, s.end))
    ends = []
    for p in pts:
        if sum(1 for q in pts if q.distance_to(p) <= tol) == 1:
            ends.append(p)
    return ends


def snap_beams_to_wall_ends(model: StructuralModel, config: Config) -> StepResult:
    """Viga que encontra o eixo de uma parede a menos de wall_end_snap da ponta dessa parede
    e deslocada transversalmente (toda a linha do eixo) ate passar pela ponta ("no do pilar"),
    evitando o dente entre o fim da parede e a viga. Candidatos de varias paredes na mesma
    viga: escolhe-se o deslocamento que minimiza o maior residuo; pontas de junção (canto de
    nucleo) nao contam como ponta."""
    tolc = config.tolerances
    dec = tolc.rounding_decimals
    diag = DiagnosticCollector()
    ends_cache = {cid: _wall_true_ends(c, tolc.node_merge)
                  for cid, c in model.columns.items() if c.kind_hint == ColumnKind.WALL and c.axes}

    node_targets: dict[str, list[tuple[Point, str, str]]] = {}
    beam_shift: dict[str, tuple[float, str]] = {}
    for beam in model.beams.values():
        if len(beam.axis) < 2:
            continue
        a, b = model.node(beam.axis[0]).point, model.node(beam.axis[-1]).point
        u = _dir(a, b)
        if u is None:
            continue
        nrm = (-u[1], u[0])
        # candidatos: (deslocamento perpendicular, pilar, no)
        cands: list[tuple[float, str, str]] = []
        for sup in beam.supports:
            if sup.kind != SupportKind.COLUMN or sup.ref_id not in ends_cache:
                continue
            p = model.node(sup.node_id).point
            for e in ends_cache[sup.ref_id]:
                d = p.distance_to(e)
                if d > tolc.wall_end_snap:
                    continue
                along = abs((e.x - p.x) * u[0] + (e.y - p.y) * u[1])
                if along > tolc.node_merge:      # ponta na direcao da viga: nao e caso de dente
                    continue
                s = (e.x - p.x) * nrm[0] + (e.y - p.y) * nrm[1]
                cands.append((s, sup.ref_id, sup.node_id))
        if not cands:
            continue

        def keeps_supports(s: float) -> bool:
            """Nenhum apoio em pilar da viga pode sair do eixo do seu pilar com o deslocamento."""
            for sup in beam.supports:
                if sup.kind != SupportKind.COLUMN:
                    continue
                col = model.columns.get(sup.ref_id or "")
                if col is None or col.kind_hint != ColumnKind.WALL or not col.axes:
                    continue
                p = model.node(sup.node_id).point
                q = Point(p.x + s * nrm[0], p.y + s * nrm[1])
                d = min(_dist_to_segment(q, seg) for seg in col.axes)
                if d > tolc.node_merge:
                    return False
            return True

        options = [s for s in sorted({round(s, 6) for s, _, _ in cands}, key=abs) if keeps_supports(s)]
        if not options:
            diag.info("ALIGN-I-WALL-END-SKIPPED", f"{beam.id}: deslocamento para ponta de parede tiraria a viga do eixo "
                      "de outro apoio; mantida", Source.ENGINE, refs=(beam.id,))
            continue
        def worst(s: float) -> float:
            return max(abs(s - c[0]) for c in cands)
        best = min(options, key=lambda s: (round(worst(s), 6), abs(s)))
        if abs(best) < 1e-9:
            continue
        ref = next(c[1] for c in cands if abs(c[0] - best) < 1e-9)
        beam_shift[beam.id] = (best, ref)
        for nid in beam.axis:
            node_targets.setdefault(nid, []).append(((best * nrm[0], best * nrm[1]), ref, beam.id))
        if worst(best) > tolc.node_merge:
            diag.warning("ALIGN-W-WALL-END-RESIDUAL", f"{beam.id}: deslocada {fmt(best)} m para a ponta de {ref}; "
                         f"residuo de {fmt(worst(best))} m em outro apoio", Source.ENGINE, refs=(beam.id, ref))

    nodes = dict(model.nodes)
    changes: list[ChangeRecord] = []
    moved: dict[str, float] = {}
    for nid, props in node_targets.items():
        # compoe deslocamentos ortogonais (vigas perpendiculares no mesmo no); conflito se paralelos e diferentes
        total = [0.0, 0.0]
        conflict = False
        for (vx, vy), _, _ in props:
            for (wx, wy), _, _ in props:
                dot = vx * wx + vy * wy
                if abs(dot) > 1e-9 and (abs(vx - wx) > 1e-6 or abs(vy - wy) > 1e-6):
                    conflict = True
        seen: list[tuple[float, float]] = []
        for (vx, vy), _, _ in props:
            if not any(abs(vx - sx) < 1e-6 and abs(vy - sy) < 1e-6 for sx, sy in seen):
                seen.append((vx, vy))
                total[0] += vx
                total[1] += vy
        if conflict:
            diag.warning("ALIGN-W-CONFLICT", f"No {nid}: deslocamentos para ponta de parede conflitantes "
                         f"{[(round(v[0], 3), round(v[1], 3)) for v, _, _ in props]}; nao movido", Source.ENGINE,
                         refs=(nid,) + tuple({b for _, _, b in props}))
            continue
        n = nodes[nid]
        target = Point(round_to(n.x + total[0], dec), round_to(n.y + total[1], dec))
        ref = props[0][1]
        if n.point.distance_to(target) < 1e-9:
            continue
        beams = sorted({b for _, _, b in props})
        changes.append(ChangeRecord(nid, "xy", fmt_pt(n.point), fmt_pt(target),
                                    f"Viga {'/'.join(beams)} deslocada ate a ponta da parede {ref}", RULE_WALL_END,
                                    "snap_beams_to_wall_ends", tolc.wall_end_snap, ref))
        moved[nid] = n.point.distance_to(target)
        nodes[nid] = replace(n, x=target.x, y=target.y)
    for beam_id, (s, ref) in beam_shift.items():
        diag.info("ALIGN-I-WALL-END", f"{beam_id} -> linha do eixo deslocada {fmt(abs(s))} m ate a ponta de {ref}",
                  Source.ENGINE, refs=(beam_id, ref), action="beam moved to wall end node", distance=abs(s))
    new_model = replace(model, nodes=nodes, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("snap_beams_to_wall_ends", new_model, tuple(changes), diag.as_tuple(),
                      {"nodes_moved": len(changes), "beams_moved": len(beam_shift), "moved": moved})
