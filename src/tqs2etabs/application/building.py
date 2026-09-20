"""Caso de uso 'building': pasta do edificio TQS -> modelo ETABS completo (.e2k).

Fluxo: scan_building -> por planta (da mais baixa para a mais alta): analyze + alinhamento
dos eixos dos pilares a planta de referencia + geometry_engine + avisos do TQS ->
EtabsMapper (objetos definidos uma vez, atribuidos a cada piso que usa a planta, com o
material de cada piso) -> .e2k -> releitura e validacao.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from ..domain.building import BuildingDefinition, PisoDefinition
from ..domain.config import Config, load_config
from ..domain.diagnostics import Diagnostic, Level
from ..domain.elements import Column
from ..exporters.etabs import EtabsDescription, read_e2k_text, verify_export, write_e2k_text
from ..exporters.etabs.mapping import EtabsMapper, ascii_name
from ..geometry_engine import run_engine
from ..geometry_engine.common import fmt
from ..geometry_engine.multi_story import align_columns_to_reference
from ..importers.tqs.building import scan_building
from .analyze import analyze
from .comparison import compare
from .normalize import NormalizationResult, format_audit_report
from .tqs_warnings import map_tqs_warnings


@dataclass(frozen=True, slots=True)
class PlanResult:
    tag: str
    normalization: NormalizationResult
    stories: tuple[str, ...]
    aligned: int


@dataclass(frozen=True, slots=True)
class BuildingResult:
    definition: BuildingDefinition
    plans: dict[str, PlanResult]
    description: EtabsDescription
    e2k_text: str
    output_path: Path | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def has_errors(self) -> bool:
        return (any(d.level == Level.ERROR for d in self.definition.diagnostics)
                or any(d.level == Level.ERROR for d in self.diagnostics)
                or any(p.normalization.engine.has_errors for p in self.plans.values()))


def convert_building(folder: Path | str, output: Path | str | None, config: Config | None = None) -> BuildingResult:
    config = config or load_config()
    return convert_building_definition(scan_building(folder, config), output, config)


def convert_building_definition(bd: BuildingDefinition, output: Path | str | None,
                                config: Config | None = None) -> BuildingResult:
    """Converte uma definicao de edificio (possivelmente editada pelo usuario) em .e2k."""
    config = config or load_config()
    plans: dict[str, PlanResult] = {}
    reference: dict[str, Column] = {}      # pilar -> coluna (com eixos) da planta mais baixa em que existe

    # ordem de processamento: planta de fundacao, depois plantas na ordem dos pisos
    order: list[str] = []
    if bd.base_plan_tag:
        order.append(bd.base_plan_tag)
    for piso in bd.pisos:
        if piso.plan_tag not in order:
            order.append(piso.plan_tag)

    for tag in order:
        plan = bd.plans[tag]
        analysis = analyze(plan.ldf_path, plan.lst_path, config)
        model = analysis.model
        step = align_columns_to_reference(model, reference, config)
        model = step.model
        engine = run_engine(model, config)
        mapped = map_tqs_warnings(analysis.lst, engine.original, engine.model)
        if mapped:
            engine = replace(engine, model=engine.model.with_diagnostics(mapped))
        norm = NormalizationResult(analysis, engine, compare(engine.original, engine.model), config)
        stories = tuple(p.story_name for p in bd.pisos if p.plan_tag == tag)
        plans[tag] = PlanResult(tag, norm, stories, step.stats.get("shifted", 0))
        for cid, col in engine.model.columns.items():
            reference.setdefault(cid, col)

    # ------------------------------------------------------------ mapeamento
    mapper = EtabsMapper(config, bd.name, bd.concrete_catalog)
    mapper.set_base(bd.base_elevation)
    master_by_plan: dict[str, str] = {}
    story_names: dict[int, str] = {}
    for piso in bd.pisos:
        similar = master_by_plan.get(piso.plan_tag)
        name = mapper.add_story(piso.story_name, piso.elevation, piso.height, similar)
        master_by_plan.setdefault(piso.plan_tag, name)
        story_names[piso.index] = name
    lowest_plan = bd.pisos[0].plan_tag if bd.pisos else None
    for tag, pr in plans.items():
        if bd.plans[tag].is_base:
            continue                       # fundacao: so referencia de eixos e restricoes
        pisos_of_plan = [p for p in bd.pisos if p.plan_tag == tag]
        names = tuple(story_names[p.index] for p in pisos_of_plan)
        mats = {story_names[p.index]: p.materials for p in pisos_of_plan}
        mapper.add_plan(pr.normalization.model, names, plan_key=tag, materials_by_story=mats,
                        name_prefix=f"{tag}.", restrain_base=(tag == lowest_plan))
    desc, map_diags = mapper.build()
    label = Path(output).name if output else f"{ascii_name(bd.name)}.e2k"
    text = write_e2k_text(desc, config.etabs, label)
    e2k = read_e2k_text(text, config.etabs.decimal_separator)
    diags: list[Diagnostic] = list(map_diags)
    first = True
    for tag, pr in plans.items():
        if bd.plans[tag].is_base:
            continue
        diags.extend(verify_export(pr.normalization.model, desc, e2k, plan_key=tag, name_prefix=f"{tag}.",
                                   check_counts=first))
        first = False
    out_path = None
    if output:
        out_path = Path(output)
        out_path.write_text(text, encoding="ascii", errors="replace", newline="\r\n")
    return BuildingResult(bd, plans, desc, text, out_path, tuple(diags))


# ------------------------------------------------------------------ relatorio

def format_building_report(result: BuildingResult, verbose: bool = False) -> str:
    bd = result.definition
    out: list[str] = []
    add = out.append
    add("TQS Building")
    add("------------")
    add(f"Edificio: {bd.name}   Pasta: {bd.folder}")
    add(f"Plantas: {len(bd.plans)}   Pisos: {len(bd.pisos)}   Base: {bd.base_elevation:.2f} m"
        f"{' (planta ' + bd.base_plan_tag + ')' if bd.base_plan_tag else ''}")
    for tag, plan in bd.plans.items():
        pisos = [p.index for p in bd.pisos if p.plan_tag == tag]
        add(f"  {tag:10} {plan.name:20} LDF={Path(plan.ldf_path).name:22} LST={'sim' if plan.lst_path else 'nao':3} "
            f"pisos={pisos if pisos else '(fundacao)'}")
    add("")
    add("Pisos (de baixo para cima)")
    add("--------------------------")
    for p in bd.pisos:
        add(f"  {p.index:2} {p.story_name:16} cota {p.elevation:7.2f}  PD {p.height:5.2f}  planta {p.plan_tag:10} "
            f"pilares {p.materials.get('pilares')}  vigas {p.materials.get('vigas')}  lajes {p.materials.get('lajes')}")
    for k, v in bd.sources.items():
        add(f"  fonte {k}: {v}")
    add("")
    for d in bd.diagnostics:
        if d.level != Level.INFO or verbose:
            add("  " + d.format())
    add("")
    for tag, pr in result.plans.items():
        m = pr.normalization.model
        val = pr.normalization.engine.step("validate_model").stats
        add(f"Plan {tag}: pisos {list(pr.stories) or '(fundacao)'}: {len(m.columns)} pilares, {len(m.beams)} vigas, "
            f"{len(m.slabs)} lajes; eixos alinhados: {pr.aligned}; validacao {val['ERROR']} erros / {val['WARNING']} avisos")
        if verbose:
            add(format_audit_report(pr.normalization))
            add("")
    add("")
    add("ETABS generation")
    add("----------------")
    d = result.description
    c = d.counts()
    add(f"Stories: {[s.name for s in d.stories]}")
    add(f"Points: {c['points']}   Frames: {c['frames']} ({c['frame_assignments']} atribuicoes)   "
        f"Areas: {c['walls'] + c['slabs'] + c['openings']} (walls {c['walls']}, slabs {c['slabs']}, openings {c['openings']}; "
        f"{c['area_assignments']} atribuicoes)   Piers: {c['piers']}")
    add(f"Grids: {[g.label + '=' + fmt(g.coordinate) for g in d.grids]}")
    add(f"Materials: {[(m.name, int(m.e_kn_m2 / 1000), m.source) for m in d.materials]}")
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
