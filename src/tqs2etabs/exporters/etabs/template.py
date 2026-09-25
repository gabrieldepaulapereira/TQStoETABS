"""Template .e2k: reaproveita as definicoes e as configuracoes de analise de um modelo do escritorio.

O template NAO traz geometria (pontos, barras, areas, stories, grids, grupos, cargas de objeto):
essas secoes vem sempre do modelo gerado a partir do TQS. Do template vem:

- definicoes nomeadas (LIBRARY_SECTIONS): materiais, secoes de barra/laje/parede, diafragmas,
  funcoes, conjuntos de carga de shell -> o template vence quando o nome coincide (excecao: material
  com E escolhido pelo usuario na aba Materiais);
- configuracao (CONFIG_SECTIONS): opcoes de analise (P-Delta, malha), mass source e preferencias
  de dimensionamento -> substituem integralmente o que o escritor geraria;
- casos e combinacoes: LOAD PATTERNS (merge), LOAD CASES e LOAD COMBINATIONS (do template, filtrados).

Como o template foi gravado para OUTRO edificio, o que referencia objetos que nao existem aqui e
corrigido ou descartado, sempre com diagnostico:

- casos de construcao sequencial (`Nonlinear Static Staged Construction`) tem os estagios refeitos
  sobre os pavimentos deste modelo (um estagio por pavimento, de baixo para cima, com os mesmos
  padroes de carga do template);
- load pattern citado por um caso do template mas nao definido nele e **criado vazio** (sem cargas),
  para que o caso e as combinacoes existam desde ja — e o que permite lancar depois as cargas de tunel
  de vento nas combinacoes que ja vieram prontas;
- caso cuja condicao inicial (INITCOND) aponta para um caso inexistente e descartado; combinacao que
  perde a referencia sai junto (ponto fixo).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ...domain.diagnostics import Diagnostic, DiagnosticCollector, Source

_QUOTED = re.compile(r'"([^"]*)"')
_TOK = re.compile(r'"[^"]*"|\S+')

# Definicoes nomeadas: template e modelo se completam.
LIBRARY_SECTIONS = ("DIAPHRAGM NAMES", "MATERIAL PROPERTIES", "REBAR DEFINITIONS", "FRAME SECTIONS",
                    "CONCRETE SECTIONS", "TENDON SECTIONS", "SLAB PROPERTIES", "DECK PROPERTIES",
                    "WALL PROPERTIES", "LINK PROPERTIES", "PANEL ZONE PROPERTIES",
                    "POINT SPRING PROPERTIES", "FUNCTIONS", "SHELL UNIFORM LOAD SETS")
# Configuracao: o template substitui integralmente.
CONFIG_SECTIONS = ("ANALYSIS OPTIONS", "MASS SOURCE", "STEEL DESIGN PREFERENCES",
                   "CONCRETE DESIGN PREFERENCES", "COMPOSITE DESIGN PREFERENCES",
                   "COMPOSITE COLUMN DESIGN PREFERENCES", "WALL DESIGN PREFERENCES",
                   "CONCRETE SLAB DESIGN PREFERENCES", "CONCRETE SHELL DESIGN PREFERENCES")
PATTERN_SECTION = "LOAD PATTERNS"
CASE_SECTION = "LOAD CASES"
COMBO_SECTION = "LOAD COMBINATIONS"
# Nunca vem do template (geometria/atribuicoes deste modelo ou referencias a objetos de outro).
IGNORED_SECTIONS = ("PROGRAM INFORMATION", "STORIES - IN SEQUENCE FROM TOP", "GRIDS", "PIER/SPANDREL NAMES ",
                    "POINT COORDINATES", "LINE CONNECTIVITIES", "AREA CONNECTIVITIES",
                    "POINT ASSIGNS", "LINE ASSIGNS", "AREA ASSIGNS", "POINT OBJECT LOADS",
                    "FRAME OBJECT LOADS", "SHELL OBJECT LOADS", "GENERALIZED DISPLACEMENTS",
                    "DIMENSION LINES", "DEVELOPED ELEVATIONS", "TABLE SETS", "PROJECT INFORMATION", "LOG")

STAGED_TYPE = "Nonlinear Static Staged Construction"
_WIND_RE = re.compile(r"(^|[-_ ])(WT|WIND|VENT)|W\d+YR|WX|WY", re.IGNORECASE)
_SEISMIC_RE = re.compile(r"(^|[-_ ])(EQ|SEIS|SISM)", re.IGNORECASE)


def guessed_pattern_type(name: str) -> str:
    """TYPE de um load pattern que o template cita mas nao define (vento de tunel, sismo, outros)."""
    if _WIND_RE.search(name):
        return "Wind"
    if _SEISMIC_RE.search(name):
        return "Seismic"
    return "Other"


def object_name(line: str) -> str | None:
    """Nome do objeto ao qual a linha pertence: o primeiro texto entre aspas."""
    m = _QUOTED.search(line)
    return m.group(1) if m else None


def tokens(line: str) -> list[str]:
    return [t[1:-1] if t.startswith('"') else t for t in _TOK.findall(line)]


def _value_after(toks: list[str], key: str) -> str | None:
    try:
        return toks[toks.index(key) + 1]
    except (ValueError, IndexError):
        return None


def parse_sections(text: str) -> dict[str, list[str]]:
    """Linhas (sem alterar) por secao `$ NOME`, ignorando linhas em branco."""
    out: dict[str, list[str]] = {}
    section = ""
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.strip():
            continue
        if s.lstrip().startswith("$"):
            section = s.lstrip()[1:].strip()
            out.setdefault(section, [])
            continue
        if section:
            out.setdefault(section, []).append(s)
    return out


def group_by_object(lines: list[str]) -> dict[str, list[str]]:
    """Linhas agrupadas pelo nome do objeto, preservando a ordem de aparicao."""
    out: dict[str, list[str]] = {}
    for line in lines:
        name = object_name(line)
        if name is not None:
            out.setdefault(name, []).append(line)
    return out


_NOISY_SECTIONS = ("LOG", "PROJECT INFORMATION", "TABLE SETS")


def detect_separator(text: str) -> str:
    """Separador decimal do arquivo (o ETABS grava no separador do Windows).

    So conta numeros fora de aspas e fora das secoes de texto livre: o `$ LOG` guarda o historico do
    ETABS com datas e caminhos (`v1.1.EDB`, `10/13/2010 3:07:02`) e sozinho inverteria a deteccao."""
    sections = parse_sections(text) if "$" in text else {"": text.splitlines()}
    useful = [l for name, lines in sections.items() if name not in _NOISY_SECTIONS for l in lines]
    clean = re.sub(r'"[^"]*"', '""', chr(10).join(useful) or text)
    comma = len(re.findall(r"\d,\d", clean))
    dot = len(re.findall(r"\d\.\d", clean))
    return "," if comma >= dot else "."


@dataclass(frozen=True, slots=True)
class E2kTemplate:
    """Arquivo .e2k lido como template (secoes cruas + separador decimal de origem)."""
    source: str
    sections: dict[str, list[str]]
    separator: str

    def lines(self, section: str) -> list[str]:
        return list(self.sections.get(section, ()))

    @property
    def title(self) -> str:
        for line in self.sections.get("CONTROLS", ()):
            toks = tokens(line)
            if toks and toks[0].upper() == "TITLE1" and len(toks) > 1:
                return toks[1]
        return Path(self.source).stem


@dataclass(frozen=True, slots=True)
class TemplatePlan:
    """O que o escritor deve usar do template, ja resolvido para este modelo."""
    source: str
    separator: str
    sections: dict[str, list[str]] = field(default_factory=dict)      # secao -> linhas prontas
    replaced: dict[str, set[str]] = field(default_factory=dict)       # secao -> nomes que o template define
    prefer_ours: dict[str, set[str]] = field(default_factory=dict)    # secao -> nomes em que o nosso vence
    notes: tuple[str, ...] = ()

    def has(self, section: str) -> bool:
        return section in self.sections

    def owns(self, section: str, name: str) -> bool:
        """True quando o template ja define esse objeto (o nosso nao deve ser escrito)."""
        return name in self.replaced.get(section, set()) and name not in self.prefer_ours.get(section, set())


def load_template(path: Path | str) -> E2kTemplate:
    p = Path(path)
    text = p.read_text(encoding="latin-1")
    return read_template(text, str(p))


def read_template(text: str, source: str = "template.e2k") -> E2kTemplate:
    return E2kTemplate(source, parse_sections(text), detect_separator(text))


# --------------------------------------------------------------------------- resolucao
def _staged_patterns(lines: list[str]) -> list[str]:
    """Padroes carregados pelos estagios do template, na ordem em que aparecem."""
    out: list[str] = []
    for line in lines:
        toks = tokens(line)
        if "LOADNAME" in toks:
            name = _value_after(toks, "LOADNAME")
            if name and name not in out:
                out.append(name)
    return out


def _rebuild_staged(case: str, lines: list[str], stories: tuple[str, ...], sep: str) -> list[str]:
    """Estagios refeitos sobre os pavimentos deste modelo (um por pavimento, de baixo para cima)."""
    header = [l for l in lines if "STAGE" not in tokens(l)]
    pats = _staged_patterns(lines)
    out = list(header)
    for i, story in enumerate(reversed(stories), start=1):          # stories vem de cima para baixo
        stage = f"Stage{i}"
        out.append(f'  LOADCASE "{case}"  STAGE  "{stage}"  PROVIDEOUTPUT  "Yes"  ')
        out.append(f'  LOADCASE "{case}"  STAGE  "{stage}"  OPERATION  "Add Structure"  '
                   f'OBJECTTYPE  "Story"  OBJECTNAME  "{story}"  AGE  0 ')
        for pat in pats:
            out.append(f'  LOADCASE "{case}"  STAGE  "{stage}"  OPERATION  "Load Objects If Added"  '
                       f'OBJECTTYPE  "Group"  OBJECTNAME  "All"  LOADTYPE  "Load Pattern"  '
                       f'LOADNAME  "{pat}"  SF  1 ')
    return out


def _case_patterns(lines: list[str]) -> set[str]:
    used: set[str] = set()
    for line in lines:
        toks = tokens(line)
        for key in ("LOADPAT", "LOADNAME"):
            if key in toks:
                v = _value_after(toks, key)
                if v:
                    used.add(v)
    return used


def _case_initconds(lines: list[str]) -> set[str]:
    """Casos usados como condicao inicial (INITCOND diferente de PRESET/NONE/ZERO)."""
    out: set[str] = set()
    for line in lines:
        toks = tokens(line)
        v = _value_after(toks, "INITCOND")
        if v and v.upper() not in ("PRESET", "NONE", "ZERO"):
            out.add(v)
    return out


def _combo_refs(lines: list[str]) -> tuple[set[str], set[str]]:
    cases, combos = set(), set()
    for line in lines:
        toks = tokens(line)
        if "LOADCASE" in toks[1:]:
            v = _value_after(toks[1:], "LOADCASE")
            if v:
                cases.add(v)
        if "LOADCOMBO" in toks:
            v = _value_after(toks, "LOADCOMBO")
            if v:
                combos.add(v)
    return cases, combos


def prepare_template(tpl: E2kTemplate, stories: tuple[str, ...], our_patterns: tuple[str, ...],
                     *, definitions: bool = True, analysis: bool = True, combos: bool = True,
                     prefer_ours_materials: tuple[str, ...] = (),
                     diag: DiagnosticCollector | None = None) -> tuple[TemplatePlan, tuple[Diagnostic, ...]]:
    """Resolve o template para este modelo: merge de definicoes, remap dos estagios e filtragem
    de casos/combinacoes que referenciam objetos inexistentes."""
    diag = diag or DiagnosticCollector()
    sections: dict[str, list[str]] = {}
    replaced: dict[str, set[str]] = {}
    prefer: dict[str, set[str]] = {}
    notes: list[str] = []

    if definitions:
        groups = [l for l in tpl.lines("GROUPS") if len(tokens(l)) <= 2]      # so "GROUP \"NOME\"" (sem membros)
        if groups:
            sections["GROUPS"] = groups
            replaced["GROUPS"] = set(group_by_object(groups))
            if len(groups) < len(tpl.lines("GROUPS")):
                diag.info("TPL-I-GROUPS", f"{len(groups)} grupos criados vazios (os membros pertencem ao modelo "
                          "de origem)", Source.EXPORTER)
        for name in LIBRARY_SECTIONS:
            lines = tpl.lines(name)
            if not lines:
                continue
            sections[name] = lines
            replaced[name] = set(group_by_object(lines))
        if "MATERIAL PROPERTIES" in replaced and prefer_ours_materials:
            keep = {m for m in prefer_ours_materials if m in replaced["MATERIAL PROPERTIES"]}
            if keep:
                prefer["MATERIAL PROPERTIES"] = keep
                sections["MATERIAL PROPERTIES"] = [l for l in sections["MATERIAL PROPERTIES"]
                                                   if object_name(l) not in keep]
                notes.append("Materiais " + ", ".join(sorted(keep)) + ": E da escolha do usuario "
                             "prevalece sobre o do template.")
        for name in CONFIG_SECTIONS:
            lines = tpl.lines(name)
            if lines:
                sections[name] = lines
        ctrl = [l for l in tpl.lines("CONTROLS")
                if tokens(l) and tokens(l)[0].upper() not in ("UNITS", "TITLE1", "TITLE2")]
        if ctrl:
            sections["CONTROLS"] = ctrl

    # --------------------------------------------------------- padroes, casos e combinacoes
    patterns: set[str] = set(our_patterns)
    if analysis:
        pat_lines = tpl.lines(PATTERN_SECTION)
        if pat_lines:
            sections[PATTERN_SECTION] = pat_lines
            replaced[PATTERN_SECTION] = set(group_by_object(pat_lines))
            patterns |= replaced[PATTERN_SECTION]

        case_objs = group_by_object(tpl.lines(CASE_SECTION))
        # pattern citado por um caso mas nao definido no template: criado vazio, para o caso e as
        # combinacoes existirem desde ja (o usuario lanca a carga depois, ex.: tunel de vento)
        missing_pats = sorted({p for lines in case_objs.values() for p in _case_patterns(lines)} - patterns)
        if missing_pats:
            extra = [f'  LOADPATTERN "{n}"  TYPE  "{guessed_pattern_type(n)}"  SELFWEIGHT  0' for n in missing_pats]
            sections[PATTERN_SECTION] = sections.get(PATTERN_SECTION, []) + extra
            replaced[PATTERN_SECTION] = replaced.get(PATTERN_SECTION, set()) | set(missing_pats)
            patterns |= set(missing_pats)
            diag.info("TPL-I-PATTERN-ADD", f"{len(missing_pats)} load patterns criados sem carga para manter os "
                      f"casos e combinacoes do template: " + ", ".join(f"{n} ({guessed_pattern_type(n)})"
                                                                       for n in missing_pats[:10]), Source.EXPORTER)
            notes.append(f"{len(missing_pats)} load patterns sem carga criados (template): "
                         + ", ".join(missing_pats[:6]) + (" ..." if len(missing_pats) > 6 else ""))
        kept: dict[str, list[str]] = {}
        for case, lines in case_objs.items():
            if any(STAGED_TYPE in l for l in lines):
                if not stories:
                    diag.warning("TPL-W-STAGED-DROP", f"Caso sequencial '{case}' descartado: modelo sem pavimentos",
                                 Source.EXPORTER)
                    continue
                lines = _rebuild_staged(case, lines, stories, tpl.separator)
                notes.append(f"Caso sequencial '{case}': estagios refeitos sobre os {len(stories)} "
                             "pavimentos deste modelo.")
            kept[case] = lines
        while True:                                  # ponto fixo: caso pode depender de outro (INITCOND)
            drop = {c for c, l in kept.items() if _case_initconds(l) - set(kept)}
            if not drop:
                break
            for c in drop:
                diag.warning("TPL-W-CASE-DROP", f"Caso '{c}' do template descartado: condicao inicial "
                             "depende de caso inexistente", Source.EXPORTER)
                kept.pop(c)
        if case_objs:
            sections[CASE_SECTION] = [l for lines in kept.values() for l in lines]
            replaced[CASE_SECTION] = set(kept)

        if combos:
            combo_objs = group_by_object(tpl.lines(COMBO_SECTION))
            alive = set(combo_objs)
            while True:                                  # ponto fixo: combo pode referenciar outra combo
                drop = set()
                for name in alive:
                    refs_cases, refs_combos = _combo_refs(combo_objs[name])
                    if (refs_cases - set(kept)) or (refs_combos - alive):
                        drop.add(name)
                if not drop:
                    break
                alive -= drop
            dropped = sorted(set(combo_objs) - alive)
            if dropped:
                diag.warning("TPL-W-COMBO-DROP", f"{len(dropped)} combinacoes do template descartadas por "
                             f"referenciarem casos/combinacoes inexistentes: {', '.join(dropped[:12])}"
                             + (" ..." if len(dropped) > 12 else ""), Source.EXPORTER)
            if combo_objs:
                sections[COMBO_SECTION] = [l for name in combo_objs if name in alive for l in combo_objs[name]]
                replaced[COMBO_SECTION] = set(alive)

        mass = sections.get("MASS SOURCE")
        if mass:
            keep_mass = []
            for line in mass:
                toks = tokens(line)
                if toks and toks[0].upper() == "MASSSOURCELOAD" and len(toks) > 2 and toks[2] not in patterns:
                    diag.info("TPL-I-MASS-DROP", f"Mass source: carga '{toks[2]}' ignorada (pattern inexistente)",
                              Source.EXPORTER)
                    continue
                keep_mass.append(line)
            sections["MASS SOURCE"] = keep_mass

    kept_counts = ", ".join(f"{s.lower()}={len(group_by_object(l)) or len(l)}" for s, l in sorted(sections.items()))
    diag.info("TPL-I-SUMMARY", f"Template '{tpl.title}' ({Path(tpl.source).name}): {kept_counts}", Source.EXPORTER)
    notes.append(f"Template: {Path(tpl.source).name} — definicoes, casos e combinacoes reaproveitados.")
    plan = TemplatePlan(tpl.source, tpl.separator, sections, replaced, prefer, tuple(notes))
    return plan, diag.as_tuple()


def merge_lines(plan: TemplatePlan | None, section: str, ours: list[str]) -> list[str]:
    """Linhas finais de uma secao: template + as nossas que ele nao define."""
    if plan is None or not plan.has(section):
        return ours
    if section in CONFIG_SECTIONS:
        return plan.sections[section]
    if section == "CONTROLS":                    # nossas UNITS/TITLE + preferencias do template
        theirs = plan.sections[section]
        keys = {tokens(l)[0].upper() for l in theirs if tokens(l)}
        return [l for l in ours if tokens(l) and tokens(l)[0].upper() not in keys] + theirs
    mine = [l for l in ours if not plan.owns(section, object_name(l) or "")]
    return plan.sections[section] + mine
