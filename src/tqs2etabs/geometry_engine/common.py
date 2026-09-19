"""Infraestrutura comum das regras do motor geometrico."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain.diagnostics import ChangeRecord, Diagnostic, DiagnosticCollector
from ..domain.elements import Node
from ..domain.geometry import Point
from ..domain.model import StructuralModel


@dataclass(frozen=True, slots=True)
class StepResult:
    """Saida de uma regra: novo modelo + o que mudou + diagnosticos + numeros para o log."""
    name: str
    model: StructuralModel
    changes: tuple[ChangeRecord, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)


def move_node(node: Node, x: float, y: float) -> Node:
    return replace(node, x=x, y=y)


def fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def fmt_pt(p: Point) -> str:
    return f"({fmt(p.x)}, {fmt(p.y)})"


def round_to(v: float, decimals: int) -> float:
    """Arredondamento half-up simetrico (21.245 -> 21.25), evitando o banker's rounding."""
    q = 10 ** decimals
    return math.floor(abs(v) * q + 0.5 + 1e-9) / q * (1 if v >= 0 else -1)


def with_changes(model: StructuralModel, collector: DiagnosticCollector,
                 changes: list[ChangeRecord]) -> StructuralModel:
    return replace(model, diagnostics=model.diagnostics + collector.as_tuple(),
                   changes=model.changes + tuple(changes))
