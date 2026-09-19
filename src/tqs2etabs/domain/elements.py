"""Elementos do modelo estrutural intermediario.

Independente de TQS e ETABS. Coordenadas em metros. Todo elemento guarda a
origem (arquivo/id TQS) e os atributos brutos nao interpretados em `tqs_attrs`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .geometry import Point, Polygon, polygon_area, polygon_centroid, rectangle_from_local


@dataclass(frozen=True, slots=True)
class Provenance:
    source_file: str
    source_id: str
    raw: Mapping[str, Any] = field(default_factory=dict)   # valores originais (cm, tokens)


class NodeRole(str, Enum):
    BEAM_AXIS = "BEAM_AXIS"                # pertence ao eixo de uma viga
    BEAM_SUPPORT = "BEAM_SUPPORT"          # extremidade de viga sobre pilar (P k)
    BEAM_INTERSECTION = "BEAM_INTERSECTION"  # apoio viga-viga (AV/RV)
    COLUMN_REF = "COLUMN_REF"              # no de referencia de pilar
    SLAB_VERTEX = "SLAB_VERTEX"
    LOAD_ONLY = "LOAD_ONLY"                # so aparece em cargas
    ORPHAN = "ORPHAN"                      # nao referenciado por nada


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    x: float
    y: float
    z: float
    story_id: str
    roles: frozenset[NodeRole]
    provenance: Provenance

    @property
    def point(self) -> Point:
        return Point(self.x, self.y)

    @property
    def is_structural(self) -> bool:
        return not (self.roles <= {NodeRole.LOAD_ONLY, NodeRole.ORPHAN})


# ---------------------------------------------------------------- pilares

class ColumnKind(str, Enum):
    COLUMN = "COLUMN"   # frame vertical
    WALL = "WALL"       # casca(s) verticais


@dataclass(frozen=True, slots=True)
class RectSection:
    length: float          # maior dimensao (L do TQS)
    width: float           # B do TQS
    angle_deg: float       # rotacao do eixo local u (direcao de L)
    origin: Point          # canto-origem local (global)

    @property
    def polygon(self) -> Polygon:
        return rectangle_from_local(self.origin, self.length, self.width, self.angle_deg)

    @property
    def aspect_ratio(self) -> float:
        a, b = max(self.length, self.width), min(self.length, self.width)
        return a / b if b > 0 else float("inf")


@dataclass(frozen=True, slots=True)
class PolygonSection:
    outline: Polygon


@dataclass(frozen=True, slots=True)
class AxisSegment:
    """Linha media de uma lamina de parede (ou linha de eixo de pilar-frame)."""
    start: Point
    end: Point
    thickness: float

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def direction(self) -> str:
        """'X' se paralelo ao eixo X, 'Y' se paralelo a Y, senao 'other'."""
        dx, dy = abs(self.end.x - self.start.x), abs(self.end.y - self.start.y)
        if dy <= 1e-6 * max(1.0, dx):
            return "X"
        if dx <= 1e-6 * max(1.0, dy):
            return "Y"
        return "other"


@dataclass(frozen=True, slots=True)
class Column:
    id: str
    name: str
    story_id: str
    section: RectSection | PolygonSection
    reference_node_id: str
    kind_hint: ColumnKind
    laminas: tuple[Polygon, ...] = ()
    axes: tuple[AxisSegment, ...] = ()        # preenchido pelo geometry_engine (paredes)
    section_above: Polygon | None = None      # PSU (NEEDS_REVIEW: lance superior)
    material_ref: str | None = None
    fck: str | None = None
    flags: frozenset[str] = frozenset()
    tqs_attrs: Mapping[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None

    @property
    def outline(self) -> Polygon:
        return self.section.polygon if isinstance(self.section, RectSection) else self.section.outline

    @property
    def area(self) -> float:
        return polygon_area(self.outline)

    @property
    def centroid(self) -> Point:
        return polygon_centroid(self.outline)

    @property
    def is_polygonal(self) -> bool:
        return isinstance(self.section, PolygonSection)


# ------------------------------------------------------------------ vigas

class SupportKind(str, Enum):
    COLUMN = "COLUMN"        # P k
    ON_BEAM = "ON_BEAM"      # AV k: esta viga apoia na viga k
    RECEIVES = "RECEIVES"    # RV k: esta viga recebe a viga k
    FREE = "FREE"            # N: no intermediario


@dataclass(frozen=True, slots=True)
class BeamSupport:
    node_id: str
    kind: SupportKind
    ref_id: str | None = None   # id do pilar ou da viga referenciada


@dataclass(frozen=True, slots=True)
class BeamSegment:
    start_node_id: str
    end_node_id: str
    width: float
    depth: float
    top_offset: float = 0.0    # DFS (m, sinal bruto do TQS); ignorado se policy.ignore_vertical_offsets


@dataclass(frozen=True, slots=True)
class Beam:
    id: str
    name: str
    story_id: str
    axis: tuple[str, ...]
    segments: tuple[BeamSegment, ...]
    supports: tuple[BeamSupport, ...]
    release_start: bool = False   # ARE (NEEDS_REVIEW)
    release_end: bool = False     # ARD (NEEDS_REVIEW)
    tqs_attrs: Mapping[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None


# ------------------------------------------------------------------ lajes

class EdgeSupport(str, Enum):
    BEAM = "BEAM"
    COLUMN = "COLUMN"
    FREE = "FREE"
    UNKNOWN = "UNKNOWN"   # sem qualificador e sem viga identificada


@dataclass(frozen=True, slots=True)
class SlabEdge:
    start_node_id: str
    end_node_id: str
    support: EdgeSupport
    ref_id: str | None = None


@dataclass(frozen=True, slots=True)
class Slab:
    id: str
    name: str
    story_id: str
    edges: tuple[SlabEdge, ...]
    thickness: float
    title: str | None = None
    top_offset: float = 0.0
    is_cantilever: bool = False
    in_grid_model: bool = True     # flag GRE (NEEDS_REVIEW)
    angle_deg: float = 0.0
    is_stair: bool = False
    holes: tuple[Polygon, ...] = ()
    tqs_attrs: Mapping[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None

    @property
    def boundary_node_ids(self) -> tuple[str, ...]:
        return tuple(e.start_node_id for e in self.edges)


# ------------------------------------------------------ pavimento, materiais

@dataclass(frozen=True, slots=True)
class Story:
    id: str
    name: str
    tqs_index: int | None = None
    title: str | None = None
    elevation: float | None = None   # cota do piso (m)
    height: float | None = None      # pe-direito (m)
    source: str = "LDF"


@dataclass(frozen=True, slots=True)
class Material:
    id: str
    name: str
    unit_weight: float | None = None   # tf/m3 (bruto do TQS)
    e_modulus: float | None = None     # tf/m2
    g_modulus: float | None = None
    poisson: float | None = None
    thermal_exp: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CatalogSection:
    id: str
    name: str
    ix: float
    iy: float
    iz: float
    ax: float
    extra: tuple[float, ...] = ()


# ------------------------------------------------------------------ cargas

@dataclass(frozen=True, slots=True)
class LoadItem:
    element_id: str
    kind: str                       # DIS, DIP, ADI, ARE
    value: float
    unit: str
    node_ids: tuple[str, ...] = ()
    region: Polygon | None = None


@dataclass(frozen=True, slots=True)
class LoadCase:
    id: str
    number: int
    description: str
    items: tuple[LoadItem, ...]


# ------------------------------------------------------------------- grids

@dataclass(frozen=True, slots=True)
class GridLine:
    id: str
    label: str
    direction: str           # "X" ou "Y"
    coordinate: float
    origin_column_ids: tuple[str, ...] = ()
