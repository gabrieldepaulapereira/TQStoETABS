"""Representacao bruta do LST (relatorio do TQS Formas). So as secoes uteis ao conversor."""

from __future__ import annotations

from dataclasses import dataclass, field

from ....domain.diagnostics import Diagnostic


@dataclass(frozen=True, slots=True)
class LstStory:
    index: int
    title: str
    elevation_m: float
    height_m: float
    section_code: str | None = None
    material: str | None = None


@dataclass(frozen=True, slots=True)
class LstWarning:
    number: int              # primeiro numero de sequencia (***nnn)
    text: str
    occurrences: int = 1
    line: int = 0


@dataclass(frozen=True, slots=True)
class LstBeamQuantity:
    name: str
    structured_area_m2: float
    formwork_area_m2: float
    concrete_volume_m3: float
    linear_length_m: float
    mean_span_m: float


@dataclass(frozen=True, slots=True)
class LstColumnQuantity:
    name: str
    structured_area_m2: float
    formwork_area_m2: float
    concrete_volume_m3: float
    top_volume_m3: float | None      # None para "(Cortina)"
    is_cortina: bool = False


@dataclass(frozen=True, slots=True)
class LstSlabQuantity:
    label: str                       # "L1", "ESCADA", "REBAIX"
    order: int                       # posicao na lista (para casar titulos repetidos)
    structured_area_m2: float
    formwork_area_m2: float
    concrete_volume_m3: float


@dataclass(frozen=True, slots=True)
class LstDocument:
    tqs_version: str | None
    building: str | None
    plan_name: str | None
    title: str | None
    client: str | None
    stories: tuple[LstStory, ...]
    warnings: tuple[LstWarning, ...]
    beam_quantities: dict[str, LstBeamQuantity]
    column_quantities: dict[str, LstColumnQuantity]
    slab_quantities: tuple[LstSlabQuantity, ...]
    column_fck: dict[str, str]
    diagnostics: tuple[Diagnostic, ...]
    source_path: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
