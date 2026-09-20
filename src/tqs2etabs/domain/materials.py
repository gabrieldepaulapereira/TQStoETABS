"""Fontes de modulo de elasticidade do concreto (MPa) para escolha do usuario.

- "atual":     E do modelo TQS de referencia (CONCRETO.DAT), quando definido;
- "prudencio": tabela do modelo MARAMBAIA-V40Design (ETABS 23.3.1) — valores de projeto do escritorio;
- "nbr6118":   Ecs = alpha_i * alpha_E * 5600 * sqrt(fck), NBR 6118:2014 item 8.2.8 (alpha_E = 1,0: granito/gnaisse).
"""

from __future__ import annotations

import math
import re

# MARAMBAIA-V40Design.e2k ($ MATERIAL PROPERTIES, kN/m2 -> MPa)
PRUDENCIO_E_MPA: dict[str, float] = {
    "C40": 38000.0, "C45": 40000.0, "C50": 40000.0, "C60": 42000.0,
    "C70": 45000.0, "C80": 46000.0, "C90": 47000.0,
}

SOURCES = ("atual", "prudencio", "nbr6118", "manual")


def fck_of(name: str) -> float | None:
    m = re.fullmatch(r"C(\d+(?:\.\d+)?)", name.strip().upper())
    return float(m.group(1)) if m else None


def nbr6118_ecs(fck_mpa: float, alpha_e: float = 1.0) -> float:
    """Ecs (MPa) pela NBR 6118:2014 8.2.8: Eci = alphaE*5600*sqrt(fck); Ecs = alphai*Eci, alphai <= 1."""
    eci = alpha_e * 5600.0 * math.sqrt(fck_mpa)
    alpha_i = min(1.0, 0.8 + 0.2 * fck_mpa / 80.0)
    return alpha_i * eci


def prudencio_e(name: str) -> float | None:
    return PRUDENCIO_E_MPA.get(name.strip().upper())


def material_options(name: str, tqs_e_mpa: float | None) -> dict[str, float | None]:
    """As tres fontes para uma classe (MPa); None quando nao disponivel."""
    fck = fck_of(name)
    return {
        "atual": tqs_e_mpa,
        "prudencio": prudencio_e(name),
        "nbr6118": round(nbr6118_ecs(fck), 0) if fck else None,
    }
