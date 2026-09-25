"""CLI: `tqs2etabs analyze <arquivo.LDF> [--lst arquivo.LST] [-v] [--json saida.json]`."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .application.analyze import analyze, format_summary, model_to_json
from .application.building import convert_building, format_building_report
from .application.export import export_e2k, format_export_report
from .application.normalize import format_audit_report, normalize
from .domain.config import load_config
from .domain.diagnostics import Level


def _guess_lst(ldf: Path) -> Path | None:
    for cand in (ldf.with_suffix(".LST"), ldf.with_suffix(".lst")):
        if cand.exists():
            return cand
    return None


def _with_template(config, template: Path | None):
    """--template <arquivo.e2k> entra na configuracao do exportador."""
    if not template:
        return config
    if not template.exists():
        print(f"Template nao encontrado: {template}", file=sys.stderr)
        raise SystemExit(2)
    return replace(config, etabs=replace(config.etabs, template_path=str(template)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tqs2etabs", description="Conversor TQS (LDF/LST) -> ETABS")
    sub = ap.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("analyze", help="Le LDF/LST, monta o modelo intermediario e imprime o resumo")
    an.add_argument("ldf", type=Path)
    an.add_argument("--lst", type=Path, default=None, help="LST correspondente (padrao: mesmo nome .LST)")
    an.add_argument("--config", type=Path, default=None, help="TOML de configuracao (padrao: config/default.toml)")
    an.add_argument("-v", "--verbose", action="store_true")
    an.add_argument("--json", type=Path, default=None, help="Grava o modelo intermediario em JSON")

    no = sub.add_parser("normalize", help="analyze + motor geometrico; imprime o relatorio de auditoria")
    no.add_argument("ldf", type=Path)
    no.add_argument("--lst", type=Path, default=None)
    no.add_argument("--config", type=Path, default=None)
    no.add_argument("-v", "--verbose", action="store_true", help="inclui clusters e todos os registros de alteracao")
    no.add_argument("--report", type=Path, default=None, help="grava o relatorio em arquivo texto")
    no.add_argument("--json", type=Path, default=None, help="grava o modelo normalizado em JSON")

    ex = sub.add_parser("export", help="normalize + gera o arquivo .e2k do ETABS e valida o que foi escrito")
    ex.add_argument("ldf", type=Path)
    ex.add_argument("--lst", type=Path, default=None)
    ex.add_argument("--config", type=Path, default=None)
    ex.add_argument("-o", "--out", type=Path, required=True, help="arquivo .e2k de saida")
    ex.add_argument("--report", type=Path, default=None, help="grava o relatorio completo em arquivo texto")
    ex.add_argument("--template", type=Path, default=None, help=".e2k de referencia: materiais, secoes, casos e "
                    "combinacoes do escritorio")

    bl = sub.add_parser("building", help="pasta do edificio TQS -> modelo ETABS completo (.e2k)")
    bl.add_argument("folder", type=Path)
    bl.add_argument("--config", type=Path, default=None)
    bl.add_argument("-o", "--out", type=Path, required=True, help="arquivo .e2k de saida")
    bl.add_argument("--report", type=Path, default=None)
    bl.add_argument("-v", "--verbose", action="store_true", help="inclui a auditoria de cada planta")
    bl.add_argument("--template", type=Path, default=None, help=".e2k de referencia: materiais, secoes, casos e "
                    "combinacoes do escritorio")

    args = ap.parse_args(argv)
    if args.cmd == "analyze":
        if not args.ldf.exists():
            print(f"Arquivo nao encontrado: {args.ldf}", file=sys.stderr)
            return 2
        lst = args.lst if args.lst else _guess_lst(args.ldf)
        result = analyze(args.ldf, lst, load_config(args.config))
        print(format_summary(result, verbose=args.verbose))
        if args.json:
            args.json.write_text(model_to_json(result.model), encoding="utf-8")
            print(f"\nModelo gravado em {args.json}")
        return 1 if any(d.level == Level.ERROR for d in result.model.diagnostics) else 0
    if args.cmd == "normalize":
        if not args.ldf.exists():
            print(f"Arquivo nao encontrado: {args.ldf}", file=sys.stderr)
            return 2
        lst = args.lst if args.lst else _guess_lst(args.ldf)
        result = normalize(args.ldf, lst, load_config(args.config))
        text = format_audit_report(result, verbose=args.verbose)
        print(text)
        if args.report:
            args.report.write_text(text, encoding="utf-8")
            print(f"\nRelatorio gravado em {args.report}")
        if args.json:
            args.json.write_text(model_to_json(result.model), encoding="utf-8")
            print(f"Modelo normalizado gravado em {args.json}")
        return 1 if result.engine.has_errors else 0
    if args.cmd == "export":
        if not args.ldf.exists():
            print(f"Arquivo nao encontrado: {args.ldf}", file=sys.stderr)
            return 2
        lst = args.lst if args.lst else _guess_lst(args.ldf)
        result = export_e2k(args.ldf, lst, args.out, _with_template(load_config(args.config), args.template))
        text = format_export_report(result)
        print(text)
        if args.report:
            args.report.write_text(text, encoding="utf-8")
        return 1 if result.has_errors else 0
    if args.cmd == "building":
        if not args.folder.is_dir():
            print(f"Pasta nao encontrada: {args.folder}", file=sys.stderr)
            return 2
        result = convert_building(args.folder, args.out, _with_template(load_config(args.config), args.template))
        text = format_building_report(result, verbose=args.verbose)
        print(text)
        if args.report:
            args.report.write_text(text, encoding="utf-8")
        return 1 if result.has_errors else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
