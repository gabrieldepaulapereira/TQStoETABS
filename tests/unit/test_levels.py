"""Niveis parametricos: PD e base independentes, cotas derivadas."""

import pytest

from tqs2etabs.application.levels import base_from_first, cotas_from_heights, reconcile_levels

BASE = -0.50
PD = [3.06, 3.06, 3.06, 3.15]
COTAS = [2.56, 5.62, 8.68, 11.83]


def test_cotas_from_heights():
    assert cotas_from_heights(BASE, PD) == COTAS
    assert base_from_first(2.56, 3.06) == -0.5


def test_changing_a_height_moves_that_floor_and_all_above():
    pd = [3.06, 3.50, 3.06, 3.15]
    heights, cotas = reconcile_levels(BASE, PD, COTAS, pd, COTAS)
    assert heights == pd
    assert cotas == [2.56, 6.06, 9.12, 12.27]


def test_changing_the_base_moves_everything():
    heights, cotas = reconcile_levels(0.0, PD, COTAS, PD, COTAS)
    assert heights == PD and cotas == [3.06, 6.12, 9.18, 12.33]


def test_editing_a_cota_becomes_a_height_change_and_upper_floors_follow():
    cotas_in = [2.56, 5.62, 9.00, 11.83]           # usuario digitou 9,00 no piso 3
    heights, cotas = reconcile_levels(BASE, PD, COTAS, PD, cotas_in)
    assert heights == [3.06, 3.06, 3.38, 3.15]
    assert cotas == [2.56, 5.62, 9.00, 12.15]      # piso 4 sobe junto, mantendo o seu PD


def test_height_edit_wins_over_stale_cota():
    heights, cotas = reconcile_levels(BASE, PD, COTAS, [3.06, 3.06, 4.0, 3.15], [2.56, 5.62, 8.68, 11.83])
    assert cotas == [2.56, 5.62, 9.62, 12.77]


def test_size_mismatch():
    with pytest.raises(ValueError):
        reconcile_levels(0.0, [3.0], [3.0], [3.0, 3.0], [3.0, 6.0])
