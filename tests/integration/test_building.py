"""Edificio completo (pasta TESTE: fundacao + tipo x5 + tipo 2 + cobertura)."""

from pathlib import Path

import pytest

from tqs2etabs.application.building import convert_building, format_building_report
from tqs2etabs.cli import main
from tqs2etabs.domain.config import Config
from tqs2etabs.domain.diagnostics import Level
from tqs2etabs.exporters.etabs import read_e2k_text
from tqs2etabs.geometry_engine.column_axes import decompose_rectilinear
from tqs2etabs.domain.geometry import Point, polygon_area
from tqs2etabs.importers.tqs.building import plan_tag, read_concrete_catalog, read_resest_materials, scan_building

FIXTURE = Path(__file__).parent.parent / "fixtures" / "TESTE"


@pytest.fixture(scope="module")
def building(tmp_path_factory):
    out = tmp_path_factory.mktemp("bld") / "TESTE.e2k"
    return convert_building(FIXTURE, out)


def test_scan_building():
    bd = scan_building(FIXTURE)
    assert bd.name == "TESTE"
    assert set(bd.plans) == {"FUNDACAO", "TIPO1", "TIPO2", "COBERTURA"}
    assert bd.plans["FUNDACAO"].is_base and bd.base_plan_tag == "FUNDACAO"
    assert bd.base_elevation == pytest.approx(-0.50)
    assert [p.index for p in bd.pisos] == [1, 2, 3, 4, 5, 6, 7]
    assert [p.plan_tag for p in bd.pisos] == ["TIPO1"] * 5 + ["TIPO2", "COBERTURA"]
    assert [p.elevation for p in bd.pisos] == [2.56, 5.62, 8.68, 11.74, 14.8, 17.95, 20.95]
    assert [p.height for p in bd.pisos] == [3.06] * 5 + [3.15, 3.0]
    assert bd.pisos[0].story_name == "1-Tipo" and bd.pisos[6].story_name == "7-Cobertura"
    assert [p.materials["pilares"] for p in bd.pisos] == ["C60"] * 3 + ["C50"] * 4
    assert all(p.materials["vigas"] == "C50" and p.materials["lajes"] == "C50" for p in bd.pisos)
    assert bd.concrete_catalog["C50"].e_secant_mpa == 40000 and bd.concrete_catalog["C60"].e_secant_mpa == 42000
    assert not any(d.level == Level.ERROR for d in bd.diagnostics)
    assert any(d.code == "BLD-I-STALE-LDF" for d in bd.diagnostics)      # Tipo 1/Tipo.LDF ignorado
    assert plan_tag("0 - Fundação") == "FUNDACAO" and plan_tag("Tipo 1") == "TIPO1"


def test_resest_and_concrete_readers():
    mats = read_resest_materials(FIXTURE / "ESPACIAL" / "RESEST2.TXT")
    assert mats[1] == {"pilares": "C60", "vigas": "C50", "lajes": "C50"}
    assert mats[7]["pilares"] == "C50"
    cat = read_concrete_catalog(FIXTURE / "CONCRETO.DAT")
    assert cat["C30"].e_secant_mpa == 36000 and cat["C15"].foundation_only and cat["C20"].e_secant_mpa is None


def test_decompose_rectilinear_l_shape():
    l_shape = (Point(8.0, 0.0), Point(10.3, 0.0), Point(10.3, 0.3), Point(8.3, 0.3), Point(8.3, 3.5), Point(8.0, 3.5))
    rects = decompose_rectilinear(l_shape)
    assert len(rects) == 2
    assert sum(polygon_area(r) for r in rects) == pytest.approx(polygon_area(l_shape))
    assert decompose_rectilinear((Point(0, 0), Point(1, 0), Point(0.5, 1))) == []


def test_building_conversion(building):
    assert not building.has_errors
    bd = building.definition
    desc = building.description
    assert [s.name for s in desc.stories] == ["7-Cobertura", "6-Tipo 2", "5-Tipo", "4-Tipo", "3-Tipo", "2-Tipo", "1-Tipo", "BASE"]
    assert desc.stories[-1].elevation == pytest.approx(-0.5)
    by_name = {s.name: s for s in desc.stories}
    assert by_name["1-Tipo"].master and by_name["2-Tipo"].similar_to == "1-Tipo"
    c = desc.counts()
    assert c["piers"] == 16 and c["slabs"] == 16 and c["columns"] == 7         # P6/P14/P15 (30x87,5) frames x pisos
    assert c["frame_assignments"] > c["frames"] and c["area_assignments"] > c["walls"] + c["slabs"]
    # pilares: 19 nos pisos 1-6 (5 tipo + tipo 2), 6 na cobertura
    plans = building.plans
    assert len(plans["TIPO1"].normalization.model.columns) == 19
    assert len(plans["COBERTURA"].normalization.model.columns) == 6
    assert plans["TIPO1"].stories == ("1-Tipo", "2-Tipo", "3-Tipo", "4-Tipo", "5-Tipo")
    # material por piso: parede W30-C60 nos pisos 1-3 e W30-C50 do 4 em diante
    wall = next(a for a in desc.areas if a.kind == "PANEL" and a.name.startswith("TIPO1.P2"))
    assert [(a.story, a.section) for a in wall.assignments] == [
        ("1-Tipo", "W30-C60"), ("2-Tipo", "W30-C60"), ("3-Tipo", "W30-C60"), ("4-Tipo", "W30-C50"), ("5-Tipo", "W30-C50")]
    assert all(a.pier == "P2" for a in wall.assignments)
    mats = {m.name: m for m in desc.materials}
    assert mats["C60"].e_kn_m2 == 42_000_000 and mats["C60"].source == "CONCRETO.DAT"
    # pilares em L (P17/P18) sem LAMINAS decompostos e exportados como paredes
    assert any(a.name.startswith("TIPO1.P17") for a in desc.areas)
    # origem no canto inferior esquerdo: o canto (0,15; 0,15) do TQS vira (0, 0) e existe em todos os pisos
    assert desc.origin_shift == (-0.15, -0.15)
    corner = next(p for p in desc.points if abs(p.x) < 1e-9 and abs(p.y) < 1e-9)
    assert set(corner.stories) >= {"1-Tipo", "5-Tipo", "6-Tipo 2"}
    # grids com letras/numeros, unidos entre plantas
    assert [g.label for g in desc.grids if g.direction == "X"] == ["A", "B", "C", "D"]
    # restricoes na base para os pontos das paredes/pilares do piso 1
    assert c["restraints"] > 0 and all(r.story == "BASE" for r in desc.restraints)


def test_building_e2k_text(building):
    text = building.e2k_text
    assert 'STORY "5-Tipo"  HEIGHT 3,06 SIMILARTO "1-Tipo"  ' in text
    assert 'STORY "1-Tipo"  HEIGHT 3,06 MASTERSTORY "Yes"  ' in text
    assert 'STORY "BASE"  ELEV -0,5 ' in text
    assert 'MATERIAL  "C60"    SYMTYPE "Isotropic"  E 42000000  U 0,2' in text
    assert text.count('LINEASSIGN  "TIPO1.V1"') == 5          # V1 dividida nas paredes: 1o pedaco mantem o nome
    assert 'SECTION "W30-C60"  PIER  "P2"' in text
    e2k = read_e2k_text(text, ",")
    assert e2k.stories["BASE"] == -0.5 and e2k.stories["6-Tipo 2"] == 3.15
    assert len(e2k.line_assigns) == building.description.counts()["frame_assignments"]
    # coordenadas com 2 casas
    assert all(abs(x * 100 - round(x * 100)) < 1e-6 and abs(y * 100 - round(y * 100)) < 1e-6 for x, y in e2k.points.values())


def test_building_alignment_rules_in_tipo1(building):
    m = building.plans["TIPO1"].normalization.model
    val = building.plans["TIPO1"].normalization.engine.step("validate_model").stats
    assert val["ERROR"] == 0 and val["WARNING"] == 0
    # V1 corre sobre o eixo das paredes P4/P7/P9/P13 (x = 0,15): nao pode ser deslocada para a ponta do P16;
    # os trechos sobre as paredes sao removidos e a viga vira pedacos entre paredes (V1, V1.2, ...)
    assert all(m.node(n).x == 0.15 for b in ("V1", "V1.2", "V1.3") for n in m.beams[b].axis)
    assert m.node(m.beams["V1"].axis[-1]).y == 4.51            # para na ponta do P13
    assert m.node(m.beams["V1.2"].axis[0]).y == 6.26           # recomeca na outra ponta do P13
    ov = building.plans["TIPO1"].normalization.engine.step("trim_beams_over_walls").stats
    assert ov["beams_trimmed"] == 7 and ov["removed_length"] > 30
    # V5 (x = 20,0) vai para a ponta da parede P8 (20,15) e V6 (y = 3,35) para a ponta dos bracos (3,5)
    assert all(m.node(n).x == 20.15 for n in m.beams["V5"].axis)
    assert all(m.node(n).y == 3.5 for n in m.beams["V6"].axis)
    # no interior da V3 sobre P2 levado ao eixo (8,15)
    assert m.node("N11").x == 8.15


def test_building_report_and_cli(building, tmp_path, capsys):
    text = format_building_report(building)
    for expected in ("TQS Building", "Pisos (de baixo para cima)", "1-Tipo", "ETABS generation", "Post-export validation"):
        assert expected in text
    rc = main(["building", str(FIXTURE), "-o", str(tmp_path / "b.e2k"), "--report", str(tmp_path / "r.txt")])
    assert rc == 0 and (tmp_path / "b.e2k").exists()
