"""Previa da planta (app/plan_view.py): desenho simples/detalhado e tabelas de elementos."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
pytest.importorskip("plotly")

from plan_view import element_tables, plan_figure  # noqa: E402

from tqs2etabs.application.normalize import normalize  # noqa: E402


@pytest.fixture(scope="module")
def model(ldf_path, lst_path):
    return normalize(ldf_path, lst_path).model


def test_simple_and_detailed_figures(model):
    simple = plan_figure(model)
    assert not any(t.mode == "text" for t in simple.data) and not simple.layout.annotations
    detailed = plan_figure(model, "25 - Tipo", detailed=True, show_nodes=True)
    texts = [t for t in detailed.data if t.mode in ("text", "markers+text")]
    labels = {x for t in texts for x in (t.text or ())}
    assert {"P1", "P3"} <= labels                                  # pilares nomeados
    assert any(str(l).startswith("L") for l in labels)             # lajes nomeadas
    assert {a.text for a in detailed.layout.annotations} >= {"V1"}  # vigas nomeadas ao longo do eixo


def test_element_tables(model):
    t = element_tables(model)
    assert len(t["Pilares"]) == len(model.columns) and len(t["Vigas"]) == len(model.beams)
    assert len(t["Lajes"]) == len(model.slabs) and len(t["Nós"]) == len(model.structural_nodes())
    assert set(t["Pilares"]["Tipo"]) <= {"parede (shell)", "pilar (frame)"}
