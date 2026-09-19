"""Parser LDF: gramatica decodificada em ARCHITECTURE.md secao 1."""

from tqs2etabs.domain.diagnostics import Level
from tqs2etabs.importers.tqs.ldf import parse_ldf_text

MINI_LDF = """\
$------------------------------------------------------------------------------
$     CAD/TQS - Modelador Estrutural
$
$     01/01/2026 - 10:00:00
$     Pasta D:\\X\\Tipo
$     Pavimento 3 - Tipo   Projeto 7
$     Edifício  TESTE  Projeto 1
$     TITULO
$     CLIENTE
$
GEOMETRIA
DEFINE ESCALA 50.00
         1                0.000000,      0.000000
         2              500.000000,      0.000000
         3              500.000000,    400.000000
         4                0.000000,    400.000000
         5              250.000000,      0.000000
         6              250.000000,    400.000000
         7              100.000000,      0.000000
  V1      EIXO     1P1         5N          2P2
  V2      EIXO     6AV3  ESTRANHO   ARD     5RV1
  V3      EIXO     4P3     ARE     6RV2         3P4
  P1      1     CON
  P2      2     CON CORTINA
  P3      4     CON
  P4      3     CON
  L1      GRE AREA 200000.00     1     5     2 P2     3     6 LIV     4 P3 -
          ANG 0.000
FIM
DIMENSOES
  V1     S1 20.0/60.0 S2 20.0/60.0 DFS -10.00 VOL 10000.00 600000.00
  V2     S1 15.0/50.0 VOL 6000.00 300000.00
  V3     S1 20.0/60.0 S2 20.0/60.0 VOL 10000.00 600000.00 XPTO 3
  P1     R 40.000/20.000  ANG 0.000 BASE 20.000000,10.000000 FCK 'C30'
  PSU 1  0.0,-10.0; 40.0,-10.0; 40.0,5.0; 0.0,5.0; 0.0,-10.0;
  P2     R 40.000/20.000  ANG 90.000 BASE 20.000000,10.000000 FCK 'C30'
  P3     G -10.0,390.0; 10.0,390.0; 10.0,410.0; -10.0,410.0; BASE 0.0,400.0 DSC 1 FCK 'C30'
     LAMINAS 3 2
          -10.0,390.0; 10.0,390.0; 10.0,400.0; -10.0,400.0; -10.0,390.0;
          -10.0,400.0; 10.0,400.0; 10.0,410.0; -10.0,410.0; -10.0,400.0;
  P4     R 40.000/20.000  ANG 0.000 BASE 20.000000,10.000000 FCK 'C30'
  L1     12.000 DFS 2.000 BALANCO LARM 2 1
FIM
CARGAS CASO 1
   V1      DIP 1 7   0.09
   V2      DIS    0.61
   L1      ADI    0.45
   L1      ARE   0.0,0.0;   100.0,0.0;   100.0,100.0; -
           0.0,100.0;   0.0,0.0;VAL   0.10
FIM
"""


def test_header():
    d = parse_ldf_text(MINI_LDF)
    assert d.header.plan_name == "3 - Tipo"
    assert d.header.plan_project == 7
    assert d.header.building == "TESTE"
    assert d.header.title == "TITULO"
    assert d.header.client == "CLIENTE"
    assert d.header.generated_at == "01/01/2026 - 10:00:00"
    assert d.scale == 50.0


def test_nodes_beams_columns_slabs():
    d = parse_ldf_text(MINI_LDF)
    assert len(d.nodes) == 7 and d.nodes[3] == (500.0, 400.0)
    v1 = d.beams["V1"]
    assert [(a.node, a.qualifier) for a in v1.axis] == [(1, "P1"), (5, "N"), (2, "P2")]
    assert not v1.release_start and not v1.release_end
    v2 = d.beams["V2"]
    assert v2.release_end and not v2.release_start
    assert v2.unknown_tokens == ("ESTRANHO",)
    v3 = d.beams["V3"]
    assert v3.release_start and [a.qualifier for a in v3.axis] == ["P3", "RV2", "P4"]
    assert d.columns["P2"].flags == ("CORTINA",)
    l1 = d.slabs["L1"]
    assert l1.flags == ("GRE",) and l1.area_cm2 == 200000.0 and l1.angle_deg == 0.0
    assert [(v.node, v.qualifier) for v in l1.vertices] == [
        (1, None), (5, None), (2, "P2"), (3, None), (6, "LIV"), (4, "P3")]


def test_dimensions():
    d = parse_ldf_text(MINI_LDF)
    v1 = d.beam_dims["V1"]
    assert [(s.width_cm, s.depth_cm, s.dfs_cm) for s in v1.sections] == [(20.0, 60.0, None), (20.0, 60.0, -10.0)]
    assert v1.plan_area_cm2 == 10000.0 and v1.volume_cm3 == 600000.0
    assert d.beam_dims["V3"].unknown_tokens == ("XPTO", "3")
    p1 = d.column_dims["P1"]
    assert p1.kind == "R" and p1.length_cm == 40.0 and p1.width_cm == 20.0
    assert p1.angle_deg == 0.0 and p1.base == (20.0, 10.0) and p1.fck == "C30"
    assert p1.psu is not None and len(p1.psu) == 5
    p3 = d.column_dims["P3"]
    assert p3.kind == "G" and len(p3.polygon) == 4 and p3.base == (0.0, 400.0) and p3.dsc == 1
    assert len(p3.laminas) == 2 and len(p3.laminas[0]) == 5
    l1 = d.slab_dims["L1"]
    assert l1.thickness_cm == 12.0 and l1.dfs_cm == 2.0 and l1.cantilever and l1.larm == ("2", "1")


def test_loads():
    d = parse_ldf_text(MINI_LDF)
    assert len(d.load_cases) == 1
    items = d.load_cases[0].items
    kinds = [(i.element, i.kind, i.value) for i in items]
    assert kinds == [("V1", "DIP", 0.09), ("V2", "DIS", 0.61), ("L1", "ADI", 0.45), ("L1", "ARE", 0.10)]
    assert items[0].nodes == (1, 7)
    assert len(items[3].region) == 5


def test_unknown_tokens_produce_warnings_not_errors():
    d = parse_ldf_text(MINI_LDF)
    codes = [x.code for x in d.diagnostics]
    assert "PARSE-W-BEAM-TOKEN" in codes
    assert "PARSE-W-BEAM-DIM-TOKEN" in codes
    assert all(x.level != Level.ERROR for x in d.diagnostics)


def test_missing_geometry_gives_error():
    d = parse_ldf_text("DIMENSOES\n  V1 S1 20.0/60.0\nFIM\n")
    assert any(x.code == "PARSE-E-NO-NODES" for x in d.diagnostics)


# ------------------------------------------------------------ arquivo real

def test_real_file_counts(ldf_doc):
    assert len(ldf_doc.nodes) == 121
    assert len(ldf_doc.beams) == 22
    assert len(ldf_doc.columns) == 8
    assert len(ldf_doc.slabs) == 14
    assert len(ldf_doc.beam_dims) == 22 and len(ldf_doc.column_dims) == 8 and len(ldf_doc.slab_dims) == 14
    assert [lc.number for lc in ldf_doc.load_cases] == [1, 2, 3, 4]
    assert [c.name for c in ldf_doc.catalog_sections] == ["W 150 x 13.0", "W 250 x 17,9"]
    assert [m.name for m in ldf_doc.materials] == ["ASTM A36 250MPa", "Concreto estrutural"]
    assert ldf_doc.diagnostics == ()


def test_real_file_v18_split_qualifier(ldf_doc):
    v18 = ldf_doc.beams["V18"]
    assert [(a.node, a.qualifier) for a in v18.axis] == [
        (5, "P6"), (6, "RV11"), (7, "RV10"), (8, "RV9"), (50, "N"), (9, "AV8")]
    assert v18.release_start and not v18.release_end


def test_real_file_sections_match_segments(ldf_doc):
    for name, beam in ldf_doc.beams.items():
        assert len(ldf_doc.beam_dims[name].sections) == len(beam.axis) - 1, name


def test_real_file_columns(ldf_doc):
    p1 = ldf_doc.column_dims["P1"]
    assert (p1.kind, p1.length_cm, p1.width_cm, p1.angle_deg) == ("R", 229.0, 40.0, 90.0)
    assert p1.base == (166.905472, 40.000011)
    p3 = ldf_doc.column_dims["P3"]
    assert p3.kind == "G" and len(p3.polygon) == 8 and p3.base == (-464.746213, 2214.368018)
    assert {n: len(ldf_doc.column_dims[n].laminas) for n in ("P3", "P4", "P6", "P8")} == {
        "P3": 6, "P4": 4, "P6": 10, "P8": 4}
    assert all(ldf_doc.column_dims[n].psu is not None for n in ldf_doc.column_dims)
    assert ldf_doc.columns["P4"].flags == ("CORTINA", "FURADO")


def test_real_file_slab_l3(ldf_doc):
    l3 = ldf_doc.slabs["L3"]
    assert [(v.node, v.qualifier) for v in l3.vertices] == [
        (3, "P6"), (22, None), (9, None), (50, "LIV"), (75, "P6"), (1, None), (2, "P3"),
        (18, None), (12, None), (17, None), (16, None), (15, "P3"), (4, None)]
    assert ldf_doc.slabs["L8"].title == "ESCADA" and "GRE" not in ldf_doc.slabs["L8"].flags
