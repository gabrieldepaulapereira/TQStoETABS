"""Builder: LdfDocument (+ LstDocument opcional) -> StructuralModel em metros.

Aplica apenas as regras de INTERPRETACAO do formato (ARCHITECTURE.md secao 1):
reconstrucao de pilares R, conversao cm -> m, classificacao de papeis dos nos,
inferencia da viga de cada bordo de laje. Nao corrige geometria.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ....domain.config import Config, ModelingPolicy
from ....domain.diagnostics import DiagnosticCollector, Level, Source
from ....domain.elements import (Beam, BeamSegment, BeamSupport, CatalogSection, Column,
                                 ColumnKind, EdgeSupport, LoadCase, LoadItem, Material, Node,
                                 NodeRole, PolygonSection, Provenance, RectSection, Slab,
                                 SlabEdge, Story, SupportKind)
from ....domain.geometry import Point, Polygon, open_polygon, rotate, scale_polygon
from ....domain.model import ProjectInfo, StructuralModel
from ..lst.document import LstDocument
from .document import LdfColumnDimensions, LdfDocument, Pair, RawPolygon

CM_TO_M = 0.01


def _pt(pair: Pair) -> Point:
    return Point(pair[0] * CM_TO_M, pair[1] * CM_TO_M)


def _poly(raw: RawPolygon) -> Polygon:
    return open_polygon(tuple(_pt(p) for p in raw), tol=1e-9)


def _node_id(n: int) -> str:
    return f"N{n}"


# ------------------------------------------------------------------ pilares

def rect_origin_from_reference(node_cm: Pair, base_uv: Pair, angle_deg: float) -> Pair:
    """Canto-origem local do pilar R: origem = no - R(ang)*BASE (ARCHITECTURE 1.4.2), em cm."""
    gx, gy = rotate(base_uv[0], base_uv[1], angle_deg)
    return node_cm[0] - gx, node_cm[1] - gy


def _column_kind(section, flags: frozenset[str], policy: ModelingPolicy) -> ColumnKind:
    if isinstance(section, PolygonSection):
        return ColumnKind.WALL if policy.polygon_always_wall else ColumnKind.COLUMN
    if "CORTINA" in flags:
        return ColumnKind.WALL
    return ColumnKind.WALL if section.aspect_ratio > policy.wall_aspect_ratio else ColumnKind.COLUMN


def _story_from_lst(ldf: LdfDocument, lst: LstDocument | None, diag: DiagnosticCollector) -> Story:
    name = ldf.header.plan_name or "Pavimento"
    story_id = "S1"
    if lst is None or not lst.stories:
        if lst is None:
            diag.warning("BUILD-W-NO-LST", "LST ausente: cota e pe-direito do pavimento desconhecidos",
                         Source.BUILDER)
        else:
            diag.warning("BUILD-W-NO-STORY", "LST sem tabela 'Definicao de Pisos'", Source.BUILDER)
        return Story(story_id, name, source="LDF")
    rows = lst.stories
    chosen = rows[0]
    if len(rows) > 1:
        m = re.match(r"\s*(\d+)", name)
        wanted = int(m.group(1)) if m else None
        match = [r for r in rows if r.index == wanted]
        if match:
            chosen = match[0]
        else:
            diag.warning("BUILD-W-STORY-AMBIGUOUS",
                         f"LST com {len(rows)} pisos; usando o primeiro ({chosen.index})", Source.BUILDER)
    return Story(story_id, name, tqs_index=chosen.index, title=chosen.title,
                 elevation=chosen.elevation_m, height=chosen.height_m, source="LST")


def build_model(ldf: LdfDocument, lst: LstDocument | None = None,
                config: Config | None = None) -> StructuralModel:
    config = config or Config()
    policy = config.policy
    diag = DiagnosticCollector()
    diag.items.extend(ldf.diagnostics)
    if lst is not None:
        diag.items.extend(lst.diagnostics)
    src = ldf.source_path or "LDF"

    story = _story_from_lst(ldf, lst, diag)
    z = story.elevation or 0.0

    # ---------------------------------------------------------- pilares
    columns: dict[str, Column] = {}
    for name, geo in ldf.columns.items():
        dims = ldf.column_dims.get(name)
        if dims is None:
            diag.error("BUILD-E-COLUMN-NO-DIM", f"Pilar {name} sem dimensoes; ignorado", Source.BUILDER, refs=(name,))
            continue
        if geo.node not in ldf.nodes:
            diag.error("BUILD-E-COLUMN-NODE", f"Pilar {name} referencia no inexistente {geo.node}", Source.BUILDER, refs=(name,))
            continue
        node_cm = ldf.nodes[geo.node]
        section = _build_column_section(name, dims, node_cm, diag)
        if section is None:
            continue
        flags = frozenset(geo.flags)
        kind = _column_kind(section, flags, policy)
        columns[name] = Column(
            id=name, name=name, story_id=story.id, section=section,
            reference_node_id=_node_id(geo.node), kind_hint=kind,
            laminas=tuple(_poly(l) for l in dims.laminas),
            section_above=_poly(dims.psu) if dims.psu else None,
            material_ref=geo.material, fck=dims.fck, flags=flags,
            tqs_attrs={"DSC": dims.dsc, "kind": dims.kind, "unknown": dims.unknown_tokens},
            provenance=Provenance(src, name, {"node": geo.node, "base_cm": dims.base,
                                              "angle": dims.angle_deg, "L_cm": dims.length_cm,
                                              "B_cm": dims.width_cm}))
        if "FURADO" in flags and not _has_gap(dims):
            diag.info("BUILD-I-FURADO-NO-GAP", f"Pilar {name} marcado FURADO sem vao visivel nas laminas "
                      "(NEEDS_REVIEW)", Source.BUILDER, refs=(name,))

    # ------------------------------------------------------------ vigas
    beams: dict[str, Beam] = {}
    node_beams: dict[int, set[str]] = defaultdict(set)
    for name, geo in ldf.beams.items():
        dims = ldf.beam_dims.get(name)
        axis_nodes = [a.node for a in geo.axis]
        missing = [n for n in axis_nodes if n not in ldf.nodes]
        if missing:
            diag.error("BUILD-E-BEAM-NODE", f"Viga {name} referencia nos inexistentes {missing}", Source.BUILDER, refs=(name,))
            continue
        nseg = len(axis_nodes) - 1
        sections = list(dims.sections) if dims else []
        if len(sections) != nseg:
            diag.warning("BUILD-W-BEAM-SECTIONS",
                         f"Viga {name}: {len(sections)} secoes para {nseg} trechos; "
                         f"{'repetindo a ultima' if sections else 'sem secao'}",
                         Source.BUILDER, refs=(name,))
        segments = []
        for k in range(nseg):
            sec = sections[k] if k < len(sections) else (sections[-1] if sections else None)
            segments.append(BeamSegment(
                _node_id(axis_nodes[k]), _node_id(axis_nodes[k + 1]),
                width=(sec.width_cm * CM_TO_M) if sec else 0.0,
                depth=(sec.depth_cm * CM_TO_M) if sec else 0.0,
                top_offset=(sec.dfs_cm or 0.0) * CM_TO_M if sec else 0.0))
        supports = []
        for a in geo.axis:
            node_beams[a.node].add(name)
            q = a.qualifier
            if q is None or q == "N":
                supports.append(BeamSupport(_node_id(a.node), SupportKind.FREE))
            elif q.startswith("P"):
                supports.append(BeamSupport(_node_id(a.node), SupportKind.COLUMN, q))
            elif q.startswith("AV"):
                supports.append(BeamSupport(_node_id(a.node), SupportKind.ON_BEAM, "V" + q[2:]))
            elif q.startswith("RV"):
                supports.append(BeamSupport(_node_id(a.node), SupportKind.RECEIVES, "V" + q[2:]))
        beams[name] = Beam(
            id=name, name=name, story_id=story.id, axis=tuple(_node_id(n) for n in axis_nodes),
            segments=tuple(segments), supports=tuple(supports),
            release_start=geo.release_start, release_end=geo.release_end,
            tqs_attrs={"plan_area_cm2": dims.plan_area_cm2 if dims else None,
                       "volume_cm3": dims.volume_cm3 if dims else None,
                       "unknown": geo.unknown_tokens + (dims.unknown_tokens if dims else ())},
            provenance=Provenance(src, name, {"axis": [(a.node, a.qualifier) for a in geo.axis]}))

    # reciprocidade AV/RV
    for b in beams.values():
        for s in b.supports:
            if s.kind in (SupportKind.ON_BEAM, SupportKind.RECEIVES):
                other = beams.get(s.ref_id or "")
                want = SupportKind.RECEIVES if s.kind == SupportKind.ON_BEAM else SupportKind.ON_BEAM
                ok = other is not None and any(
                    o.node_id == s.node_id and o.kind == want and o.ref_id == b.id for o in other.supports)
                if not ok:
                    diag.warning("BUILD-W-SUPPORT-NOT-RECIPROCAL",
                                 f"{b.id} {s.kind.value} {s.ref_id} no no {s.node_id} sem reciproco",
                                 Source.BUILDER, refs=(b.id, s.ref_id or "?"))

    # ------------------------------------------------------------ lajes
    slabs: dict[str, Slab] = {}
    for name, geo in ldf.slabs.items():
        dims = ldf.slab_dims.get(name)
        vids = [v.node for v in geo.vertices]
        missing = [n for n in vids if n not in ldf.nodes]
        if missing:
            diag.error("BUILD-E-SLAB-NODE", f"Laje {name} referencia nos inexistentes {missing}", Source.BUILDER, refs=(name,))
            continue
        edges = []
        for k, v in enumerate(geo.vertices):
            nxt = geo.vertices[(k + 1) % len(geo.vertices)]
            if v.qualifier == "LIV":
                edges.append(SlabEdge(_node_id(v.node), _node_id(nxt.node), EdgeSupport.FREE))
            elif v.qualifier and v.qualifier.startswith("P"):
                edges.append(SlabEdge(_node_id(v.node), _node_id(nxt.node), EdgeSupport.COLUMN, v.qualifier))
            else:
                beam_id = _infer_edge_beam(v.node, nxt.node, node_beams, ldf)
                if beam_id is None:
                    diag.warning("BUILD-W-SLAB-EDGE",
                                 f"Laje {name}: bordo {v.node}->{nxt.node} sem qualificador e sem viga comum",
                                 Source.BUILDER, refs=(name,))
                    edges.append(SlabEdge(_node_id(v.node), _node_id(nxt.node), EdgeSupport.UNKNOWN))
                else:
                    edges.append(SlabEdge(_node_id(v.node), _node_id(nxt.node), EdgeSupport.BEAM, beam_id))
        title = geo.title
        is_stair = bool(title and "ESCADA" in title.upper())
        slabs[name] = Slab(
            id=name, name=name, story_id=story.id, edges=tuple(edges),
            thickness=(dims.thickness_cm * CM_TO_M) if dims else 0.0, title=title,
            top_offset=((dims.dfs_cm or 0.0) * CM_TO_M) if dims else 0.0,
            is_cantilever=bool(dims and dims.cantilever), in_grid_model="GRE" in geo.flags,
            angle_deg=geo.angle_deg or 0.0, is_stair=is_stair,
            tqs_attrs={"area_cm2": geo.area_cm2, "LARM": dims.larm if dims else (), "flags": geo.flags},
            provenance=Provenance(src, name, {"vertices": [(v.node, v.qualifier) for v in geo.vertices]}))
        if dims is None:
            diag.error("BUILD-E-SLAB-NO-DIM", f"Laje {name} sem espessura", Source.BUILDER, refs=(name,))

    # --------------------------------------------------------- cargas
    load_cases = []
    load_nodes: set[int] = set()
    for lc in ldf.load_cases:
        items = []
        for it in lc.items:
            load_nodes.update(it.nodes)
            unit = {"DIS": "tf/m", "DIP": "tf/m", "ADI": "tf/m2", "ARE": "tf/m2"}.get(it.kind, "?")
            items.append(LoadItem(it.element, it.kind, it.value, unit,
                                  tuple(_node_id(n) for n in it.nodes),
                                  _poly(it.region) if it.region else None))
        load_cases.append(LoadCase(f"LC{lc.number}", lc.number, lc.description, tuple(items)))

    # ------------------------------------------------------------- nos
    roles: dict[int, set[NodeRole]] = defaultdict(set)
    for b in ldf.beams.values():
        for a in b.axis:
            roles[a.node].add(NodeRole.BEAM_AXIS)
            if a.qualifier and a.qualifier.startswith("P"):
                roles[a.node].add(NodeRole.BEAM_SUPPORT)
            elif a.qualifier and (a.qualifier.startswith("AV") or a.qualifier.startswith("RV")):
                roles[a.node].add(NodeRole.BEAM_INTERSECTION)
    for c in ldf.columns.values():
        roles[c.node].add(NodeRole.COLUMN_REF)
    for s in ldf.slabs.values():
        for v in s.vertices:
            roles[v.node].add(NodeRole.SLAB_VERTEX)
    nodes: dict[str, Node] = {}
    for nid, (x, y) in ldf.nodes.items():
        r = roles.get(nid, set())
        if not r:
            r = {NodeRole.LOAD_ONLY} if nid in load_nodes else {NodeRole.ORPHAN}
        nodes[_node_id(nid)] = Node(_node_id(nid), x * CM_TO_M, y * CM_TO_M, z, story.id,
                                    frozenset(r), Provenance(src, str(nid), {"x_cm": x, "y_cm": y}))
    n_load = sum(1 for n in nodes.values() if NodeRole.LOAD_ONLY in n.roles)
    n_orphan = sum(1 for n in nodes.values() if NodeRole.ORPHAN in n.roles)
    if n_load:
        diag.info("BUILD-I-LOAD-NODES", f"{n_load} nos usados apenas por cargas (nao geram joints)", Source.BUILDER)
    if n_orphan:
        diag.info("BUILD-I-ORPHAN-NODES", f"{n_orphan} nos nao referenciados por nenhum elemento", Source.BUILDER,
                  refs=tuple(n.id for n in nodes.values() if NodeRole.ORPHAN in n.roles))

    # ------------------------------------------------- materiais/catalogo
    materials = {}
    for m in ldf.materials:
        v = m.values
        materials[m.name] = Material(m.name, m.name,
                                     unit_weight=v[0] if len(v) > 0 else None,
                                     e_modulus=v[1] if len(v) > 1 else None,
                                     g_modulus=v[2] if len(v) > 2 else None,
                                     poisson=v[3] if len(v) > 3 else None,
                                     thermal_exp=v[4] if len(v) > 4 else None,
                                     extra={"raw": v})
    catalog = {s.name: CatalogSection(s.name, s.name, s.ix, s.iy, s.iz, s.ax, s.extra)
               for s in ldf.catalog_sections}

    h = ldf.header
    project = ProjectInfo(
        building=h.building or (lst.building if lst else None),
        plan_name=h.plan_name or (lst.plan_name if lst else None),
        plan_project=h.plan_project, building_project=h.building_project,
        title=h.title or (lst.title if lst else None), client=h.client or (lst.client if lst else None),
        source_folder=h.folder, generated_at=h.generated_at,
        tqs_version=lst.tqs_version if lst else None,
        source_files=tuple(p for p in (ldf.source_path, lst.source_path if lst else None) if p))

    return StructuralModel(
        project=project, stories={story.id: story}, nodes=nodes, columns=columns, beams=beams,
        slabs=slabs, materials=materials, catalog_sections=catalog, load_cases=tuple(load_cases),
        diagnostics=diag.as_tuple(),
        meta={"units": "m", "scale": ldf.scale, "ctor": ldf.ctor, "config": config.source,
              "lst_warnings": len(lst.warnings) if lst else 0})


def _build_column_section(name: str, dims: LdfColumnDimensions, node_cm: Pair,
                          diag: DiagnosticCollector):
    if dims.kind == "R":
        if None in (dims.length_cm, dims.width_cm, dims.angle_deg) or dims.base is None:
            diag.error("BUILD-E-COLUMN-R", f"Pilar {name} R incompleto (L/B, ANG ou BASE ausentes)",
                       Source.BUILDER, refs=(name,))
            return None
        ox, oy = rect_origin_from_reference(node_cm, dims.base, dims.angle_deg)
        return RectSection(dims.length_cm * CM_TO_M, dims.width_cm * CM_TO_M, dims.angle_deg,
                           Point(ox * CM_TO_M, oy * CM_TO_M))
    if dims.kind == "G":
        if not dims.polygon or len(dims.polygon) < 3:
            diag.error("BUILD-E-COLUMN-G", f"Pilar {name} G sem poligono valido", Source.BUILDER, refs=(name,))
            return None
        if dims.base is not None:
            dx = abs(dims.base[0] - node_cm[0])
            dy = abs(dims.base[1] - node_cm[1])
            if max(dx, dy) > 1e-3:
                diag.warning("BUILD-W-COLUMN-G-BASE",
                             f"Pilar {name}: BASE {dims.base} difere do no de referencia {node_cm}",
                             Source.BUILDER, refs=(name,))
        return PolygonSection(_poly(dims.polygon))
    diag.error("BUILD-E-COLUMN-KIND", f"Pilar {name} com tipo de secao desconhecido '{dims.kind}'",
               Source.BUILDER, refs=(name,))
    return None


def _has_gap(dims: LdfColumnDimensions) -> bool:
    """Heuristica: FURADO deveria deixar um intervalo entre laminas colineares."""
    if not dims.laminas or len(dims.laminas) < 2:
        return True   # sem laminas nao ha como verificar; nao alertar
    ys = sorted((min(p[1] for p in l), max(p[1] for p in l)) for l in dims.laminas)
    xs = sorted((min(p[0] for p in l), max(p[0] for p in l)) for l in dims.laminas)
    for ranges in (ys, xs):
        for (a0, a1), (b0, b1) in zip(ranges, ranges[1:]):
            if b0 - a1 > 1.0:
                return True
    return False


def _infer_edge_beam(n1: int, n2: int, node_beams: dict[int, set[str]], ldf: LdfDocument) -> str | None:
    """Viga cujo eixo contem n1 e n2 como nos consecutivos (em qualquer ordem)."""
    common = node_beams.get(n1, set()) & node_beams.get(n2, set())
    for name in sorted(common):
        axis = [a.node for a in ldf.beams[name].axis]
        for a, b in zip(axis, axis[1:]):
            if {a, b} == {n1, n2}:
                return name
    return None
