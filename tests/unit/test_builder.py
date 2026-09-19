"""Builder: LDF bruto -> StructuralModel (m), reconstrucao de pilares e papeis dos nos."""

import math

import pytest

from tqs2etabs.domain.config import Config, ModelingPolicy
from tqs2etabs.domain.elements import ColumnKind, EdgeSupport, NodeRole, RectSection, SupportKind
from tqs2etabs.domain.geometry import polygon_bbox, polyline_length
from tqs2etabs.importers.tqs.ldf import build_model, parse_ldf_text
from tqs2etabs.importers.tqs.ldf.builder import rect_origin_from_reference

# Valores esperados (cm) validados em ARCHITECTURE.md tabela 1.4.2
EXPECTED_RECT = {
    "P1": ((-1975.731, -1935.731), (2254.449, 2483.449), (-1955.731, 2368.949)),
    "P2": ((295.239, 335.239), (2254.449, 2483.449), (315.239, 2368.949)),
    "P4": ((-2820.357, -2770.357), (1509.454, 2430.354), (-2795.357, 1969.904)),
    "P5": ((-1975.743, -1935.743), (1509.468, 1685.468), (-1955.743, 1597.468)),
    "P7": ((295.251, 335.251), (1509.449, 1685.449), (315.251, 1597.449)),
    "P8": ((1129.864, 1179.864), (1509.454, 2430.354), (1154.864, 1969.904)),
}


def test_rect_origin_formula():
    # P1: no 36, BASE (166.905472, 40.000011), ANG 90 -> origem (-1935.731, 2254.449)
    ox, oy = rect_origin_from_reference((-1975.731319, 2421.354221), (166.905472, 40.000011), 90.0)
    assert ox == pytest.approx(-1935.731308, abs=1e-3)
    assert oy == pytest.approx(2254.448749, abs=1e-3)


@pytest.mark.parametrize("name", sorted(EXPECTED_RECT))
def test_rect_columns_reconstructed(model, name):
    col = model.columns[name]
    assert isinstance(col.section, RectSection)
    lo, hi = polygon_bbox(col.outline)
    (x0, x1), (y0, y1), (cx, cy) = EXPECTED_RECT[name]
    assert lo.x * 100 == pytest.approx(x0, abs=1e-2)
    assert hi.x * 100 == pytest.approx(x1, abs=1e-2)
    assert lo.y * 100 == pytest.approx(y0, abs=1e-2)
    assert hi.y * 100 == pytest.approx(y1, abs=1e-2)
    assert col.centroid.x * 100 == pytest.approx(cx, abs=1e-2)
    assert col.centroid.y * 100 == pytest.approx(cy, abs=1e-2)


def test_rect_column_shares_outer_face_with_psu(model):
    """A face externa do R coincide com a face do PSU (evidencia da hipotese PSU)."""
    for name in ("P1", "P4", "P5"):
        col = model.columns[name]
        lo, _ = polygon_bbox(col.outline)
        plo, _ = polygon_bbox(col.section_above)
        assert lo.x == pytest.approx(plo.x, abs=1e-6)


def test_polygon_columns(model):
    p3, p6 = model.columns["P3"], model.columns["P6"]
    assert p3.is_polygonal and p6.is_polygonal
    assert p3.area == pytest.approx(6.603, abs=1e-3)
    assert p6.area == pytest.approx(10.035, abs=1e-3)
    assert len(p3.laminas) == 6 and len(p6.laminas) == 10
    # laminas nao se sobrepoem: soma das areas == area do poligono
    assert sum(_area(l) for l in p3.laminas) == pytest.approx(p3.area, abs=1e-4)
    assert p3.reference_node_id == "N2" and p6.reference_node_id == "N1"
    assert p6.flags == {"FURADO"} and p3.fck == "C60"


def _area(poly):
    from tqs2etabs.domain.geometry import polygon_area
    return polygon_area(poly)


def test_column_kind_policy(ldf_doc, lst_doc):
    m = build_model(ldf_doc, lst_doc, Config(policy=ModelingPolicy(wall_aspect_ratio=3.0)))
    assert all(c.kind_hint == ColumnKind.WALL for c in m.columns.values())
    m = build_model(ldf_doc, lst_doc, Config(policy=ModelingPolicy(wall_aspect_ratio=10.0)))
    kinds = {n: c.kind_hint for n, c in m.columns.items()}
    assert kinds["P5"] == ColumnKind.COLUMN and kinds["P1"] == ColumnKind.COLUMN
    assert kinds["P4"] == ColumnKind.WALL          # CORTINA
    assert kinds["P3"] == ColumnKind.WALL          # poligonal


def test_units_and_story(model):
    n = model.node("N1")
    assert n.x == pytest.approx(-4.64746213) and n.y == pytest.approx(20.64458607)
    assert n.z == pytest.approx(74.77)
    story = model.stories["S1"]
    assert (story.tqs_index, story.elevation, story.height, story.source) == (25, 74.77, 3.24, "LST")
    assert model.project.plan_name == "25 - Tipo"


def test_node_roles(model):
    assert len(model.nodes) == 121
    assert len(model.structural_nodes()) == 74
    assert len(model.nodes_with_role(NodeRole.LOAD_ONLY)) == 38
    assert {n.id for n in model.nodes_with_role(NodeRole.ORPHAN)} == {
        "N55", "N57", "N76", "N77", "N78", "N80", "N81", "N82", "N83"}
    assert len(model.nodes_with_role(NodeRole.COLUMN_REF)) == 8
    assert NodeRole.BEAM_SUPPORT in model.node("N36").roles


def test_beams(model):
    assert len(model.beams) == 22 and model.beam_segment_count() == 41
    v7 = model.beams["V7"]
    assert v7.axis == ("N15", "N16", "N17", "N12", "N18")
    assert [(s.width, s.depth) for s in v7.segments] == [(0.14, 0.79), (0.14, 0.74), (0.14, 0.74), (0.14, 0.79)]
    assert v7.release_start and v7.release_end
    assert [(s.kind, s.ref_id) for s in v7.supports] == [
        (SupportKind.COLUMN, "P3"), (SupportKind.RECEIVES, "V17"), (SupportKind.RECEIVES, "V19"),
        (SupportKind.RECEIVES, "V21"), (SupportKind.COLUMN, "P3")]
    v1 = model.beams["V1"]
    pts = [model.node(n).point for n in v1.axis]
    assert polyline_length(pts) == pytest.approx(7.8099, abs=1e-3)
    assert model.beams["V16"].segments[0].width == pytest.approx(0.60)


def test_slab_edges_match_lst_influence_table(model):
    l3 = model.slabs["L3"]
    got = [(e.support, e.ref_id) for e in l3.edges]
    assert got == [
        (EdgeSupport.COLUMN, "P6"), (EdgeSupport.BEAM, "V8"), (EdgeSupport.BEAM, "V18"),
        (EdgeSupport.FREE, None), (EdgeSupport.COLUMN, "P6"), (EdgeSupport.BEAM, "V22"),
        (EdgeSupport.COLUMN, "P3"), (EdgeSupport.BEAM, "V7"), (EdgeSupport.BEAM, "V7"),
        (EdgeSupport.BEAM, "V7"), (EdgeSupport.BEAM, "V7"), (EdgeSupport.COLUMN, "P3"),
        (EdgeSupport.BEAM, "V16")]
    assert l3.thickness == pytest.approx(0.20)
    l8 = model.slabs["L8"]
    assert l8.is_stair and not l8.in_grid_model and l8.thickness == pytest.approx(0.14)
    l100 = model.slabs["L100"]
    assert l100.is_cantilever and l100.top_offset == pytest.approx(0.03)
    assert all(e.support != EdgeSupport.UNKNOWN for s in model.slabs.values() for e in s.edges)


def test_loads_captured(model):
    assert [lc.number for lc in model.load_cases] == [1, 2, 3, 4]
    are = [i for i in model.load_cases[0].items if i.kind == "ARE"]
    assert len(are) == 2 and are[0].region is not None and are[0].unit == "tf/m2"


def test_non_reciprocal_support_warns():
    text = """\
GEOMETRIA
 1 0,0
 2 100,0
 3 50,0
 4 50,100
 V1 EIXO 1P1 3RV2 2P2
 V2 EIXO 4N 3N
 P1 1 CON
 P2 2 CON
FIM
DIMENSOES
 V1 S1 20/50 S2 20/50 VOL 1 1
 V2 S1 20/50 VOL 1 1
 P1 R 40/20 ANG 0 BASE 20,10 FCK 'C30'
 P2 R 40/20 ANG 0 BASE 20,10 FCK 'C30'
FIM
"""
    m = build_model(parse_ldf_text(text))
    assert any(d.code == "BUILD-W-SUPPORT-NOT-RECIPROCAL" for d in m.diagnostics)
    assert any(d.code == "BUILD-W-NO-LST" for d in m.diagnostics)
    assert m.stories["S1"].elevation is None
    assert math.isclose(m.node("N2").x, 1.0)
