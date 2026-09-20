"""Caso de uso 'normalize': analyze + geometry engine + relatorio de auditoria."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..domain.config import Config, load_config
from ..domain.diagnostics import Level, Source
from ..domain.elements import ColumnKind
from ..geometry_engine import EngineResult, run_engine
from ..geometry_engine.common import fmt
from .analyze import AnalysisResult, analyze, format_summary
from .comparison import ComparisonReport, compare
from .tqs_warnings import map_tqs_warnings


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    analysis: AnalysisResult
    engine: EngineResult
    comparison: ComparisonReport
    config: Config

    @property
    def model(self):
        return self.engine.model


def normalize(ldf_path: Path | str, lst_path: Path | str | None = None,
              config: Config | None = None) -> NormalizationResult:
    config = config or load_config()
    analysis = analyze(ldf_path, lst_path, config)
    engine = run_engine(analysis.model, config)
    mapped = map_tqs_warnings(analysis.lst, engine.original, engine.model)
    if mapped:
        engine = replace(engine, model=engine.model.with_diagnostics(mapped))
    return NormalizationResult(analysis, engine, compare(engine.original, engine.model), config)


# ------------------------------------------------------------------ relatorio

def _h(title: str) -> list[str]:
    return [title, "-" * len(title)]


def format_audit_report(result: NormalizationResult, verbose: bool = False) -> str:
    eng = result.engine
    m = eng.model
    out: list[str] = []
    add = out.append

    add(format_summary(result.analysis, verbose=False).split("\nDiagnosticos")[0].rstrip())
    add("")

    st = eng.step("derive_column_axes").stats
    out.extend(_h("Column axes"))
    add(f"Walls (shell): {st['walls']}   Frame columns: {st['frame_columns']}   Wall axis segments: {st['wall_segments']}")
    for col in m.columns.values():
        if col.kind_hint == ColumnKind.WALL:
            add(f"  {col.id:4} " + "; ".join(f"({fmt(s.start.x)},{fmt(s.start.y)})-({fmt(s.end.x)},{fmt(s.end.y)}) t={fmt(s.thickness)}"
                                            for s in col.axes))
        else:
            c = col.centroid
            add(f"  {col.id:4} frame @ ({fmt(c.x)}, {fmt(c.y)})")
    add("")

    st = eng.step("normalize_coordinates").stats
    out.extend(_h("Geometry normalization"))
    add(f"Coordinates normalized: {st['nodes_changed']} nodes")
    add(f"Coordinate clusters: X={st['clusters_x']} Y={st['clusters_y']} (with >1 raw value: {st['clusters_multi']})")
    if verbose:
        for axis in ("x", "y"):
            for c in st["clusters"][axis]:
                if len(c.values) > 1:
                    add(f"  {axis.upper()} {sorted(c.values)} -> {c.representative}{' [pilar]' if c.has_column else ''}")
    add("")

    out.extend(_h("Beam alignment"))
    snap = eng.step("snap_beams_transverse")
    ext = eng.step("extend_beam_ends")
    add(f"Transverse snaps: {snap.stats['nodes_moved']} nodes   End extensions: {ext.stats['nodes_moved']} nodes"
        f"   Errors: {ext.stats['errors']}")
    for d in snap.diagnostics + ext.diagnostics:
        if d.level != Level.INFO or d.code in ("ALIGN-I-EXTENDED", "ALIGN-I-TRANSVERSE"):
            add("  " + d.format())
    add("")

    we = eng.step("snap_beams_to_wall_ends")
    out.extend(_h("Wall-end alignment"))
    add(f"Beams moved to wall end node: {we.stats['beams_moved']}   Nodes moved: {we.stats['nodes_moved']}")
    for d in we.diagnostics:
        if d.level != Level.INFO or d.code == "ALIGN-I-WALL-END":
            add("  " + d.format())
    add("")

    ov = eng.step("trim_beams_over_walls")
    out.extend(_h("Beam/wall overlap"))
    add(f"Beams trimmed over walls: {ov.stats['beams_trimmed']}   Length removed: {fmt(ov.stats['removed_length'])} m")
    for d in ov.diagnostics:
        if d.code == "OVL-I-BEAM-TRIMMED" or d.level != Level.INFO:
            add("  " + d.format())
    add("")

    out.extend(_h("Slab outlines"))
    sv = eng.step("snap_slab_vertices_to_column_axes")
    ab = eng.step("absorb_offset_slabs")
    si = eng.step("simplify_slab_outlines")
    add(f"Vertices moved to wall axes: {sv.stats['nodes_moved']}   Offset slabs absorbed: {ab.stats['absorbed']}   "
        f"Openings created: {si.stats['openings']}   Slabs: {si.stats['slabs']}")
    for d in sv.diagnostics + ab.diagnostics + si.diagnostics:
        if d.level != Level.INFO or d.code in ("SLAB-I-THICKNESS", "SLAB-I-ABSORBED"):
            add("  " + d.format())
    for sl in m.slabs.values():
        pts = [m.node(e.start_node_id).point for e in sl.edges]
        add(f"  {sl.id:4} {len(pts):2} vertices, {len(sl.holes)} opening(s): " +
            " ".join(f"({fmt(p.x)},{fmt(p.y)})" for p in pts))
    add("")

    st = eng.step("merge_nodes").stats
    out.extend(_h("Node merge"))
    add(f"Nodes merged: {st['merged']}")
    add("")

    out.extend(_h("Grid generation"))
    add(f"X grids: {sum(1 for g in m.grids if g.direction == 'X')}")
    add(f"Y grids: {sum(1 for g in m.grids if g.direction == 'Y')}")
    for g in m.grids:
        add(f"  {g.label:4} {g.direction} = {g.coordinate:8.2f}   ({', '.join(g.origin_column_ids)})")
    add("")

    val = eng.step("validate_model")
    out.extend(_h("Validation"))
    add(f"Errors: {val.stats['ERROR']}   Warnings: {val.stats['WARNING']}")
    for d in val.diagnostics:
        if d.level != Level.INFO:
            add("  " + d.format())
    add("")

    cmp = result.comparison
    out.extend(_h("TQS x normalized"))
    for kind, c in cmp.counts.items():
        add(f"  {kind:7} tqs={c['tqs']:4} kept={c['kept']:4} modified={c['modified']:4} removed={c['removed']}")
    for item in cmp.modified("beam"):
        add(f"  {item.element_id:5} {item.before}  ->  {item.after}   [{', '.join(item.reasons)}]")
    add("")

    out.extend(_h("Changes (audit)"))
    changes = m.changes if verbose else [c for c in m.changes if not c.rule.startswith("coordinate-normalization")]
    if not verbose:
        add(f"({len(m.changes) - len(changes)} coordinate-normalization records omitted; use -v)")
    for c in changes:
        add(f"{c.element_id} {c.attribute}")
        add(f"  BEFORE {c.before}")
        add(f"  AFTER  {c.after}")
        add(f"  REASON {c.reason} | rule={c.rule}" + (f" | ref={c.reference}" if c.reference else "")
            + (f" | tol={c.tolerance}" if c.tolerance is not None else ""))
    add("")

    lst_warns = [d for d in m.diagnostics if d.source == Source.TQS_LST]
    if lst_warns:
        out.extend(_h("TQS warnings mapped"))
        for d in lst_warns:
            add("  " + d.format())
    return "\n".join(out)
