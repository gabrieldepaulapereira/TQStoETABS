"""Leitor minimo de .e2k (pontos, barras, areas, stories, grids) para a validacao
pos-exportacao: o que foi escrito e relido e comparado com o modelo intermediario."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOK = re.compile(r'"[^"]*"|\S+')


def _tokens(line: str) -> list[str]:
    return [t[1:-1] if t.startswith('"') else t for t in _TOK.findall(line)]


def _num(tok: str, sep: str) -> float:
    return float(tok.replace(sep, "."))


@dataclass
class E2kModel:
    stories: dict[str, float] = field(default_factory=dict)      # nome -> HEIGHT ou ELEV
    grids: list[tuple[str, str, float]] = field(default_factory=list)
    points: dict[str, tuple[float, float]] = field(default_factory=dict)
    lines: dict[str, tuple[str, str, str]] = field(default_factory=dict)      # nome -> (tipo, pi, pj)
    areas: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    line_sections: dict[str, str] = field(default_factory=dict)
    area_sections: dict[str, str] = field(default_factory=dict)
    area_piers: dict[str, str] = field(default_factory=dict)
    restraints: dict[str, str] = field(default_factory=dict)
    openings: set[str] = field(default_factory=set)
    sections_seen: list[str] = field(default_factory=list)


def read_e2k_text(text: str, decimal_separator: str = ",") -> E2kModel:
    m = E2kModel()
    section = ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("$"):
            section = s[1:].strip()
            m.sections_seen.append(section)
            continue
        t = _tokens(s)
        if not t:
            continue
        key = t[0].upper()
        if key == "STORY":
            m.stories[t[1]] = _num(t[3], decimal_separator)
        elif key == "GRID":
            m.grids.append((t[3], t[5], _num(t[7], decimal_separator)))
        elif key == "POINT":
            m.points[t[1]] = (_num(t[2], decimal_separator), _num(t[3], decimal_separator))
        elif key == "LINE":
            m.lines[t[1]] = (t[2], t[3], t[4])
        elif key == "AREA":
            npts = int(t[3])
            m.areas[t[1]] = (t[2], tuple(t[4:4 + npts]))
        elif key == "LINEASSIGN":
            m.line_sections[t[1]] = t[t.index("SECTION") + 1]
        elif key == "AREAASSIGN":
            if "OPENING" in t:
                m.openings.add(t[1])
            if "SECTION" in t:
                m.area_sections[t[1]] = t[t.index("SECTION") + 1]
            if "PIER" in t:
                m.area_piers[t[1]] = t[t.index("PIER") + 1]
        elif key == "POINTASSIGN" and "RESTRAINT" in t:
            m.restraints[t[1]] = t[t.index("RESTRAINT") + 1]
    return m
