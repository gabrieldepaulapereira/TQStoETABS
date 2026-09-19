"""Regra: fusao de nos coincidentes (node merge) e reindexacao das referencias."""

from __future__ import annotations

from dataclasses import replace

from ..domain.config import Config
from ..domain.diagnostics import ChangeRecord, DiagnosticCollector, Source
from ..domain.elements import Beam, BeamSegment, BeamSupport, LoadCase, LoadItem, Node, Slab, SlabEdge
from ..domain.model import StructuralModel
from .common import StepResult, fmt_pt

RULE = "node-merge"


def _node_num(nid: str) -> int:
    digits = "".join(ch for ch in nid if ch.isdigit())
    return int(digits) if digits else 0


def merge_nodes(model: StructuralModel, config: Config) -> StepResult:
    tol = config.tolerances.node_merge
    diag = DiagnosticCollector()
    structural = [n for n in model.nodes.values() if n.is_structural]
    structural.sort(key=lambda n: (-len(n.roles), _node_num(n.id)))

    mapping: dict[str, str] = {}
    survivors: list[Node] = []
    for n in structural:
        target = None
        for s in survivors:
            if n.point.distance_to(s.point) <= tol:
                target = s
                break
        if target is None:
            survivors.append(n)
        else:
            mapping[n.id] = target.id

    if not mapping:
        return StepResult("merge_nodes", model, (), (), {"merged": 0})

    changes: list[ChangeRecord] = []
    nodes: dict[str, Node] = {}
    for n in model.nodes.values():
        if n.id in mapping:
            continue
        merged_roles = set(n.roles)
        for src, dst in mapping.items():
            if dst == n.id:
                merged_roles |= set(model.nodes[src].roles)
        nodes[n.id] = replace(n, roles=frozenset(merged_roles))
    for src, dst in mapping.items():
        changes.append(ChangeRecord(src, "id", src, dst, f"No {src} {fmt_pt(model.node(src).point)} coincide com "
                                    f"{dst} {fmt_pt(model.node(dst).point)}", RULE, "merge_nodes", tol, dst))

    def m(nid: str) -> str:
        return mapping.get(nid, nid)

    beams: dict[str, Beam] = {}
    for b in model.beams.values():
        axis: list[str] = []
        segments: list[BeamSegment] = []
        for k, nid in enumerate(b.axis):
            new = m(nid)
            if axis and axis[-1] == new:
                diag.warning("MERGE-W-SEGMENT-DROPPED", f"{b.id}: trecho {k} colapsou apos fusao de nos",
                             Source.ENGINE, refs=(b.id,))
                continue
            if axis:
                seg = b.segments[k - 1]
                segments.append(replace(seg, start_node_id=axis[-1], end_node_id=new))
            axis.append(new)
        if len(axis) < 2:
            diag.error("MERGE-E-BEAM-COLLAPSED", f"{b.id}: eixo com menos de 2 nos apos fusao", Source.ENGINE,
                       refs=(b.id,))
        seen = set()
        supports = []
        for s in b.supports:
            key = (m(s.node_id), s.kind, s.ref_id)
            if key not in seen:
                seen.add(key)
                supports.append(BeamSupport(*key))
        beams[b.id] = replace(b, axis=tuple(axis), segments=tuple(segments), supports=tuple(supports))

    slabs: dict[str, Slab] = {}
    for s in model.slabs.values():
        edges = [SlabEdge(m(e.start_node_id), m(e.end_node_id), e.support, e.ref_id) for e in s.edges]
        edges = [e for e in edges if e.start_node_id != e.end_node_id]
        if len(edges) < 3:
            diag.error("MERGE-E-SLAB-COLLAPSED", f"{s.id}: menos de 3 bordos apos fusao", Source.ENGINE, refs=(s.id,))
        slabs[s.id] = replace(s, edges=tuple(edges))

    columns = {cid: replace(c, reference_node_id=m(c.reference_node_id)) for cid, c in model.columns.items()}
    load_cases = tuple(
        LoadCase(lc.id, lc.number, lc.description,
                 tuple(LoadItem(i.element_id, i.kind, i.value, i.unit, tuple(m(n) for n in i.node_ids), i.region)
                       for i in lc.items))
        for lc in model.load_cases)

    diag.info("MERGE-I-SUMMARY", f"{len(mapping)} nos fundidos (tol {tol} m)", Source.ENGINE)
    new_model = replace(model, nodes=nodes, beams=beams, slabs=slabs, columns=columns, load_cases=load_cases,
                        diagnostics=model.diagnostics + diag.as_tuple(), changes=model.changes + tuple(changes))
    return StepResult("merge_nodes", new_model, tuple(changes), diag.as_tuple(), {"merged": len(mapping)})
