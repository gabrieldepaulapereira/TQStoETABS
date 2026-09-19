"""Parser do LDF: transforma o texto em LdfDocument (valores brutos em cm).

Responde apenas "o que existe no arquivo". Nao corrige geometria. Tokens ou
linhas nao reconhecidos geram diagnosticos WARNING e sao preservados em
`unknown_tokens`/`extra` — nunca uma excecao fatal.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from ....domain.diagnostics import DiagnosticCollector, Source
from ..common import (LogicalLine, is_number, is_pair, logical_lines, parse_pair,
                      read_tqs_text, tokenize, unquote)
from .document import (LdfAxisNode, LdfBeamDimensions, LdfBeamGeometry, LdfBeamSection,
                       LdfCatalogSection, LdfColumnDimensions, LdfColumnGeometry, LdfDocument,
                       LdfHeader, LdfLoadCase, LdfLoadItem, LdfMaterial, LdfSlabDimensions,
                       LdfSlabGeometry, LdfSlabVertex, RawPolygon)

_BEAM_RE = re.compile(r"V\d+")
_COL_RE = re.compile(r"P\d+")
_SLAB_RE = re.compile(r"L\d+")
_AXIS_TOKEN_RE = re.compile(r"^(\d+)([A-Z]+\d*)?$")
_AXIS_QUAL_RE = re.compile(r"^(N|P\d+|AV\d+|RV\d+)$")
_SLAB_QUAL_RE = re.compile(r"^(LIV|P\d+)$")

# Secoes de primeiro nivel que abrem um bloco terminado por FIM.
_BLOCK_SECTIONS = {"TSECOES", "TMATERIAIS", "GEOMETRIA", "DIMENSOES", "CARGAS",
                   "CAD/LAJES", "CAD/VIGAS", "GRELHA"}

_LOAD_CASE_DESCRIPTIONS = {
    1: "Casos 1 a 4 agrupados (permanentes + acidentais)",
    2: "Peso proprio",
    3: "Cargas permanentes",
    4: "Cargas acidentais",
}


class _Parser:
    def __init__(self, text: str, source_path: str | None) -> None:
        self.text = text
        self.source_path = source_path
        self.diag = DiagnosticCollector()
        self.header = self._parse_header(text)
        self.scale: float | None = None
        self.ctor = None
        self.nodes: dict[int, tuple[float, float]] = {}
        self.beams: dict[str, LdfBeamGeometry] = {}
        self.columns: dict[str, LdfColumnGeometry] = {}
        self.slabs: dict[str, LdfSlabGeometry] = {}
        self.beam_dims: dict[str, LdfBeamDimensions] = {}
        self.column_dims: dict[str, LdfColumnDimensions] = {}
        self.slab_dims: dict[str, LdfSlabDimensions] = {}
        self.load_cases: list[LdfLoadCase] = []
        self.catalog: list[LdfCatalogSection] = []
        self.materials: list[LdfMaterial] = []
        self.sections_seen: list[str] = []
        self.extra: dict[str, str] = {}

    # ------------------------------------------------------------ utils
    def _loc(self, line: LogicalLine) -> str:
        return f"LDF:{line.number}"

    def _warn(self, code: str, msg: str, line: LogicalLine, **data) -> None:
        self.diag.warning(code, msg, Source.PARSER, location=self._loc(line), **data)

    # ----------------------------------------------------------- header
    @staticmethod
    def _parse_header(text: str) -> LdfHeader:
        """Le os comentarios iniciais ($ ...) do Modelador."""
        kw: dict[str, str | int | None] = {}
        for raw in text.splitlines()[:40]:
            s = raw.strip()
            if not s.startswith("$"):
                continue
            body = s.lstrip("$").strip()
            m = re.match(r"(\d{2}/\d{2}/\d{4} - \d{2}:\d{2}:\d{2})$", body)
            if m:
                kw["generated_at"] = m.group(1)
                continue
            m = re.match(r"Pasta\s+(.+)$", body)
            if m:
                kw["folder"] = m.group(1).strip()
                continue
            m = re.match(r"Pavimento\s+(.+?)\s{2,}Projeto\s+(\d+)$", body)
            if m:
                kw["plan_name"] = m.group(1).strip()
                kw["plan_project"] = int(m.group(2))
                continue
            m = re.match(r"Edif[ií]cio\s+(.+?)\s{2,}Projeto\s+(\d+)$", body)
            if m:
                kw["building"] = m.group(1).strip()
                kw["building_project"] = int(m.group(2))
                continue
            if "building" in kw and "title" not in kw and body and not body.startswith("Arquivo"):
                kw["title"] = body
                continue
            if "title" in kw and "client" not in kw and body and not body.startswith("Arquivo"):
                kw["client"] = body
                continue
        return LdfHeader(**kw)  # type: ignore[arg-type]

    # ------------------------------------------------------------- main
    def run(self) -> LdfDocument:
        lines = logical_lines(self.text)
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            tok = tokenize(line.text)
            if not tok:
                i += 1
                continue
            head = tok[0].upper()
            if head == "DEFINE":
                self._define(tok, line)
                i += 1
            elif head == "PROJETO":
                self.extra["PROJETO"] = " ".join(tok[1:])
                i += 1
            elif head == "CTOR":
                self.ctor = parse_pair(tok[1]) if len(tok) > 1 and is_pair(tok[1]) else None
                i += 1
            elif head in _BLOCK_SECTIONS:
                self.sections_seen.append(" ".join(tok))
                block, i = self._collect_block(lines, i + 1)
                self._dispatch_block(head, tok, block)
            elif head == "FIM":
                i += 1
            else:
                self._warn("PARSE-W-UNKNOWN-LINE", f"Linha nao reconhecida no nivel superior: '{line.text}'", line)
                i += 1
        return self._build_document()

    def _collect_block(self, lines: list[LogicalLine], start: int) -> tuple[list[LogicalLine], int]:
        block: list[LogicalLine] = []
        i = start
        while i < len(lines):
            if lines[i].text.strip().upper() == "FIM":
                return block, i + 1
            block.append(lines[i])
            i += 1
        self.diag.error("PARSE-E-UNTERMINATED-BLOCK", "Bloco sem FIM ate o final do arquivo", Source.PARSER)
        return block, i

    def _dispatch_block(self, head: str, header_tokens: list[str], block: list[LogicalLine]) -> None:
        if head == "TSECOES":
            self._tsecoes(block)
        elif head == "TMATERIAIS":
            self._tmateriais(block)
        elif head == "GEOMETRIA":
            self._geometria(block)
        elif head == "DIMENSOES":
            self._dimensoes(block)
        elif head == "CARGAS":
            self._cargas(header_tokens, block)
        # CAD/LAJES, CAD/VIGAS, GRELHA: ignorados (vazios; conteudo preservado em extra)
        elif block:
            self.extra[head] = "\n".join(l.text for l in block)

    def _define(self, tok: list[str], line: LogicalLine) -> None:
        if len(tok) >= 3 and tok[1].upper() == "ESCALA" and is_number(tok[2]):
            self.scale = float(tok[2])
        else:
            self.extra.setdefault("DEFINE", "")
            self.extra["DEFINE"] += " ".join(tok[1:]) + ";"

    # ------------------------------------------------------- catalogos
    def _tsecoes(self, block: list[LogicalLine]) -> None:
        for line in block:
            tok = tokenize(line.text)
            if not tok or not tok[0].startswith("'"):
                self._warn("PARSE-W-TSECOES", f"Entrada de secao nao reconhecida: '{line.text}'", line)
                continue
            nums = [float(t) for t in tok[1:] if is_number(t)]
            if len(nums) < 4:
                self._warn("PARSE-W-TSECOES", f"Secao '{tok[0]}' com menos de 4 propriedades", line)
                continue
            self.catalog.append(LdfCatalogSection(unquote(tok[0]), *nums[:4], tuple(nums[4:])))

    def _tmateriais(self, block: list[LogicalLine]) -> None:
        for line in block:
            tok = tokenize(line.text)
            if not tok or not tok[0].startswith("'"):
                self._warn("PARSE-W-TMATERIAIS", f"Entrada de material nao reconhecida: '{line.text}'", line)
                continue
            nums = tuple(float(t) for t in tok[1:] if is_number(t))
            self.materials.append(LdfMaterial(unquote(tok[0]), nums))

    # -------------------------------------------------------- GEOMETRIA
    def _geometria(self, block: list[LogicalLine]) -> None:
        for line in block:
            tok = tokenize(line.text)
            if not tok:
                continue
            head = tok[0]
            up = head.upper()
            if up == "DEFINE":
                self._define(tok, line)
            elif head.isdigit() and len(tok) == 2 and is_pair(tok[1]):
                nid = int(head)
                if nid in self.nodes:
                    self._warn("PARSE-W-DUP-NODE", f"No {nid} redefinido", line)
                self.nodes[nid] = parse_pair(tok[1])
            elif _BEAM_RE.fullmatch(head) and len(tok) > 1 and tok[1].upper() == "EIXO":
                self._beam_geometry(tok, line)
            elif _COL_RE.fullmatch(head) and len(tok) > 1 and tok[1].isdigit():
                self.columns[head] = LdfColumnGeometry(
                    name=head, node=int(tok[1]),
                    material=tok[2] if len(tok) > 2 else None,
                    flags=tuple(t.upper() for t in tok[3:]), line=line.number)
            elif _SLAB_RE.fullmatch(head) and "AREA" in [t.upper() for t in tok]:
                self._slab_geometry(tok, line)
            else:
                self._warn("PARSE-W-GEOMETRIA", f"Linha nao reconhecida em GEOMETRIA: '{line.text}'", line)

    def _beam_geometry(self, tok: list[str], line: LogicalLine) -> None:
        axis: list[LdfAxisNode] = []
        unknown: list[str] = []
        rel_start = rel_end = False
        rest = tok[2:]
        i = 0
        while i < len(rest):
            t = rest[i]
            up = t.upper()
            m = _AXIS_TOKEN_RE.match(t)
            if m:
                nid = int(m.group(1))
                qual = m.group(2)
                if qual is None and i + 1 < len(rest) and _AXIS_QUAL_RE.match(rest[i + 1]):
                    qual = rest[i + 1]
                    i += 1
                if qual is not None and not _AXIS_QUAL_RE.match(qual):
                    unknown.append(t)
                    qual = None
                axis.append(LdfAxisNode(nid, qual))
            elif up == "ARE":
                if len(axis) == 1:
                    rel_start = True
                else:
                    unknown.append(f"{t}@{len(axis)}")
            elif up == "ARD":
                # ARD precede o ultimo no; validado apos o laco
                rel_end = True
                if i != len(rest) - 2:
                    unknown.append(f"{t}@{len(axis)}")
            else:
                unknown.append(t)
            i += 1
        if unknown:
            self._warn("PARSE-W-BEAM-TOKEN", f"Tokens nao interpretados em {tok[0]}: {unknown}", line,
                       tokens=unknown)
        if len(axis) < 2:
            self._warn("PARSE-W-BEAM-AXIS", f"Viga {tok[0]} com menos de 2 nos no eixo", line)
        self.beams[tok[0]] = LdfBeamGeometry(tok[0], tuple(axis), rel_start, rel_end, tuple(unknown), line.number)

    def _slab_geometry(self, tok: list[str], line: LogicalLine) -> None:
        ups = [t.upper() for t in tok]
        ia = ups.index("AREA")
        title = None
        flags: list[str] = []
        for t in tok[1:ia]:
            if t.startswith("'"):
                title = unquote(t)
            else:
                flags.append(t.upper())
        area = float(tok[ia + 1]) if ia + 1 < len(tok) and is_number(tok[ia + 1]) else None
        rest = tok[ia + 2:]
        angle = None
        verts: list[LdfSlabVertex] = []
        unknown: list[str] = []
        i = 0
        while i < len(rest):
            t = rest[i]
            if t.upper() == "ANG":
                angle = float(rest[i + 1]) if i + 1 < len(rest) and is_number(rest[i + 1]) else None
                i += 2
                continue
            if t.isdigit():
                qual = None
                if i + 1 < len(rest) and _SLAB_QUAL_RE.match(rest[i + 1]):
                    qual = rest[i + 1].upper()
                    i += 1
                verts.append(LdfSlabVertex(int(t), qual))
            else:
                unknown.append(t)
            i += 1
        if unknown:
            self._warn("PARSE-W-SLAB-TOKEN", f"Tokens nao interpretados em {tok[0]}: {unknown}", line, tokens=unknown)
        if len(verts) < 3:
            self._warn("PARSE-W-SLAB-VERTICES", f"Laje {tok[0]} com menos de 3 vertices", line)
        self.slabs[tok[0]] = LdfSlabGeometry(tok[0], title, tuple(flags), area, tuple(verts), angle, line.number)

    # -------------------------------------------------------- DIMENSOES
    def _dimensoes(self, block: list[LogicalLine]) -> None:
        pending_laminas: tuple[str, int] | None = None   # (pilar, quantidade restante)
        for line in block:
            tok = tokenize(line.text)
            if not tok:
                continue
            head = tok[0]
            up = head.upper()
            if pending_laminas and is_pair(head):
                name, remaining = pending_laminas
                poly = self._polygon(tok, line)
                cd = self.column_dims[name]
                self.column_dims[name] = replace(cd, laminas=cd.laminas + (poly,))
                remaining -= 1
                pending_laminas = (name, remaining) if remaining > 0 else None
                continue
            if pending_laminas:
                self._warn("PARSE-W-LAMINAS", f"Esperava {pending_laminas[1]} laminas de {pending_laminas[0]}", line)
                pending_laminas = None
            if _BEAM_RE.fullmatch(head):
                self._beam_dims(tok, line)
            elif _COL_RE.fullmatch(head) and len(tok) > 1 and tok[1].upper() in ("R", "G"):
                self._column_dims(tok, line)
            elif up == "PSU" and len(tok) > 2:
                name = "P" + tok[1]
                poly = self._polygon(tok[2:], line)
                if name in self.column_dims:
                    cd = self.column_dims[name]
                    self.column_dims[name] = replace(cd, psu=poly)
                else:
                    self._warn("PARSE-W-PSU", f"PSU para pilar desconhecido {name}", line)
            elif up == "LAMINAS" and len(tok) >= 3:
                name = "P" + tok[1]
                if name in self.column_dims and tok[2].isdigit():
                    pending_laminas = (name, int(tok[2]))
                else:
                    self._warn("PARSE-W-LAMINAS", f"LAMINAS para pilar desconhecido {name}", line)
            elif _SLAB_RE.fullmatch(head) and len(tok) > 1 and is_number(tok[1]):
                self._slab_dims(tok, line)
            else:
                self._warn("PARSE-W-DIMENSOES", f"Linha nao reconhecida em DIMENSOES: '{line.text}'", line)

    def _polygon(self, tok: list[str], line: LogicalLine) -> RawPolygon:
        pts = []
        for t in tok:
            if is_pair(t):
                pts.append(parse_pair(t))
            elif t != ";":
                self._warn("PARSE-W-POLYGON", f"Token inesperado em poligono: '{t}'", line)
        return tuple(pts)

    def _beam_dims(self, tok: list[str], line: LogicalLine) -> None:
        sections: list[LdfBeamSection] = []
        unknown: list[str] = []
        plan_area = volume = None
        i = 1
        while i < len(tok):
            t = tok[i]
            up = t.upper()
            if re.fullmatch(r"S\d+", up) and i + 1 < len(tok) and "/" in tok[i + 1]:
                b, h = tok[i + 1].split("/")
                sections.append(LdfBeamSection(int(up[1:]), float(b), float(h)))
                i += 2
            elif up == "DFS" and i + 1 < len(tok) and is_number(tok[i + 1]):
                if sections:
                    last = sections[-1]
                    sections[-1] = LdfBeamSection(last.index, last.width_cm, last.depth_cm, float(tok[i + 1]))
                else:
                    unknown.append("DFS-sem-secao")
                i += 2
            elif up == "VOL" and i + 2 < len(tok) and is_number(tok[i + 1]) and is_number(tok[i + 2]):
                plan_area, volume = float(tok[i + 1]), float(tok[i + 2])
                i += 3
            else:
                unknown.append(t)
                i += 1
        if unknown:
            self._warn("PARSE-W-BEAM-DIM-TOKEN", f"Tokens nao interpretados em {tok[0]}: {unknown}", line, tokens=unknown)
        self.beam_dims[tok[0]] = LdfBeamDimensions(tok[0], tuple(sections), plan_area, volume, tuple(unknown), line.number)

    def _column_dims(self, tok: list[str], line: LogicalLine) -> None:
        name = tok[0]
        kind = tok[1].upper()
        unknown: list[str] = []
        length = width = angle = None
        base = None
        polygon = None
        dsc = None
        fck = None
        i = 2
        if kind == "R":
            if i < len(tok) and "/" in tok[i]:
                a, b = tok[i].split("/")
                length, width = float(a), float(b)
                i += 1
            else:
                self._warn("PARSE-W-COLUMN-DIM", f"Pilar {name} R sem L/B", line)
        else:  # G: poligono ate BASE
            pts = []
            while i < len(tok) and tok[i].upper() != "BASE":
                if is_pair(tok[i]):
                    pts.append(parse_pair(tok[i]))
                elif tok[i] != ";":
                    unknown.append(tok[i])
                i += 1
            polygon = tuple(pts)
        while i < len(tok):
            t = tok[i]
            up = t.upper()
            if up == "ANG" and i + 1 < len(tok) and is_number(tok[i + 1]):
                angle = float(tok[i + 1])
                i += 2
            elif up == "BASE" and i + 1 < len(tok) and is_pair(tok[i + 1]):
                base = parse_pair(tok[i + 1])
                i += 2
            elif up == "DSC" and i + 1 < len(tok) and tok[i + 1].isdigit():
                dsc = int(tok[i + 1])
                i += 2
            elif up == "FCK" and i + 1 < len(tok):
                fck = unquote(tok[i + 1])
                i += 2
            else:
                unknown.append(t)
                i += 1
        if unknown:
            self._warn("PARSE-W-COLUMN-DIM-TOKEN", f"Tokens nao interpretados em {name}: {unknown}", line, tokens=unknown)
        self.column_dims[name] = LdfColumnDimensions(
            name=name, kind=kind, length_cm=length, width_cm=width, angle_deg=angle, base=base,
            polygon=polygon, dsc=dsc, fck=fck, unknown_tokens=tuple(unknown), line=line.number)

    def _slab_dims(self, tok: list[str], line: LogicalLine) -> None:
        thickness = float(tok[1])
        dfs = None
        cantilever = False
        larm: list[str] = []
        unknown: list[str] = []
        i = 2
        while i < len(tok):
            up = tok[i].upper()
            if up == "DFS" and i + 1 < len(tok) and is_number(tok[i + 1]):
                dfs = float(tok[i + 1])
                i += 2
            elif up == "BALANCO":
                cantilever = True
                i += 1
            elif up == "LARM":
                larm = tok[i + 1:]
                break
            else:
                unknown.append(tok[i])
                i += 1
        if unknown:
            self._warn("PARSE-W-SLAB-DIM-TOKEN", f"Tokens nao interpretados em {tok[0]}: {unknown}", line, tokens=unknown)
        self.slab_dims[tok[0]] = LdfSlabDimensions(tok[0], thickness, dfs, cantilever, tuple(larm),
                                                   tuple(unknown), line.number)

    # ----------------------------------------------------------- CARGAS
    def _cargas(self, header_tokens: list[str], block: list[LogicalLine]) -> None:
        number = 0
        if len(header_tokens) >= 3 and header_tokens[1].upper() == "CASO" and header_tokens[2].isdigit():
            number = int(header_tokens[2])
        items: list[LdfLoadItem] = []
        for line in block:
            tok = tokenize(line.text)
            if not tok:
                continue
            if tok[0].upper() == "DEFINE":
                self._define(tok, line)
                continue
            elem = tok[0]
            kind = tok[1].upper() if len(tok) > 1 else ""
            try:
                if kind == "DIS":
                    items.append(LdfLoadItem(elem, "DIS", float(tok[2]), line=line.number))
                elif kind == "DIP":
                    items.append(LdfLoadItem(elem, "DIP", float(tok[4]), (int(tok[2]), int(tok[3])), line=line.number))
                elif kind == "ADI":
                    items.append(LdfLoadItem(elem, "ADI", float(tok[2]), line=line.number))
                elif kind == "ARE":
                    ups = [t.upper() for t in tok]
                    iv = ups.index("VAL")
                    poly = self._polygon(tok[2:iv], line)
                    items.append(LdfLoadItem(elem, "ARE", float(tok[iv + 1]), region=poly, line=line.number))
                else:
                    self._warn("PARSE-W-LOAD", f"Carga nao reconhecida: '{line.text}'", line)
            except (IndexError, ValueError):
                self._warn("PARSE-W-LOAD", f"Carga mal formada: '{line.text}'", line)
        self.load_cases.append(LdfLoadCase(number, _LOAD_CASE_DESCRIPTIONS.get(number, ""), tuple(items)))

    # ---------------------------------------------------------- resultado
    def _build_document(self) -> LdfDocument:
        if not self.nodes:
            self.diag.error("PARSE-E-NO-NODES", "Nenhum no encontrado (bloco GEOMETRIA ausente?)", Source.PARSER)
        for name in self.beams:
            if name not in self.beam_dims:
                self.diag.warning("PARSE-W-MISSING-DIM", f"Viga {name} sem DIMENSOES", Source.PARSER, refs=(name,))
        for name in self.columns:
            if name not in self.column_dims:
                self.diag.warning("PARSE-W-MISSING-DIM", f"Pilar {name} sem DIMENSOES", Source.PARSER, refs=(name,))
        for name in self.slabs:
            if name not in self.slab_dims:
                self.diag.warning("PARSE-W-MISSING-DIM", f"Laje {name} sem DIMENSOES", Source.PARSER, refs=(name,))
        return LdfDocument(
            header=self.header, scale=self.scale, ctor=self.ctor, nodes=self.nodes,
            beams=self.beams, columns=self.columns, slabs=self.slabs,
            beam_dims=self.beam_dims, column_dims=self.column_dims, slab_dims=self.slab_dims,
            load_cases=tuple(self.load_cases), catalog_sections=tuple(self.catalog),
            materials=tuple(self.materials), sections_seen=tuple(self.sections_seen),
            diagnostics=self.diag.as_tuple(), source_path=self.source_path, extra=self.extra)


def parse_ldf_text(text: str, source_path: str | None = None) -> LdfDocument:
    return _Parser(text, source_path).run()


def parse_ldf(path: Path | str) -> LdfDocument:
    return parse_ldf_text(read_tqs_text(path), str(path))
