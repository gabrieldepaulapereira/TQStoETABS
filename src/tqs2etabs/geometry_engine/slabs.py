"""Regras de contorno de lajes (feedback da importacao no ETABS, itens A/B/D):

1. snap_slab_vertices_to_column_axes — vertice de laje cujo bordo esta sobre um pilar (P k)
   vai para a linha media da parede (a laje encosta na wall; sem vazio entre elas).
2. absorb_offset_slabs — laje rebaixada/em balanco que compartilha bordos livres com uma
   laje-mae e absorvida por ela (tudo no nivel do piso, decisao 18.5).
3. simplify_slab_outlines — reentrancia de bordo livre vira contorno reto pelo cruzamento
   das linhas de apoio vizinhas (viga x eixo do pilar); o vazio removido vira abertura
   (Slab.holes) quando maior que min_opening_area. Espigoes colineares e vertices
   colineares que so pertencem a laje sao removidos.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import ChangeRecord, DiagnosticCollector, Source
from ..domain.elements import (AxisSegment, ColumnKind, EdgeSupport, Node, NodeRole, Provenance,
                               Slab, SlabEdge)
from ..domain.geometry import Point, Polygon, polygon_area, polygon_area_signed
from ..domain.model import StructuralModel
from .common import StepResult, fmt, fmt_pt, round_to

RULE_VERTEX = "slab-to-column-axis"
RULE_ABSORB = "slab-absorb-offset"
RULE_SIMPLIFY = "slab-outline-simplify"


def _unit(a: Point, b: Point) -> tuple[float, float] | None:
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else None


def _line_intersection(p: Point, u, q: Point, v) -> Point | None:
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) < 1e-9:
        return None
    wx, wy = q.x - p.x, q.y - p.y
    t = (wx * v[1] - wy * v[0]) / den
    return Point(p.x + u[0] * t, p.y + u[1] * t)


def _project_on_line(p: Point, origin: Point, u) -> Point:
    t = (p.x - origin.x) * u[0] + (p.y - origin.y) * u[1]
    return Point(origin.x + u[0] * t, origin.y + u[1] * t)


def _nearest_segment_to_edge(a: Point, b: Point, segments: tuple[AxisSegment, ...]) -> AxisSegment | None:
    """Segmento de eixo mais proximo do ponto medio do bordo, preferindo os paralelos ao bordo."""
    u = _unit(a, b)
    mid = Point((a.x + b.x) / 2, (a.y + b.y) / 2)
    best = None
    for s in segments:
        v = _unit(s.start, s.end)
        if v is None:
            continue
        parallel = u is not None and abs(u[0] * v[1] - u[1] * v[0]) < 1e-6
        perp = abs((mid.x - s.start.x) * v[1] - (mid.y - s.start.y) * v[0])
        key = (0 if parallel else 1, perp)
        if best is None or key < best[0]:
            best = (key, s)
    return best[1] if best else None


# ------------------------------------------------------------- (1) vertices

def snap_slab_vertices_to_column_axes(model: StructuralModel, config: Config) -> StepResult:
    tolc = config.tolerances
    dec = tolc.rounding_decimals
    diag = DiagnosticCollector()
    targets: dict[str, list[tuple[Point, str, str]]] = {}
    for slab in model.slabs.values():
        n = len(slab.edges)
        for k, e in enumerate(slab.edges):
            prev = slab.edges[k - 1]
            vid = e.start_node_id
            col_edges = [x for x in (prev, e) if x.support == EdgeSupport.COLUMN and x.ref_id in model.columns]
            if not col_edges:
                continue
            p = model.node(vid).point
            lines = []
            for ce in col_edges:
                col = model.columns[ce.ref_id]
                if col.kind_hint != ColumnKind.WALL or not col.axes:
                    continue
                a, b = model.node(ce.start_node_id).point, model.node(ce.end_node_id).point
                seg = _nearest_segment_to_edge(a, b, col.axes)
                if seg is None:
                    continue
                v = _unit(seg.start, seg.end)
                perp = abs((p.x - seg.start.x) * v[1] - (p.y - seg.start.y) * v[0])
                if perp <= seg.thickness / 2 + tolc.beam_column_snap:
                    lines.append((seg, v, ce.ref_id))
            if not lines:
                continue
            if len(lines) == 2 and abs(lines[0][1][0] * lines[1][1][1] - lines[0][1][1] * lines[1][1][0]) > 1e-6:
                t = _line_intersection(lines[0][0].start, lines[0][1], lines[1][0].start, lines[1][1])
            else:
                t = _project_on_line(p, lines[0][0].start, lines[0][1])
            if t is None:
                continue
            t = Point(round_to(t.x, dec), round_to(t.y, dec))
            if t.distance_to(p) > 1e-9:
                targets.setdefault(vid, []).append((t, lines[0][2], slab.id))

    nodes = dict(model.nodes)
    changes: list[ChangeRecord] = []
    moved: dict[str, float] = {}
    for vid, props in targets.items():
        pts = {(t.x, t.y) for t, _, _ in props}
        if len(pts) > 1:
            diag.warning("SLAB-W-VERTEX-CONFLICT", f"No {vid}: alvos conflitantes {[fmt_pt(t) for t, _, _ in props]}",
                         Source.ENGINE, refs=(vid,))
            continue
        t, col_id, slab_id = props[0]
        n = nodes[vid]
        if NodeRole.BEAM_AXIS in n.roles:
            if not _on_beam_lines(vid, t, model, tolc.node_merge):
                diag.warning("SLAB-W-VERTEX-ON-BEAM", f"No {vid} (viga) esta a {fmt(n.point.distance_to(t))} m do eixo de "
                             f"{col_id}; alvo fora da linha da viga; nao movido", Source.ENGINE, refs=(vid, col_id))
                continue
        changes.append(ChangeRecord(vid, "xy", fmt_pt(n.point), fmt_pt(t), f"Vertice de {slab_id} levado ao eixo "
                                    f"do pilar {col_id}", RULE_VERTEX, "snap_slab_vertices_to_column_axes",
                                    None, col_id))
        moved[vid] = n.point.distance_to(t)
        nodes[vid] = replace(n, x=t.x, y=t.y)
    diag.info("SLAB-I-VERTICES", f"{len(changes)} vertices de laje levados a linha media de paredes", Source.ENGINE)
    new_model = replace(model, nodes=nodes, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("snap_slab_vertices_to_column_axes", new_model, tuple(changes), diag.as_tuple(),
                      {"nodes_moved": len(changes), "moved": moved})


def _on_beam_lines(vid: str, target: Point, model: StructuralModel, tol: float) -> bool:
    """O alvo esta sobre a linha de todas as vigas que passam pelo no (deslizamento ao longo da viga)."""
    for b in model.beams.values():
        if vid not in b.axis:
            continue
        k = b.axis.index(vid)
        other = b.axis[k + 1] if k + 1 < len(b.axis) else b.axis[k - 1]
        p, q = model.node(vid).point, model.node(other).point
        u = _unit(p, q)
        if u is None:
            return False
        if abs((target.x - p.x) * u[1] - (target.y - p.y) * u[0]) > tol:
            return False
    return True


# ------------------------------------------------------------- (2) absorcao

def _edge_key(e: SlabEdge) -> frozenset[str]:
    return frozenset((e.start_node_id, e.end_node_id))


def _absorb(parent: Slab, child: Slab) -> Slab | None:
    """Substitui, no anel de `parent`, a cadeia (contigua) de bordos LIVRES compartilhada com
    `child` pelo restante do anel de `child`. Devolve o novo parent ou None se nao aplicavel."""
    child_keys = {_edge_key(e) for e in child.edges}
    n = len(parent.edges)
    shared_set = {k for k, e in enumerate(parent.edges)
                  if e.support == EdgeSupport.FREE and _edge_key(e) in child_keys}
    if not shared_set:
        return None
    starts = [k for k in shared_set if (k - 1) % n not in shared_set]
    if len(starts) != 1:
        return None                      # cadeia nao contigua (ou anel inteiro)
    chain = []
    k = starts[0]
    while k in shared_set and len(chain) < n:
        chain.append(k)
        k = (k + 1) % n
    a = parent.edges[chain[0]].start_node_id
    b = parent.edges[chain[-1]].end_node_id
    shared_keys = {_edge_key(parent.edges[c]) for c in chain}
    adj: dict[str, list[SlabEdge]] = {}
    for e in child.edges:
        if _edge_key(e) in shared_keys:
            continue
        adj.setdefault(e.start_node_id, []).append(e)
        adj.setdefault(e.end_node_id, []).append(e)
    path: list[SlabEdge] = []              # b -> ... -> a pelo anel de child
    cur, prev_key = b, None
    while cur != a and len(path) <= len(child.edges):
        nxt = [e for e in adj.get(cur, []) if _edge_key(e) != prev_key]
        if len(nxt) != 1:
            return None
        e = nxt[0]
        other = e.end_node_id if e.start_node_id == cur else e.start_node_id
        path.append(SlabEdge(cur, other, e.support, e.ref_id))
        prev_key = _edge_key(e)
        cur = other
    if cur != a:
        return None
    new_edges = [SlabEdge(e.end_node_id, e.start_node_id, e.support, e.ref_id) for e in reversed(path)]
    after = [(k + i) % n for i in range(n - len(chain))]        # bordos apos a cadeia, em ordem ciclica
    return replace(parent, edges=tuple(new_edges + [parent.edges[i] for i in after]))


def absorb_offset_slabs(model: StructuralModel, config: Config) -> StepResult:
    diag = DiagnosticCollector()
    changes: list[ChangeRecord] = []
    if not config.policy.absorb_offset_slabs:
        return StepResult("absorb_offset_slabs", model, (), (), {"absorbed": 0})
    slabs = dict(model.slabs)
    absorbed: list[str] = []
    changed = True
    while changed:
        changed = False
        for cid, child in list(slabs.items()):
            if not (child.is_cantilever or abs(child.top_offset) > 1e-9):
                continue
            for pid, parent in list(slabs.items()):
                if pid == cid or parent.is_stair:
                    continue
                merged = _absorb(parent, child)
                if merged is None:
                    continue
                slabs[pid] = merged
                del slabs[cid]
                absorbed.append(cid)
                changes.append(ChangeRecord(cid, "slab", f"{len(child.edges)} bordos, h={fmt(child.thickness)}",
                                            f"absorvida por {pid} (h={fmt(parent.thickness)})",
                                            "Laje rebaixada/em balanco incorporada a laje-mae (DFS ignorado)",
                                            RULE_ABSORB, "absorb_offset_slabs", None, pid))
                if abs(child.thickness - parent.thickness) > 1e-9:
                    diag.info("SLAB-I-THICKNESS", f"{cid} (h={fmt(child.thickness)}) passa a ter a espessura de "
                              f"{pid} (h={fmt(parent.thickness)})", Source.ENGINE, refs=(cid, pid))
                changed = True
                break
            if changed:
                break
    diag.info("SLAB-I-ABSORBED", f"{len(absorbed)} lajes rebaixadas/em balanco absorvidas: {', '.join(absorbed)}",
              Source.ENGINE)
    new_model = replace(model, slabs=slabs, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("absorb_offset_slabs", new_model, tuple(changes), diag.as_tuple(), {"absorbed": len(absorbed)})


# --------------------------------------------------------- (3) simplificacao

def _support_line(e: SlabEdge, model: StructuralModel) -> tuple[Point, tuple[float, float]] | None:
    a, b = model.node(e.start_node_id).point, model.node(e.end_node_id).point
    if e.support == EdgeSupport.COLUMN and e.ref_id in model.columns:
        col = model.columns[e.ref_id]
        seg = _nearest_segment_to_edge(a, b, col.axes) if col.axes else None
        if seg is not None:
            v = _unit(seg.start, seg.end)
            return (seg.start, v) if v else None
    u = _unit(a, b)
    return (a, u) if u else None


def _remove_spikes_and_collinear(edges: list[SlabEdge], model: StructuralModel, nodes: dict[str, Node],
                                 tol: float) -> tuple[list[SlabEdge], list[str]]:
    """Remove (a) vertices repetidos, (b) espigoes (ida e volta colineares) e (c) vertices
    colineares que so pertencem a laje. Devolve tambem os ids removidos."""
    removed: list[str] = []
    changed = True
    while changed and len(edges) > 3:
        changed = False
        n = len(edges)
        for k in range(n):
            e0, e1 = edges[k - 1], edges[k]
            v = e1.start_node_id
            p0, p1, p2 = nodes[e0.start_node_id].point, nodes[v].point, nodes[e1.end_node_id].point
            if p1.distance_to(p0) <= tol:
                edges[k - 1] = SlabEdge(e0.start_node_id, e1.end_node_id, e1.support, e1.ref_id)
                del edges[k]
                removed.append(v)
                changed = True
                break
            u0, u1 = _unit(p0, p1), _unit(p1, p2)
            if u0 is None or u1 is None:
                continue
            cross = u0[0] * u1[1] - u0[1] * u1[0]
            dot = u0[0] * u1[0] + u0[1] * u1[1]
            spike = abs(cross) < 1e-6 and dot < 0
            plain_vertex = nodes[v].roles <= {NodeRole.SLAB_VERTEX, NodeRole.ORPHAN}
            collinear = abs(cross) < 1e-6 and dot > 0 and plain_vertex and e0.support == e1.support and e0.ref_id == e1.ref_id
            if spike or collinear:
                sup = e0 if spike and p0.distance_to(p2) >= p1.distance_to(p2) else e1
                edges[k - 1] = SlabEdge(e0.start_node_id, e1.end_node_id, sup.support, sup.ref_id)
                del edges[k]
                removed.append(v)
                changed = True
                break
    return edges, removed


def simplify_slab_outlines(model: StructuralModel, config: Config) -> StepResult:
    tolc = config.tolerances
    dec = tolc.rounding_decimals
    diag = DiagnosticCollector()
    changes: list[ChangeRecord] = []
    nodes = dict(model.nodes)
    slabs = dict(model.slabs)
    story = next(iter(model.stories))
    next_id = max((int("".join(ch for ch in n if ch.isdigit()) or 0) for n in nodes), default=0) + 1
    openings_total = 0

    def node_at(p: Point, slab_id: str) -> str:
        nonlocal next_id
        for n in nodes.values():
            if n.is_structural and n.point.distance_to(p) <= tolc.node_merge:
                return n.id
        nid = f"N{next_id}"
        next_id += 1
        nodes[nid] = Node(nid, p.x, p.y, model.stories[story].elevation or 0.0, story,
                          frozenset({NodeRole.SLAB_VERTEX}), Provenance("engine", nid, {"created_for": slab_id}))
        changes.append(ChangeRecord(nid, "xy", "-", fmt_pt(p), f"Vertice criado no cruzamento das linhas de apoio de "
                                    f"{slab_id}", RULE_SIMPLIFY, "simplify_slab_outlines"))
        return nid

    if not config.policy.simplify_slab_outlines:
        return StepResult("simplify_slab_outlines", model, (), (), {"openings": 0})

    for sid, slab in list(slabs.items()):
        edges = list(slab.edges)
        holes = list(slab.holes)
        edges, removed = _remove_spikes_and_collinear(edges, model, nodes, tolc.node_merge)
        edges = _flatten_small_dents(sid, edges, nodes, tolc.slab_dent_flatten_max, tolc.node_merge, changes)
        edges, removed2 = _remove_spikes_and_collinear(edges, model, nodes, tolc.node_merge)
        removed += removed2
        guard = 0
        while guard < 50:
            guard += 1
            n = len(edges)
            free_idx = [k for k, e in enumerate(edges) if e.support == EdgeSupport.FREE]
            if not free_idx or len(free_idx) == n:
                break
            # cadeia maxima de bordos livres comecando apos um bordo nao livre
            start = next(k for k in free_idx if edges[(k - 1) % n].support != EdgeSupport.FREE)
            chain = []
            k = start
            while edges[k % n].support == EdgeSupport.FREE and len(chain) < n:
                chain.append(k % n)
                k += 1
            a_id = edges[chain[0]].start_node_id
            b_id = edges[chain[-1]].end_node_id
            prev_e, next_e = edges[(chain[0] - 1) % n], edges[(chain[-1] + 1) % n]
            lp, ln = _support_line(prev_e, model_with(model, nodes)), _support_line(next_e, model_with(model, nodes))
            chain_pts = [nodes[edges[c].start_node_id].point for c in chain] + [nodes[b_id].point]
            ring_ccw = polygon_area_signed([nodes[e.start_node_id].point for e in edges]) > 0
            corner = None
            if lp and ln:
                corner = _line_intersection(lp[0], lp[1], ln[0], ln[1])
            if corner is not None:
                xs = [p.x for p in chain_pts]
                ys = [p.y for p in chain_pts]
                slack = tolc.wall_end_snap
                if not (min(xs) - slack <= corner.x <= max(xs) + slack and min(ys) - slack <= corner.y <= max(ys) + slack):
                    corner = None
            if corner is not None:
                corner = Point(round_to(corner.x, dec), round_to(corner.y, dec))
                notch = chain_pts + [corner]
                new_chain_pts = [nodes[a_id].point, corner, nodes[b_id].point]
            else:
                notch = chain_pts
                new_chain_pts = [nodes[a_id].point, nodes[b_id].point]
            notch_area = polygon_area_signed(notch)
            concave = (notch_area < 0) == ring_ccw
            if abs(notch_area) < 1e-9 or not concave:
                # bordo livre reto ou saliencia convexa: nao mexer; marca como tratado
                edges = _mark_chain(edges, chain)
                continue
            # substituir a cadeia
            if corner is not None:
                c_id = node_at(corner, sid)
                new_edges = [SlabEdge(a_id, c_id, prev_e.support, prev_e.ref_id),
                             SlabEdge(c_id, b_id, next_e.support, next_e.ref_id)]
            else:
                new_edges = [SlabEdge(a_id, b_id, prev_e.support, prev_e.ref_id)]
            keep = [edges[i] for i in range(n) if i not in set(chain)]
            # reinsere na posicao da cadeia
            pos = chain[0] if chain[0] < chain[-1] else 0
            if chain[0] < chain[-1]:
                edges = keep[:pos] + new_edges + keep[pos:]
            else:  # cadeia ciclica: keep comeca apos a cadeia
                edges = new_edges + keep
            area = abs(notch_area)
            if area >= tolc.min_opening_area and config.policy.openings == "opening":
                holes.append(tuple(notch))
                openings_total += 1
                what = f"abertura de {fmt(area)} m2"
            else:
                what = f"reentrancia de {fmt(area)} m2 preenchida"
            changes.append(ChangeRecord(sid, "outline", f"cadeia livre {a_id}->{b_id} ({len(chain)} bordos)",
                                        "contorno reto" + (f" por {c_id}" if corner is not None else ""),
                                        f"Contorno alinhado as linhas de apoio; {what}", RULE_SIMPLIFY,
                                        "simplify_slab_outlines"))
            edges, removed2 = _remove_spikes_and_collinear(edges, model, nodes, tolc.node_merge)
            removed += removed2
        edges = [SlabEdge(e.start_node_id, e.end_node_id, EdgeSupport.FREE, None) if e.ref_id == "__free__" else e
                 for e in edges]
        if removed:
            changes.append(ChangeRecord(sid, "vertices", f"{len(slab.edges)} bordos", f"{len(edges)} bordos",
                                        f"Vertices removidos (espigoes/colineares): {', '.join(removed)}",
                                        RULE_SIMPLIFY, "simplify_slab_outlines"))
        slabs[sid] = replace(slab, edges=tuple(edges), holes=tuple(holes))
        if len(edges) > 8:
            diag.warning("SLAB-W-COMPLEX", f"{sid} ainda tem {len(edges)} bordos apos a simplificacao",
                         Source.ENGINE, refs=(sid,))

    # vertices que sairam de todos os contornos (e nao pertencem a vigas/pilares) viram orfaos
    referenced: set[str] = set()
    for sl in slabs.values():
        referenced.update(e.start_node_id for e in sl.edges)
    for b in model.beams.values():
        referenced.update(b.axis)
    for c in model.columns.values():
        referenced.add(c.reference_node_id)
    orphaned = 0
    for nid, nd in list(nodes.items()):
        if nid not in referenced and nd.roles == frozenset({NodeRole.SLAB_VERTEX}):
            nodes[nid] = replace(nd, roles=frozenset({NodeRole.ORPHAN}))
            orphaned += 1
    diag.info("SLAB-I-SIMPLIFIED", f"Contornos simplificados; {openings_total} abertura(s) criada(s); "
              f"{orphaned} vertices descartados", Source.ENGINE)
    new_model = replace(model, nodes=nodes, slabs=slabs, diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("simplify_slab_outlines", new_model, tuple(changes), diag.as_tuple(),
                      {"openings": openings_total, "slabs": len(slabs)})


def _flatten_small_dents(sid: str, edges: list[SlabEdge], nodes: dict[str, Node], dent_max: float,
                         tol: float, changes: list[ChangeRecord]) -> list[SlabEdge]:
    """Bordo livre A->B deslocado de um degrau curto (X->A e B->Y perpendiculares, <= dent_max)
    em relacao aos apoios vizinhos: A vai para X e B para Y. Reentrancia -> a laje cresce;
    saliencia -> a laje encolhe (registrado com a area)."""
    n = len(edges)
    if n < 4:
        return edges
    ring_ccw = polygon_area_signed([nodes[e.start_node_id].point for e in edges]) > 0
    for k in range(n):
        e = edges[k]
        if e.support != EdgeSupport.FREE:
            continue
        prev, nxt = edges[k - 1], edges[(k + 1) % n]
        if prev.support == EdgeSupport.FREE or nxt.support == EdgeSupport.FREE:
            continue
        X, A = nodes[prev.start_node_id].point, nodes[e.start_node_id].point
        B, Y = nodes[e.end_node_id].point, nodes[nxt.end_node_id].point
        if X.distance_to(A) > dent_max or B.distance_to(Y) > dent_max:
            continue
        u = _unit(A, B)
        ua, ub = _unit(X, A), _unit(B, Y)
        if u is None or ua is None or ub is None:
            continue
        if abs(u[0] * ua[0] + u[1] * ua[1]) > 1e-6 or abs(u[0] * ub[0] + u[1] * ub[1]) > 1e-6:
            continue                                  # degraus nao perpendiculares ao bordo
        dent = polygon_area_signed([X, A, B, Y])
        if abs(dent) < 1e-9:
            continue
        concave = (dent < 0) == ring_ccw              # reentrancia (laje cresce) ou saliencia (laje encolhe)
        what = ("reentrancia preenchida" if concave else "saliencia removida") + f" ({fmt(abs(dent))} m2)"
        for vid, target in ((e.start_node_id, X), (e.end_node_id, Y)):
            nd = nodes[vid]
            changes.append(ChangeRecord(vid, "xy", fmt_pt(nd.point), fmt_pt(target),
                                        f"Degrau de {fmt(nd.point.distance_to(target))} m no bordo livre de {sid} "
                                        f"achatado; {what}", RULE_SIMPLIFY, "simplify_slab_outlines", dent_max))
            nodes[vid] = replace(nd, x=target.x, y=target.y)
        return _flatten_small_dents(sid, edges, nodes, dent_max, tol, changes)
    return edges


_TREATED = EdgeSupport.UNKNOWN   # marcador temporario para cadeias livres retas ja analisadas


def _mark_chain(edges: list[SlabEdge], chain: list[int]) -> list[SlabEdge]:
    out = list(edges)
    for k in chain:
        e = out[k]
        out[k] = SlabEdge(e.start_node_id, e.end_node_id, _TREATED, "__free__")
    return out


def model_with(model: StructuralModel, nodes: dict[str, Node]) -> StructuralModel:
    return replace(model, nodes=nodes)
