"""Tolerancias e politicas de modelagem (carregadas de config/default.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
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
    wall_end_snap: float = 0.15           # viga a menos disto da ponta da parede vai para a ponta (regra C)
    min_opening_area: float = 0.05        # reentrancia menor que isto e simplesmente preenchida
    trim_wall_stub_max: float = 0.15      # toco terminal de parede sem no, menor que isto, e eliminado
    slab_dent_flatten_max: float = 0.20   # degrau de bordo livre menor que isto e achatado (laje cresce)


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
    absorb_offset_slabs: bool = True       # lajes rebaixadas/balanco que compartilham bordo livre entram na laje-mae
    simplify_slab_outlines: bool = True    # reentrancias de bordo livre -> contorno reto + abertura
    openings: str = "opening"              # "opening" = area OPENING no ETABS; "fill" = preenche sem abertura


@dataclass(frozen=True, slots=True)
class GridNaming:
    x_style: str = "letters"                # letters (A, B, ...) | numbers | prefix
    y_style: str = "numbers"                # numbers (1, 2, ...) | letters | prefix
    x_prefix: str = "X"
    y_prefix: str = "Y"
    start_index: int = 1
    secondary_grids_from_beams: bool = False
    grids_at_frame_columns: bool = True     # pilar-frame gera grid X e Y pelo centroide
    boundary_tolerance: float = 0.25        # grid de extremo so e criado se o ultimo estiver a mais que isto
                                            # (0,25 m ignora a meia-espessura tipica de parede/viga)


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
    export_loads: bool = True             # cargas de uso: ADI de laje e DIS de viga (casos 3 e 4 do LDF)
    include_columns: bool = True          # selecao de elementos a importar
    include_beams: bool = True
    include_slabs: bool = True
    e_overrides: dict[str, float] = field(default_factory=dict)   # classe -> E adotado (MPa); vence CONCRETO.DAT/NBR
    tf_to_kn: float = 9.80665
    pattern_dead_extra: str = "SDL"       # caso 3 (permanentes) -> Super Dead
    pattern_live: str = "RLIVE"           # caso 4 (acidentais)  -> Reducible Live (convencao do escritorio)
    pattern_live_type: str = "Reducible Live"
    mass_live_factor: float = 0.25
    template_path: str = ""               # .e2k de referencia: definicoes, casos e combinacoes do escritorio
    template_definitions: bool = True     # materiais, secoes, diafragmas, funcoes e preferencias do template
    template_analysis: bool = True        # load patterns, load cases, mass source e opcoes de analise
    template_combos: bool = True          # combinacoes de carga
    origin_at_min_corner: bool = True     # translada o modelo para que (0,0) seja o canto inferior esquerdo
    bounding_grids: bool = True           # garante grid nos extremos do perimetro (inclui vigas e lajes)


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
