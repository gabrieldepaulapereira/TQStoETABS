"""Escritor do arquivo texto .e2k do ETABS (File > Import > ETABS .e2k Text File).

Estrutura copiada de um arquivo gravado pelo ETABS 23.3.1 que importa corretamente
(MARAMBAIA-V40Design.e2k; ver docs/E2K_FORMAT.md): todas as secoes na ordem do ETABS
(vazias ficam so com o cabecalho), materiais STEEL/C<fck>/A615Gr60, REBAR DEFINITIONS,
CONCRETESECTION completa, MASTERSTORY, `  END` antes de `$ END OF MODEL FILE`.
  - separador decimal = o do Windows (pt-BR: virgula) -> configuravel;
  - pontos 2D; o pavimento e atribuido no objeto (POINTASSIGN/LINEASSIGN/AREAASSIGN);
  - LINE "nome" COLUMN pi pj 1 (1 = ponto i no pavimento de baixo); BEAM pi pj 0;
  - AREA "nome" PANEL 4 a b b a 1 1 0 0 (dois primeiros pontos no pavimento de baixo);
  - AREA "nome" FLOOR n p1..pn 0..0; AREAASSIGN ... SECTION "..." PIER "P1" ...
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ...domain.config import EtabsOptions
from . import e2k_template as T
from .description import EtabsDescription
from .template import merge_lines


class E2kFormatter:
    def __init__(self, decimal_separator: str = ",") -> None:
        self.sep = decimal_separator

    def num(self, v: float, decimals: int = 6) -> str:
        if abs(v) < 0.5 * 10 ** (-decimals):
            return "0"
        s = f"{v:.{decimals}f}".rstrip("0").rstrip(".")
        if s in ("-0", ""):
            s = "0"
        return s.replace(".", self.sep)

    def big(self, v: float) -> str:
        """Numeros grandes (E, fc) sem casas decimais."""
        return str(int(round(v)))

    def fixed(self, block: str) -> str:
        """Blocos fixos do template (gravados com virgula) no separador configurado."""
        return block if self.sep == "," else block.replace(",", self.sep)


def write_e2k_text(desc: EtabsDescription, opt: EtabsOptions, file_label: str = "model.e2k") -> str:
    f = E2kFormatter(opt.decimal_separator)
    n = f.num
    story = desc.story.name
    body: dict[str, list[str]] = {name: [] for name in T.SECTION_ORDER}

    def put(section: str, *lines: str) -> None:
        body[section].extend(lines)

    put("PROGRAM INFORMATION", f'  PROGRAM  "ETABS"  VERSION "{opt.version}"  ')
    put("CONTROLS",
        f'  UNITS  "{desc.units[0]}"  "{desc.units[1]}"  "{desc.units[2]}"  ',
        f'  TITLE1  "{desc.title}"  ',
        '  TITLE2  "Gerado por tqs2etabs a partir do LDF/LST do TQS"  ',
        f"  PREFERENCE  MERGETOL {n(0.00254)}",
        '  RLLF  METHOD "UBC97"  USEDEFAULTMIN "YES"  ')
    for s in desc.stories:
        if s.is_base:
            put("STORIES - IN SEQUENCE FROM TOP", f'  STORY "{s.name}"  ELEV {n(s.elevation)} ')
        elif s.similar_to:
            put("STORIES - IN SEQUENCE FROM TOP", f'  STORY "{s.name}"  HEIGHT {n(s.height)} SIMILARTO "{s.similar_to}"  ')
        else:
            put("STORIES - IN SEQUENCE FROM TOP", f'  STORY "{s.name}"  HEIGHT {n(s.height)} MASTERSTORY "Yes"  ')
    put("GRIDS", f'  GRIDSYSTEM "{desc.grid_system}"  TYPE "CARTESIAN"  BUBBLESIZE {n(1.25)} ')
    for g in desc.grids:
        loc = "End" if g.direction == "X" else "Start"
        put("GRIDS", f'  GRID "{desc.grid_system}"  LABEL "{g.label}"  DIR "{g.direction}"  COORD {n(g.coordinate)} '
                     f'VISIBLE "Yes"  BUBBLELOC "{loc}"  ')

    put("MATERIAL PROPERTIES", f.fixed(T.STEEL_MATERIAL))
    for m in desc.materials:
        put("MATERIAL PROPERTIES", f.fixed(T.CONCRETE_MATERIAL).format(
            name=m.name, weight=n(m.unit_weight), e=f.big(m.e_kn_m2), fc=f.big(m.fck_mpa * 1000)))
    put("MATERIAL PROPERTIES", f.fixed(T.REBAR_MATERIAL))
    put("REBAR DEFINITIONS", f.fixed(T.REBAR_DEFINITIONS))

    for s in desc.frame_sections:
        put("FRAME SECTIONS", f'  FRAMESECTION  "{s.name}"  MATERIAL "{s.material}"  SHAPE "Concrete Rectangular"  '
                              f'D {n(s.depth)} B {n(s.width)} ')
        tpl = T.CONCRETE_SECTION_BEAM if s.kind == "Beam" else T.CONCRETE_SECTION_COLUMN
        put("CONCRETE SECTIONS", f.fixed(tpl).format(name=s.name))
    for s in desc.shell_sections:
        if s.kind == "Slab":
            put("SLAB PROPERTIES", f'  SHELLPROP  "{s.name}"  PROPTYPE  "Slab"  MATERIAL "{s.material}"  '
                                   f'MODELINGTYPE "{s.modeling}"  SLABTYPE "Slab"  SLABTHICKNESS {n(s.thickness)} ')
        else:
            put("WALL PROPERTIES", f'  SHELLPROP  "{s.name}"  PROPTYPE  "Wall"  MATERIAL "{s.material}"  '
                                   f'MODELINGTYPE "{s.modeling}"  WALLTHICKNESS {n(s.thickness)} ')
    for p in desc.piers:
        put("PIER/SPANDREL NAMES ", f'  PIERNAME  "{p}"  ')

    for p in desc.points:
        put("POINT COORDINATES", f'  POINT "{p.name}"  {n(p.x)} {n(p.y)} ')
    for fr in desc.frames:
        put("LINE CONNECTIVITIES", f'  LINE  "{fr.name}"  {fr.kind}  "{fr.point_i}"  "{fr.point_j}"  '
                                   f'{1 if fr.kind == "COLUMN" else 0}')
    for a in desc.areas:
        pts = "  ".join(f'"{p}"' for p in a.points)
        flags = "1  1  0  0" if a.kind == "PANEL" else "  ".join("0" for _ in a.points)
        kind = "FLOOR" if a.kind == "OPENING" else a.kind
        put("AREA CONNECTIVITIES", f'  AREA "{a.name}"  {kind}  {len(a.points)}  {pts}  {flags}  ')

    for p in desc.points:
        for st in (p.stories or (story,)):
            put("POINT ASSIGNS", f'  POINTASSIGN  "{p.name}"  "{st}"  USERJOINT  "Yes"  ')
    for r in desc.restraints:
        put("POINT ASSIGNS", f'  POINTASSIGN  "{r.point}"  "{r.story}"  RESTRAINT "{r.dofs}"  ')
    for fr in desc.frames:
        rel = f'RELEASE "{fr.releases}"  ' if fr.releases else ""
        for asg in fr.assignments:
            if fr.kind == "COLUMN":
                ang = f"ANG  {n(fr.angle)} " if abs(fr.angle) > 1e-9 else ""
                put("LINE ASSIGNS", f'  LINEASSIGN  "{fr.name}"  "{asg.story}"  SECTION "{asg.section}"  {rel}'
                                    f'CARDINALPT {fr.cardinal_point}  {ang}MINNUMSTA 3 AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
            else:
                put("LINE ASSIGNS", f'  LINEASSIGN  "{fr.name}"  "{asg.story}"  SECTION "{asg.section}"  {rel}'
                                    f'CARDINALPT {fr.cardinal_point}  MAXSTASPC {n(0.5)} AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
    for a in desc.areas:
        for asg in a.assignments:
            if a.kind == "PANEL":
                pier = f'PIER  "{asg.pier}"  ' if asg.pier else ""
                put("AREA ASSIGNS", f'  AREAASSIGN  "{a.name}"  "{asg.story}"  SECTION "{asg.section}"  {pier}OBJMESHTYPE "DEFAULT"  '
                                    f'ADDRESTRAINT "Yes"  CARDINALPOINT "MIDDLE"  TRANSFORMSTIFFNESSFOROFFSETS "No"  ')
            elif a.kind == "OPENING":
                put("AREA ASSIGNS", f'  AREAASSIGN  "{a.name}"  "{asg.story}"  OPENING "Yes"  ')
            else:
                put("AREA ASSIGNS", f'  AREAASSIGN  "{a.name}"  "{asg.story}"  SECTION "{asg.section}"  OBJMESHTYPE "DEFAULT"  '
                                    f'ADDRESTRAINT "No"  CARDINALPOINT "TOP"  TRANSFORMSTIFFNESSFOROFFSETS "No"  ')

    patterns = desc.load_patterns or (type("P", (), {"name": "DEAD", "kind": "Dead", "self_weight": 1.0, "mass_factor": 1.0})(),)
    for lp in patterns:
        put("LOAD PATTERNS", f'  LOADPATTERN "{lp.name}"  TYPE  "{lp.kind}"  SELFWEIGHT  {n(lp.self_weight)}')
    for ll in desc.line_loads:
        put("FRAME OBJECT LOADS", f'  LINELOAD  "{ll.frame}"  "{ll.story}"  TYPE "UNIFF"  DIR "GRAV"  LC "{ll.pattern}"  FVAL {n(ll.value)}')
    for al in desc.area_loads:
        put("SHELL OBJECT LOADS", f'  AREALOAD  "{al.area}"  "{al.story}"  TYPE "UNIFF"  DIR "GRAV"  LC "{al.pattern}"  FVAL {n(al.value)}')
    put("ANALYSIS OPTIONS",
        '  ACTIVEDOF "UX UY UZ RX RY RZ"  ',
        '  MODELHINGESINLINKS "No"  ',
        f'  AUTOMESHOPTIONS  MESHTYPE  "GENERAL"  FLOORMESHMAXSIZE  {n(opt.floor_mesh_max)} WALLMESHMAXSIZE  {n(opt.wall_mesh_max)} ')
    put("MASS SOURCE", f.fixed(T.MASS_SOURCE_HEADER))
    for lp in patterns:
        if lp.mass_factor > 0:
            put("MASS SOURCE", f'  MASSSOURCELOAD  "MsSrc1"  "{lp.name}"  {n(lp.mass_factor)} ')
    put("LOAD CASES",
        '  LOADCASE "Modal"  TYPE  "Modal - Eigen"  INITCOND  "PRESET"  ',
        '  LOADCASE "Modal"  MAXMODES  12 MINMODES  12 EIGENSHIFTFREQ  0 EIGENCUTOFF  0 EIGENTOL  1E-07 ')
    for lp in patterns:
        put("LOAD CASES",
            f'  LOADCASE "{lp.name}"  TYPE  "Linear Static"  INITCOND  "PRESET"  ',
            f'  LOADCASE "{lp.name}"  LOADPAT  "{lp.name}"  SF  1 ')
    for name, block in T.DESIGN_PREFERENCES.items():
        put(name, f.fixed(block))
    put("DIMENSION LINES", f"  DIMLINE DEFAULTSYSTEM {desc.grid_system}")
    put("PROJECT INFORMATION", f'  PROJECTINFO    COMPANYNAME "{opt.company}"    MODELNAME "{desc.title}"  ')
    # LOG: so texto simples, como o ETABS grava (as notas do mapeamento ficam no relatorio)
    put("LOG", "  STARTCOMMENTS  ",
        f"tqs2etabs generated {len(desc.points)} points {len(desc.frames)} lines {len(desc.areas)} areas "
        f"at {_dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        "  ENDCOMMENTS  ", "", "  END")

    tpl = desc.template
    if tpl is not None and tpl.separator != opt.decimal_separator:
        # o template foi gravado com outro separador decimal: converte os numeros das linhas cruas
        tpl = _reseparated(tpl, opt.decimal_separator)

    L: list[str] = [f"$ File {file_label} saved {_dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", " "]
    for name in T.SECTION_ORDER:
        L.append(f"$ {name}")
        L.extend(merge_lines(tpl, name, body[name]))
        L.append("")
    L.append("$ END OF MODEL FILE")
    L.append("")
    return "\n".join(L)


def write_e2k_file(desc: EtabsDescription, opt: EtabsOptions, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(write_e2k_text(desc, opt, path.name), encoding="ascii", errors="replace", newline="\r\n")
    return path


def _reseparated(plan, separator: str):
    """Template gravado com outro separador decimal: troca apenas em numeros (1,5 <-> 1.5)."""
    import re
    from dataclasses import replace as _replace
    src, dst = (",", ".") if separator == "." else (".", ",")
    pat = re.compile(rf"(?<=\d){re.escape(src)}(?=\d)")
    sections = {name: [pat.sub(dst, line) for line in lines] for name, lines in plan.sections.items()}
    return _replace(plan, sections=sections, separator=separator)
