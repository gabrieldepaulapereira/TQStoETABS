"""Regra: clustering de coordenadas + arredondamento global por alinhamento (ARCHITECTURE.md 10.2).

1. Todas as coordenadas X (e, separadamente, Y) de nos e de geometria de pilares sao
   agrupadas em clusters por varredura ordenada (gap <= coordinate_cluster).
2. Cada cluster recebe um unico representante: media das coordenadas de PILAR se houver
   (prioridade do pilar), senao media de todas; arredondado a `rounding_decimals`.
3. Todos os membros do cluster passam a usar exatamente o representante.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..domain.config import Config
from ..domain.diagnostics import ChangeRecord, DiagnosticCollector, Source
from ..domain.elements import AxisSegment, Column, Node, PolygonSection, RectSection
from ..domain.geometry import Point, Polygon
from ..domain.model import StructuralModel
from .common import StepResult, fmt, round_to

RULE = "coordinate-normalization"


@dataclass(frozen=True, slots=True)
class Cluster:
    axis: str
    values: tuple[float, ...]
    representative: float
    has_column: bool

    @property
    def spread(self) -> float:
        return max(self.values) - min(self.values)


def cluster_values(samples: list[tuple[float, bool]], tol: float, decimals: int) -> list[Cluster]:
    """samples: (valor, veio_de_pilar). Varredura ordenada; gap <= tol une ao cluster corrente."""
    if not samples:
        return []
    ordered = sorted(samples, key=lambda s: s[0])
    groups: list[list[tuple[float, bool]]] = [[ordered[0]]]
    for s in ordered[1:]:
        if s[0] - groups[-1][-1][0] <= tol:
            groups[-1].append(s)
        else:
            groups.append([s])
    out = []
    for g in groups:
        col_vals = [v for v, is_col in g if is_col]
        base = col_vals if col_vals else [v for v, _ in g]
        rep = round_to(sum(base) / len(base), decimals)
        out.append(Cluster("", tuple(sorted({v for v, _ in g})), rep, bool(col_vals)))
    return out


def _column_points(col: Column) -> list[Point]:
    pts: list[Point] = list(col.outline)
    for l in col.laminas:
        pts.extend(l)
    for s in col.axes:
        pts.extend((s.start, s.end))
    if col.section_above:
        pts.extend(col.section_above)
    return pts


def normalize_coordinates(model: StructuralModel, config: Config) -> StepResult:
    tol = config.tolerances.coordinate_cluster
    dec = config.tolerances.rounding_decimals
    diag = DiagnosticCollector()
    changes: list[ChangeRecord] = []

    samples = {"x": [], "y": []}
    for n in model.nodes.values():
        samples["x"].append((n.x, False))
        samples["y"].append((n.y, False))
    for col in model.columns.values():
        for p in _column_points(col):
            samples["x"].append((p.x, True))
            samples["y"].append((p.y, True))

    lookup: dict[str, dict[float, float]] = {"x": {}, "y": {}}
    clusters: dict[str, list[Cluster]] = {}
    for axis in ("x", "y"):
        cl = cluster_values(samples[axis], tol, dec)
        clusters[axis] = [replace(c, axis=axis) for c in cl]
        for c in clusters[axis]:
            for v in c.values:
                lookup[axis][v] = c.representative

    def mapx(v: float) -> float:
        return lookup["x"][v]

    def mapy(v: float) -> float:
        return lookup["y"][v]

    def mappt(p: Point) -> Point:
        return Point(mapx(p.x), mapy(p.y))

    def mappoly(poly: Polygon) -> Polygon:
        return tuple(mappt(p) for p in poly)

    # ---- nos
    nodes: dict[str, Node] = {}
    n_changed = 0
    for nid, n in model.nodes.items():
        nx, ny = mapx(n.x), mapy(n.y)
        if abs(nx - n.x) > 1e-9 or abs(ny - n.y) > 1e-9:
            n_changed += 1
            for attr, before, after in (("x", n.x, nx), ("y", n.y, ny)):
                if abs(before - after) > 1e-9:
                    changes.append(ChangeRecord(nid, attr, round(before, 6), after,
                                                "Coordinate normalization", RULE, "normalize_coordinates", tol))
        nodes[nid] = replace(n, x=nx, y=ny)

    # ---- pilares
    columns: dict[str, Column] = {}
    for cid, col in model.columns.items():
        sec = col.section
        if isinstance(sec, RectSection):
            # so a posicao (canto-origem) e normalizada; L e B do pilar ficam exatos
            new_sec = RectSection(sec.length, sec.width, sec.angle_deg, mappt(sec.polygon[0]))
        else:
            new_sec = PolygonSection(mappoly(sec.outline))
        old_c = col.centroid
        new_col = replace(
            col, section=new_sec,
            laminas=tuple(mappoly(l) for l in col.laminas),
            axes=tuple(AxisSegment(mappt(s.start), mappt(s.end), s.thickness) for s in col.axes),
            section_above=mappoly(col.section_above) if col.section_above else None)
        new_c = new_col.centroid
        if old_c.distance_to(new_c) > 1e-9:
            changes.append(ChangeRecord(cid, "centroid", f"({old_c.x:.6f}, {old_c.y:.6f})",
                                        f"({new_c.x:.4f}, {new_c.y:.4f})", "Coordinate normalization", RULE,
                                        "normalize_coordinates", tol))
        columns[cid] = new_col

    multi = [c for ax in clusters.values() for c in ax if len(c.values) > 1]
    for c in multi:
        if c.spread > tol / 2:
            diag.info("NORM-I-CLUSTER", f"Cluster {c.axis}: {len(c.values)} valores (spread {c.spread*1000:.2f} mm) "
                      f"-> {c.representative}{' [pilar]' if c.has_column else ''}", Source.ENGINE)
    diag.info("NORM-I-SUMMARY", f"{n_changed} nos normalizados; clusters X={len(clusters['x'])} "
              f"Y={len(clusters['y'])} ({len(multi)} com mais de um valor)", Source.ENGINE)

    new_model = replace(model, nodes=nodes, columns=columns,
                        diagnostics=model.diagnostics + diag.as_tuple(),
                        changes=model.changes + tuple(changes))
    return StepResult("normalize_coordinates", new_model, tuple(changes), diag.as_tuple(),
                      {"nodes_changed": n_changed, "clusters_x": len(clusters["x"]),
                       "clusters_y": len(clusters["y"]), "clusters_multi": len(multi),
                       "clusters": clusters})
