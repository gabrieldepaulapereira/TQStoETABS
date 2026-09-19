"""Representacao bruta do LDF: o que esta escrito no arquivo, em cm, sem interpretacao geometrica.

Ver ARCHITECTURE.md secao 1 para a semantica de cada campo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....domain.diagnostics import Diagnostic

Pair = tuple[float, float]
RawPolygon = tuple[Pair, ...]


@dataclass(frozen=True, slots=True)
class LdfHeader:
    generated_at: str | None = None
    folder: str | None = None
    plan_name: str | None = None        # "25 - Tipo"
    plan_project: int | None = None     # 11
    building: str | None = None
    building_project: int | None = None
    title: str | None = None
    client: str | None = None


@dataclass(frozen=True, slots=True)
class LdfCatalogSection:
    name: str
    ix: float
    iy: float
    iz: float
    ax: float
    extra: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class LdfMaterial:
    name: str
    values: tuple[float, ...]   # gamma, E, G, nu, alpha, fy? (NEEDS_REVIEW ultimo campo)


@dataclass(frozen=True, slots=True)
class LdfAxisNode:
    node: int
    qualifier: str | None        # 'P3', 'N', 'AV17', 'RV5' ou None


@dataclass(frozen=True, slots=True)
class LdfBeamGeometry:
    name: str
    axis: tuple[LdfAxisNode, ...]
    release_start: bool          # ARE
    release_end: bool            # ARD
    unknown_tokens: tuple[str, ...] = ()
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfColumnGeometry:
    name: str
    node: int
    material: str | None
    flags: tuple[str, ...]      # CORTINA, FURADO, ...
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfSlabVertex:
    node: int
    qualifier: str | None        # 'LIV', 'P6' ou None (bordo sobre viga)


@dataclass(frozen=True, slots=True)
class LdfSlabGeometry:
    name: str
    title: str | None
    flags: tuple[str, ...]       # GRE, ...
    area_cm2: float | None
    vertices: tuple[LdfSlabVertex, ...]
    angle_deg: float | None
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfBeamSection:
    index: int
    width_cm: float
    depth_cm: float
    dfs_cm: float | None = None


@dataclass(frozen=True, slots=True)
class LdfBeamDimensions:
    name: str
    sections: tuple[LdfBeamSection, ...]
    plan_area_cm2: float | None = None
    volume_cm3: float | None = None
    unknown_tokens: tuple[str, ...] = ()
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfColumnDimensions:
    name: str
    kind: str                            # 'R' ou 'G'
    length_cm: float | None = None       # R
    width_cm: float | None = None        # R
    angle_deg: float | None = None       # R
    base: Pair | None = None             # R: local (u,v); G: global (x,y)
    polygon: RawPolygon | None = None    # G
    dsc: int | None = None
    fck: str | None = None
    psu: RawPolygon | None = None
    laminas: tuple[RawPolygon, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfSlabDimensions:
    name: str
    thickness_cm: float
    dfs_cm: float | None = None
    cantilever: bool = False
    larm: tuple[str, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfLoadItem:
    element: str                 # 'V1', 'L4'
    kind: str                    # DIS, DIP, ADI, ARE
    value: float
    nodes: tuple[int, ...] = ()
    region: RawPolygon | None = None
    line: int = 0


@dataclass(frozen=True, slots=True)
class LdfLoadCase:
    number: int
    description: str
    items: tuple[LdfLoadItem, ...]


@dataclass(frozen=True, slots=True)
class LdfDocument:
    header: LdfHeader
    scale: float | None
    ctor: Pair | None
    nodes: dict[int, Pair]
    beams: dict[str, LdfBeamGeometry]
    columns: dict[str, LdfColumnGeometry]
    slabs: dict[str, LdfSlabGeometry]
    beam_dims: dict[str, LdfBeamDimensions]
    column_dims: dict[str, LdfColumnDimensions]
    slab_dims: dict[str, LdfSlabDimensions]
    load_cases: tuple[LdfLoadCase, ...]
    catalog_sections: tuple[LdfCatalogSection, ...]
    materials: tuple[LdfMaterial, ...]
    sections_seen: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    source_path: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
