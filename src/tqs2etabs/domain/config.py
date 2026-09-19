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
class EtabsOptions:
    version: str = "23.2.0"
    decimal_separator: str = ","          # o ETABS grava/le o E2K com o separador do Windows (pt-BR: virgula)
    default_material: str = "C40"         # fck de vigas/lajes nao esta no LDF/LST (UNKNOWN)
    concrete_unit_weight: float = 25.0    # kN/m3
    beam_cardinal_point: int = 8          # 8 = topo-centro (padrao do ETABS); 5 = centroide
    apply_releases: bool = False          # ARE/ARD (NEEDS_REVIEW) nao viram releases
    assign_piers: bool = True             # paineis de parede recebem PIER "<nome do pilar>"
    split_walls_at_nodes: bool = True     # divide os paineis nos nos de viga/laje sobre o eixo (juntas explicitas)
    base_restraint: str = "UX UY UZ RX RY RZ"
    base_story_name: str = "BASE"
    grid_system: str = "G1"
    floor_mesh_max: float = 1.0
    wall_mesh_max: float = 1.0
    company: str = ""


@dataclass(frozen=True, slots=True)
class Config:
    tolerances: Tolerances = Tolerances()
    policy: ModelingPolicy = ModelingPolicy()
    grids: GridNaming = GridNaming()
    etabs: EtabsOptions = EtabsOptions()
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
        etabs=_build(EtabsOptions, raw.get("etabs", {})),
        source=str(p),
    )
