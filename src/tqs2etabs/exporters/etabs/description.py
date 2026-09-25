"""Descricao neutra de um modelo ETABS (o que sera criado), independente do meio
(arquivo .e2k ou API COM). Produzida por `mapping.EtabsMapper`.

Unidades: kN, m, C. Coordenadas em planta; objetos (LINE/AREA) sao definidos uma vez
pelos pontos e atribuidos a um ou mais pavimentos (`assignments`), como no E2K.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .template import TemplatePlan


@dataclass(frozen=True, slots=True)
class EStory:
    name: str
    height: float          # 0 para a base
    elevation: float
    is_base: bool = False
    similar_to: str | None = None    # pavimento tipo: repete o mestre
    master: bool = True


@dataclass(frozen=True, slots=True)
class EGrid:
    label: str
    direction: str         # X | Y
    coordinate: float


@dataclass(frozen=True, slots=True)
class EMaterial:
    name: str              # "C60"
    fck_mpa: float
    e_kn_m2: float
    unit_weight: float     # kN/m3
    poisson: float = 0.2
    thermal: float = 1e-5
    source: str = ""       # "CONCRETO.DAT" | "NBR 6118" ...


@dataclass(frozen=True, slots=True)
class EFrameSection:
    name: str              # "B18X120-C40"
    material: str
    depth: float           # D (m)
    width: float           # B (m)
    kind: str              # Beam | Column


@dataclass(frozen=True, slots=True)
class EShellSection:
    name: str              # "W60-C60", "S20-C40-SH"
    material: str
    thickness: float
    kind: str              # Wall | Slab
    modeling: str          # ShellThin | Membrane


@dataclass(frozen=True, slots=True)
class EPoint:
    name: str
    x: float
    y: float
    source_node: str | None        # "<plan>:<no>" do primeiro uso (None = ponto auxiliar)
    stories: tuple[str, ...] = ()  # pavimentos em que o ponto e usado (POINTASSIGN)


@dataclass(frozen=True, slots=True)
class EAssign:
    story: str
    section: str
    pier: str | None = None


@dataclass(frozen=True, slots=True)
class EFrame:
    name: str
    kind: str              # BEAM | COLUMN
    point_i: str
    point_j: str
    assignments: tuple[EAssign, ...]
    angle: float = 0.0
    cardinal_point: int = 8
    releases: str = ""     # ex.: "M2I M3I M2J M3J"
    source: str = ""       # elemento TQS de origem

    @property
    def story(self) -> str:
        return self.assignments[0].story

    @property
    def section(self) -> str:
        return self.assignments[0].section


@dataclass(frozen=True, slots=True)
class EArea:
    name: str
    kind: str              # PANEL | FLOOR | OPENING (escrito como FLOOR + OPENING "Yes")
    points: tuple[str, ...]
    assignments: tuple[EAssign, ...]
    source: str = ""

    @property
    def story(self) -> str:
        return self.assignments[0].story

    @property
    def section(self) -> str:
        return self.assignments[0].section

    @property
    def pier(self) -> str | None:
        return self.assignments[0].pier


@dataclass(frozen=True, slots=True)
class ELoadPattern:
    name: str
    kind: str              # Dead | Super Dead | Live
    self_weight: float = 0.0
    mass_factor: float = 0.0


@dataclass(frozen=True, slots=True)
class EAreaLoad:
    area: str
    story: str
    pattern: str
    value: float           # kN/m2 (gravidade)
    source: str = ""


@dataclass(frozen=True, slots=True)
class ELineLoad:
    frame: str
    story: str
    pattern: str
    value: float           # kN/m (gravidade)
    source: str = ""


@dataclass(frozen=True, slots=True)
class ERestraint:
    point: str
    story: str
    dofs: str


@dataclass(frozen=True, slots=True)
class EtabsDescription:
    title: str
    units: tuple[str, str, str]
    stories: tuple[EStory, ...]              # de cima para baixo (ordem do E2K)
    grid_system: str
    grids: tuple[EGrid, ...]
    materials: tuple[EMaterial, ...]
    frame_sections: tuple[EFrameSection, ...]
    shell_sections: tuple[EShellSection, ...]
    points: tuple[EPoint, ...]
    frames: tuple[EFrame, ...]
    areas: tuple[EArea, ...]
    restraints: tuple[ERestraint, ...]
    piers: tuple[str, ...]
    notes: tuple[str, ...] = ()
    node_to_point: dict[str, str] = field(default_factory=dict)   # "<plan>:<no>" -> ponto (single: "<no>")
    load_patterns: tuple[ELoadPattern, ...] = ()
    area_loads: tuple[EAreaLoad, ...] = ()
    line_loads: tuple[ELineLoad, ...] = ()
    template: "TemplatePlan | None" = None       # secoes vindas de um .e2k de referencia (exporters.etabs.template)
    origin_shift: tuple[float, float] = (0.0, 0.0)   # translacao aplicada ao modelo (origem no canto inf. esquerdo)

    @property
    def story(self) -> EStory:
        return next(s for s in self.stories if not s.is_base)

    @property
    def story_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.stories if not s.is_base)

    def counts(self) -> dict[str, int]:
        return {
            "stories": len(self.stories), "grids": len(self.grids), "materials": len(self.materials),
            "frame_sections": len(self.frame_sections), "shell_sections": len(self.shell_sections),
            "points": len(self.points), "frames": len(self.frames),
            "beams": sum(1 for f in self.frames if f.kind == "BEAM"),
            "columns": sum(1 for f in self.frames if f.kind == "COLUMN"),
            "walls": sum(1 for a in self.areas if a.kind == "PANEL"),
            "slabs": sum(1 for a in self.areas if a.kind == "FLOOR"),
            "openings": sum(1 for a in self.areas if a.kind == "OPENING"),
            "frame_assignments": sum(len(f.assignments) for f in self.frames),
            "area_assignments": sum(len(a.assignments) for a in self.areas),
            "restraints": len(self.restraints), "piers": len(self.piers),
            "area_loads": len(self.area_loads), "line_loads": len(self.line_loads),
        }
