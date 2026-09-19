"""Leitura de texto TQS: codificacao, comentarios, continuacoes e tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TQS_ENCODING = "latin-1"

_PAIR_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")
_TOKEN_RE = re.compile(r"'[^']*'|\S+")


def read_tqs_text(path: Path | str) -> str:
    return Path(path).read_text(encoding=TQS_ENCODING)


@dataclass(frozen=True, slots=True)
class LogicalLine:
    """Linha logica (continuacoes '-' ja unidas), sem comentarios."""
    number: int          # primeira linha fisica (1-based)
    text: str


def _strip_comment(line: str) -> str:
    """Remove comentario iniciado por '$' fora de aspas simples."""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == "'":
            in_quote = not in_quote
        elif ch == "$" and not in_quote:
            return line[:i]
    return line


def _is_continuation(line: str) -> bool:
    s = line.rstrip()
    return s.endswith("-") and (len(s) == 1 or s[-2] in " \t")


def logical_lines(text: str) -> list[LogicalLine]:
    out: list[LogicalLine] = []
    buf: list[str] = []
    start = 0
    for i, raw in enumerate(text.splitlines(), start=1):
        s = _strip_comment(raw).rstrip()
        if not s.strip():
            if buf:
                # linha vazia dentro de uma continuacao: fecha a linha logica
                out.append(LogicalLine(start, " ".join(buf)))
                buf = []
            continue
        if not buf:
            start = i
        if _is_continuation(s):
            buf.append(s.rstrip()[:-1].strip())
            continue
        buf.append(s.strip())
        out.append(LogicalLine(start, " ".join(buf)))
        buf = []
    if buf:
        out.append(LogicalLine(start, " ".join(buf)))
    return out


def normalize_pairs(text: str) -> str:
    """Compacta 'x,   y' em 'x,y' e separa ';' dos tokens vizinhos."""
    t = _PAIR_RE.sub(lambda m: f"{m.group(1)},{m.group(2)}", text)
    t = t.replace(";", " ; ")
    return t


def tokenize(text: str) -> list[str]:
    """Tokens separados por espaco; strings entre aspas simples viram um token."""
    return _TOKEN_RE.findall(normalize_pairs(text))


def is_pair(token: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?", token))


def parse_pair(token: str) -> tuple[float, float]:
    a, b = token.split(",")
    return float(a), float(b)


def is_number(token: str) -> bool:
    return bool(re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", token))


def unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1]
    return token
