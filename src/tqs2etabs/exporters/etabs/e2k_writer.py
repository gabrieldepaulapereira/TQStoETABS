"""Escritor do arquivo texto .e2k do ETABS (File > Import > ETABS .e2k Text File).

Formato observado em arquivos gravados pelo ETABS 23.2 (docs/E2K_FORMAT.md):
  - secoes iniciadas por "$ TITULO", registros com 2 espacos de recuo;
  - separador decimal = o do Windows (pt-BR: virgula) -> configuravel;
  - pontos 2D; o pavimento e atribuido no objeto (POINTASSIGN/LINEASSIGN/AREAASSIGN);
  - LINE "nome" COLUMN pi pj 1  (1 = ponto i no pavimento de baixo); BEAM pi pj 0;
  - AREA "nome" PANEL 4 a b b a 1 1 0 0 (dois primeiros pontos no pavimento de baixo);
  - AREA "nome" FLOOR n p1..pn 0..0.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ...domain.config import EtabsOptions
from .description import EtabsDescription


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


def write_e2k_text(desc: EtabsDescription, opt: EtabsOptions, file_label: str = "model.e2k") -> str:
    f = E2kFormatter(opt.decimal_separator)
    n = f.num
    story = desc.story.name
    L: list[str] = []
    add = L.append

    add(f"$ File {file_label} saved {_dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    add(" ")
    add("$ PROGRAM INFORMATION")
    add(f'  PROGRAM  "ETABS"  VERSION "{opt.version}"  ')
    add("")
    add("$ CONTROLS")
    add(f'  UNITS  "{desc.units[0]}"  "{desc.units[1]}"  "{desc.units[2]}"  ')
    add(f'  TITLE1  "{desc.title}"  ')
    add('  TITLE2  "Gerado por tqs2etabs a partir do LDF/LST do TQS"  ')
    add(f"  PREFERENCE  MERGETOL {n(0.001)}")
    add('  RLLF  METHOD "UBC97"  USEDEFAULTMIN "YES"  ')
    add("")
    add("$ STORIES - IN SEQUENCE FROM TOP")
    for s in desc.stories:
        if s.is_base:
            add(f'  STORY "{s.name}"  ELEV {n(s.elevation)} ')
        else:
            add(f'  STORY "{s.name}"  HEIGHT {n(s.height)} ')
    add("")
    add("$ GRIDS")
    add(f'  GRIDSYSTEM "{desc.grid_system}"  TYPE "CARTESIAN"  BUBBLESIZE {n(1.25)} ')
    for g in desc.grids:
        loc = "End" if g.direction == "X" else "Start"
        add(f'  GRID "{desc.grid_system}"  LABEL "{g.label}"  DIR "{g.direction}"  COORD {n(g.coordinate)} '
            f'VISIBLE "Yes"  BUBBLELOC "{loc}"  ')
    add("")
    add("$ MATERIAL PROPERTIES")
    for m in desc.materials:
        add(f'  MATERIAL  "{m.name}"    TYPE "Concrete"    WEIGHTPERVOLUME {n(m.unit_weight)}')
        add(f'  MATERIAL  "{m.name}"    SYMTYPE "Isotropic"  E {f.big(m.e_kn_m2)}  U {n(m.poisson)}  A {n(m.thermal, 8)}')
        add(f'  MATERIAL  "{m.name}"    FC {f.big(m.fck_mpa * 1000)}')
    add("")
    add("$ FRAME SECTIONS")
    for s in desc.frame_sections:
        add(f'  FRAMESECTION  "{s.name}"  MATERIAL "{s.material}"  SHAPE "Concrete Rectangular"  '
            f'D {n(s.depth)} B {n(s.width)} ')
    add("")
    add("$ CONCRETE SECTIONS")
    for s in desc.frame_sections:
        if s.kind == "Beam":
            add(f'  CONCRETESECTION  "{s.name}"  TYPE "Beam"  COVERTOP {n(0.04)} COVERBOTTOM {n(0.04)} ')
        else:
            add(f'  CONCRETESECTION  "{s.name}"  TYPE "Column"  DESIGNCHECK "DESIGN"  COVER {n(0.04)} ')
    add("")
    add("$ SLAB PROPERTIES")
    for s in desc.shell_sections:
        if s.kind == "Slab":
            add(f'  SHELLPROP  "{s.name}"  PROPTYPE  "Slab"  MATERIAL "{s.material}"  MODELINGTYPE "{s.modeling}"  '
                f'SLABTYPE "Slab"  SLABTHICKNESS {n(s.thickness)} ')
    add("")
    add("$ WALL PROPERTIES")
    for s in desc.shell_sections:
        if s.kind == "Wall":
            add(f'  SHELLPROP  "{s.name}"  PROPTYPE  "Wall"  MATERIAL "{s.material}"  MODELINGTYPE "{s.modeling}"  '
                f'WALLTHICKNESS {n(s.thickness)} ')
    add("")
    add("$ PIER/SPANDREL NAMES ")
    for p in desc.piers:
        add(f'  PIERNAME  "{p}"  ')
    add("")
    add("$ POINT COORDINATES")
    for p in desc.points:
        add(f'  POINT "{p.name}"  {n(p.x)} {n(p.y)} ')
    add("")
    add("$ LINE CONNECTIVITIES")
    for fr in desc.frames:
        flag = 1 if fr.kind == "COLUMN" else 0
        add(f'  LINE  "{fr.name}"  {fr.kind}  "{fr.point_i}"  "{fr.point_j}"  {flag}')
    add("")
    add("$ AREA CONNECTIVITIES")
    for a in desc.areas:
        pts = "  ".join(f'"{p}"' for p in a.points)
        if a.kind == "PANEL":
            flags = "1  1  0  0"
        else:
            flags = "  ".join("0" for _ in a.points)
        add(f'  AREA "{a.name}"  {a.kind}  {len(a.points)}  {pts}  {flags}  ')
    add("")
    add("$ POINT ASSIGNS")
    for p in desc.points:
        add(f'  POINTASSIGN  "{p.name}"  "{story}"  USERJOINT  "Yes"  ')
    for r in desc.restraints:
        add(f'  POINTASSIGN  "{r.point}"  "{r.story}"  RESTRAINT "{r.dofs}"  ')
    add("")
    add("$ LINE ASSIGNS")
    for fr in desc.frames:
        rel = f'RELEASE "{fr.releases}"  ' if fr.releases else ""
        if fr.kind == "COLUMN":
            ang = f"ANG  {n(fr.angle)} " if abs(fr.angle) > 1e-9 else ""
            add(f'  LINEASSIGN  "{fr.name}"  "{fr.story}"  SECTION "{fr.section}"  {rel}CARDINALPT {fr.cardinal_point}  '
                f'{ang}MINNUMSTA 3 AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
        else:
            add(f'  LINEASSIGN  "{fr.name}"  "{fr.story}"  SECTION "{fr.section}"  {rel}CARDINALPT {fr.cardinal_point}  '
                f'MAXSTASPC {n(0.5)} AUTOMESH "YES"  MESHATINTERSECTIONS "YES"  ')
    add("")
    add("$ AREA ASSIGNS")
    for a in desc.areas:
        if a.kind == "PANEL":
            pier = f'PIER "{a.pier}"  ' if a.pier else ""
            add(f'  AREAASSIGN  "{a.name}"  "{a.story}"  SECTION "{a.section}"  {pier}OBJMESHTYPE "DEFAULT"  '
                f'ADDRESTRAINT "Yes"  CARDINALPOINT "MIDDLE"  TRANSFORMSTIFFNESSFOROFFSETS "No"  ')
        else:
            add(f'  AREAASSIGN  "{a.name}"  "{a.story}"  SECTION "{a.section}"  ADDRESTRAINT "No"  '
                f'CARDINALPOINT "TOP"  TRANSFORMSTIFFNESSFOROFFSETS "No"  ')
    add("")
    add("$ LOAD PATTERNS")
    add('  LOADPATTERN "DEAD"  TYPE  "Dead"  SELFWEIGHT  1')
    add("")
    add("$ ANALYSIS OPTIONS")
    add('  ACTIVEDOF "UX UY UZ RX RY RZ"  ')
    add(f'  AUTOMESHOPTIONS  MESHTYPE  "GENERAL"  FLOORMESHMAXSIZE  {n(opt.floor_mesh_max)} WALLMESHMAXSIZE  {n(opt.wall_mesh_max)} ')
    add("")
    add("$ MASS SOURCE")
    add('  MASSSOURCE  "MsSrc1"    INCLUDEELEMENTS "Yes"    INCLUDEADDEDMASS "Yes"    INCLUDELOADS "No"    '
        'INCLUDEMOVE "No"    INCLUDELATERALMASS "Yes"    INCLUDEVERTICALMASS "No"    LUMPATSTORIES "Yes"    ISDEFAULT "Yes"  ')
    add("")
    add("$ LOAD CASES")
    add('  LOADCASE "DEAD"  TYPE  "Linear Static"  INITCOND  "PRESET"  ')
    add('  LOADCASE "DEAD"  LOADPAT  "DEAD"  SF  1 ')
    add("")
    add("$ PROJECT INFORMATION")
    add(f'  PROJECTINFO    COMPANYNAME "{opt.company}"    MODELNAME "{desc.title}"  ')
    add("")
    add("$ LOG")
    add("  STARTCOMMENTS  ")
    add(f"tqs2etabs: {len(desc.points)} pontos, {len(desc.frames)} barras, {len(desc.areas)} areas")
    for note in desc.notes:
        add(f"tqs2etabs: {note}")
    add("  ENDCOMMENTS  ")
    add("")
    add("$ END OF MODEL FILE")
    add("")
    return "\n".join(L)


def write_e2k_file(desc: EtabsDescription, opt: EtabsOptions, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(write_e2k_text(desc, opt, path.name), encoding="ascii", errors="replace", newline="\r\n")
    return path
