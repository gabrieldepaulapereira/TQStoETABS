"""Parser tolerante do LST: pisos, avisos, quantitativos e cabecalho.

O LST e um relatorio dependente da versao do TQS; cada secao e localizada por
titulo e lida por expressoes regulares. Secoes ausentes geram WARNING, nunca erro.
"""

from __future__ import annotations

import re
from pathlib import Path

from ....domain.diagnostics import DiagnosticCollector, Source
from ..common import read_tqs_text
from .document import (LstBeamQuantity, LstColumnQuantity, LstDocument, LstSlabQuantity,
                       LstStory, LstWarning)

_NUM = r"(-?(?:\d+\.?\d*|\.\d+))"
_WARN_RE = re.compile(r"^\*\*\*(\d+)\s+AVISO:\s*(.*?)\s*$")
# "3  1ºpav Ter  2.76  .60  1  CON  TERREO": secao, material (opcional) e marcadores extras (TERREO etc.)
_STORY_RE = re.compile(rf"^\s*(\d+)\s+(.+?)\s+{_NUM}\s+{_NUM}\s+(\d+)(?:\s+(\S+))?((?:\s+\S+)*)\s*$")
_BEAM_Q_RE = re.compile(rf"^\s*(V\d+)\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$")
_COL_Q_RE = re.compile(rf"^\s*(P\d+)\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+(?:{_NUM}|\(Cortina\))\s*$")
_SLAB_Q_RE = re.compile(rf"^\s*([A-Z][A-Z0-9]*)\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$")
_FCK_RE = re.compile(r"^\s*(P\d+)\s+(C\d+)\s*$")
_VERSION_RE = re.compile(r"^(TQS Formas\s+\S+)\s+-\s+Processamento", re.IGNORECASE)


def _header_value(lines: list[str], label_prefix: str) -> str | None:
    for s in lines[:80]:
        m = re.match(rf"^\s*{label_prefix}\s*\.{{2,}}\s+(.*?)\s*$", s)
        if m and m.group(1):
            return m.group(1)
    return None


def _section(lines: list[str], title_regex: str, end_regex: str) -> tuple[int, int] | None:
    start = None
    for i, s in enumerate(lines):
        if start is None:
            if re.match(title_regex, s.strip()):
                start = i + 1
        elif re.match(end_regex, s.strip()):
            return start, i
    return (start, len(lines)) if start is not None else None


def parse_lst_text(text: str, source_path: str | None = None) -> LstDocument:
    diag = DiagnosticCollector()
    lines = text.splitlines()

    tqs_version = None
    for s in lines[:10]:
        m = _VERSION_RE.match(s.strip())
        if m:
            tqs_version = m.group(1).strip()
            break

    building = _header_value(lines, r"Edif[ií]cio")
    plan_name = _header_value(lines, r"Planta")
    title = _header_value(lines, r"T[ií]tulo geral")
    client = _header_value(lines, r"Cliente")

    # ---- Definicao de Pisos
    stories: list[LstStory] = []
    sec = _section(lines, r"^Defini[cç][aã]o de Pisos", r"^(Tabela|Quantitativos|\*\*\*|Planta)")
    if sec:
        for s in lines[sec[0]:sec[1]]:
            m = _STORY_RE.match(s)
            if m:
                stories.append(LstStory(int(m.group(1)), m.group(2).strip(), float(m.group(3)),
                                        float(m.group(4)), m.group(5), m.group(6),
                                        tuple((m.group(7) or "").split())))
    if not stories:
        diag.warning("LST-W-NO-STORIES", "Tabela 'Definicao de Pisos' nao encontrada no LST", Source.PARSER)

    # ---- Avisos (deduplicados por texto)
    warnings: dict[str, LstWarning] = {}
    for i, s in enumerate(lines, start=1):
        m = _WARN_RE.match(s.strip())
        if m:
            txt = re.sub(r"\s+", " ", m.group(2))
            if txt in warnings:
                w = warnings[txt]
                warnings[txt] = LstWarning(w.number, w.text, w.occurrences + 1, w.line)
            else:
                warnings[txt] = LstWarning(int(m.group(1)), txt, 1, i)

    # ---- Quantitativos
    beam_q: dict[str, LstBeamQuantity] = {}
    col_q: dict[str, LstColumnQuantity] = {}
    slab_q: list[LstSlabQuantity] = []
    sec = _section(lines, r"^Quantitativos", r"^Legenda")
    if sec:
        order = 0
        for s in lines[sec[0]:sec[1]]:
            m = _BEAM_Q_RE.match(s)
            if m:
                beam_q[m.group(1)] = LstBeamQuantity(m.group(1), *(float(m.group(k)) for k in range(2, 7)))
                continue
            m = _COL_Q_RE.match(s)
            if m:
                cort = "(Cortina)" in s
                col_q[m.group(1)] = LstColumnQuantity(
                    m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4)),
                    None if cort else float(m.group(5)), cort)
                continue
            m = _SLAB_Q_RE.match(s)
            if m and m.group(1) not in ("Total",):
                slab_q.append(LstSlabQuantity(m.group(1), order, float(m.group(2)), float(m.group(3)),
                                              float(m.group(4))))
                order += 1
    else:
        diag.warning("LST-W-NO-QUANTITIES", "Secao 'Quantitativos' nao encontrada no LST", Source.PARSER)

    # ---- fck diferenciado
    fck: dict[str, str] = {}
    sec = _section(lines, r"^Pilares com fck diferenciado", r"^(\*\*\*|Cargas|Somat)")
    if sec:
        for s in lines[sec[0]:sec[1]]:
            m = _FCK_RE.match(s)
            if m:
                fck[m.group(1)] = m.group(2)

    return LstDocument(
        tqs_version=tqs_version, building=building, plan_name=plan_name, title=title, client=client,
        stories=tuple(stories), warnings=tuple(warnings.values()),
        beam_quantities=beam_q, column_quantities=col_q, slab_quantities=tuple(slab_q),
        column_fck=fck, diagnostics=diag.as_tuple(), source_path=source_path)


def parse_lst(path: Path | str) -> LstDocument:
    return parse_lst_text(read_tqs_text(path), str(path))
