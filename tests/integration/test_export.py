"""Exportacao E2K do pavimento real."""

import pytest

from tqs2etabs.application.export import export_e2k, format_export_report
from tqs2etabs.cli import main
from tqs2etabs.domain.config import Config, ModelingPolicy
from tqs2etabs.domain.diagnostics import Level
from tqs2etabs.exporters.etabs import read_e2k_text


@pytest.fixture(scope="module")
def result(ldf_path, lst_path, tmp_path_factory):
    out = tmp_path_factory.mktemp("e2k") / "25-Tipo.e2k"
    return export_e2k(ldf_path, lst_path, out)


def test_export_counts_and_validation(result):
    assert not result.has_errors
    c = result.description.counts()
    assert c["beams"] == 40 and c["columns"] == 0 and c["slabs"] == 8 and c["openings"] == 2
    assert c["piers"] == 8 and c["grids"] == 8
    assert c["walls"] >= 16
    assert {m.name for m in result.description.materials} == {"C40", "C60"}
    assert {s.name for s in result.description.shell_sections} == {
        "W40-C60", "W50-C60", "W60-C60", "S20-C40-SH", "S14-C40-M"}
    assert result.output_path is not None and result.output_path.exists()


def test_e2k_file_content(result):
    text = result.output_path.read_text(encoding="ascii")
    assert 'STORY "25 - Tipo"  HEIGHT 3,24 ' in text
    assert 'STORY "BASE"  ELEV 71,53 ' in text
    assert 'GRID "G1"  LABEL "C"  DIR "X"  COORD 16,19 ' in text      # origem no canto inferior esquerdo
    assert 'GRID "G1"  LABEL "1"  DIR "Y"  COORD 0,15 ' in text
    assert 'AREAASSIGN  "L4-O1"  "25 - Tipo"  OPENING "Yes"  ' in text
    assert 'MATERIAL  "C60"    FC 60000' in text
    assert 'FRAMESECTION  "B18X120-C40"  MATERIAL "C40"  SHAPE "Concrete Rectangular"  D 1,2 B 0,18 ' in text
    assert 'SHELLPROP  "S14-C40-M"  PROPTYPE  "Slab"  MATERIAL "C40"  MODELINGTYPE "Membrane"' in text
    assert 'LINE  "V7-1"  BEAM  "4"  "16"  0' in text          # N15 fundido em N4 (ponta do braco)
    assert 'LINE  "V16"  BEAM  "3"  "4"  0' in text
    assert 'SECTION "W60-C60"  PIER  "P3"  OBJMESHTYPE' in text
    assert 'MASTERSTORY "Yes"' in text and text.rstrip().endswith('$ END OF MODEL FILE')
    assert 'POINT "37"  8,39 9,59 ' in text               # V1 reta no eixo da alma do P3 (origem transladada)
    assert text.count('RESTRAINT "UX UY UZ RX RY RZ"') == result.description.counts()["restraints"]
    e2k = read_e2k_text(text, ",")
    assert e2k.stories == {"25 - Tipo": 3.24, "BASE": 71.53}
    assert all(abs(x * 100 - round(x * 100)) < 1e-6 and abs(y * 100 - round(y * 100)) < 1e-6
               for x, y in e2k.points.values())


def test_wall_panels_share_joints_with_beams(result):
    """Toda extremidade de viga apoiada em parede e vertice de algum painel dessa parede."""
    desc = result.description
    model = result.normalization.model
    panel_points = {}
    for a in desc.areas:
        if a.kind == "PANEL":
            panel_points.setdefault(a.source, set()).update(a.points)
    for beam in model.beams.values():
        for sup in beam.supports:
            if sup.kind.value == "COLUMN":
                assert desc.node_to_point[sup.node_id] in panel_points[sup.ref_id], (beam.id, sup)


def test_report_and_cli(result, ldf_path, tmp_path, capsys):
    text = format_export_report(result)
    assert "ETABS generation" in text and "Post-export validation" in text and "0 erro(s)" in text
    out = tmp_path / "m.e2k"
    rc = main(["export", str(ldf_path), "-o", str(out), "--report", str(tmp_path / "r.txt")])
    assert rc == 0 and out.exists() and (tmp_path / "r.txt").exists()


def test_frame_policy_export(ldf_path, lst_path, tmp_path):
    cfg = Config(policy=ModelingPolicy(wall_aspect_ratio=10.0))
    res = export_e2k(ldf_path, lst_path, tmp_path / "f.e2k", cfg)
    c = res.description.counts()
    assert c["columns"] == 4 and c["piers"] == 4            # P1/P2/P5/P7 como frames
    cols = {f.name: f for f in res.description.frames if f.kind == "COLUMN"}
    assert cols["P5"].section == "C176X40-C60" and cols["P5"].angle == 90.0
    assert not any(d.level == Level.ERROR for d in res.diagnostics)


def test_element_selection_and_e_override(ldf_path, lst_path, tmp_path):
    """Selecao de elementos (sem vigas/lajes) e E adotado pelo usuario por classe."""
    from dataclasses import replace
    from tqs2etabs.domain.materials import material_options, nbr6118_ecs

    base = Config()
    cfg = replace(base, etabs=replace(base.etabs, include_beams=False, include_slabs=False,
                                      e_overrides={"C60": 42000.0}))
    res = export_e2k(ldf_path, lst_path, tmp_path / "sel.e2k", cfg)
    c = res.description.counts()
    assert c["beams"] == 0 and c["slabs"] == 0 and c["walls"] >= 16 and c["piers"] == 8
    assert not any(d.level == Level.ERROR for d in res.diagnostics)
    mats = {m.name: m for m in res.description.materials}
    assert mats["C60"].e_kn_m2 == pytest.approx(42000.0 * 1000.0)
    assert "C40" not in mats                      # so materiais usados (C40 era das lajes)
    assert mats["C60"].source == "escolha do usuario"
    assert nbr6118_ecs(40.0) == pytest.approx(31876.0, abs=1.0)
    text = (tmp_path / "sel.e2k").read_text(encoding="utf-8")
    assert "LINE  " not in text or "  BEAM" not in text
    assert any("nao importados" in n for n in res.description.notes)
    opts = material_options("C50", 40000.0)
    assert opts == {"atual": 40000.0, "prudencio": 40000.0, "nbr6118": 36628.0}   # 0,925 * 5600 * sqrt(50)


def test_origin_at_min_corner_and_bounding_grids(ldf_path, lst_path, tmp_path):
    """(0,0) no canto inferior esquerdo; com tolerancia pequena, grid em cada extremo do perimetro."""
    from dataclasses import replace
    from tqs2etabs.domain.config import GridNaming

    res = export_e2k(ldf_path, lst_path, tmp_path / "o.e2k")
    d = res.description
    assert d.origin_shift == (27.95, -15.09)
    assert min(p.x for p in d.points) == 0.0 and min(p.y for p in d.points) == 0.0
    assert min(g.coordinate for g in d.grids if g.direction == "X") == 0.0
    assert max(g.coordinate for g in d.grids if g.direction == "X") == max(p.x for p in d.points)

    base = Config()
    cfg = replace(base, grids=replace(base.grids, boundary_tolerance=0.01))
    tight = export_e2k(ldf_path, lst_path, tmp_path / "b.e2k", cfg).description
    for direction, coord in (("X", "x"), ("Y", "y")):
        vals = [getattr(p, coord) for p in tight.points]
        grids = [g.coordinate for g in tight.grids if g.direction == direction]
        assert min(grids) == round(min(vals), 2) and max(grids) == round(max(vals), 2)

    off = replace(base, etabs=replace(base.etabs, origin_at_min_corner=False, bounding_grids=False))
    raw = export_e2k(ldf_path, lst_path, tmp_path / "r.e2k", off).description
    assert raw.origin_shift == (0.0, 0.0) and min(p.x for p in raw.points) == -27.95


def test_export_with_office_template(ldf_path, lst_path, tmp_path):
    """Template .e2k: definicoes e combinacoes do escritorio + geometria do TQS."""
    from dataclasses import replace
    from tqs2etabs.exporters.etabs.template import group_by_object, parse_sections

    tpl = tmp_path / "office.e2k"
    tpl.write_text(TEMPLATE_E2K, encoding="latin-1")
    base = Config()
    cfg = replace(base, etabs=replace(base.etabs, template_path=str(tpl)))
    res = export_e2k(ldf_path, lst_path, tmp_path / "t.e2k", cfg)
    assert not res.has_errors
    sec = parse_sections(res.output_path.read_text(encoding="latin-1"))
    # material do template (com o E do escritorio) em vez do nosso; o que ele nao define continua nosso
    mats = group_by_object(sec["MATERIAL PROPERTIES"])
    assert "E 42000000" in " ".join(mats["C60"]) and "C40" in mats and "STEEL" in mats
    # secoes de parede: a do template vence e traz os modificadores; as nossas extras permanecem
    walls = group_by_object(sec["WALL PROPERTIES"])
    assert "F11MOD" in " ".join(walls["W40-C60"]) and {"W50-C60", "W60-C60"} <= set(walls)
    # casos e combinacoes do template; o caso sequencial foi refeito sobre o pavimento deste modelo
    assert set(group_by_object(sec["LOAD COMBINATIONS"])) == {"TOTDL", "ULS1"}
    staged = " ".join(l for l in sec["LOAD CASES"] if "Seq-DEAD" in l)
    assert "116-TEC" not in staged and '"25 - Tipo"' in staged
    assert "PDELTA" in " ".join(sec["ANALYSIS OPTIONS"])           # opcoes de analise do template
    assert any("MERGETOL 0,00254" in l for l in sec["CONTROLS"])
    assert sum(1 for l in sec["CONTROLS"] if "UNITS" in l) == 1


TEMPLATE_E2K = """$ File office.e2k saved 19/09/2026 16:50:05
 
$ CONTROLS
  UNITS  "KN"  "M"  "C"  
  TITLE1  "Escritorio"  
  PREFERENCE  MERGETOL 0,00254

$ MATERIAL PROPERTIES
  MATERIAL  "C60"    TYPE "Concrete"    WEIGHTPERVOLUME 25
  MATERIAL  "C60"    SYMTYPE "Isotropic"  E 42000000  U 0,2

$ WALL PROPERTIES
  SHELLPROP  "W40-C60"  PROPTYPE  "Wall"  MATERIAL "C60"  MODELINGTYPE "ShellThin"  WALLTHICKNESS 0,4 
  SHELLPROP  "W40-C60"  F11MOD 0,7 F22MOD 0,7 

$ LOAD PATTERNS
  LOADPATTERN "DEAD"  TYPE  "Dead"  SELFWEIGHT  1
  LOADPATTERN "SDL"  TYPE  "Super Dead"  SELFWEIGHT  0
  LOADPATTERN "LIVE"  TYPE  "Live"  SELFWEIGHT  0

$ ANALYSIS OPTIONS
  PDELTA  METHOD "ITERATIVE"  TOL 0,0001
  PDELTA  LOAD "DEAD"  FACTOR 1

$ LOAD CASES
  LOADCASE "Seq-DEAD"  TYPE  "Nonlinear Static Staged Construction"  INITCOND  "NONE"  
  LOADCASE "Seq-DEAD"  STAGE  "Stage1"  OPERATION  "Add Structure"  OBJECTTYPE  "Story"  OBJECTNAME  "116-TEC"  AGE  0 
  LOADCASE "Seq-DEAD"  STAGE  "Stage1"  OPERATION  "Load Objects If Added"  OBJECTTYPE  "Group"  OBJECTNAME  "All"  LOADTYPE  "Load Pattern"  LOADNAME  "DEAD"  SF  1 

$ LOAD COMBINATIONS
  COMBO "TOTDL"  TYPE "Linear Add"  
  COMBO "TOTDL"  LOADCASE "Seq-DEAD"  SF 1 
  COMBO "ULS1"  TYPE "Linear Add"  
  COMBO "ULS1"  LOADCOMBO "TOTDL"  SF 1,4 

$ END OF MODEL FILE
"""
