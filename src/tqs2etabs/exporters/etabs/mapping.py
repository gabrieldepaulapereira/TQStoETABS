"""Mapeamento StructuralModel -> EtabsDescription (ARCHITECTURE.md 11.1).

Politicas de representacao (pilar-frame x parede, secoes, materiais, nomes) vivem aqui;
nenhuma regra geometrica. O modelo de entrada deve ja ter passado pelo geometry_engine
(Column.axes preenchidos, coordenadas normalizadas, grids gerados).
"""

from __future__ import annotations

import math
import re
import unicodedata

from ...domain.config import Config
from ...domain.diagnostics import DiagnosticCollector, Source
from ...domain.elements import Column, ColumnKind, NodeRole, RectSection
from ...domain.geometry import Point
from ...domain.model import StructuralModel
from .description import (EArea, EFrame, EFrameSection, EGrid, EMaterial, EPoint, ERestraint,
                          EShellSection, EStory, EtabsDescription)


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
    """Rotacao da secao no ETABS. Eixo local 3 de um pilar vertical = +Y global (ANG 0);
    D (depth) e medido ao longo de 3. TQS: ANG = direcao de L a partir de +X. Logo
    ETABS = TQS - 90 (mod 180). NEEDS_REVIEW: conferir orientacao no ETABS."""
    return (tqs_angle_deg - 90.0) % 180.0


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


def build_description(model: StructuralModel, config: Config) -> tuple[EtabsDescription, tuple]:
    opt = config.etabs
    diag = DiagnosticCollector()
    notes: list[str] = []
    story_obj = next(iter(model.stories.values()))
    story_name = ascii_name(story_obj.name)
    if story_obj.elevation is None or story_obj.height is None:
        diag.warning("EXP-W-STORY", "Cota/pe-direito desconhecidos: usando cota 0 e altura 3,0 m", Source.EXPORTER)
        elev, height = 0.0, 3.0
    else:
        elev, height = story_obj.elevation, story_obj.height
    stories = (EStory(story_name, height, elev), EStory(opt.base_story_name, 0.0, round(elev - height, 4), True))

    # ---------------------------------------------------------- materiais
    materials: dict[str, EMaterial] = {}

    def material(name: str, source: str) -> str:
        key = name.upper()
        if key not in materials:
            fck = _fck_from_name(key)
            if fck is None:
                diag.warning("EXP-W-MATERIAL", f"fck nao reconhecido em '{name}'; usando {opt.default_material}",
                             Source.EXPORTER)
                return material(opt.default_material, "config default")
            e_mpa = concrete_e_modulus_nbr6118(fck)
            materials[key] = EMaterial(key, fck, round(e_mpa * 1000, 0), opt.concrete_unit_weight, source=source)
        return key

    default_mat = material(opt.default_material, "config default (fck de vigas/lajes nao consta do TQS)")
    notes.append(f"Vigas e lajes com material {default_mat} (config); fck de vigas/lajes nao existe no LDF/LST.")

    frame_sections: dict[str, EFrameSection] = {}
    shell_sections: dict[str, EShellSection] = {}

    def frame_section(kind: str, depth: float, width: float, mat: str) -> str:
        prefix = "B" if kind == "Beam" else "C"
        name = f"{prefix}{_cm(width)}X{_cm(depth)}-{mat}"
        frame_sections.setdefault(name, EFrameSection(name, mat, round(depth, 4), round(width, 4), kind))
        return name

    def shell_section(kind: str, thickness: float, mat: str, modeling: str) -> str:
        suffix = "" if kind == "Wall" else ("-SH" if modeling == "ShellThin" else "-M")
        name = f"{'W' if kind == 'Wall' else 'S'}{_cm(thickness)}-{mat}{suffix}"
        shell_sections.setdefault(name, EShellSection(name, mat, round(thickness, 4), kind, modeling))
        return name

    # ------------------------------------------------------------- pontos
    points: dict[str, EPoint] = {}
    node_to_point: dict[str, str] = {}
    by_coord: list[tuple[Point, str]] = []
    tol = config.tolerances.node_merge
    next_aux = max((int(re.sub(r"\D", "", n.id) or 0) for n in model.nodes.values()), default=0) + 1

    def point_for_node(node_id: str) -> str:
        if node_id in node_to_point:
            return node_to_point[node_id]
        n = model.node(node_id)
        name = re.sub(r"\D", "", n.id) or n.id
        points[name] = EPoint(name, n.x, n.y, n.id)
        node_to_point[n.id] = name
        by_coord.append((n.point, name))
        return name

    def point_for_coord(p: Point, purpose: str) -> str:
        nonlocal next_aux
        for q, name in by_coord:
            if q.distance_to(p) <= tol:
                return name
        name = str(next_aux)
        next_aux += 1
        points[name] = EPoint(name, p.x, p.y, None)
        by_coord.append((p, name))
        notes.append(f"Ponto auxiliar {name} ({p.x:.2f}, {p.y:.2f}) criado para {purpose}")
        return name

    for n in model.structural_nodes():
        point_for_node(n.id)

    # ------------------------------------------------------ pilares/paredes
    frames: list[EFrame] = []
    areas: list[EArea] = []
    restraints: list[ERestraint] = []
    piers: list[str] = []
    base_points: set[str] = set()

    for col in model.columns.values():
        mat = material(col.fck, "LDF FCK") if col.fck else default_mat
        if col.kind_hint == ColumnKind.WALL:
            if not col.axes:
                diag.error("EXP-E-WALL-NO-AXES", f"Pilar {col.id} sem linhas de eixo; nao exportado", Source.EXPORTER,
                           refs=(col.id,))
                continue
            pier = ascii_name(col.name) if opt.assign_piers else None
            if pier:
                piers.append(pier)
            pieces = []
            for seg in col.axes:
                pieces.extend(_split_at_nodes(seg, model, tol) if opt.split_walls_at_nodes else [(seg.start, seg.end, seg.thickness)])
            for k, (pa, pb, thick) in enumerate(pieces, start=1):
                a = point_for_coord(pa, f"parede {col.id}")
                b = point_for_coord(pb, f"parede {col.id}")
                sec = shell_section("Wall", thick, mat, "ShellThin")
                name = f"{ascii_name(col.name)}-{k}" if len(pieces) > 1 else ascii_name(col.name)
                areas.append(EArea(name, "PANEL", (a, b, b, a), story_name, sec, pier, col.id))
                base_points.update((a, b))
        else:
            sec_geom = col.section
            c = col.centroid
            p = point_for_coord(c, f"pilar {col.id}")
            if isinstance(sec_geom, RectSection):
                sec = frame_section("Column", sec_geom.length, sec_geom.width, mat)
                angle = etabs_column_angle(sec_geom.angle_deg)
            else:
                side = math.sqrt(col.area)
                sec = frame_section("Column", side, side, mat)
                angle = 0.0
                diag.warning("EXP-W-POLY-FRAME", f"Pilar {col.id} poligonal como frame: secao quadrada equivalente",
                             Source.EXPORTER, refs=(col.id,))
            frames.append(EFrame(ascii_name(col.name), "COLUMN", p, p, story_name, sec, angle, 5, "", col.id))
            base_points.add(p)

    for p in sorted(base_points, key=lambda s: (len(s), s)):
        restraints.append(ERestraint(p, opt.base_story_name, opt.base_restraint))

    # -------------------------------------------------------------- vigas
    for beam in model.beams.values():
        multi = len(beam.segments) > 1
        for k, seg in enumerate(beam.segments, start=1):
            if seg.depth <= 0 or seg.width <= 0:
                diag.error("EXP-E-BEAM-SECTION", f"{beam.id} trecho {k} sem secao; nao exportado", Source.EXPORTER,
                           refs=(beam.id,))
                continue
            sec = frame_section("Beam", seg.depth, seg.width, default_mat)
            rel = ""
            if opt.apply_releases:
                parts = []
                if beam.release_start and k == 1:
                    parts += ["M2I", "M3I"]
                if beam.release_end and k == len(beam.segments):
                    parts += ["M2J", "M3J"]
                rel = " ".join(parts)
            name = f"{ascii_name(beam.name)}-{k}" if multi else ascii_name(beam.name)
            frames.append(EFrame(name, "BEAM", point_for_node(seg.start_node_id), point_for_node(seg.end_node_id),
                                 story_name, sec, 0.0, opt.beam_cardinal_point, rel, beam.id))
    if not opt.apply_releases and any(b.release_start or b.release_end for b in model.beams.values()):
        notes.append("ARE/ARD do TQS (NEEDS_REVIEW) nao aplicados como releases (etabs.apply_releases = false).")

    # -------------------------------------------------------------- lajes
    for slab in model.slabs.values():
        modeling = "Membrane" if (slab.is_stair and config.policy.stair_area_type == "membrane") else "ShellThin"
        sec = shell_section("Slab", slab.thickness, default_mat, modeling)
        pts = tuple(point_for_node(e.start_node_id) for e in slab.edges)
        if len(pts) < 3:
            diag.error("EXP-E-SLAB", f"{slab.id} com menos de 3 vertices; nao exportada", Source.EXPORTER, refs=(slab.id,))
            continue
        areas.append(EArea(ascii_name(slab.name), "FLOOR", pts, story_name, sec, None, slab.id))
    if config.policy.ignore_vertical_offsets:
        notes.append("DFS de vigas e lajes ignorado: tudo no nivel do pavimento (decisao 18.5).")

    grids = tuple(EGrid(g.label, g.direction, g.coordinate) for g in model.grids)
    title = ascii_name(f"{model.project.building or ''} - {model.project.plan_name or ''}").strip(" -")
    desc = EtabsDescription(
        title=title or "tqs2etabs", units=("KN", "M", "C"), stories=stories, grid_system=opt.grid_system,
        grids=grids, materials=tuple(materials.values()), frame_sections=tuple(frame_sections.values()),
        shell_sections=tuple(shell_sections.values()), points=tuple(points.values()), frames=tuple(frames),
        areas=tuple(areas), restraints=tuple(restraints), piers=tuple(piers), notes=tuple(notes),
        node_to_point=node_to_point)
    unused = [n.id for n in model.nodes.values() if not n.is_structural]
    if unused:
        diag.info("EXP-I-NODES-SKIPPED", f"{len(unused)} nos de carga/orfaos nao exportados", Source.EXPORTER)
    diag.info("EXP-I-SUMMARY", "ETABS description: " + ", ".join(f"{k}={v}" for k, v in desc.counts().items()),
              Source.EXPORTER)
    return desc, diag.as_tuple()
