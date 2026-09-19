"""Diagnosticos (INFO/WARNING/ERROR) e registros de alteracao para auditoria."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Level(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Source(str, Enum):
    TQS_LST = "TQS_LST"      # aviso emitido pelo proprio TQS
    PARSER = "PARSER"        # leitura dos arquivos
    BUILDER = "BUILDER"      # construcao do modelo intermediario
    ENGINE = "ENGINE"        # motor geometrico
    EXPORTER = "EXPORTER"    # gerador ETABS
    VALIDATION = "VALIDATION"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    level: Level
    code: str
    message: str
    source: Source
    refs: tuple[str, ...] = ()
    action: str | None = None
    location: str | None = None   # ex.: "LDF:185"
    data: dict[str, Any] = field(default_factory=dict)

    def format(self) -> str:
        loc = f" [{self.location}]" if self.location else ""
        refs = f" ({', '.join(self.refs)})" if self.refs else ""
        act = f" -> {self.action}" if self.action else ""
        return f"{self.level.value:7} {self.code}{loc}{refs}: {self.message}{act}"


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    element_id: str
    attribute: str
    before: Any
    after: Any
    reason: str
    rule: str
    step: str
    tolerance: float | None = None
    reference: str | None = None

    def format(self) -> str:
        ref = f" ref={self.reference}" if self.reference else ""
        tol = f" tol={self.tolerance}" if self.tolerance is not None else ""
        return (f"{self.element_id}.{self.attribute}: {self.before} -> {self.after}"
                f" | {self.reason} | rule={self.rule}{ref}{tol}")


class DiagnosticCollector:
    """Acumulador mutavel usado durante parsing/builder; o modelo final recebe tuplas."""

    def __init__(self) -> None:
        self.items: list[Diagnostic] = []

    def add(self, level: Level, code: str, message: str, source: Source, *,
            refs: tuple[str, ...] = (), action: str | None = None,
            location: str | None = None, **data: Any) -> Diagnostic:
        d = Diagnostic(level, code, message, source, refs, action, location, dict(data))
        self.items.append(d)
        return d

    def info(self, code, message, source, **kw):
        return self.add(Level.INFO, code, message, source, **kw)

    def warning(self, code, message, source, **kw):
        return self.add(Level.WARNING, code, message, source, **kw)

    def error(self, code, message, source, **kw):
        return self.add(Level.ERROR, code, message, source, **kw)

    def count(self, level: Level) -> int:
        return sum(1 for d in self.items if d.level == level)

    def as_tuple(self) -> tuple[Diagnostic, ...]:
        return tuple(self.items)
