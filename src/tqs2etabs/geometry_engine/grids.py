"""Regra: geracao de grids a partir dos eixos dos pilares (ARCHITECTURE.md 10.4).

- Parede: cada AxisSegment paralelo a X gera um grid Y (na sua ordenada) e cada
  segmento paralelo a Y gera um grid X. Segmentos inclinados nao geram grid (INFO).
- Pilar-frame: grid X e grid Y pelo centroide (se grids_at_frame_columns).
- Opcional: eixos de vigas ortogonais (secondary_grids_from_beams).
Coordenadas ja normalizadas; duplicatas (<= coordinate_cluster) sao unidas.
A nomeacao e independente da geometria (name_grids).
"""

from __future__ import annotations

from dataclasses import replace

from ..domain.config import Config, GridNaming
from ..domain.diagnostics import DiagnosticCollector, Source
from ..domain.elements import ColumnKind, GridLine
from ..domain.model import StructuralModel
from .common import StepResult, fmt, round_to


def _collect(model: StructuralModel, config: Config) -> dict[str, dict[float, set[str]]]:
    dec = config.tolerances.rounding_decimals
    cand: dict[str, dict[float, set[str]]] = {"X": {}, "Y": {}}

    def add(direction: str, coord: float, origin: str) -> None:
        cand[direction].setdefault(round_to(coord, dec), set()).add(origin)

    for col in model.columns.values():
        if col.kind_hint == ColumnKind.WALL:
            for seg in col.axes:
                if seg.direction == "Y":
                    add("X", seg.start.x, col.id)
                elif seg.direction == "X":
                    add("Y", seg.start.y, col.id)
        elif config.grids.grids_at_frame_columns:
            c = col.centroid
            add("X", c.x, col.id)
            add("Y", c.y, col.id)
    if config.grids.secondary_grids_from_beams:
        for b in model.beams.values():
            for k in range(len(b.axis) - 1):
                a, c = model.node(b.axis[k]).point, model.node(b.axis[k + 1]).point
                if abs(a.y - c.y) < 1e-9:
                    add("Y", a.y, b.id)
                elif abs(a.x - c.x) < 1e-9:
                    add("X", a.x, b.id)
    return cand


def _dedupe(values: dict[float, set[str]], tol: float) -> list[tuple[float, set[str]]]:
    out: list[tuple[float, set[str]]] = []
    for v in sorted(values):
        if out and v - out[-1][0] <= tol:
            out[-1] = (out[-1][0], out[-1][1] | values[v])
        else:
            out.append((v, set(values[v])))
    return out


def _letters(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA ..."""
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def grid_label(style: str, prefix: str, index: int, start: int) -> str:
    if style == "letters":
        return _letters(index)
    if style == "numbers":
        return str(start + index)
    return f"{prefix}{start + index}"


def name_grids(lines: list[tuple[str, float, tuple[str, ...]]], naming: GridNaming) -> tuple[GridLine, ...]:
    """lines: (direcao, coordenada, origens) -> GridLine nomeados por direcao, em ordem crescente.
    Estilos: letters (A, B, ...), numbers (1, 2, ...), prefix (X1, X2, ...)."""
    out = []
    styles = {"X": naming.x_style, "Y": naming.y_style}
    prefixes = {"X": naming.x_prefix, "Y": naming.y_prefix}
    for direction in ("X", "Y"):
        for i, (d, coord, origins) in enumerate(sorted((l for l in lines if l[0] == direction), key=lambda l: l[1])):
            label = grid_label(styles[d], prefixes[d], i, naming.start_index)
            out.append(GridLine(f"G-{label}", label, d, coord, origins))
    return tuple(out)


def generate_grids(model: StructuralModel, config: Config) -> StepResult:
    diag = DiagnosticCollector()
    tol = config.tolerances.coordinate_cluster
    cand = _collect(model, config)
    lines: list[tuple[str, float, tuple[str, ...]]] = []
    for direction in ("X", "Y"):
        for coord, origins in _dedupe(cand[direction], tol):
            lines.append((direction, coord, tuple(sorted(origins))))
    grids = name_grids(lines, config.grids)

    covered = {o for g in grids for o in g.origin_column_ids}
    for col in model.columns.values():
        if col.id not in covered:
            diag.warning("GRID-W-COLUMN-WITHOUT-GRID", f"Pilar {col.id} nao gerou nenhum grid "
                         f"(eixos inclinados ou sem eixo)", Source.ENGINE, refs=(col.id,))
    nx = sum(1 for g in grids if g.direction == "X")
    ny = len(grids) - nx
    diag.info("GRID-I-SUMMARY", f"Grids: X={nx} Y={ny}: " +
              ", ".join(f"{g.label}={fmt(g.coordinate)}" for g in grids), Source.ENGINE)
    new_model = replace(model, grids=grids, diagnostics=model.diagnostics + diag.as_tuple())
    return StepResult("generate_grids", new_model, (), diag.as_tuple(), {"x": nx, "y": ny})
