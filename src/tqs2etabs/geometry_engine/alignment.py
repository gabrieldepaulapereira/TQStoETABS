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
        # canto de L/U sem toco: a lamina termina no cruzamento dos eixos, e a viga que chegava na regiao
        # do canto (ate meia espessura alem da ponta) vai para o proprio canto
        corner_slack = max(s.thickness for s in col.axes) / 2 + snap_tol
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
                if t < -corner_slack or t > seg.length + corner_slack:
                    continue
                # alem da ponta (regiao do canto): a extremidade vai ate a linha do eixo; o deslocamento
                # lateral da viga ate a ponta e feito depois pela regra da ponta da parede (snap_beams_to_wall_ends)
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
    """Extremidades reais das linhas de eixo: pontos de um unico segmento, ou canto em L (dois segmentos
    nao colineares). Como os cantos nao tem mais toco, o cruzamento dos eixos e a ponta da parede para a
    viga que chega de fora. Juncoes em T (3+ segmentos) e emendas colineares continuam sendo interiores."""
    ends = []
    for s in col.axes:
        for p in (s.start, s.end):
            touching = [t for t in col.axes if t.start.distance_to(p) <= tol or t.end.distance_to(p) <= tol]
            if len(touching) == 1:
                ends.append(p)
            elif len(touching) == 2:
                a, b = (_seg_dir(t) for t in touching)
                if a is not None and b is not None and abs(a[0] * b[1] - a[1] * b[0]) > 1e-6:
                    if all(p.distance_to(q) > tol for q in ends):
                        ends.append(p)
    return ends


def snap_beams_to_wall_ends(model: StructuralModel, config: Config) -> StepResult:
    """Viga que encontra o eixo de uma parede a menos de wall_end_snap da ponta dessa parede e deslocada
    transversalmente ate passar pela ponta ("no do pilar"), evitando o dente entre o fim da parede e a viga.

    A viga **nunca e inclinada**: o deslocamento e rigido e decidido por alinhamento — vigas colineares que
    compartilham nos (V12-V13 na mesma linha de fachada) andam juntas. Entre os deslocamentos possiveis
    (nenhum, ou o que leva a cada ponta candidata) escolhe-se o que mantem mais apoios em parede conectados
    (no sobre o eixo da parede), depois o que alcanca mais pontas, depois o menor. Apoio que nao esta perto
    de ponta nenhuma (viga correndo sobre a parede) nunca pode sair do eixo. Ponta nao alcancada: a viga
    termina no alinhamento do pilar (onde a extensao ja a deixou)."""
    tolc = config.tolerances
    dec = tolc.rounding_decimals
    tol = tolc.node_merge
    diag = DiagnosticCollector()
    ends_cache = {cid: _wall_true_ends(c, tol)
                  for cid, c in model.columns.items() if c.kind_hint == ColumnKind.WALL and c.axes}

    # ---------------------------------------------------------------- dados por viga reta
    info: dict[str, tuple[tuple[float, float], tuple[float, float], float]] = {}   # viga -> (u, nrm, offset)
    for beam in model.beams.values():
        if len(beam.axis) < 2:
            continue
        a, b = model.node(beam.axis[0]).point, model.node(beam.axis[-1]).point
        u = _dir(a, b)
        if u is None:
            continue
        nrm = (-u[1], u[0])
        if any(abs((model.node(n).point.x - a.x) * nrm[0] + (model.node(n).point.y - a.y) * nrm[1]) > tol
               for n in beam.axis):
            continue                                   # poligonal/curva: fora da regra
        info[beam.id] = (u, nrm, a.x * nrm[0] + a.y * nrm[1])

    # ---------------------------------------------------------------- alinhamentos (vigas colineares ligadas)
    parent = {bid: bid for bid in info}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_node: dict[str, list[str]] = {}
    for bid in info:
        for nid in model.beams[bid].axis:
            by_node.setdefault(nid, []).append(bid)
    for bids in by_node.values():
        for i in range(len(bids)):
            for j in range(i + 1, len(bids)):
                (u1, n1, c1), (u2, _, _) = info[bids[i]], info[bids[j]]
                if abs(u1[0] * u2[1] - u1[1] * u2[0]) > 1e-6:
                    continue                           # perpendiculares: nao sao o mesmo alinhamento
                p2 = model.node(model.beams[bids[j]].axis[0]).point
                if abs(p2.x * n1[0] + p2.y * n1[1] - c1) <= tol:
                    parent[find(bids[i])] = find(bids[j])
    groups: dict[str, list[str]] = {}
    for bid in info:
        groups.setdefault(find(bid), []).append(bid)

    # ---------------------------------------------------------------- decisao por alinhamento
    node_targets: dict[str, list[tuple[tuple[float, float], str, str]]] = {}
    beam_shift: dict[str, tuple[float, str]] = {}
    for bids in groups.values():
        u, nrm, _ = info[bids[0]]
        supports: dict[tuple[str, str], Point] = {}           # (no, pilar) -> ponto
        cands: list[tuple[float, str, str]] = []              # (deslocamento, pilar, no)
        for bid in bids:
            for sup in model.beams[bid].supports:
                if sup.kind != SupportKind.COLUMN or sup.ref_id not in ends_cache:
                    continue
                p = model.node(sup.node_id).point
                supports[(sup.node_id, sup.ref_id)] = p
                for e in ends_cache[sup.ref_id]:
                    if p.distance_to(e) > tolc.wall_end_snap:
                        continue
                    if abs((e.x - p.x) * u[0] + (e.y - p.y) * u[1]) > tol:
                        continue                               # ponta na direcao da viga: nao e caso de dente
                    cands.append(((e.x - p.x) * nrm[0] + (e.y - p.y) * nrm[1], sup.ref_id, sup.node_id))
        if not cands:
            continue
        cand_nodes = {(nid, cid) for _, cid, nid in cands}

        def on_wall(key: tuple[str, str], s: float) -> bool:
            p = supports[key]
            q = Point(p.x + s * nrm[0], p.y + s * nrm[1])
            return min(_dist_to_segment(q, seg) for seg in model.columns[key[1]].axes) <= tol

        def score(s: float) -> tuple[int, int, float] | None:
            if any(not on_wall(k, s) for k in supports if k not in cand_nodes and on_wall(k, 0.0)):
                return None                                    # viga correndo sobre parede: nao sai do eixo
            connected = sum(1 for k in supports if on_wall(k, s))
            reached = sum(1 for c, _, _ in cands if abs(c - s) <= tol)
            return (connected, reached, -abs(s))

        options = {0.0} | {round(c, 6) for c, _, _ in cands}
        scored = [(sc, s) for s in options if (sc := score(s)) is not None]
        if not scored:
            continue
        best_score, best = max(scored)
        if abs(best) < 1e-9:
            missed = sorted({cid for c, cid, _ in cands})
            diag.info("ALIGN-I-WALL-END-KEPT", f"{'/'.join(sorted(bids))}: pontas de {', '.join(missed)} nao alcancadas "
                      "sem inclinar a viga; viga mantida no alinhamento", Source.ENGINE, refs=tuple(sorted(bids)))
            continue
        refs = sorted({cid for c, cid, _ in cands if abs(c - best) <= tol})
        missed = sorted({cid for c, cid, _ in cands if abs(c - best) > tol} - set(refs))
        for bid in bids:
            beam_shift[bid] = (best, "/".join(refs))
            vec = (best * nrm[0], best * nrm[1])
            for nid in model.beams[bid].axis:
                node_targets.setdefault(nid, []).append((vec, refs[0], bid))
        if missed:
            diag.info("ALIGN-I-WALL-END-MISSED", f"{'/'.join(sorted(bids))}: deslocada {fmt(best)} m ate a ponta de "
                      f"{', '.join(refs)}; ponta de {', '.join(missed)} nao alcancada sem inclinar a viga -> viga termina "
                      "no alinhamento do pilar", Source.ENGINE, refs=tuple(sorted(bids)) + tuple(missed))

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
