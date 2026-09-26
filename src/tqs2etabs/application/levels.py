"""Niveis parametricos dos pavimentos: a cota de cada piso e derivada da cota da base e dos pes-direitos.

    cota_i = base + PD_1 + ... + PD_i        (pisos de baixo para cima)

Os parametros independentes sao `base` e os `PD`. Editar um PD desloca o piso e todos os de cima;
editar a base desloca todos. Editar diretamente uma cota e convertido em alteracao do PD daquele piso
(PD_i = cota_i - cota_(i-1)), e os pisos de cima acompanham mantendo os seus PDs.
"""

from __future__ import annotations

from collections.abc import Sequence

_TOL = 1e-6


def cotas_from_heights(base: float, heights: Sequence[float], decimals: int = 4) -> list[float]:
    """Cotas acumuladas a partir da base."""
    out, z = [], base
    for h in heights:
        z += float(h)
        out.append(round(z, decimals))
    return out


def reconcile_levels(base: float, prev_heights: Sequence[float], prev_cotas: Sequence[float],
                     new_heights: Sequence[float], new_cotas: Sequence[float],
                     decimals: int = 4) -> tuple[list[float], list[float]]:
    """Aplica a edicao do usuario e devolve (pes-direitos, cotas) coerentes.

    Para cada piso, de baixo para cima: se o PD mudou, vale o PD novo; senao, se a cota mudou, o PD
    passa a ser a cota nova menos a cota (ja recalculada) do piso de baixo. Listas de mesmo tamanho."""
    if not (len(prev_heights) == len(prev_cotas) == len(new_heights) == len(new_cotas)):
        raise ValueError("listas de pisos com tamanhos diferentes")
    heights: list[float] = []
    below = base
    for ph, pc, nh, nc in zip(prev_heights, prev_cotas, new_heights, new_cotas):
        if abs(float(nh) - float(ph)) > _TOL:
            h = float(nh)
        elif abs(float(nc) - float(pc)) > _TOL:
            h = float(nc) - below
        else:
            h = float(ph)
        h = round(h, decimals)
        heights.append(h)
        below = round(below + h, decimals)
    return heights, cotas_from_heights(base, heights, decimals)


def base_from_first(first_cota: float, first_height: float, decimals: int = 4) -> float:
    """Cota da base (nivel inicial) a partir do primeiro piso: cota - PD."""
    return round(float(first_cota) - float(first_height), decimals)
