"""Mapeamento StructuralModel(s) -> EtabsDescription (ARCHITECTURE.md 11.1).

Politicas de representacao (pilar-frame x parede, secoes, materiais, nomes) vivem aqui;
nenhuma regra geometrica. Os modelos de entrada devem ja ter passado pelo geometry_engine.

`EtabsMapper` acumula varias plantas: cada planta gera objetos (definidos uma vez) e os
atribui aos pavimentos que a usam, com a secao/material de cada pavimento. Pontos sao
compartilhados entre plantas por coordenada (mesmo ponto nos varios pavimentos).
"""

from __future__ import annotations

import math
import re
import unicodedata

from ...domain.building import ConcreteClass
from ...domain.config import Config
from ...domain.diagnostics import Diagnostic, DiagnosticCollector, Source
from ...domain.elements import ColumnKind, RectSection
from ...domain.geometry import Point
from ...domain.model import StructuralModel
from .description import (EArea, EAreaLoad, EAssign, EFrame, EFrameSection, EGrid, ELineLoad, ELoadPattern,
                          EMaterial, EPoint, ERestraint, EShellSection, EStory, EtabsDescription)


def ascii_name(text: str) -> str:
    """Nomes do E2K sem acentos e sem aspas."""
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return norm.replace('"', "'").strip()


def concrete_e_modulus_nbr6118(fck_mpa: float, alpha_e: float = 1.0) -> float:
    """Ecs (MPa) pela NBR 6118:2014 item 8.2.8: Eci = alphaE*5600*sqrt(fck); Ecs = alphai*Eci."""
    eci = alpha_e * 5600.0 * math.sqrt(fck_mpa)
    alpha_i = min(1.0, 0.8 + 0.2 * fck_mpa / 80.0)
    return alpha_i * eci


def _fck_from_name(name: str) -> float | None:
    m = re.fullmatch(r"C(\d+(?:\.\d+)?)", name.strip().upper())
    return float(m.group(1)) if m else None


def _cm(v: float) -> str:
    n = round(v * 100, 1)
    return str(int(n)) if abs(n - int(n)) < 1e-9 else f"{n:g}".replace(".", "_")


def etabs_column_angle(tqs_angle_deg: float) -> float:
    """Rotacao da secao no ETABS (confirmado na importacao): a secao e escrita com
    D = B do TQS e B = L do TQS, e o angulo do ETABS e o proprio ANG do TQS (mod 180)."""
    return tqs_angle_deg % 180.0


def _split_at_nodes(seg, model: StructuralModel, tol: float) -> list[tuple[Point, Point, float]]:
    """Divide a linha de eixo nos nos estruturais que caem sobre ela (juntas explicitas viga/laje/parede)."""
    ux, uy = seg.end.x - seg.start.x, seg.end.y - seg.start.y
    length = math.hypot(ux, uy)
    if length < tol:
        return [(seg.start, seg.end, seg.thickness)]
    ux, uy = ux / length, uy / length
    params = []
    for n in model.structural_nodes():
        wx, wy = n.x - seg.start.x, n.y - seg.start.y
        t = wx * ux + wy * uy
        perp = abs(wx * uy - wy * ux)
        if perp <= tol and tol < t < length - tol:
            params.append(round(t, 6))
    cuts = [0.0] + sorted(set(params)) + [length]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a > tol:
            out.append((Point(seg.start.x + ux * a, seg.start.y + uy * a),
                        Point(seg.start.x + ux * b, seg.start.y + uy * b), seg.thickness))
    return out


def _trim_stubs(pieces: list[tuple[Point, Point, float]], model: StructuralModel, tol: float,
                stub_max: float) -> tuple[list[tuple[Point, Point, float]], list[float]]:
    """Remove pedacos terminais curtos cuja extremidade livre nao tem no nem outra lamina (toco de canto)."""
    anchors = [n.point for n in model.structural_nodes()]

    def anchored(p: Point, ends: list[Point]) -> bool:
        if any(p.distance_to(q) <= tol for q in anchors):
            return True
        return sum(1 for q in ends if q.distance_to(p) <= tol) > 1

    trimmed: list[float] = []
    changed = True
    while changed and len(pieces) > 1:
        changed = False
        ends = [p for piece in pieces for p in piece[:2]]
        for i, (a, b, t) in enumerate(pieces):
            length = a.distance_to(b)
            if length <= stub_max and (not anchored(a, ends) or not anchored(b, ends)):
                del pieces[i]
                trimmed.append(length)
                changed = True
                break
    return pieces, trimmed


class EtabsMapper:
    """Acumula plantas/pavimentos e produz a EtabsDescription."""

    def __init__(self, config: Config, title: str, concrete_catalog: dict[str, ConcreteClass] | None = None) -> None:
        self.config = config
        self.opt = config.etabs
        self.title = ascii_name(title) or "tqs2etabs"
        self.catalog = concrete_catalog or {}
        self.diag = DiagnosticCollector()
        self.notes: list[str] = []
        self.stories: list[EStory] = []
        self.base: EStory | None = None
        self.materials: dict[str, EMaterial] = {}
        self.frame_sections: dict[str, EFrameSection] = {}
        self.shell_sections: dict[str, EShellSection] = {}
        self.points: dict[str, EPoint] = {}
        self.point_stories: dict[str, set[str]] = {}
        self._by_coord: list[tuple[Point, str]] = []
        self._next_point = 1
        self.node_to_point: dict[str, str] = {}
        self.frames: list[EFrame] = []
        self.areas: list[EArea] = []
        self.restraints: list[ERestraint] = []
        self.piers: list[str] = []
        self.grids: dict[tuple[str, float], EGrid] = {}
        self.tol = config.tolerances.node_merge
        self.area_loads: list[EAreaLoad] = []
        self.line_loads: list[ELineLoad] = []

    # ------------------------------------------------------------ pavimentos
    def add_story(self, name: str, elevation: float, height: float, similar_to: str | None = None) -> str:
        sname = ascii_name(name)
        self.stories.append(EStory(sname, height, elevation, False, similar_to, similar_to is None))
        return sname

    def set_base(self, elevation: float) -> None:
        self.base = EStory(self.opt.base_story_name, 0.0, round(elevation, 4), True)

    # ---------------------------------------------------------- materiais
    def material(self, name: str | None) -> str:
        key = (name or self.opt.default_material).upper()
        if key not in self.materials:
            fck = _fck_from_name(key)
            if fck is None:
                self.diag.warning("EXP-W-MATERIAL", f"fck nao reconhecido em '{name}'; usando {self.opt.default_material}",
                                  Source.EXPORTER)
                return self.material(self.opt.default_material)
            cat = self.catalog.get(key)
            if cat and cat.e_secant_mpa:
                e_mpa, src = cat.e_secant_mpa, "CONCRETO.DAT"
            else:
                e_mpa, src = concrete_e_modulus_nbr6118(fck), "NBR 6118 8.2.8"
            self.materials[key] = EMaterial(key, fck, round(e_mpa * 1000, 0), self.opt.concrete_unit_weight, source=src)
        return key

    def frame_section(self, kind: str, depth: float, width: float, mat: str) -> str:
        prefix = "B" if kind == "Beam" else "C"
        name = f"{prefix}{_cm(width)}X{_cm(depth)}-{mat}"
        self.frame_sections.setdefault(name, EFrameSection(name, mat, round(depth, 4), round(width, 4), kind))
        return name

    def shell_section(self, kind: str, thickness: float, mat: str, modeling: str) -> str:
        suffix = "" if kind == "Wall" else ("-SH" if modeling == "ShellThin" else "-M")
        name = f"{'W' if kind == 'Wall' else 'S'}{_cm(thickness)}-{mat}{suffix}"
        self.shell_sections.setdefault(name, EShellSection(name, mat, round(thickness, 4), kind, modeling))
        return name

    # -------------------------------------------------------------- pontos
    def point_for_coord(self, p: Point, key: str | None, stories: tuple[str, ...],
                        preferred_name: str | None = None) -> str:
        for q, name in self._by_coord:
            if q.distance_to(p) <= self.tol:
                self.point_stories[name].update(stories)
                if key:
                    self.node_to_point.setdefault(key, name)
                return name
        if preferred_name and preferred_name not in self.points:
            name = preferred_name
        else:
            while str(self._next_point) in self.points:
                self._next_point += 1
            name = str(self._next_point)
        if name.isdigit():
            self._next_point = max(self._next_point, int(name) + 1)
        self.points[name] = EPoint(name, p.x, p.y, key)
        self.point_stories[name] = set(stories)
        self._by_coord.append((p, name))
        if key:
            self.node_to_point[key] = name
        return name

    # -------------------------------------------------------------- planta
    def add_plan(self, model: StructuralModel, story_names: tuple[str, ...], plan_key: str = "",
                 materials_by_story: dict[str, dict[str, str]] | None = None, name_prefix: str = "",
                 restrain_base: bool = False) -> None:
        """Cria os objetos da planta e os atribui a `story_names` (de baixo para cima)."""
        opt, cfg = self.opt, self.config
        mats = materials_by_story or {}

        def node_key(nid: str) -> str:
            return f"{plan_key}:{nid}" if plan_key else nid

        def point_for_node(nid: str) -> str:
            n = model.node(nid)
            preferred = None if plan_key else (re.sub(r"\D", "", nid) or nid)
            return self.point_for_coord(n.point, node_key(nid), story_names, preferred)

        for n in model.structural_nodes():
            point_for_node(n.id)

        def mat_for(story: str, kind: str, explicit: str | None) -> str:
            if explicit:
                return self.material(explicit)
            return self.material(mats.get(story, {}).get(kind))

        # ---------------------------------------------------- pilares/paredes
        base_points: set[str] = set()
        for col in model.columns.values():
            oname = f"{name_prefix}{ascii_name(col.name)}"
            if col.kind_hint == ColumnKind.WALL:
                if not col.axes:
                    self.diag.error("EXP-E-WALL-NO-AXES", f"Pilar {col.id} sem linhas de eixo; nao exportado",
                                    Source.EXPORTER, refs=(col.id,))
                    continue
                pier = ascii_name(col.name) if opt.assign_piers else None
                if pier and pier not in self.piers:
                    self.piers.append(pier)
                pieces = []
                for seg in col.axes:
                    pieces.extend(_split_at_nodes(seg, model, self.tol) if opt.split_walls_at_nodes
                                  else [(seg.start, seg.end, seg.thickness)])
                pieces, trimmed = _trim_stubs(pieces, model, self.tol, cfg.tolerances.trim_wall_stub_max)
                for t in trimmed:
                    self.notes.append(f"{plan_key or 'planta'}: toco de parede {col.id} de {t:.2f} m sem no eliminado")
                for k, (pa, pb, thick) in enumerate(pieces, start=1):
                    a = self.point_for_coord(pa, None, story_names)
                    b = self.point_for_coord(pb, None, story_names)
                    assigns = tuple(EAssign(s, self.shell_section("Wall", thick, mat_for(s, "pilares", col.fck), "ShellThin"), pier)
                                    for s in story_names)
                    name = f"{oname}-{k}" if len(pieces) > 1 else oname
                    self.areas.append(EArea(name, "PANEL", (a, b, b, a), assigns, col.id))
                    base_points.update((a, b))
            else:
                dec = cfg.tolerances.rounding_decimals
                c = Point(round(col.centroid.x, dec), round(col.centroid.y, dec))
                p = self.point_for_coord(c, None, story_names)
                sec_geom = col.section
                if isinstance(sec_geom, RectSection):
                    dims = (sec_geom.width, sec_geom.length)      # D = B do TQS, B = L do TQS (feedback da importacao)
                    angle = etabs_column_angle(sec_geom.angle_deg)
                else:
                    side = math.sqrt(col.area)
                    dims = (side, side)
                    angle = 0.0
                    self.diag.warning("EXP-W-POLY-FRAME", f"Pilar {col.id} poligonal como frame: secao quadrada equivalente",
                                      Source.EXPORTER, refs=(col.id,))
                assigns = tuple(EAssign(s, self.frame_section("Column", dims[0], dims[1], mat_for(s, "pilares", col.fck)))
                                for s in story_names)
                self.frames.append(EFrame(oname, "COLUMN", p, p, assigns, angle, 5, "", col.id))
                base_points.add(p)
        if restrain_base:
            for p in sorted(base_points, key=lambda s: (len(s), s)):
                self.restraints.append(ERestraint(p, opt.base_story_name, opt.base_restraint))

        # ---------------------------------------------------------- vigas
        for beam in model.beams.values():
            multi = len(beam.segments) > 1
            for k, seg in enumerate(beam.segments, start=1):
                if seg.depth <= 0 or seg.width <= 0:
                    self.diag.error("EXP-E-BEAM-SECTION", f"{beam.id} trecho {k} sem secao; nao exportado",
                                    Source.EXPORTER, refs=(beam.id,))
                    continue
                rel = ""
                if opt.apply_releases:
                    parts = []
                    if beam.release_start and k == 1:
                        parts += ["M2I", "M3I"]
                    if beam.release_end and k == len(beam.segments):
                        parts += ["M2J", "M3J"]
                    rel = " ".join(parts)
                name = f"{name_prefix}{ascii_name(beam.name)}" + (f"-{k}" if multi else "")
                assigns = tuple(EAssign(s, self.frame_section("Beam", seg.depth, seg.width, mat_for(s, "vigas", None)))
                                for s in story_names)
                self.frames.append(EFrame(name, "BEAM", point_for_node(seg.start_node_id), point_for_node(seg.end_node_id),
                                          assigns, 0.0, opt.beam_cardinal_point, rel, beam.id))
        if not opt.apply_releases and any(b.release_start or b.release_end for b in model.beams.values()):
            note = "ARE/ARD do TQS (NEEDS_REVIEW) nao aplicados como releases (etabs.apply_releases = false)."
            if note not in self.notes:
                self.notes.append(note)

        # ---------------------------------------------------------- lajes
        for slab in model.slabs.values():
            modeling = "Membrane" if (slab.is_stair and cfg.policy.stair_area_type == "membrane") else "ShellThin"
            pts = tuple(point_for_node(e.start_node_id) for e in slab.edges)
            if len(pts) < 3:
                self.diag.error("EXP-E-SLAB", f"{slab.id} com menos de 3 vertices; nao exportada", Source.EXPORTER,
                                refs=(slab.id,))
                continue
            sname = f"{name_prefix}{ascii_name(slab.name)}"
            assigns = tuple(EAssign(s, self.shell_section("Slab", slab.thickness, mat_for(s, "lajes", None), modeling))
                            for s in story_names)
            self.areas.append(EArea(sname, "FLOOR", pts, assigns, slab.id))
            for k, hole in enumerate(slab.holes, start=1):
                hpts = tuple(self.point_for_coord(p, None, story_names) for p in hole)
                if len(set(hpts)) >= 3:
                    self.areas.append(EArea(f"{sname}-O{k}", "OPENING", hpts,
                                            tuple(EAssign(s, "") for s in story_names), slab.id))

        # ---------------------------------------------------------- cargas
        if opt.export_loads:
            self._add_loads(model, story_names, name_prefix, plan_key)

        # ---------------------------------------------------------- grids
        for g in model.grids:
            key = (g.direction, round(g.coordinate, 4))
            self.grids.setdefault(key, EGrid(g.label, g.direction, g.coordinate))
        unused = [n.id for n in model.nodes.values() if not n.is_structural]
        if unused:
            self.diag.info("EXP-I-NODES-SKIPPED", f"{plan_key or 'planta'}: {len(unused)} nos de carga/orfaos nao exportados",
                           Source.EXPORTER)

    # -------------------------------------------------------------- cargas
    def _add_loads(self, model: StructuralModel, story_names: tuple[str, ...], name_prefix: str, plan_key: str) -> None:
        """ADI de lajes e DIS de vigas: caso 3 -> permanente adicional, caso 4 -> acidental (tf -> kN)."""
        opt = self.opt
        by_number = {lc.number: lc for lc in model.load_cases}
        mapping = {}
        if 3 in by_number:
            mapping[3] = opt.pattern_dead_extra
        if 4 in by_number:
            mapping[4] = opt.pattern_live
        if not mapping and 1 in by_number:
            mapping[1] = opt.pattern_dead_extra
            self.notes.append(f"{plan_key or 'planta'}: LDF sem casos 3/4; caso 1 (total) exportado como {opt.pattern_dead_extra}")
        if not mapping:
            return
        area_names = {a.source: a.name for a in self.areas if a.kind == "FLOOR" and a.name.startswith(name_prefix)}
        beam_names: dict[str, list[str]] = {}
        for f in self.frames:
            if f.kind == "BEAM" and f.name.startswith(name_prefix):
                beam_names.setdefault(f.source, []).append(f.name)
        skipped: dict[str, int] = {}
        for number, pattern in mapping.items():
            for it in by_number[number].items:
                value = it.value * opt.tf_to_kn
                if it.kind == "ADI" and it.element_id in area_names:
                    for s in story_names:
                        self.area_loads.append(EAreaLoad(area_names[it.element_id], s, pattern, round(value, 4),
                                                         f"{plan_key}:{it.element_id} caso {number}"))
                elif it.kind == "DIS" and it.element_id in beam_names:
                    for fname in beam_names[it.element_id]:
                        for s in story_names:
                            self.line_loads.append(ELineLoad(fname, s, pattern, round(value, 4),
                                                             f"{plan_key}:{it.element_id} caso {number}"))
                else:
                    key = it.kind if it.kind in ("DIP", "ARE") else f"{it.kind} ({it.element_id} ausente)"
                    skipped[key] = skipped.get(key, 0) + 1
        for kind, n in skipped.items():
            self.notes.append(f"{plan_key or 'planta'}: {n} carga(s) {kind} nao exportadas (so ADI de laje e DIS de viga)")

    def _load_patterns(self) -> tuple[ELoadPattern, ...]:
        pats = [ELoadPattern("DEAD", "Dead", 1.0, 1.0)]
        used = {l.pattern for l in self.area_loads} | {l.pattern for l in self.line_loads}
        if self.opt.pattern_dead_extra in used:
            pats.append(ELoadPattern(self.opt.pattern_dead_extra, "Super Dead", 0.0, 1.0))
        if self.opt.pattern_live in used:
            pats.append(ELoadPattern(self.opt.pattern_live, "Live", 0.0, self.opt.mass_live_factor))
        return tuple(pats)

    # ------------------------------------------------------------ resultado
    def build(self) -> tuple[EtabsDescription, tuple[Diagnostic, ...]]:
        if self.config.policy.ignore_vertical_offsets:
            self.notes.append("DFS de vigas e lajes ignorado: tudo no nivel do pavimento (decisao 18.5).")
        assert self.base is not None, "set_base() nao chamado"
        stories = tuple(sorted(self.stories, key=lambda s: -s.elevation)) + (self.base,)
        points = tuple(EPoint(p.name, p.x, p.y, p.source_node,
                              tuple(sorted(self.point_stories[p.name], key=lambda n: -self._story_elev(n))))
                       for p in self.points.values())
        desc = EtabsDescription(
            title=self.title, units=("KN", "M", "C"), stories=stories, grid_system=self.opt.grid_system,
            grids=self._renamed_grids(), materials=tuple(self.materials.values()),
            frame_sections=tuple(self.frame_sections.values()), shell_sections=tuple(self.shell_sections.values()),
            points=points, frames=tuple(self.frames), areas=tuple(self.areas), restraints=tuple(self.restraints),
            piers=tuple(self.piers), notes=tuple(self.notes), node_to_point=dict(self.node_to_point),
            load_patterns=self._load_patterns(), area_loads=tuple(self.area_loads), line_loads=tuple(self.line_loads))
        self.diag.info("EXP-I-SUMMARY", "ETABS description: " + ", ".join(f"{k}={v}" for k, v in desc.counts().items()),
                       Source.EXPORTER)
        return desc, self.diag.as_tuple()

    def _story_elev(self, name: str) -> float:
        for s in self.stories:
            if s.name == name:
                return s.elevation
        return -1e9

    def _renamed_grids(self) -> tuple[EGrid, ...]:
        """Grids de varias plantas unidos por coordenada e renomeados em ordem crescente."""
        from ...geometry_engine.grids import grid_label
        naming = self.config.grids
        out = []
        for direction in ("X", "Y"):
            coords = sorted({k[1] for k in self.grids if k[0] == direction})
            merged: list[float] = []
            for c in coords:
                if merged and c - merged[-1] <= self.config.tolerances.coordinate_cluster:
                    continue
                merged.append(c)
            style = naming.x_style if direction == "X" else naming.y_style
            prefix = naming.x_prefix if direction == "X" else naming.y_prefix
            for i, c in enumerate(merged):
                out.append(EGrid(grid_label(style, prefix, i, naming.start_index), direction, c))
        return tuple(out)


def build_description(model: StructuralModel, config: Config) -> tuple[EtabsDescription, tuple]:
    """Caso de uma planta / um pavimento (fluxo `export`)."""
    story_obj = next(iter(model.stories.values()))
    if story_obj.elevation is None or story_obj.height is None:
        elev, height = 0.0, 3.0
    else:
        elev, height = story_obj.elevation, story_obj.height
    title = f"{model.project.building or ''} - {model.project.plan_name or ''}".strip(" -")
    mapper = EtabsMapper(config, title)
    if story_obj.elevation is None:
        mapper.diag.warning("EXP-W-STORY", "Cota/pe-direito desconhecidos: usando cota 0 e altura 3,0 m", Source.EXPORTER)
    story_name = mapper.add_story(story_obj.name, elev, height)
    mapper.set_base(elev - height)
    mapper.notes.append(f"Vigas e lajes com material {config.etabs.default_material} (config); "
                        "fck de vigas/lajes nao existe no LDF/LST.")
    mapper.add_plan(model, (story_name,), plan_key="", restrain_base=True)
    return mapper.build()
