"""Template .e2k: merge de definicoes, remap dos estagios e filtragem de casos/combinacoes."""

from tqs2etabs.exporters.etabs.template import (group_by_object, merge_lines, prepare_template,
                                                read_template, detect_separator)

TEMPLATE = """$ File template.e2k saved 19/09/2026 16:50:05

$ CONTROLS
  UNITS  "KN"  "M"  "C"
  TITLE1  "TS"
  PREFERENCE  MERGETOL 0,00254

$ MATERIAL PROPERTIES
  MATERIAL  "C40"    TYPE "Concrete"    WEIGHTPERVOLUME 25
  MATERIAL  "C40"    SYMTYPE "Isotropic"  E 38000000  U 0,2
  MATERIAL  "C60"    TYPE "Concrete"    WEIGHTPERVOLUME 25
  MATERIAL  "C60"    SYMTYPE "Isotropic"  E 42000000  U 0,2

$ WALL PROPERTIES
  SHELLPROP  "W30-C60"  PROPTYPE  "Wall"  MATERIAL "C60"  MODELINGTYPE "ShellThin"  WALLTHICKNESS 0,3
  SHELLPROP  "W30-C60"  F11MOD 0,7 F22MOD 0,7

$ SHELL UNIFORM LOAD SETS
  SHELLUNIFORMLOADSET "RESIDENTIAL"  LOADPAT "SDL"  VALUE 2

$ LOAD PATTERNS
  LOADPATTERN "DEAD"  TYPE  "Dead"  SELFWEIGHT  1
  LOADPATTERN "SDL"  TYPE  "Super Dead"  SELFWEIGHT  0
  LOADPATTERN "WIND"  TYPE  "Wind"  SELFWEIGHT  0

$ ANALYSIS OPTIONS
  PDELTA  METHOD "ITERATIVE"  TOL 0,0001
  PDELTA  LOAD "DEAD"  FACTOR 1

$ MASS SOURCE
  MASSSOURCE  "MsSrc1"    INCLUDELOADS "Yes"    ISDEFAULT "Yes"
  MASSSOURCELOAD  "MsSrc1"  "DEAD"  1
  MASSSOURCELOAD  "MsSrc1"  "FACADE"  1

$ LOAD CASES
  LOADCASE "Modal"  TYPE  "Modal - Eigen"  INITCOND  "PRESET"
  LOADCASE "DEAD"  TYPE  "Linear Static"  INITCOND  "PRESET"
  LOADCASE "DEAD"  LOADPAT  "DEAD"  SF  1
  LOADCASE "FACADE"  TYPE  "Linear Static"  INITCOND  "PRESET"
  LOADCASE "FACADE"  LOADPAT  "FACADE"  SF  1
  LOADCASE "Seq-DEAD"  TYPE  "Nonlinear Static Staged Construction"  INITCOND  "NONE"
  LOADCASE "Seq-DEAD"  NLGEOMTYPE  "PDelta"
  LOADCASE "Seq-DEAD"  STAGE  "Stage1"  PROVIDEOUTPUT  "Yes"
  LOADCASE "Seq-DEAD"  STAGE  "Stage1"  OPERATION  "Add Structure"  OBJECTTYPE  "Story"  OBJECTNAME  "116-TEC"  AGE  0
  LOADCASE "Seq-DEAD"  STAGE  "Stage1"  OPERATION  "Load Objects If Added"  OBJECTTYPE  "Group"  OBJECTNAME  "All"  LOADTYPE  "Load Pattern"  LOADNAME  "DEAD"  SF  1
  LOADCASE "Pos-Seq"  TYPE  "Linear Static"  INITCOND  "Seq-DEAD"
  LOADCASE "Pos-Seq"  LOADPAT  "SDL"  SF  1

$ LOAD COMBINATIONS
  COMBO "TOTDL"  TYPE "Linear Add"
  COMBO "TOTDL"  LOADCASE "Seq-DEAD"  SF 1
  COMBO "ULS1"  TYPE "Linear Add"
  COMBO "ULS1"  LOADCOMBO "TOTDL"  SF 1,4
  COMBO "COM-FACADE"  TYPE "Linear Add"
  COMBO "COM-FACADE"  LOADCASE "FACADE"  SF 1

$ END OF MODEL FILE
"""


def prepared(stories=("2-Tipo", "1-Tipo"), patterns=("DEAD", "SDL", "LIVE"), **kw):
    tpl = read_template(TEMPLATE, "template.e2k")
    return tpl, *prepare_template(tpl, stories, patterns, **kw)


def test_reads_sections_and_separator():
    tpl = read_template(TEMPLATE, "template.e2k")
    assert tpl.title == "TS" and tpl.separator == ","
    assert len(group_by_object(tpl.lines("MATERIAL PROPERTIES"))) == 2
    assert tpl.lines("LOAD COMBINATIONS")[0].strip().startswith('COMBO "TOTDL"')


def test_case_without_pattern_is_dropped_with_its_combo():
    """FACADE nao existe neste modelo: o caso e a combinacao que o usa saem, o resto fica."""
    _, plan, diags = prepared()
    cases = set(plan.replaced["LOAD CASES"])
    assert cases == {"Modal", "DEAD", "Seq-DEAD", "Pos-Seq"}
    assert plan.replaced["LOAD COMBINATIONS"] == {"TOTDL", "ULS1"}      # COM-FACADE descartada
    assert any(d.code == "TPL-W-CASE-DROP" and "FACADE" in d.message for d in diags)
    # mass source perde a linha da carga inexistente
    assert not any("FACADE" in l for l in plan.sections["MASS SOURCE"])


def test_staged_case_rebuilt_over_our_stories():
    _, plan, _ = prepared()
    staged = [l for l in plan.sections["LOAD CASES"] if '"Seq-DEAD"' in l]
    assert any(l.strip() == 'LOADCASE "Seq-DEAD"  NLGEOMTYPE  "PDelta"' for l in staged)
    assert "116-TEC" not in "\n".join(staged)                          # story do template sumiu
    stages = [l for l in staged if "Add Structure" in l]
    assert len(stages) == 2 and '"Stage1"' in stages[0] and '"1-Tipo"' in stages[0]   # de baixo para cima
    assert '"Stage2"' in stages[1] and '"2-Tipo"' in stages[1]
    assert sum(1 for l in staged if "LOADNAME" in l) == 2               # DEAD em cada estagio


def test_merge_prefers_template_and_keeps_ours():
    _, plan, _ = prepared()
    ours = ['  SHELLPROP  "W30-C60"  PROPTYPE  "Wall"  MATERIAL "C60"  WALLTHICKNESS 0,3 ',
            '  SHELLPROP  "W50-C60"  PROPTYPE  "Wall"  MATERIAL "C60"  WALLTHICKNESS 0,5 ']
    merged = merge_lines(plan, "WALL PROPERTIES", ours)
    assert sum(1 for l in merged if '"W30-C60"' in l) == 2              # so a versao do template (2 linhas)
    assert any("F11MOD" in l for l in merged)                           # com os modificadores do escritorio
    assert any('"W50-C60"' in l for l in merged)                        # a nossa secao extra permanece
    # CONTROLS: nossas UNITS/TITLE + preferencias do template
    ctrl = merge_lines(plan, "CONTROLS", ['  UNITS  "KN"  "M"  "C"  ', "  PREFERENCE  MERGETOL 0,1"])
    assert [l.strip() for l in ctrl] == ['UNITS  "KN"  "M"  "C"', "PREFERENCE  MERGETOL 0,00254"]


def test_material_override_keeps_ours():
    _, plan, _ = prepared(prefer_ours_materials=("C60",))
    assert not any('"C60"' in l for l in plan.sections["MATERIAL PROPERTIES"])
    assert plan.owns("MATERIAL PROPERTIES", "C40") and not plan.owns("MATERIAL PROPERTIES", "C60")


def test_selective_import():
    _, plan, _ = prepared(definitions=False, combos=False)
    assert "MATERIAL PROPERTIES" not in plan.sections and "LOAD COMBINATIONS" not in plan.sections
    assert "LOAD CASES" in plan.sections
    _, plan2, _ = prepared(analysis=False)
    assert "LOAD CASES" not in plan2.sections and "MATERIAL PROPERTIES" in plan2.sections


def test_separator_detection():
    assert detect_separator("COORD 1,25  TOL 0,0001") == ","
    assert detect_separator("COORD 1.25  TOL 0.0001") == "."
