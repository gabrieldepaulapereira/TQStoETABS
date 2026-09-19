"""Caso de uso 'export': normalize + mapeamento ETABS + escrita do .e2k + validacao pos-exportacao."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..domain.config import Config, load_config
from ..domain.diagnostics import Diagnostic, Level
from ..exporters.etabs import (EtabsDescription, build_description, read_e2k_text, verify_export,
                               write_e2k_text)
from .normalize import NormalizationResult, format_audit_report, normalize


@dataclass(frozen=True, slots=True)
class ExportResult:
    normalization: NormalizationResult
    description: EtabsDescription
    e2k_text: str
    output_path: Path | None
    diagnostics: tuple[Diagnostic, ...]     # mapeamento + validacao pos-exportacao

    @property
    def has_errors(self) -> bool:
        return self.normalization.engine.has_errors or any(d.level == Level.ERROR for d in self.diagnostics)


def export_e2k(ldf_path: Path | str, lst_path: Path | str | None, output: Path | str | None,
               config: Config | None = None) -> ExportResult:
    config = config or load_config()
    norm = normalize(ldf_path, lst_path, config)
    desc, map_diags = build_description(norm.model, config)
    label = Path(output).name if output else "model.e2k"
    text = write_e2k_text(desc, config.etabs, label)
    verify_diags = verify_export(norm.model, desc, read_e2k_text(text, config.etabs.decimal_separator))
    out_path = None
    if output:
        out_path = Path(output)
        out_path.write_text(text, encoding="ascii", errors="replace", newline="\r\n")
    return ExportResult(norm, desc, text, out_path, map_diags + verify_diags)


def format_export_report(result: ExportResult) -> str:
    out = [format_audit_report(result.normalization), ""]
    add = out.append
    add("ETABS generation")
    add("----------------")
    d = result.description
    c = d.counts()
    add(f"Story: {d.story.name} (altura {d.story.height} m; base {d.stories[-1].elevation} m)")
    add(f"Points created: {c['points']}   Frame elements: {c['frames']} (beams {c['beams']}, columns {c['columns']})")
    add(f"Area elements: {c['walls'] + c['slabs']} (wall panels {c['walls']}, slabs {c['slabs']})   Piers: {c['piers']}")
    add(f"Grids: {c['grids']}   Materials: {[m.name for m in d.materials]}")
    add(f"Frame sections: {[s.name for s in d.frame_sections]}")
    add(f"Shell sections: {[s.name for s in d.shell_sections]}")
    add(f"Restraints at base: {c['restraints']}")
    for note in d.notes:
        add(f"  note: {note}")
    if result.output_path:
        add(f"File: {result.output_path}")
    add("")
    add("Post-export validation")
    add("----------------------")
    for diag in result.diagnostics:
        if diag.level != Level.INFO or diag.code.endswith("SUMMARY"):
            add("  " + diag.format())
    return "\n".join(out)
