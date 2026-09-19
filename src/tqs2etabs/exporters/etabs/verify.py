"""Validacao pos-exportacao (ARCHITECTURE.md secao 13): rele o .e2k e confere contra o
modelo intermediario normalizado — contagens, coordenadas, conectividade e secoes."""

from __future__ import annotations

from ...domain.diagnostics import Diagnostic, DiagnosticCollector, Level, Source
from ...domain.model import StructuralModel
from .description import EtabsDescription
from .e2k_reader import E2kModel


def verify_export(model: StructuralModel, desc: EtabsDescription, e2k: E2kModel,
                  coord_tol: float = 0.0005) -> tuple[Diagnostic, ...]:
    diag = DiagnosticCollector()
    V = Source.VALIDATION

    # pontos: todo no estrutural tem ponto com as mesmas coordenadas
    for n in model.structural_nodes():
        pname = desc.node_to_point.get(n.id)
        if pname is None or pname not in e2k.points:
            diag.error("XPT-E-NODE-MISSING", f"No {n.id} sem ponto no E2K", V, refs=(n.id,))
            continue
        x, y = e2k.points[pname]
        if abs(x - n.x) > coord_tol or abs(y - n.y) > coord_tol:
            diag.error("XPT-E-COORD", f"No {n.id}: ({n.x}, {n.y}) x E2K ({x}, {y})", V, refs=(n.id,))

    # contagens
    exp = desc.counts()
    got = {"points": len(e2k.points), "frames": len(e2k.lines), "walls": sum(1 for a in e2k.areas.values() if a[0] == "PANEL"),
           "slabs": sum(1 for n, a in e2k.areas.items() if a[0] == "FLOOR" and n not in e2k.openings),
           "openings": len(e2k.openings), "grids": len(e2k.grids),
           "restraints": len(e2k.restraints)}
    for k, v in got.items():
        if exp[k] != v:
            diag.error("XPT-E-COUNT", f"{k}: descricao {exp[k]} x E2K {v}", V)

    # vigas: cada trecho do modelo e uma LINE BEAM entre os pontos certos, com a secao b/h
    for beam in model.beams.values():
        multi = len(beam.segments) > 1
        for k, seg in enumerate(beam.segments, start=1):
            name = f"{beam.id}-{k}" if multi else beam.id
            ln = e2k.lines.get(name)
            if ln is None:
                diag.error("XPT-E-BEAM-MISSING", f"{name} nao encontrada no E2K", V, refs=(beam.id,))
                continue
            pi, pj = desc.node_to_point[seg.start_node_id], desc.node_to_point[seg.end_node_id]
            if (ln[1], ln[2]) != (pi, pj):
                diag.error("XPT-E-BEAM-CONN", f"{name}: pontos {ln[1:]} x esperado ({pi}, {pj})", V, refs=(beam.id,))
            sec = e2k.line_sections.get(name, "")
            b, h = int(round(seg.width * 100)), int(round(seg.depth * 100))
            if not sec.startswith(f"B{b}X{h}"):
                diag.error("XPT-E-BEAM-SECTION", f"{name}: secao {sec} x esperado B{b}X{h}", V, refs=(beam.id,))

    # paredes: numero de paineis por pilar = numero de linhas de eixo
    for col in model.columns.values():
        if not col.axes:
            continue
        panels = [a for a in e2k.areas if a == col.id or a.startswith(col.id + "-")]
        expected_n = sum(1 for a in desc.areas if a.kind == "PANEL" and a.source == col.id)
        if len(panels) != expected_n or expected_n < len(col.axes):
            diag.error("XPT-E-WALL-COUNT", f"{col.id}: {len(panels)} paineis x {expected_n} esperados", V, refs=(col.id,))
        for pname in panels:
            kind, pts = e2k.areas[pname]
            if kind != "PANEL" or pts[0] != pts[3] or pts[1] != pts[2]:
                diag.error("XPT-E-WALL-CONN", f"{pname}: conectividade de painel invalida {pts}", V, refs=(col.id,))
            if desc.piers and e2k.area_piers.get(pname) != col.id:
                diag.error("XPT-E-WALL-PIER", f"{pname}: pier {e2k.area_piers.get(pname)} x {col.id}", V, refs=(col.id,))

    # lajes: mesmos vertices, mesma ordem
    for slab in model.slabs.values():
        a = e2k.areas.get(slab.id)
        if a is None:
            diag.error("XPT-E-SLAB-MISSING", f"{slab.id} nao encontrada", V, refs=(slab.id,))
            continue
        expected = tuple(desc.node_to_point[e.start_node_id] for e in slab.edges)
        if a[0] != "FLOOR" or a[1] != expected:
            diag.error("XPT-E-SLAB-CONN", f"{slab.id}: vertices diferentes", V, refs=(slab.id,))

    # grids e story
    grids = {(g.label, g.direction, round(g.coordinate, 4)) for g in desc.grids}
    got_g = {(l, d, round(c, 4)) for l, d, c in e2k.grids}
    if grids != got_g:
        diag.error("XPT-E-GRIDS", f"grids diferem: {sorted(grids ^ got_g)}", V)
    st = desc.story
    if abs(e2k.stories.get(st.name, -1) - st.height) > 1e-6:
        diag.error("XPT-E-STORY", f"story {st.name}: altura {e2k.stories.get(st.name)} x {st.height}", V)

    errors = diag.count(Level.ERROR)
    diag.info("XPT-I-SUMMARY", f"Validacao pos-exportacao: {errors} erro(s); "
              + ", ".join(f"{k}={v}" for k, v in got.items()), V)
    return diag.as_tuple()
