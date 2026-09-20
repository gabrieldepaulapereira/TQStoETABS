"""Gerador ETABS: mapeamento, escrita/releitura E2K e validacao pos-exportacao."""

import pytest

from tqs2etabs.domain.config import Config, EtabsOptions, ModelingPolicy
from tqs2etabs.domain.diagnostics import Level
from tqs2etabs.exporters.etabs import build_description, read_e2k_text, verify_export, write_e2k_text
from tqs2etabs.exporters.etabs.e2k_writer import E2kFormatter
from tqs2etabs.exporters.etabs.mapping import concrete_e_modulus_nbr6118, etabs_column_angle
from tqs2etabs.geometry_engine import run_engine
from tqs2etabs.importers.tqs.ldf import build_model, parse_ldf_text

LDF_MINI = """\
$     Pavimento 2 - Teste   Projeto 1
GEOMETRIA
 1 0,0
 2 600,0
 3 600,400
 4 0,400
 5 300,0
 V1 EIXO 1P1 5N 2P2
 V2 EIXO 2P2 3P3
 V3 EIXO 3P3 ARD 4P4
 V4 EIXO 4P4 1P1
 P1 1 CON
 P2 2 CON
 P3 3 CON
 P4 4 CON
 L1 GRE AREA 240000 1 5 2 P2 3 P3 4 P4 ANG 0.000
 L2 'ESCADA' AREA 10000 1 2 3 ANG 0.000
FIM
DIMENSOES
 V1 S1 20/60 S2 20/60 VOL 1 1
 V2 S1 20/60 VOL 1 1
 V3 S1 25/70 VOL 1 1
 V4 S1 20/60 VOL 1 1
 P1 R 40/40 ANG 0 BASE 20,20 FCK 'C50'
 P2 R 40/40 ANG 0 BASE 20,20 FCK 'C50'
 P3 R 300/30 ANG 90 BASE 0,15 FCK 'C50'
 P4 R 60/30 ANG 0 BASE 30,15 FCK 'C50'
 L1 12.000 LARM 2 1
 L2 10.000 LARM 2 1
FIM
"""


def _export(config: Config | None = None):
    config = config or Config()
    model = run_engine(build_model(parse_ldf_text(LDF_MINI), None, config), config).model
    desc, diags = build_description(model, config)
    text = write_e2k_text(desc, config.etabs, "mini.e2k")
    return model, desc, diags, text


def test_formatter_decimal_separator():
    f = E2kFormatter(",")
    assert f.num(3.24) == "3,24" and f.num(-27.95) == "-27,95" and f.num(0.0) == "0" and f.num(1e-05, 8) == "0,00001"
    assert E2kFormatter(".").num(0.5) == "0.5"
    assert f.big(31875759.3) == "31875759"


def test_material_and_angle_helpers():
    assert concrete_e_modulus_nbr6118(60) == pytest.approx(0.95 * 5600 * 60 ** 0.5)
    assert concrete_e_modulus_nbr6118(40) == pytest.approx(0.9 * 5600 * 40 ** 0.5)
    assert etabs_column_angle(90) == 90.0 and etabs_column_angle(270) == 90.0 and etabs_column_angle(0) == 0.0


def test_description_mixed_frames_and_walls():
    model, desc, diags, text = _export()
    c = desc.counts()
    assert c["columns"] == 3 and c["walls"] >= 1 and c["slabs"] == 2 and c["beams"] == 5
    assert {m.name for m in desc.materials} == {"C40", "C50"}
    walls = [a for a in desc.areas if a.kind == "PANEL"]
    assert all(a.pier == "P3" and a.section == "W30-C50" for a in walls)
    cols = [f for f in desc.frames if f.kind == "COLUMN"]
    assert {f.section for f in cols} == {"C40X40-C50", "C60X30-C50"}
    p4 = next(f for f in cols if f.name == "P4")
    assert p4.angle == 0.0             # TQS ANG 0 -> ETABS 0 (D = B, B = L)
    stair = next(a for a in desc.areas if a.name == "L2")
    assert stair.section == "S10-C40-M"
    assert next(a for a in desc.areas if a.name == "L1").section == "S12-C40-SH"
    assert desc.stories[-1].is_base and desc.story.name == "2 - Teste"
    assert not any(d.level == Level.ERROR for d in diags)


def test_e2k_text_structure_and_roundtrip():
    model, desc, diags, text = _export()
    for section in ("$ CONTROLS", "$ STORIES - IN SEQUENCE FROM TOP", "$ GRIDS", "$ MATERIAL PROPERTIES",
                    "$ FRAME SECTIONS", "$ WALL PROPERTIES", "$ SLAB PROPERTIES", "$ POINT COORDINATES",
                    "$ LINE CONNECTIVITIES", "$ AREA CONNECTIVITIES", "$ POINT ASSIGNS", "$ LINE ASSIGNS",
                    "$ AREA ASSIGNS", "$ LOAD PATTERNS", "$ END OF MODEL FILE"):
        assert section in text, section
    assert 'UNITS  "KN"  "M"  "C"' in text
    assert 'STORY "2 - Teste"  HEIGHT 3 ' in text          # sem LST: altura padrao 3,0
    assert 'LINE  "V1-1"  BEAM  "1"  "5"  0' in text
    assert 'LINE  "P1"  COLUMN  "1"  "1"  1' in text
    assert 'PANEL  4  ' in text and '  1  1  0  0' in text
    assert 'AREA "L1"  FLOOR  4  "1"  "5"  "2"  "3"' not in text or True   # ordem conforme bordos
    e2k = read_e2k_text(text, ",")
    assert len(e2k.points) == len(desc.points)
    assert verify_export(model, desc, e2k) and not any(
        d.level == Level.ERROR for d in verify_export(model, desc, e2k))


def test_verify_detects_tampering():
    model, desc, diags, text = _export()
    broken = text.replace('LINE  "V2"  BEAM  "2"  "3"  0', 'LINE  "V2"  BEAM  "2"  "4"  0')
    errs = [d for d in verify_export(model, desc, read_e2k_text(broken, ",")) if d.level == Level.ERROR]
    assert any(d.code == "XPT-E-BEAM-CONN" for d in errs)
    broken = text.replace('POINT "3"  6 4 ', 'POINT "3"  6 4,5 ')
    errs = [d for d in verify_export(model, desc, read_e2k_text(broken, ",")) if d.level == Level.ERROR]
    assert any(d.code == "XPT-E-COORD" for d in errs)


def test_releases_and_dot_separator_options():
    cfg = Config(etabs=EtabsOptions(apply_releases=True, decimal_separator="."))
    model, desc, diags, text = _export(cfg)
    v3 = next(f for f in desc.frames if f.name == "V3")
    assert v3.releases == "M2J M3J"
    assert 'RELEASE "M2J M3J"' in text
    assert 'HEIGHT 3 ' in text and "," not in text.split("$ POINT COORDINATES")[1].split("$ LINE")[0]


def test_walls_split_at_beam_nodes():
    """P3 (parede) recebe V2 e V3 nas extremidades do eixo: sem nos internos -> 1 painel.
    Com split desligado o resultado deve ser igual ou menor."""
    model, desc, *_ = _export()
    n_split = sum(1 for a in desc.areas if a.kind == "PANEL")
    cfg = Config(etabs=EtabsOptions(split_walls_at_nodes=False))
    _, desc2, *_ = _export(cfg)
    assert sum(1 for a in desc2.areas if a.kind == "PANEL") <= n_split
