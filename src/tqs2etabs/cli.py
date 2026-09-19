"""CLI: `tqs2etabs analyze <arquivo.LDF> [--lst arquivo.LST] [-v] [--json saida.json]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .application.analyze import analyze, format_summary, model_to_json
from .domain.config import load_config
from .domain.diagnostics import Level


def _guess_lst(ldf: Path) -> Path | None:
    for cand in (ldf.with_suffix(".LST"), ldf.with_suffix(".lst")):
        if cand.exists():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tqs2etabs", description="Conversor TQS (LDF/LST) -> ETABS")
    sub = ap.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("analyze", help="Le LDF/LST, monta o modelo intermediario e imprime o resumo")
    an.add_argument("ldf", type=Path)
    an.add_argument("--lst", type=Path, default=None, help="LST correspondente (padrao: mesmo nome .LST)")
    an.add_argument("--config", type=Path, default=None, help="TOML de configuracao (padrao: config/default.toml)")
    an.add_argument("-v", "--verbose", action="store_true")
    an.add_argument("--json", type=Path, default=None, help="Grava o modelo intermediario em JSON")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
