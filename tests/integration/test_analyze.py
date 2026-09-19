import json

from tqs2etabs.application.analyze import analyze, format_summary, model_to_json
from tqs2etabs.cli import main
from tqs2etabs.domain.diagnostics import Level


def test_analyze_real_files(ldf_path, lst_path):
    r = analyze(ldf_path, lst_path)
    m = r.model
    assert (len(m.nodes), len(m.columns), len(m.beams), len(m.slabs)) == (121, 8, 22, 14)
    assert not any(d.level == Level.ERROR for d in m.diagnostics)
    tol = r.config.tolerances.area_check_relative
    assert len(r.cross_checks) == 44
    assert all(c.ok(tol) for c in r.cross_checks)
    text = format_summary(r, verbose=True)
    for expected in ("Nos encontrados:      121", "Pilares encontrados:    8", "Vigas encontradas:     22",
                     "Lajes encontradas:     14", "cota 74.77 m", "44/44 grandezas conferem",
                     "P3   WALL   G 8 vertices", "V7   L= 6.509 m  trechos 4"):
        assert expected in text, expected


def test_analyze_without_lst(ldf_path):
    r = analyze(ldf_path, None)
    assert r.cross_checks == ()
    assert r.model.stories["S1"].elevation is None
    assert any(d.code == "BUILD-W-NO-LST" for d in r.model.diagnostics)


def test_model_json_roundtrip(ldf_path, lst_path):
    r = analyze(ldf_path, lst_path)
    data = json.loads(model_to_json(r.model))
    assert len(data["nodes"]) == 121
    assert data["columns"]["P1"]["kind_hint"] == "WALL"
    assert data["stories"]["S1"]["elevation"] == 74.77


def test_cli(ldf_path, tmp_path, capsys):
    out = tmp_path / "m.json"
    rc = main(["analyze", str(ldf_path), "--json", str(out)])
    assert rc == 0
    assert out.exists()
    captured = capsys.readouterr().out
    assert "Pavimento: 25 - Tipo" in captured
