"""Caso de uso 'analyze': ler LDF (+LST), construir o modelo e produzir o resumo.

A UI/CLI consome apenas AnalysisResult; nenhuma regra geometrica vive aqui.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..domain.config import Config, load_config
from ..domain.diagnostics import Level
from ..domain.elements import ColumnKind, NodeRole, RectSection
from ..domain.geometry import polyline_length
from ..domain.model import StructuralModel
from ..importers.tqs.ldf import build_model, parse_ldf
from ..importers.tqs.ldf.document import LdfDocument
from ..importers.tqs.lst import parse_lst
from ..importers.tqs.lst.document import LstDocument
from .cross_validation import CrossCheck, cross_validate


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    model: StructuralModel
    ldf: LdfDocument
    lst: LstDocument | None
    cross_checks: tuple[CrossCheck, ...]
    config: Config


def analyze(ldf_path: Path | str, lst_path: Path | str | None = None,
            config: Config | None = None) -> AnalysisResult:
    config = config or load_config()
    ldf = parse_ldf(ldf_path)
    lst = parse_lst(lst_path) if lst_path else None
    model = build_model(ldf, lst, config)
    checks, diags = cross_validate(model, lst, config.tolerances)
    model = model.with_diagnostics(diags)
    return AnalysisResult(model, ldf, lst, checks, config)


# ------------------------------------------------------------------ resumo

def _fmt_pt(p) -> str:
    return f"({p.x:9.3f}, {p.y:9.3f})"


def format_summary(result: AnalysisResult, verbose: bool = False) -> str:
    m = result.model
    story = next(iter(m.stories.values()))
    out: list[str] = []
    add = out.append

    add("TQS Import")
    add("----------")
    for f in m.project.source_files:
        add(f"Arquivo: {f}")
    add(f"Edificio: {m.project.building}   (projeto {m.project.building_project})")
    add(f"Pavimento: {m.project.plan_name}   (projeto {m.project.plan_project})")
    if story.elevation is not None:
        add(f"Piso {story.tqs_index} '{story.title}': cota {story.elevation:.2f} m, "
            f"pe-direito {story.height:.2f} m  [fonte: {story.source}]")
    else:
        add("Piso: cota/pe-direito desconhecidos (sem LST)")
    add(f"Gerado em: {m.project.generated_at}   TQS: {m.project.tqs_version}")
    add("")
    n_load = len(m.nodes_with_role(NodeRole.LOAD_ONLY))
    n_orph = len(m.nodes_with_role(NodeRole.ORPHAN))
    n_struct = len(m.structural_nodes())
    add(f"Nos encontrados:     {len(m.nodes):4}  (estruturais {n_struct}, so carga {n_load}, orfaos {n_orph})")
    n_rect = sum(1 for c in m.columns.values() if isinstance(c.section, RectSection))
    n_wall = sum(1 for c in m.columns.values() if c.kind_hint == ColumnKind.WALL)
    n_lam = sum(len(c.laminas) for c in m.columns.values())
    add(f"Pilares encontrados: {len(m.columns):4}  (retangulares {n_rect}, poligonais {len(m.columns) - n_rect}; "
        f"laminas {n_lam}; shell {n_wall}, frame {len(m.columns) - n_wall})")
    add(f"Vigas encontradas:   {len(m.beams):4}  (trechos {m.beam_segment_count()})")
    n_st = sum(1 for s in m.slabs.values() if s.is_stair)
    n_cant = sum(1 for s in m.slabs.values() if s.is_cantilever)
    add(f"Lajes encontradas:   {len(m.slabs):4}  (escada {n_st}, balanco {n_cant})")
    add(f"Materiais: {len(m.materials)}   Secoes catalogadas: {len(m.catalog_sections)}   "
        f"Casos de carga: {len(m.load_cases)} ({sum(len(lc.items) for lc in m.load_cases)} itens)")
    if result.lst:
        add(f"Avisos do TQS (LST): {len(result.lst.warnings)} distintos")
    add("")

    if verbose:
        add("Pilares")
        add("-------")
        for c in m.columns.values():
            sec = c.section
            if isinstance(sec, RectSection):
                desc = f"R {sec.length*100:.1f}x{sec.width*100:.1f} cm ang {sec.angle_deg:g}"
            else:
                desc = f"G {len(sec.outline)} vertices"
            add(f"  {c.id:4} {c.kind_hint.value:6} {desc:28} area {c.area:6.3f} m2  "
                f"centroide {_fmt_pt(c.centroid)}  laminas {len(c.laminas):2}  no {c.reference_node_id}"
                f"  fck {c.fck}  {' '.join(sorted(c.flags))}")
        add("")
        add("Vigas")
        add("-----")
        for b in m.beams.values():
            pts = [m.node(n).point for n in b.axis]
            secs = ", ".join(f"{s.width*100:g}/{s.depth*100:g}" for s in b.segments)
            sup = " ".join(f"{s.node_id}:{s.kind.value[:3]}{'(' + s.ref_id + ')' if s.ref_id else ''}"
                           for s in b.supports)
            rel = ("ARE " if b.release_start else "") + ("ARD" if b.release_end else "")
            add(f"  {b.id:4} L={polyline_length(pts):6.3f} m  trechos {len(b.segments)}  sec [{secs}]  {rel}")
            add(f"        {sup}")
        add("")
        add("Lajes")
        add("-----")
        for s in m.slabs.values():
            kinds = "".join(e.support.value[0] for e in s.edges)
            add(f"  {s.id:5} h={s.thickness*100:g} cm  bordos {len(s.edges):2} [{kinds}]  "
                f"area(LDF) {s.tqs_attrs.get('area_cm2', 0)/1e4:7.2f} m2  "
                f"{'ESCADA ' if s.is_stair else ''}{'BALANCO ' if s.is_cantilever else ''}"
                f"{'' if s.in_grid_model else 'nao-GRE '}{s.title or ''}")
        add("")

    if result.cross_checks:
        add("Validacao cruzada LDF x LST")
        add("---------------------------")
        tol = result.config.tolerances.area_check_relative
        bad = [c for c in result.cross_checks if not c.ok(tol)]
        add(f"  {len(result.cross_checks) - len(bad)}/{len(result.cross_checks)} grandezas conferem (tol {tol:.0%})")
        for c in (result.cross_checks if verbose else bad):
            flag = "  " if c.ok(tol) else "!!"
            add(f"  {flag} {c.element_id:5} {c.quantity:7} LDF {c.ldf_value:9.3f}  LST {c.lst_value:9.3f} {c.unit}"
                f"  ({c.relative_error:.1%})")
        add("")

    add("Diagnosticos")
    add("------------")
    counts = {lv: sum(1 for d in m.diagnostics if d.level == lv) for lv in Level}
    add(f"  INFO {counts[Level.INFO]}  WARNING {counts[Level.WARNING]}  ERROR {counts[Level.ERROR]}")
    for d in m.diagnostics:
        if verbose or d.level != Level.INFO:
            add("  " + d.format())
    if result.lst and verbose:
        add("")
        add("Avisos do TQS (LST)")
        add("-------------------")
        for w in result.lst.warnings:
            add(f"  ***{w.number:03d} x{w.occurrences}: {w.text}")
    return "\n".join(out)


# ------------------------------------------------------------------- JSON

def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (frozenset, set, tuple, list)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    return obj


def model_to_json(model: StructuralModel, indent: int = 1) -> str:
    return json.dumps(_to_jsonable(model), indent=indent, ensure_ascii=False, default=str)
