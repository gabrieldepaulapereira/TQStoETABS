"""Comparacao TQS (importado) x modelo normalizado (ARCHITECTURE.md secao 14).

Responde: quantos elementos existem, quantos foram mantidos, quantos modificados,
quais e por que — a partir dos snapshots do pipeline e dos ChangeRecords.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.geometry import polyline_length
from ..domain.model import StructuralModel
from ..geometry_engine.common import fmt


@dataclass(frozen=True, slots=True)
class ElementComparison:
    kind: str            # node | column | beam | slab
    element_id: str
    before: str
    after: str
    reasons: tuple[str, ...]

    @property
    def modified(self) -> bool:
        return bool(self.reasons)


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    items: tuple[ElementComparison, ...]
    counts: dict[str, dict[str, int]] = field(default_factory=dict)   # kind -> {tqs, kept, modified, removed}

    def modified(self, kind: str | None = None) -> list[ElementComparison]:
        return [i for i in self.items if i.modified and (kind is None or i.kind == kind)]


def compare(original: StructuralModel, final: StructuralModel) -> ComparisonReport:
    reasons: dict[str, set[str]] = {}
    for c in final.changes:
        reasons.setdefault(c.element_id, set()).add(c.reason.split(" (")[0])

    items: list[ElementComparison] = []
    counts: dict[str, dict[str, int]] = {}

    # nos
    kept = 0
    for nid, n0 in original.nodes.items():
        raw = n0.provenance.raw
        before = f"({raw.get('x_cm', n0.x * 100):.6f}, {raw.get('y_cm', n0.y * 100):.6f}) cm"
        n1 = final.nodes.get(nid)
        if n1 is None:
            items.append(ElementComparison("node", nid, before, "removido (fundido)", tuple(sorted(reasons.get(nid, {"node merge"})))))
            continue
        kept += 1
        after = f"({n1.x:.2f}, {n1.y:.2f}) m"
        r = tuple(sorted(reasons.get(nid, set()))) if n0.point.distance_to(n1.point) > 1e-9 else ()
        items.append(ElementComparison("node", nid, before, after, r))
    counts["node"] = {"tqs": len(original.nodes), "kept": kept,
                      "modified": sum(1 for i in items if i.kind == "node" and i.modified and i.element_id in final.nodes),
                      "removed": len(original.nodes) - kept}

    # pilares
    for cid, c0 in original.columns.items():
        c1 = final.columns.get(cid)
        if c1 is None:
            items.append(ElementComparison("column", cid, "", "removido", ("removed",)))
            continue
        b, a = c0.centroid, c1.centroid
        before = f"centroide ({b.x * 100:.3f}, {b.y * 100:.3f}) cm, A={c0.area:.4f} m2"
        after = f"centroide ({a.x:.2f}, {a.y:.2f}) m, A={c1.area:.4f} m2, {len(c1.axes)} eixo(s)"
        r = tuple(sorted(reasons.get(cid, set())))
        items.append(ElementComparison("column", cid, before, after, r))
    counts["column"] = _count("column", items, original.columns, final.columns)

    # vigas: modificada se algum no do eixo mudou
    node_reasons = reasons
    for bid, b0 in original.beams.items():
        b1 = final.beams.get(bid)
        if b1 is None:
            items.append(ElementComparison("beam", bid, "", "removida", ("removed",)))
            continue
        l0 = polyline_length([original.node(n).point for n in b0.axis])
        l1 = polyline_length([final.node(n).point for n in b1.axis])
        r: set[str] = set()
        for n in b0.axis:
            r |= node_reasons.get(n, set())
        before = f"L={fmt(l0)} m, nos {list(b0.axis)}"
        after = f"L={fmt(l1)} m, nos {list(b1.axis)}"
        items.append(ElementComparison("beam", bid, before, after, tuple(sorted(r))))
    counts["beam"] = _count("beam", items, original.beams, final.beams)

    # lajes
    for sid, s0 in original.slabs.items():
        s1 = final.slabs.get(sid)
        if s1 is None:
            items.append(ElementComparison("slab", sid, "", "removida", ("removed",)))
            continue
        r = set()
        for e in s0.edges:
            r |= node_reasons.get(e.start_node_id, set())
        items.append(ElementComparison("slab", sid, f"{len(s0.edges)} bordos", f"{len(s1.edges)} bordos",
                                       tuple(sorted(r))))
    counts["slab"] = _count("slab", items, original.slabs, final.slabs)
    return ComparisonReport(tuple(items), counts)


def _count(kind: str, items: list[ElementComparison], before: dict, after: dict) -> dict[str, int]:
    return {"tqs": len(before), "kept": sum(1 for k in before if k in after),
            "modified": sum(1 for i in items if i.kind == kind and i.modified and i.element_id in after),
            "removed": sum(1 for k in before if k not in after)}
