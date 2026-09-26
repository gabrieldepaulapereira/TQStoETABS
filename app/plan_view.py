"""Desenho da planta (plotly) e tabelas dos elementos identificados.

- `plan_figure(model, detailed=False)`: previa limpa (pilares/paredes, vigas, lajes);
- `plan_figure(model, detailed=True, ...)`: com nomes de pilares, vigas, lajes e, opcionalmente, nos;
- `element_tables(model)`: DataFrames de pilares, vigas, lajes e nos.
"""

from __future__ import annotations

import math

import pandas as pd

from tqs2etabs.domain.elements import ColumnKind, RectSection
from tqs2etabs.domain.geometry import polygon_area, polygon_centroid
from tqs2etabs.domain.model import StructuralModel

COLORS = {"wall": "#f59e0b", "column": "#fb923c", "beam": "#4f8cff", "slab": "#22d3ee", "node": "#93a0bd",
          "text": "#e8edf7", "grid": "#243055"}


def _slab_polygon(model: StructuralModel, slab):
    return [model.node(e.start_node_id).point for e in slab.edges]


def _beam_mid(model: StructuralModel, beam):
    pts = [model.node(n).point for n in beam.axis]
    if len(pts) == 1:
        return pts[0], 0.0
    lengths = [math.dist((a.x, a.y), (b.x, b.y)) for a, b in zip(pts, pts[1:])]
    half, acc = sum(lengths) / 2, 0.0
    for (a, b), L in zip(zip(pts, pts[1:]), lengths):
        if acc + L >= half and L > 0:
            t = (half - acc) / L
            ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            if ang > 90 or ang <= -90:                      # texto sempre legivel (nao de cabeca para baixo)
                ang -= 180 if ang > 0 else -180
            return type(a)(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)), ang
        acc += L
    return pts[0], 0.0


def plan_figure(model: StructuralModel, title: str = "", *, detailed: bool = False, show_nodes: bool = False,
                show_beams: bool = True, show_slabs: bool = True, show_columns: bool = True, height: int = 560):
    import plotly.graph_objects as go

    fig = go.Figure()
    # lajes primeiro (fundo)
    if show_slabs:
        for s in model.slabs.values():
            pts = _slab_polygon(model, s)
            if len(pts) < 3:
                continue
            fig.add_trace(go.Scatter(
                x=[p.x for p in pts] + [pts[0].x], y=[p.y for p in pts] + [pts[0].y], fill="toself",
                fillcolor="rgba(34,211,238,.07)", mode="lines", line=dict(color=COLORS["slab"], width=1),
                showlegend=False, hoverinfo="text",
                hovertext=f"{s.id} · h={s.thickness * 100:.0f} cm · {polygon_area(pts):.2f} m²"))
            for hole in s.holes:
                fig.add_trace(go.Scatter(x=[p.x for p in hole] + [hole[0].x], y=[p.y for p in hole] + [hole[0].y],
                                         mode="lines", line=dict(color=COLORS["slab"], width=1, dash="dot"),
                                         showlegend=False, hoverinfo="skip"))
    if show_beams:
        for b in model.beams.values():
            pts = [model.node(n).point for n in b.axis]
            seg = b.segments[0] if b.segments else None
            sec = f"{seg.width * 100:.0f}/{seg.depth * 100:.0f}" if seg else ""
            fig.add_trace(go.Scatter(x=[p.x for p in pts], y=[p.y for p in pts], mode="lines",
                                     line=dict(color=COLORS["beam"], width=2), showlegend=False,
                                     hoverinfo="text", hovertext=f"{b.id} {sec}"))
    if show_columns:
        for col in model.columns.values():
            if col.kind_hint == ColumnKind.WALL and col.axes:
                for s in col.axes:
                    fig.add_trace(go.Scatter(x=[s.start.x, s.end.x], y=[s.start.y, s.end.y], mode="lines",
                                             line=dict(color=COLORS["wall"], width=max(3, s.thickness * 14)),
                                             showlegend=False, hoverinfo="text",
                                             hovertext=f"{col.id} parede t={s.thickness * 100:.0f} cm"))
            else:
                out = list(col.outline)
                fig.add_trace(go.Scatter(x=[p.x for p in out] + [out[0].x], y=[p.y for p in out] + [out[0].y],
                                         fill="toself", fillcolor="rgba(251,146,60,.55)", mode="lines",
                                         line=dict(color=COLORS["column"], width=1), showlegend=False,
                                         hoverinfo="text", hovertext=f"{col.id} pilar"))

    if detailed:
        tx, ty, tt, tc, ts = [], [], [], [], []

        def label(p, text, color, size):
            tx.append(p.x); ty.append(p.y); tt.append(text); tc.append(color); ts.append(size)

        if show_slabs:
            for s in model.slabs.values():
                pts = _slab_polygon(model, s)
                if len(pts) >= 3:
                    label(polygon_centroid(pts), f"{s.id}<br>h={s.thickness * 100:.0f}", COLORS["slab"], 11)
        if show_columns:
            for col in model.columns.values():
                label(col.centroid, col.id, COLORS["wall"], 12)
        fig.add_trace(go.Scatter(x=tx, y=ty, mode="text", text=tt, showlegend=False, hoverinfo="skip",
                                 textfont=dict(color=tc, size=ts)))
        if show_beams:
            for b in model.beams.values():
                mid, ang = _beam_mid(model, b)
                fig.add_annotation(x=mid.x, y=mid.y, text=b.id, showarrow=False, textangle=-ang,
                                   font=dict(color=COLORS["beam"], size=10), yshift=7)
        if show_nodes:
            nodes = model.structural_nodes()
            fig.add_trace(go.Scatter(x=[n.x for n in nodes], y=[n.y for n in nodes], mode="markers+text",
                                     marker=dict(color=COLORS["node"], size=4), text=[n.id for n in nodes],
                                     textposition="top right", textfont=dict(color=COLORS["node"], size=8),
                                     showlegend=False, hoverinfo="text",
                                     hovertext=[f"{n.id} ({n.x:.2f}; {n.y:.2f})" for n in nodes]))

    fig.update_layout(title=title or None, height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=COLORS["text"]), margin=dict(l=10, r=10, t=40 if title else 10, b=10),
                      hoverlabel=dict(bgcolor="#121a2e"))
    fig.update_yaxes(scaleanchor="x", scaleratio=1, gridcolor=COLORS["grid"], zeroline=False)
    fig.update_xaxes(gridcolor=COLORS["grid"], zeroline=False)
    return fig


def element_tables(model: StructuralModel) -> dict[str, pd.DataFrame]:
    cols = []
    for c in model.columns.values():
        if isinstance(c.section, RectSection):
            sec = f"{c.section.width * 100:.0f}×{c.section.length * 100:.0f}"
        else:
            sec = "poligonal"
        cols.append({"Pilar": c.id, "Tipo": "parede (shell)" if c.kind_hint == ColumnKind.WALL else "pilar (frame)",
                     "Seção (cm)": sec, "Área (m²)": round(c.area, 3), "Lâminas": len(c.axes) or "",
                     "Status": c.status or "", "fck": c.fck or ""})
    beams = []
    for b in model.beams.values():
        pts = [model.node(n).point for n in b.axis]
        length = sum(math.dist((a.x, a.y), (q.x, q.y)) for a, q in zip(pts, pts[1:]))
        seg = b.segments[0] if b.segments else None
        beams.append({"Viga": b.id, "Seção (cm)": f"{seg.width * 100:.0f}/{seg.depth * 100:.0f}" if seg else "",
                      "Trechos": len(b.segments), "Comprimento (m)": round(length, 2),
                      "Nós": " → ".join(b.axis)})
    slabs = []
    for s in model.slabs.values():
        pts = _slab_polygon(model, s)
        slabs.append({"Laje": s.id, "Espessura (cm)": round(s.thickness * 100, 1),
                      "Área (m²)": round(polygon_area(pts), 2) if len(pts) >= 3 else 0.0,
                      "Vértices": len(pts), "Aberturas": len(s.holes),
                      "Tipo": "escada" if s.is_stair else ("balanço" if s.is_cantilever else "laje")})
    nodes = [{"Nó": n.id, "X (m)": round(n.x, 3), "Y (m)": round(n.y, 3),
              "Papéis": ", ".join(sorted(r.value if hasattr(r, "value") else str(r) for r in n.roles))}
             for n in model.structural_nodes()]
    return {"Pilares": pd.DataFrame(cols), "Vigas": pd.DataFrame(beams), "Lajes": pd.DataFrame(slabs),
            "Nós": pd.DataFrame(nodes)}
