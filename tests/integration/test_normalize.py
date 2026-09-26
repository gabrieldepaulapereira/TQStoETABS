"""Pipeline completo sobre o pavimento real (25 - Tipo)."""

import pytest

from tqs2etabs.application.normalize import format_audit_report, normalize
from tqs2etabs.cli import main
from tqs2etabs.domain.config import Config, ModelingPolicy
from tqs2etabs.domain.diagnostics import Level, Source
from tqs2etabs.domain.elements import ColumnKind
from tqs2etabs.domain.geometry import polygon_area, polyline_length


@pytest.fixture(scope="module")
def result(ldf_path, lst_path):
    return normalize(ldf_path, lst_path)


def test_no_errors_no_validation_warnings(result):
    m = result.model
    assert not result.engine.has_errors
    val = result.engine.step("validate_model")
    assert val.stats["ERROR"] == 0 and val.stats["WARNING"] == 0
    # 8 nos fundidos (vigas levadas as pontas/cantos das paredes); 6 lajes rebaixadas absorvidas
    assert (len(m.nodes), len(m.columns), len(m.beams), len(m.slabs)) == (113, 8, 22, 8)


def test_all_coordinates_have_two_decimals(result):
    for n in result.model.nodes.values():
        assert abs(n.x * 100 - round(n.x * 100)) < 1e-6, n
        assert abs(n.y * 100 - round(n.y * 100)) < 1e-6, n
    for g in result.model.grids:
        assert abs(g.coordinate * 100 - round(g.coordinate * 100)) < 1e-6


def test_column_axes(result):
    cols = result.model.columns
    assert all(c.kind_hint == ColumnKind.WALL for c in cols.values())
    assert len(cols["P3"].axes) == 3 and len(cols["P6"].axes) == 3      # U sem tocos: 2 bracos + alma
    for cid in ("P3", "P6"):                                              # cantos exatamente no cruzamento
        ends = [(round(p.x, 2), round(p.y, 2)) for seg in cols[cid].axes for p in (seg.start, seg.end)]
        web = next(seg for seg in cols[cid].axes if seg.direction == "X")
        for corner in ((round(web.start.x, 2), round(web.start.y, 2)), (round(web.end.x, 2), round(web.end.y, 2))):
            assert ends.count(corner) == 2
    assert len(cols["P4"].axes) == 1 and cols["P4"].axes[0].start.x == pytest.approx(-27.95)
    xs = sorted({round(s.start.x, 2) for s in cols["P3"].axes if s.direction == "Y"})
    assert xs == [-11.76, -4.65]
    ys = {round(s.start.y, 2) for s in cols["P3"].axes if s.direction == "X"}
    assert ys == {24.68}
    assert any(d.code == "AXES-I-OPENING-MERGED" and "P4" in d.refs for d in result.model.diagnostics)


def test_beam_end_extensions_match_expected_table(result):
    ext = result.engine.step("extend_beam_ends").stats["extended"]
    expected = {"N37": 0.11, "N26": 0.11, "N35": 0.05, "N34": 0.05, "N36": 0.20, "N27": 0.20,
                "N28": 0.15, "N30": 0.15, "N40": 0.30, "N41": 0.30, "N15": 0.30, "N18": 0.30,
                "N22": 0.30, "N24": 0.30, "N11": 0.30, "N39": 0.30, "N21": 0.25, "N5": 0.25,
                "N19": 0.25, "N14": 0.25, "N38": 0.10, "N25": 0.10, "N33": 0.10, "N31": 0.10}
    assert {k: round(v, 2) for k, v in ext.items()} == expected
    m = result.engine.snapshots["snap_beams_to_wall_ends"]      # antes da regra da ponta
    assert (m.node("N15").x, m.node("N15").y) == (-11.76, 22.21)
    assert (m.node("N38").x, m.node("N38").y) == (-11.76, 24.74)
    assert (m.node("N28").x, m.node("N28").y) == (11.55, 24.21)
    # V16/V22 ja estavam nas linhas medias dos bracos: nao movidas
    assert "N3" not in ext and "N4" not in ext and "N1" not in ext and "N2" not in ext
    assert result.engine.step("snap_beams_transverse").stats["nodes_moved"] == 0
    assert result.engine.step("merge_nodes").stats["merged"] == 8      # extremidades que coincidiram nas pontas/cantos


def test_grids(result):
    grids = [(g.label, g.coordinate) for g in result.model.grids]
    assert grids == [("A", -27.95), ("B", -19.56), ("C", -11.76), ("D", -4.65), ("E", 3.15), ("F", 11.55),
                     ("1", 15.24), ("2", 24.68)]
    by_label = {g.label: g for g in result.model.grids}
    assert by_label["B"].origin_column_ids == ("P1", "P5")
    assert by_label["C"].origin_column_ids == ("P3", "P6")


def test_wall_end_snap_and_slab_rules(result):
    m = result.model
    wall = result.engine.step("snap_beams_to_wall_ends")
    assert wall.stats["beams_moved"] == 10
    # vigas NUNCA inclinadas: o alinhamento inteiro desloca junto. A fachada de cima (V1, V2) vai para o eixo da
    # alma do P3 (24,68), alcancando os cantos do nucleo e continuando sobre P1/P2 (apoio no eixo da parede);
    # a de baixo (V12-V13, V14-V15) vai para a alma do P6 (15,24), conectada tambem a P4/P5/P7/P8
    for b, y in (("V1", 24.68), ("V2", 24.68), ("V12", 15.24), ("V13", 15.24), ("V14", 15.24), ("V15", 15.24)):
        assert {m.node(n).y for n in m.beams[b].axis} == {y}, b
    assert (m.node("N38").x, m.node("N38").y) == (-11.76, 24.68) and (m.node("N33").x, m.node("N33").y) == (-11.76, 15.24)
    assert all(m.node(n).y == 22.14 for n in m.beams["V7"].axis)     # V7 pelas pontas dos bracos do P3
    for beam in m.beams.values():                                      # nenhuma viga inclinada neste pavimento
        pts = [m.node(n).point for n in beam.axis]
        assert len({round(p.x, 4) for p in pts}) == 1 or len({round(p.y, 4) for p in pts}) == 1, beam.id
    # lajes: rebaixos absorvidos, vertices nas linhas medias, aberturas nos vazios
    assert result.engine.step("absorb_offset_slabs").stats["absorbed"] == 6
    assert set(m.slabs) == {"L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"}
    assert len(m.slabs["L4"].holes) == 1 and len(m.slabs["L5"].holes) == 1
    xs = {round(m.node(e.start_node_id).x, 2) for e in m.slabs["L4"].edges}
    assert -27.95 in xs and -27.7 not in xs             # bordo da L4 na linha media do P4, nao na face
    l3 = [(m.node(e.start_node_id).x, m.node(e.start_node_id).y) for e in m.slabs["L3"].edges]
    assert {round(y, 2) for _, y in l3} == {20.64, 22.14}   # L3 virou retangulo
    assert all(len(s.edges) <= 13 for s in m.slabs.values())


def test_lengths_and_areas_preserved_except_recorded_extensions(result):
    orig, final = result.engine.original, result.model
    moved: dict[str, float] = {}
    for step in ("extend_beam_ends", "snap_beams_to_wall_ends", "snap_slab_vertices_to_column_axes"):
        stats = result.engine.step(step).stats
        for k, v in stats.get("extended", stats.get("moved", {})).items():
            moved[k] = moved.get(k, 0.0) + v
    for c in result.model.changes:
        if c.rule == "node-merge":
            moved[c.after] = max(moved.get(c.after, 0.0), moved.get(c.element_id, 0.0))
    for bid, b0 in orig.beams.items():
        b1 = final.beams[bid]
        l0 = polyline_length([orig.node(n).point for n in b0.axis])
        l1 = polyline_length([final.node(n).point for n in b1.axis])
        ends = {b0.axis[0], b0.axis[-1], b1.axis[0], b1.axis[-1]}
        allowed = 0.01 + sum(moved.get(n, 0.0) for n in ends)
        assert abs(l1 - l0) <= allowed + 1e-9, bid
    assert polyline_length([final.node(n).point for n in final.beams["V22"].axis]) == pytest.approx(1.50)
    for sid, s1 in final.slabs.items():
        pts = [final.node(e.start_node_id).point for e in s1.edges]
        ref = s1.tqs_attrs["area_cm2"] * 1e-4
        assert polygon_area(pts) >= ref * 0.95, sid


def test_lst_warnings_mapped_to_actions(result):
    mapped = [d for d in result.model.diagnostics if d.code == "TQS-W-NODE-OFF-COLUMN"]
    assert len(mapped) == 4
    assert all(d.source == Source.TQS_LST and d.level == Level.INFO for d in mapped)
    by_node = {d.refs[2]: d.action for d in mapped}
    assert by_node["N41"].startswith("corrigido") and by_node["N18"].startswith("corrigido")
    assert by_node["N4"].startswith("resolvido") and by_node["N2"].startswith("resolvido")


def test_comparison_counts(result):
    c = result.comparison.counts
    assert c["node"] == {"tqs": 121, "kept": 113, "modified": 113, "removed": 8}
    assert c["beam"]["modified"] == 22 and c["column"]["modified"] == 8
    assert c["slab"] == {"tqs": 14, "kept": 8, "modified": 8, "removed": 6}
    v16 = next(i for i in result.comparison.items if i.element_id == "V16")
    assert v16.reasons == ("Coordinate normalization",)


def test_report_and_cli(result, ldf_path, tmp_path, capsys):
    text = format_audit_report(result)
    for expected in ("Geometry normalization", "Coordinates normalized: 121 nodes", "End extensions: 24 nodes",
                     "X grids: 6", "Y grids: 2", "Errors: 0   Warnings: 0", "BEFORE (-19.67, 24.74)",
                     "AFTER  (-19.56, 24.74)", "TQS warnings mapped"):
        assert expected in text, expected
    rc = main(["normalize", str(ldf_path), "--report", str(tmp_path / "audit.txt"), "--json", str(tmp_path / "m.json")])
    assert rc == 0
    assert (tmp_path / "audit.txt").exists() and (tmp_path / "m.json").exists()


def test_frame_policy_changes_grids(ldf_path, lst_path):
    cfg = Config(policy=ModelingPolicy(wall_aspect_ratio=10.0))
    res = normalize(ldf_path, lst_path, cfg)
    kinds = {cid: c.kind_hint for cid, c in res.model.columns.items()}
    assert kinds["P5"] == ColumnKind.COLUMN and kinds["P4"] == ColumnKind.WALL
    ys = {g.coordinate for g in res.model.grids if g.direction == "Y"}
    assert {15.97, 23.69} <= ys        # centroides de P5/P7 e P1/P2
    # vigas de fachada passam a 79 cm do centroide de P5/P7: excentricidade avisada, nao corrigida
    assert any(d.code == "ALIGN-W-ECCENTRIC" for d in res.model.diagnostics)
