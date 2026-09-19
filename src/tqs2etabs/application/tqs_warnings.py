"""Mapeia os avisos do TQS (LST) para diagnosticos do conversor, cruzando com o que o
motor geometrico fez (ARCHITECTURE.md secao 12)."""

from __future__ import annotations

import re

from ..domain.diagnostics import Diagnostic, DiagnosticCollector, Level, Source
from ..domain.geometry import distance_point_to_polygon_boundary, point_in_polygon
from ..domain.model import StructuralModel
from ..geometry_engine.common import fmt
from ..importers.tqs.lst.document import LstDocument

_NODE_OFF_COLUMN = re.compile(r"N[oó]\s+(\d+)\s+na viga\s+(\d+)\s+n[aã]o cai sobre o pilar P\s*(\d+)", re.IGNORECASE)
_NOT_LEVEL = re.compile(r"Viga\s+(\d+)\s+e laje\s+(\d+)\s+n[aã]o est[aã]o niveladas", re.IGNORECASE)
_SLAB_LEVEL = re.compile(r"Lajes\s+(\d+)\s+e\s+(\d+)\s+tem engastamento em desnivel", re.IGNORECASE)
_WIDTH = re.compile(r"Viga V\s*(\d+)\s+v[aã]o\s+(\d+)\s+tem largura", re.IGNORECASE)
_FREE_EDGE = re.compile(r"Laje\s+(\d+)\s+bordo livre em concavidade", re.IGNORECASE)


def map_tqs_warnings(lst: LstDocument | None, original: StructuralModel,
                     final: StructuralModel) -> tuple[Diagnostic, ...]:
    if lst is None:
        return ()
    diag = DiagnosticCollector()
    moved = {c.element_id: c for c in final.changes if c.attribute == "xy"}
    for w in lst.warnings:
        txt = w.text
        m = _NODE_OFF_COLUMN.search(txt)
        if m:
            nid, beam, col = f"N{m.group(1)}", f"V{m.group(2)}", f"P{m.group(3)}"
            n0 = original.nodes.get(nid)
            c0 = original.columns.get(col)
            detail = ""
            if n0 and c0:
                d = distance_point_to_polygon_boundary(n0.point, c0.outline)
                inside = point_in_polygon(n0.point, c0.outline)
                detail = f" (no a {d * 1000:.1f} mm {'dentro' if inside else 'fora'} da secao no TQS)"
            n1 = final.nodes.get(nid)
            on_axis = False
            if n1 and col in final.columns and final.columns[col].axes:
                from ..domain.geometry import distance_point_to_segment
                on_axis = min(distance_point_to_segment(n1.point, s.start, s.end)
                              for s in final.columns[col].axes) <= 1e-6
            if nid in moved:
                action = f"corrigido: {moved[nid].reason}"
            elif on_axis:
                action = "resolvido pela normalizacao de coordenadas (no ja sobre o eixo do pilar)"
            else:
                action = "NAO corrigido; verificar"
            diag.add(Level.WARNING if not (nid in moved or on_axis) else Level.INFO, "TQS-W-NODE-OFF-COLUMN",
                     f"Extremidade da viga {beam} (no {nid}) nao coincide com o pilar {col}{detail}",
                     Source.TQS_LST, refs=(beam, col, nid), action=action, tqs_number=w.number)
            continue
        if _NOT_LEVEL.search(txt) or _SLAB_LEVEL.search(txt):
            diag.info("TQS-I-LEVEL", txt, Source.TQS_LST, action="ignorado: desniveis nao modelados (decisao 18.5)",
                      tqs_number=w.number)
            continue
        if _WIDTH.search(txt) or txt.lower().startswith("aumente a largura"):
            diag.info("TQS-I-DESIGN", txt, Source.TQS_LST, action="verificacao de dimensionamento; sem efeito geometrico",
                      tqs_number=w.number)
            continue
        if _FREE_EDGE.search(txt) or "engastamento" in txt.lower() or "engaste" in txt.lower():
            diag.info("TQS-I-SLAB-SUPPORT", txt, Source.TQS_LST, action="vinculacao de laje; sem efeito geometrico",
                      tqs_number=w.number)
            continue
        diag.info("TQS-I-OTHER", txt, Source.TQS_LST, action="sem efeito no conversor", tqs_number=w.number)
    return diag.as_tuple()
