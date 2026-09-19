"""Geometry Engine: casos sinteticos (ARCHITECTURE.md secoes 10 e 16).

Os modelos sao escritos como mini-LDFs (cm) para exercitar o caminho real
parser -> builder -> engine.
"""

import pytest

from tqs2etabs.domain.config import Config, GridNaming, ModelingPolicy, Tolerances
from tqs2etabs.domain.diagnostics import Level
from tqs2etabs.domain.elements import AxisSegment, EdgeSupport
from tqs2etabs.domain.geometry import Point
from tqs2etabs.geometry_engine import run_engine
from tqs2etabs.geometry_engine.column_axes import join_corners, lamina_axis, merge_collinear
from tqs2etabs.geometry_engine.common import round_to
from tqs2etabs.geometry_engine.normalization import cluster_values
from tqs2etabs.importers.tqs.ldf import build_model, parse_ldf_text


def _model(text: str, config: Config | None = None):
    return build_model(parse_ldf_text(text), None, config or Config())


def _codes(result, level=None):
    return [d.code for d in result.model.diagnostics if level is None or d.level == level]


# ------------------------------------------------------------ primitivas

def test_round_half_up():
    assert round_to(21.244, 2) == 21.24
    assert round_to(21.245, 2) == 21.25
    assert round_to(21.246, 2) == 21.25
    assert round_to(-21.245, 2) == -21.25


def test_cluster_values_column_priority_and_tolerance():
    # 21.244 (pilar) + 21.246 (viga) -> um cluster, representante do pilar -> 21.24
    cl = cluster_values([(21.244, True), (21.246, False), (21.2441, True)], tol=0.005, decimals=2)
    assert len(cl) == 1 and cl[0].representative == 21.24 and cl[0].has_column
    # 9 mm de distancia com tol 5 mm -> clusters separados (alinhamentos legitimos distintos)
    cl = cluster_values([(-27.03263, False), (-27.02365, False)], tol=0.005, decimals=2)
    assert len(cl) == 2
    # sem pilar: media arredondada
    cl = cluster_values([(10.001, False), (10.004, False)], tol=0.005, decimals=2)
    assert cl[0].representative == 10.0


def test_lamina_axis_and_merge():
    a = lamina_axis((Point(0, 0), Point(2, 0), Point(2, 0.3), Point(0, 0.3), Point(0, 0)))
    assert a.thickness == pytest.approx(0.3) and a.direction == "X"
    assert {a.start.y, a.end.y} == {0.15}
    b = lamina_axis((Point(2, 0), Point(3, 0), Point(3, 0.3), Point(2, 0.3)))
    c = lamina_axis((Point(3.8, 0), Point(5, 0), Point(5, 0.3), Point(3.8, 0.3)))   # furo de 0,8 m
    merged, gaps = merge_collinear([a, b, c], gap_max=1.0, tol=0.005)
    assert len(merged) == 1 and gaps == pytest.approx([0.8])
    assert sorted((merged[0].start.x, merged[0].end.x)) == [0.0, 5.0]
    merged, gaps = merge_collinear([a, b, c], gap_max=0.5, tol=0.005)
    assert len(merged) == 2


def test_join_corners_u_shape():
    # U aberto para baixo: bracos x=0 e x=5 (t=0.6, y 0..3), alma y=2.75 (t=0.5) entre faces internas
    arm_l = AxisSegment(Point(0, 0), Point(0, 3), 0.6)
    arm_r = AxisSegment(Point(5, 0), Point(5, 3), 0.6)
    web = AxisSegment(Point(0.3, 2.75), Point(4.7, 2.75), 0.5)
    out = join_corners([arm_l, arm_r, web], tol=0.005)
    assert len(out) == 5
    webs = [s for s in out if s.direction == "X"]
    assert len(webs) == 1 and sorted((webs[0].start.x, webs[0].end.x)) == [0.0, 5.0]
    arms = sorted((min(s.start.y, s.end.y), max(s.start.y, s.end.y)) for s in out if s.direction == "Y")
    assert arms == [(0.0, 2.75), (0.0, 2.75), (2.75, 3.0), (2.75, 3.0)]


# ------------------------------------------------------------- pipeline

LDF_CLUSTER = """\
GEOMETRIA
 1 2124.4,500
 2 2124.4,1000
 3 2124.6,750
 V1 EIXO 1P1 3N 2P2
 P1 1 CON
 P2 2 CON
FIM
DIMENSOES
 V1 S1 20/50 S2 20/50 VOL 1 1
 P1 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
FIM
"""


def test_global_rounding_per_alignment_keeps_length():
    res = run_engine(_model(LDF_CLUSTER), Config())
    m = res.model
    assert {m.node(n).x for n in ("N1", "N2", "N3")} == {21.24}
    assert m.columns["P1"].centroid.x == pytest.approx(21.24)
    assert m.columns["P2"].centroid.x == pytest.approx(21.24)
    assert m.node("N2").y - m.node("N1").y == pytest.approx(5.00)
    reasons = {c.reason for c in m.changes if c.element_id == "N3"}
    assert reasons == {"Coordinate normalization"}
    assert not res.has_errors
    assert "GEO-W-LENGTH-CHANGED" not in _codes(res)


LDF_SNAP = """\
GEOMETRIA
 1 1000.7,0
 2 1000.7,800
 V1 EIXO 1P1 2P2
 P1 1 CON
 P2 2 CON
FIM
DIMENSOES
 V1 S1 20/50 VOL 1 1
 P1 R 300/30 ANG 90 BASE 0,14.3 FCK 'C30'
 P2 R 300/30 ANG 90 BASE 300,14.3 FCK 'C30'
FIM
"""


def test_column_priority_transverse_snap():
    """P1 em X=10.00, viga em X=10.007: P1 fica, viga vai para 10.00 (dentro de beam_column_snap)."""
    res = run_engine(_model(LDF_SNAP), Config())
    m = res.model
    assert m.columns["P1"].centroid.x == pytest.approx(10.00)
    assert m.node("N1").x == 10.00 and m.node("N2").x == 10.00
    snap = res.step("snap_beams_transverse")
    assert snap.stats["nodes_moved"] == 2
    assert all(c.rule.endswith("transverse-snap") and c.reference == "P1" for c in snap.changes)
    # fora da tolerancia: nada se move e a validacao avisa
    cfg = Config(tolerances=Tolerances(beam_column_snap=0.005, wall_end_snap=0.0))
    res2 = run_engine(_model(LDF_SNAP, cfg), cfg)
    assert res2.step("snap_beams_transverse").stats["nodes_moved"] == 0
    assert res2.model.node("N1").x == pytest.approx(10.01)
    assert "ALN-W-END-OFF-AXIS" in _codes(res2, Level.WARNING)


LDF_EXTEND = """\
GEOMETRIA
 1 15,150
 2 500,150
 3 -20,150
 V1 EIXO 1P1 2P2
 V2 EIXO 3P3 1AV1
 P1 1 CON
 P2 2 CON
 P3 3 CON
FIM
DIMENSOES
 V1 S1 20/50 VOL 1 1
 V2 S1 20/50 VOL 1 1
 P1 R 300/30 ANG 90 BASE 150,0 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P3 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
FIM
"""


def test_end_extension_to_wall_axis_and_frame_centroid():
    """Parede P1 com eixo em x=0 (t=0,30): viga que para na face (x=0,15) e estendida 0,15 m.
    Pilar-frame P2 centrado no no: extensao zero."""
    res = run_engine(_model(LDF_EXTEND), Config())
    m = res.model
    assert m.columns["P1"].axes[0].start.x == pytest.approx(0.0)
    assert m.node("N1").x == pytest.approx(0.0) and m.node("N1").y == pytest.approx(1.5)
    ext = res.step("extend_beam_ends")
    assert ext.stats["nodes_moved"] == 1
    assert ext.stats["extended"]["N1"] == pytest.approx(0.15)
    assert ext.changes[0].reference == "P1" and ext.changes[0].rule.endswith("end-extension")
    assert m.node("N2").x == pytest.approx(5.0)
    assert not res.has_errors and "GEO-W-LENGTH-CHANGED" not in _codes(res)


def test_end_extension_exceeding_max_is_error_not_correction():
    cfg = Config(tolerances=Tolerances(max_end_extension=0.10))
    res = run_engine(_model(LDF_EXTEND, cfg), cfg)
    assert res.has_errors
    assert "ALIGN-E-EXTENSION-EXCEEDS-MAX" in _codes(res, Level.ERROR)
    assert res.model.node("N1").x == pytest.approx(0.15)   # nao movido


LDF_ECCENTRIC = """\
GEOMETRIA
 1 0,160
 2 500,160
 V1 EIXO 1P1 2P2
 P1 1 CON
 P2 2 CON
FIM
DIMENSOES
 V1 S1 20/50 VOL 1 1
 P1 R 40/40 ANG 0 BASE 20,30 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,30 FCK 'C30'
FIM
"""


def test_frame_column_eccentric_beam_warns():
    """Viga a 10 cm do centroide do pilar-frame: aviso de junta nao conectada, sem correcao."""
    res = run_engine(_model(LDF_ECCENTRIC), Config())
    assert "ALIGN-W-ECCENTRIC" in _codes(res, Level.WARNING)
    assert res.model.node("N1").y == pytest.approx(1.6)


LDF_MERGE = """\
GEOMETRIA
 1 0,0
 2 250.0,0
 3 250.2,0
 4 500,0
 V1 EIXO 1P1 2N
 V2 EIXO 3N 4P2
 P1 1 CON
 P2 4 CON
FIM
DIMENSOES
 V1 S1 20/50 VOL 1 1
 V2 S1 20/50 VOL 1 1
 P1 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
FIM
"""


def test_node_merge_reindexes_beams():
    res = run_engine(_model(LDF_MERGE), Config())
    m = res.model
    assert res.step("merge_nodes").stats["merged"] == 1
    assert "N3" not in m.nodes
    assert m.beams["V2"].axis == ("N2", "N4")
    assert m.beams["V1"].axis == ("N1", "N2")
    merged = [c for c in m.changes if c.rule == "node-merge"]
    assert merged and merged[0].element_id == "N3" and merged[0].after == "N2"
    assert "GEO-E-DUP-NODE" not in _codes(res)


LDF_CROSSING = """\
GEOMETRIA
 1 -300,150
 2 300,150
 3 0,0
 V1 EIXO 1P1 2P2
 P1 1 CON
 P2 2 CON
 P3 3 CON
FIM
DIMENSOES
 V1 S1 20/50 VOL 1 1
 P1 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P3 R 400/30 ANG 90 BASE 0,15 FCK 'C30'
FIM
"""


def test_beam_crossing_wall_without_node_warns():
    res = run_engine(_model(LDF_CROSSING), Config())
    assert "CON-W-BEAM-CROSSES-WALL" in _codes(res, Level.WARNING)
    assert "CON-W-ISOLATED-COLUMN" in _codes(res, Level.WARNING)


def test_grid_naming_and_frame_columns():
    cfg = Config(grids=GridNaming(x_style="prefix", y_style="prefix", x_prefix="A", y_prefix="B", start_index=1))
    res = run_engine(_model(LDF_CLUSTER, cfg), cfg)
    labels = [(g.label, g.direction, g.coordinate) for g in res.model.grids]
    assert labels == [("A1", "X", 21.24), ("B1", "Y", 5.0), ("B2", "Y", 10.0)]
    assert res.model.grids[0].origin_column_ids == ("P1", "P2")
    res = run_engine(_model(LDF_CLUSTER), Config())      # padrao: letras em X, numeros em Y
    assert [g.label for g in res.model.grids] == ["A", "1", "2"]
    from tqs2etabs.geometry_engine.grids import _letters
    assert [_letters(i) for i in (0, 25, 26, 27)] == ["A", "Z", "AA", "AB"]


LDF_SLAB_NOTCH = """\
GEOMETRIA
 1 15,0
 2 600,300
 3 600,0
 4 15,300
 6 300,300
 7 300,200
 8 100,200
 9 100,300
 V1 EIXO 4P1 9N 6N 2P2
 V2 EIXO 1P1 3P3
 V3 EIXO 3P3 2P2
 P1 1 CON
 P2 2 CON
 P3 3 CON
 L1 GRE AREA 160000 1 3 2 6 LIV 7 LIV 8 LIV 9 4 P1 ANG 0.000
FIM
DIMENSOES
 V1 S1 20/50 S2 20/50 S3 20/50 VOL 1 1
 V2 S1 20/50 VOL 1 1
 V3 S1 20/50 VOL 1 1
 P1 R 300/30 ANG 90 BASE 0,0 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P3 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 L1 12.000 LARM 2 1
FIM
"""


def test_slab_notch_becomes_straight_outline_with_opening():
    res = run_engine(_model(LDF_SLAB_NOTCH), Config())
    m = res.model
    assert not res.has_errors
    l1 = m.slabs["L1"]
    ring = [(round(m.node(e.start_node_id).x, 2), round(m.node(e.start_node_id).y, 2)) for e in l1.edges]
    assert (3.0, 2.0) not in ring and (1.0, 2.0) not in ring          # reentrancia removida
    assert {p for p in ring} == {(0.0, 0.0), (6.0, 0.0), (6.0, 3.0), (3.0, 3.0), (1.0, 3.0), (0.0, 3.0)}
    assert len(l1.holes) == 1
    hole = {(round(p.x, 2), round(p.y, 2)) for p in l1.holes[0]}
    assert hole == {(3.0, 3.0), (3.0, 2.0), (1.0, 2.0), (1.0, 3.0)}
    assert all(e.support != EdgeSupport.FREE for e in l1.edges)
    from tqs2etabs.domain.elements import NodeRole
    assert m.node("N7").roles == {NodeRole.ORPHAN} and m.node("N8").roles == {NodeRole.ORPHAN}
    # com aberturas desligadas a reentrancia e apenas preenchida
    cfg = Config(policy=ModelingPolicy(openings="fill"))
    res2 = run_engine(_model(LDF_SLAB_NOTCH, cfg), cfg)
    assert res2.model.slabs["L1"].holes == ()


LDF_SLAB_ABSORB = """\
GEOMETRIA
 1 0,0
 2 600,0
 3 600,300
 4 0,300
 5 200,0
 6 400,0
 7 200,100
 8 400,100
 V1 EIXO 1P1 5N 6N 2P2
 V2 EIXO 2P2 3P3
 V3 EIXO 3P3 4P4
 V4 EIXO 4P4 1P1
 P1 1 CON
 P2 2 CON
 P3 3 CON
 P4 4 CON
 L1 GRE AREA 160000 1 5 7 LIV 8 LIV 6 2 3 4 ANG 0.000
 L100 'REBAIXO1' GRE AREA 20000 5 6 LIV 8 LIV 7 LIV ANG 0.000
FIM
DIMENSOES
 V1 S1 20/50 S2 20/50 S3 20/50 VOL 1 1
 V2 S1 20/50 VOL 1 1
 V3 S1 20/50 VOL 1 1
 V4 S1 20/50 VOL 1 1
 P1 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P3 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 P4 R 40/40 ANG 0 BASE 20,20 FCK 'C30'
 L1 20.000 LARM 2 1
 L100 17.000 DFS 3.000 BALANCO LARM 2 1
FIM
"""


def test_offset_slab_is_absorbed_into_parent():
    res = run_engine(_model(LDF_SLAB_ABSORB), Config())
    m = res.model
    assert set(m.slabs) == {"L1"}
    ring = [(round(m.node(e.start_node_id).x, 2), round(m.node(e.start_node_id).y, 2)) for e in m.slabs["L1"].edges]
    assert (2.0, 1.0) not in ring and (4.0, 1.0) not in ring
    assert m.slabs["L1"].holes == () and m.slabs["L1"].thickness == 0.20
    assert any(c.rule == "slab-absorb-offset" and c.element_id == "L100" for c in m.changes)
    cfg = Config(policy=ModelingPolicy(absorb_offset_slabs=False))
    res2 = run_engine(_model(LDF_SLAB_ABSORB, cfg), cfg)
    assert set(res2.model.slabs) == {"L1", "L100"}


def test_wall_end_snap_moves_beam_line():
    """Viga horizontal que encontra a parede a 9 cm da ponta e deslocada ate a ponta."""
    text = (LDF_EXTEND.replace(" 1 15,150", " 1 15,291").replace(" 2 500,150", " 2 500,291")
            .replace(" 3 -20,150", " 3 -20,291").replace("BASE 150,0", "BASE 291,0"))
    res = run_engine(_model(text), Config())
    m = res.model
    assert res.step("snap_beams_to_wall_ends").stats["beams_moved"] >= 1
    assert m.node("N1").y == 3.0 and m.node("N2").y == 3.0           # linha da V1 na ponta de P1 (y = 3,00)
    assert any(c.rule.endswith("wall-end-snap") for c in m.changes)
    cfg = Config(tolerances=Tolerances(wall_end_snap=0.05))
    res2 = run_engine(_model(text, cfg), cfg)
    assert res2.model.node("N1").y == 2.91
