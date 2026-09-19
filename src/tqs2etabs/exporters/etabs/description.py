"""Descricao neutra de um modelo ETABS (o que sera criado), independente do meio
(arquivo .e2k ou API COM). Produzida por `mapping.build_description`.

Unidades: kN, m, C. Coordenadas em planta; o pavimento e atribuido no objeto.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class EStory:
    name: str
    height: float          # 0 para a base
    elevation: float
    is_base: bool = False


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
    source: str = ""       # "LDF FCK" | "config default"


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
    source_node: str | None    # id do no do modelo (None = ponto auxiliar de parede)


@dataclass(frozen=True, slots=True)
class EFrame:
    name: str
    kind: str              # BEAM | COLUMN
    point_i: str
    point_j: str
    story: str
    section: str
    angle: float = 0.0
    cardinal_point: int = 8
    releases: str = ""     # ex.: "M2I M3I M2J M3J"
    source: str = ""       # elemento TQS de origem


@dataclass(frozen=True, slots=True)
class EArea:
    name: str
    kind: str              # PANEL | FLOOR | OPENING (escrito como FLOOR + OPENING "Yes")
    points: tuple[str, ...]
    story: str
    section: str
    pier: str | None = None
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
    node_to_point: dict[str, str] = field(default_factory=dict)

    @property
    def story(self) -> EStory:
        return next(s for s in self.stories if not s.is_base)

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
            "restraints": len(self.restraints), "piers": len(self.piers),
        }
