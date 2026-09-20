"""Pipeline do motor geometrico (ARCHITECTURE.md 10.1).

    derive_column_axes -> normalize_coordinates -> snap_beams_transverse
    -> extend_beam_ends -> snap_beams_to_wall_ends -> trim_beams_over_walls
    -> snap_slab_vertices_to_column_axes
    -> absorb_offset_slabs -> simplify_slab_outlines -> merge_nodes -> generate_grids
    -> validate_model

Cada regra e uma funcao pura (modelo -> StepResult); o pipeline guarda o snapshot
de entrada de cada passo para auditoria e comparacao.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.config import Config
from ..domain.diagnostics import Level
from ..domain.model import StructuralModel
from .alignment import extend_beam_ends, snap_beams_to_wall_ends, snap_beams_transverse
from .beam_wall_overlap import trim_beams_over_walls
from .column_axes import derive_column_axes
from .common import StepResult
from .connectivity import merge_nodes
from .grids import generate_grids
from .normalization import normalize_coordinates
from .slabs import (absorb_offset_slabs, simplify_slab_outlines,
                    snap_slab_vertices_to_column_axes)
from .validation import validate_model


@dataclass(frozen=True, slots=True)
class EngineResult:
    original: StructuralModel
    model: StructuralModel
    steps: tuple[StepResult, ...]
    snapshots: dict[str, StructuralModel] = field(default_factory=dict)   # modelo ANTES de cada passo

    def step(self, name: str) -> StepResult:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def has_errors(self) -> bool:
        return any(d.level == Level.ERROR for d in self.model.diagnostics)


def run_engine(model: StructuralModel, config: Config) -> EngineResult:
    original = model
    steps: list[StepResult] = []
    snapshots: dict[str, StructuralModel] = {}

    def run(name: str, fn) -> StructuralModel:
        snapshots[name] = current[0]
        res = fn(current[0], config)
        steps.append(res)
        current[0] = res.model
        return res.model

    current = [model]
    run("derive_column_axes", derive_column_axes)
    run("normalize_coordinates", normalize_coordinates)
    run("snap_beams_transverse", snap_beams_transverse)
    run("extend_beam_ends", extend_beam_ends)
    moved: dict[str, float] = dict(steps[-1].stats.get("extended", {}))
    run("snap_beams_to_wall_ends", snap_beams_to_wall_ends)
    for k, v in steps[-1].stats.get("moved", {}).items():
        moved[k] = moved.get(k, 0.0) + v
    run("trim_beams_over_walls", trim_beams_over_walls)
    trimmed = {c.element_id for c in steps[-1].changes}
    run("snap_slab_vertices_to_column_axes", snap_slab_vertices_to_column_axes)
    for k, v in steps[-1].stats.get("moved", {}).items():
        moved[k] = moved.get(k, 0.0) + v
    run("absorb_offset_slabs", absorb_offset_slabs)
    run("simplify_slab_outlines", simplify_slab_outlines)
    run("merge_nodes", merge_nodes)
    for c in steps[-1].changes:            # no fundido: o sobrevivente herda o deslocamento registrado
        if c.rule == "node-merge":
            moved[c.after] = max(moved.get(c.after, 0.0), moved.get(c.element_id, 0.0))
    extended = moved
    run("generate_grids", generate_grids)
    snapshots["validate_model"] = current[0]
    res = validate_model(current[0], original, config, extended, skip_length_for=trimmed)
    steps.append(res)
    current[0] = res.model
    return EngineResult(original, current[0], tuple(steps), snapshots)
