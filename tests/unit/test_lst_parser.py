from tqs2etabs.importers.tqs.lst import parse_lst_text


def test_header_and_story(lst_doc):
    assert lst_doc.tqs_version == "TQS Formas 26"
    assert lst_doc.building == "NORTH TOWER - V5 10% molas"
    assert lst_doc.plan_name == "25 - Tipo"
    assert lst_doc.title == "NORTH TOWER"
    assert lst_doc.client == "FG EMPREENDIMENTOS"
    assert len(lst_doc.stories) == 1
    s = lst_doc.stories[0]
    assert (s.index, s.title, s.elevation_m, s.height_m, s.material) == (25, "24o Andar", 74.77, 3.24, "CON")


def test_warnings_deduplicated(lst_doc):
    assert len(lst_doc.warnings) == 29
    by_num = {w.number: w for w in lst_doc.warnings}
    assert by_num[13].occurrences == 5
    assert "41" in by_num[13].text and "viga 6" in by_num[13].text
    assert by_num[6].occurrences == 2


def test_quantities(lst_doc):
    assert len(lst_doc.beam_quantities) == 22
    v1 = lst_doc.beam_quantities["V1"]
    assert (v1.concrete_volume_m3, v1.linear_length_m) == (1.58, 7.30)
    assert len(lst_doc.column_quantities) == 8
    p4 = lst_doc.column_quantities["P4"]
    assert p4.is_cortina and p4.top_volume_m3 is None and p4.structured_area_m2 == 4.60
    assert lst_doc.column_quantities["P3"].top_volume_m3 == 9.24
    labels = [q.label for q in lst_doc.slab_quantities]
    assert labels == ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "ESCADA"] + ["REBAIX"] * 6
    assert lst_doc.slab_quantities[3].structured_area_m2 == 136.21


def test_fck(lst_doc):
    assert lst_doc.column_fck == {f"P{i}": "C60" for i in range(1, 9)}


def test_missing_sections_warn_only():
    d = parse_lst_text("nada aqui\n")
    codes = {x.code for x in d.diagnostics}
    assert "LST-W-NO-STORIES" in codes and "LST-W-NO-QUANTITIES" in codes
    assert d.stories == () and d.warnings == ()
