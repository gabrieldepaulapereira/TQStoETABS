"""Definicao do edificio: plantas, pisos (com replicacao), materiais por piso."""

from __future__ import annotations

from dataclasses import dataclass, field

from .diagnostics import Diagnostic


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    tag: str                     # nome curto para objetos ETABS (ex.: TIPO1)
    name: str                    # nome do pavimento no LDF ("Tipo 1")
    folder: str
    ldf_path: str
    lst_path: str | None = None
    is_base: bool = False        # planta de fundacao (so pilares que nascem)


@dataclass(frozen=True, slots=True)
class PisoDefinition:
    index: int                   # numero do piso no TQS (1..n)
    title: str                   # titulo do piso no LST ("Tipo")
    elevation: float             # cota (m)
    height: float                # pe-direito (m)
    plan_tag: str
    materials: dict[str, str] = field(default_factory=dict)   # pilares|vigas|lajes -> "C50"

    @property
    def story_name(self) -> str:
        return f"{self.index}-{self.title}"


@dataclass(frozen=True, slots=True)
class ConcreteClass:
    name: str
    fck_mpa: float
    e_initial_mpa: float | None
    e_secant_mpa: float | None
    foundation_only: bool = False


@dataclass(frozen=True, slots=True)
class BuildingDefinition:
    name: str
    folder: str
    plans: dict[str, PlanDefinition]
    pisos: tuple[PisoDefinition, ...]          # ordenados por index crescente
    base_elevation: float
    base_plan_tag: str | None
    concrete_catalog: dict[str, ConcreteClass]
    diagnostics: tuple[Diagnostic, ...]
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def base_story_name(self) -> str:
        return "BASE"

    def plan_for(self, piso: PisoDefinition) -> PlanDefinition:
        return self.plans[piso.plan_tag]
