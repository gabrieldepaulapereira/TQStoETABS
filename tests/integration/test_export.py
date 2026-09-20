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
    assert 'GRID "G1"  LABEL "C"  DIR "X"  COORD -11,76 ' in text
    assert 'GRID "G1"  LABEL "1"  DIR "Y"  COORD 15,24 ' in text
    assert 'AREAASSIGN  "L4-O1"  "25 - Tipo"  OPENING "Yes"  ' in text
    assert 'MATERIAL  "C60"    FC 60000' in text
    assert 'FRAMESECTION  "B18X120-C40"  MATERIAL "C40"  SHAPE "Concrete Rectangular"  D 1,2 B 0,18 ' in text
    assert 'SHELLPROP  "S14-C40-M"  PROPTYPE  "Slab"  MATERIAL "C40"  MODELINGTYPE "Membrane"' in text
    assert 'LINE  "V7-1"  BEAM  "4"  "16"  0' in text          # N15 fundido em N4 (ponta do braco)
    assert 'LINE  "V16"  BEAM  "3"  "4"  0' in text
    assert 'SECTION "W60-C60"  PIER  "P3"  OBJMESHTYPE' in text
    assert 'MASTERSTORY "Yes"' in text and text.rstrip().endswith('$ END OF MODEL FILE')
    assert 'POINT "37"  -19,56 24,83 ' in text            # V1 na ponta do P1
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
