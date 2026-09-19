"""Tolerancias e politicas de modelagem (carregadas de config/default.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.toml"


@dataclass(frozen=True, slots=True)
class Tolerances:
    coordinate_cluster: float = 0.005
    rounding_decimals: int = 2
    node_merge: float = 0.005
    beam_column_snap: float = 0.010
    max_end_extension: float = 0.35
    length_change_warning: float = 0.010
    area_check_relative: float = 0.02


@dataclass(frozen=True, slots=True)
class ModelingPolicy:
    wall_aspect_ratio: float = 3.0
    polygon_always_wall: bool = True
    ignore_vertical_offsets: bool = True
    stair_area_type: str = "membrane"
    slab_area_type: str = "shell-thin"
    include_load_only_nodes: bool = False
    units: str = "kN_m"
    keep_tqs_origin: bool = True
    wall_opening_merge_max: float = 1.0    # laminas colineares separadas por vao <= isto sao unidas (furo)


@dataclass(frozen=True, slots=True)
class GridNaming:
    x_prefix: str = "X"
    y_prefix: str = "Y"
    start_index: int = 1
    secondary_grids_from_beams: bool = False
    grids_at_frame_columns: bool = True     # pilar-frame gera grid X e Y pelo centroide


@dataclass(frozen=True, slots=True)
class Config:
    tolerances: Tolerances = Tolerances()
    policy: ModelingPolicy = ModelingPolicy()
    grids: GridNaming = GridNaming()
    source: str = "<defaults>"


def _build(cls, data: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"Chaves desconhecidas em [{cls.__name__}]: {sorted(unknown)}")
    return cls(**data)


def load_config(path: Path | str | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        return Config()
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)
    return Config(
        tolerances=_build(Tolerances, raw.get("tolerances", {})),
        policy=_build(ModelingPolicy, raw.get("policy", {})),
        grids=_build(GridNaming, raw.get("grids", {})),
        source=str(p),
    )
