"""Varredura da pasta de um edificio TQS -> BuildingDefinition.

Fontes (ver docs/BUILDING_FILES.md):
- <pasta>/<planta>/<planta>.LDF          geometria de cada planta (obrigatorio)
- <pasta>/<planta>/<planta>.LST          tabela "Definicao de Pisos": pisos que usam a planta,
                                         cota e pe-direito (replicacao do tipo)
- <pasta>/ESPACIAL/RESEST2.TXT           fck por piso e tipo de elemento (pilares/vigas/lajes)
- <pasta>/CONCRETO.DAT                   catalogo de classes de concreto com E do projeto
Planta sem LST e so com pilares NAS = planta de fundacao (base do modelo).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Callable

from ...domain.building import BuildingDefinition, ConcreteClass, PisoDefinition, PlanDefinition
from ...domain.config import Config
from ...domain.diagnostics import DiagnosticCollector, Source
from .common import read_tqs_text
from .ldf import parse_ldf
from .lst import parse_lst

_SKIP_DIRS = {"ESPACIAL", "PILAR", "GERAIS", "FUNDAC", "INFRA", "PLANTAS", "PREMOLD", "TUNEL DE VENTO",
              "REFERENCIAS EXTERNAS", "VIGAS", "ESCADAS", "ESPECIAIS"}


def plan_tag(folder_name: str) -> str:
    norm = unicodedata.normalize("NFKD", folder_name).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"^\d+\s*-\s*", "", norm)          # "0 - Fundacao" -> "Fundacao"
    tag = re.sub(r"[^A-Za-z0-9]", "", norm).upper()
    return tag[:10] or "PLAN"


def _find_plan_ldf(folder: Path, diag: DiagnosticCollector) -> tuple[Path | None, Path | None]:
    ldfs = [p for p in folder.iterdir() if p.suffix.upper() == ".LDF"]
    if not ldfs:
        return None, None
    chosen = None
    for p in ldfs:                                   # preferir o LDF com o nome da pasta
        if p.stem.lower() == folder.name.lower():
            chosen = p
    if chosen is None:
        with_lst = [p for p in ldfs if p.with_suffix(".LST").exists()]
        chosen = with_lst[0] if with_lst else max(ldfs, key=lambda p: p.stat().st_mtime)
    for p in ldfs:
        if p != chosen:
            diag.info("BLD-I-STALE-LDF", f"{folder.name}: LDF adicional ignorado ({p.name})", Source.PARSER)
    lst = chosen.with_suffix(".LST")
    if not lst.exists():
        cands = [p for p in folder.iterdir() if p.suffix.upper() == ".LST" and p.stem.upper() != "MENAVI"]
        lst = cands[0] if cands else None
    return chosen, lst


def read_concrete_catalog(path: Path) -> dict[str, ConcreteClass]:
    """CONCRETO.DAT: blocos 'Cxx' seguidos de 8 linhas numericas comentadas."""
    lines = [l.split("//")[0].strip() for l in read_tqs_text(path).splitlines()]
    out: dict[str, ConcreteClass] = {}
    i = 0
    while i < len(lines):
        if re.fullmatch(r"C\d+", lines[i]):
            vals = []
            j = i + 1
            while j < len(lines) and len(vals) < 8:
                if lines[j]:
                    try:
                        vals.append(float(lines[j]))
                    except ValueError:
                        break
                j += 1
            if len(vals) >= 6:
                out[lines[i]] = ConcreteClass(lines[i], vals[0], vals[4] or None, vals[5] or None,
                                              foundation_only=len(vals) > 7 and vals[7] == 1)
            i = j
        else:
            i += 1
    return out


def read_resest_materials(path: Path) -> dict[int, dict[str, str]]:
    """RESEST2.TXT: 'Piso k: titulo' seguido de linhas Pilares/Vigas/Lajes com fck na ultima coluna."""
    out: dict[int, dict[str, str]] = {}
    current: int | None = None
    for line in read_tqs_text(path).splitlines():
        m = re.match(r"^Piso\s+(\d+)\s*:", line)
        if m:
            current = int(m.group(1))
            out.setdefault(current, {})
            continue
        cells = line.split("\t")
        if current is not None and cells and cells[0].strip().lower() in ("pilares", "vigas", "lajes"):
            nums = [c.strip() for c in cells[1:] if c.strip()]
            if nums and re.fullmatch(r"\d+(\.\d+)?", nums[-1]):
                out[current][cells[0].strip().lower()] = f"C{int(float(nums[-1]))}"
    return out


ProgressFn = Callable[[float, str], None]


def scan_building(folder: Path | str, config: Config | None = None,
                  progress: ProgressFn | None = None) -> BuildingDefinition:
    """Varre a pasta do edificio. `progress(fracao, texto)` e chamado a cada etapa (UI)."""
    config = config or Config()
    report = progress or (lambda f, t: None)
    root = Path(folder)
    diag = DiagnosticCollector()
    sources: dict[str, str] = {}
    plans: dict[str, PlanDefinition] = {}
    piso_rows: dict[int, tuple[str, float, float, str]] = {}   # index -> (titulo, cota, pd, plan_tag)
    base_candidates: list[tuple[str, int]] = []

    subdirs = [p for p in sorted(root.iterdir()) if p.is_dir()
               and p.name.upper() not in _SKIP_DIRS and not p.name.startswith(".")]
    for i, sub in enumerate(subdirs):
        ldf_path, lst_path = _find_plan_ldf(sub, diag)
        if ldf_path is None:
            continue
        report(0.05 + 0.75 * i / max(len(subdirs), 1), f"Lendo planta {sub.name}: {ldf_path.name}")
        ldf = parse_ldf(ldf_path)
        if not ldf.nodes and not ldf.columns:
            continue
        tag = plan_tag(sub.name)
        if tag in plans:
            tag = f"{tag}{len(plans)}"
        name = ldf.header.plan_name or sub.name
        lst = parse_lst(lst_path) if lst_path else None
        statuses = {c.status for c in ldf.columns.values()}
        is_base = not ldf.beams and not ldf.slabs and statuses <= {"NAS"} and bool(ldf.columns)
        plans[tag] = PlanDefinition(tag, name, str(sub), str(ldf_path), str(lst_path) if lst_path else None, is_base)
        sources[f"plan:{tag}"] = str(ldf_path)
        if lst and lst.stories:
            for row in lst.stories:
                if row.index in piso_rows and piso_rows[row.index][3] != tag:
                    diag.warning("BLD-W-PISO-DUP", f"Piso {row.index} definido em {piso_rows[row.index][3]} e {tag}",
                                 Source.PARSER)
                piso_rows[row.index] = (row.title, row.elevation_m, row.height_m, tag)
        elif is_base:
            base_candidates.append((tag, len(ldf.columns)))
        else:
            diag.error("BLD-E-NO-LST", f"Planta {name} ({sub.name}) sem LST: cota/pe-direito desconhecidos; "
                       "nao pode ser posicionada", Source.PARSER, refs=(tag,))

    if not piso_rows:
        diag.error("BLD-E-NO-PISOS", "Nenhuma tabela 'Definicao de Pisos' encontrada nos LSTs", Source.PARSER)

    # materiais por piso
    report(0.82, "Lendo ESPACIAL/RESEST2.TXT (fck por piso)")
    materials: dict[int, dict[str, str]] = {}
    resest = root / "ESPACIAL" / "RESEST2.TXT"
    if resest.exists():
        materials = read_resest_materials(resest)
        sources["materials"] = str(resest)
        diag.info("BLD-I-MATERIALS", f"fck por piso lido de {resest.name}: " +
                  "; ".join(f"piso {k}: " + ", ".join(f"{e}={c}" for e, c in v.items()) for k, v in sorted(materials.items())),
                  Source.PARSER)
    else:
        diag.warning("BLD-W-NO-RESEST", "ESPACIAL/RESEST2.TXT ausente: fck por piso desconhecido; usando "
                     f"{config.etabs.default_material}", Source.PARSER)
    report(0.9, "Lendo CONCRETO.DAT (modulo de elasticidade)")
    catalog: dict[str, ConcreteClass] = {}
    conc = root / "CONCRETO.DAT"
    if conc.exists():
        catalog = read_concrete_catalog(conc)
        sources["concrete"] = str(conc)
        with_e = {k: v.e_secant_mpa for k, v in catalog.items() if v.e_secant_mpa}
        diag.info("BLD-I-CONCRETE", f"CONCRETO.DAT: {len(catalog)} classes; E definido para {sorted(with_e)}", Source.PARSER)

    default = {"pilares": config.etabs.default_material, "vigas": config.etabs.default_material,
               "lajes": config.etabs.default_material}
    pisos = []
    for idx in sorted(piso_rows):
        title, elev, height, tag = piso_rows[idx]
        mats = dict(default)
        mats.update(materials.get(idx, {}))
        pisos.append(PisoDefinition(idx, title, elev, height, tag, mats))

    # consistencia das cotas
    for a, b in zip(pisos, pisos[1:]):
        if b.index != a.index + 1:
            diag.warning("BLD-W-PISO-GAP", f"Pisos {a.index} e {b.index} nao sao consecutivos", Source.PARSER)
        if abs((b.elevation - a.elevation) - b.height) > 0.01:
            diag.warning("BLD-W-HEIGHT", f"Piso {b.index}: cota {b.elevation} - {a.elevation} != PD {b.height}",
                         Source.PARSER)
    base_elev = (pisos[0].elevation - pisos[0].height) if pisos else 0.0
    base_tag = None
    if base_candidates:
        base_tag = max(base_candidates, key=lambda t: t[1])[0]
        diag.info("BLD-I-BASE", f"Planta de fundacao: {base_tag} (cota da base {base_elev:.2f} m)", Source.PARSER)
    name = root.name
    for p in plans.values():
        try:
            hdr = parse_ldf(p.ldf_path).header
            if hdr.building:
                name = hdr.building
                break
        except OSError:
            pass
    report(1.0, f"Edificio '{name}': {len(plans)} plantas, {len(pisos)} pisos")
    diag.info("BLD-I-SUMMARY", f"Edificio '{name}': {len(plans)} plantas, {len(pisos)} pisos "
              f"({pisos[0].elevation if pisos else 0:.2f} a {pisos[-1].elevation if pisos else 0:.2f} m)", Source.PARSER)
    return BuildingDefinition(name, str(root), plans, tuple(pisos), base_elev, base_tag, catalog,
                              diag.as_tuple(), sources)
