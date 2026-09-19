"""StructuralModel: agregado imutavel do modelo intermediario."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .diagnostics import ChangeRecord, Diagnostic
from .elements import (Beam, CatalogSection, Column, GridLine, LoadCase, Material, Node,
                       NodeRole, Slab, Story)


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    building: str | None = None
    plan_name: str | None = None
    plan_project: int | None = None
    building_project: int | None = None
    title: str | None = None
    client: str | None = None
    source_folder: str | None = None
    generated_at: str | None = None
    tqs_version: str | None = None
    source_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuralModel:
    project: ProjectInfo
    stories: Mapping[str, Story]
    nodes: Mapping[str, Node]
    columns: Mapping[str, Column]
    beams: Mapping[str, Beam]
    slabs: Mapping[str, Slab]
    materials: Mapping[str, Material] = field(default_factory=dict)
    catalog_sections: Mapping[str, CatalogSection] = field(default_factory=dict)
    load_cases: tuple[LoadCase, ...] = ()
    grids: tuple[GridLine, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    changes: tuple[ChangeRecord, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)

    # -- consultas -----------------------------------------------------
    def node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def structural_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.is_structural]

    def nodes_with_role(self, role: NodeRole) -> list[Node]:
        return [n for n in self.nodes.values() if role in n.roles]

    def beam_segment_count(self) -> int:
        return sum(len(b.segments) for b in self.beams.values())

    def with_diagnostics(self, extra: tuple[Diagnostic, ...]) -> "StructuralModel":
        return replace(self, diagnostics=self.diagnostics + extra)

    def with_changes(self, extra: tuple[ChangeRecord, ...]) -> "StructuralModel":
        return replace(self, changes=self.changes + extra)
