"""Validacao cruzada LDF x LST: confere se o parser reproduz os quantitativos do TQS."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.config import Tolerances
from ..domain.diagnostics import Diagnostic, DiagnosticCollector, Source
from ..domain.model import StructuralModel
from ..importers.tqs.lst.document import LstDocument


@dataclass(frozen=True, slots=True)
class CrossCheck:
    element_id: str
    quantity: str
    ldf_value: float
    lst_value: float
    unit: str

    @property
    def relative_error(self) -> float:
        base = max(abs(self.lst_value), 1e-9)
        return abs(self.ldf_value - self.lst_value) / base

    def ok(self, tol: float, abs_tol: float = 0.006) -> bool:
        """Confere se dentro da tolerancia relativa OU do arredondamento do LST (2 casas)."""
        return self.relative_error <= tol or abs(self.ldf_value - self.lst_value) <= abs_tol


def cross_validate(model: StructuralModel, lst: LstDocument | None,
                   tol: Tolerances) -> tuple[tuple[CrossCheck, ...], tuple[Diagnostic, ...]]:
    diag = DiagnosticCollector()
    checks: list[CrossCheck] = []
    if lst is None:
        return (), ()

    # pilares: area em planta (poligono R/G) x "Area estruturada" do LST
    for cid, col in model.columns.items():
        q = lst.column_quantities.get(cid)
        if q:
            checks.append(CrossCheck(cid, "area", round(col.area, 4), q.structured_area_m2, "m2"))

    # vigas: volume do LDF (VOL) x volume de concreto do LST
    for bid, beam in model.beams.items():
        q = lst.beam_quantities.get(bid)
        vol = beam.tqs_attrs.get("volume_cm3")
        if q and vol:
            checks.append(CrossCheck(bid, "volume", round(vol * 1e-6, 4), q.concrete_volume_m3, "m3"))

    # lajes: AREA liquida do LDF x area do LST (casamento por ordem; titulos viram ESCADA/REBAIX)
    lst_slabs = list(lst.slab_quantities)
    ldf_slabs = list(model.slabs.values())
    if len(lst_slabs) == len(ldf_slabs):
        for slab, q in zip(ldf_slabs, lst_slabs):
            label_ok = (q.label == slab.id) or (slab.title and q.label[:6].upper() == slab.title[:6].upper())
            if not label_ok:
                diag.warning("XVAL-W-SLAB-ORDER", f"Laje {slab.id} casada com linha '{q.label}' do LST por ordem",
                             Source.VALIDATION, refs=(slab.id,))
            area = slab.tqs_attrs.get("area_cm2")
            if area:
                checks.append(CrossCheck(slab.id, "area", round(area * 1e-4, 4), q.structured_area_m2, "m2"))
    elif lst_slabs:
        diag.warning("XVAL-W-SLAB-COUNT",
                     f"LDF tem {len(ldf_slabs)} lajes e LST {len(lst_slabs)}; areas nao conferidas",
                     Source.VALIDATION)

    for c in checks:
        if not c.ok(tol.area_check_relative):
            diag.warning("XVAL-W-MISMATCH",
                         f"{c.element_id} {c.quantity}: LDF {c.ldf_value} x LST {c.lst_value} {c.unit} "
                         f"(erro {c.relative_error:.1%})", Source.VALIDATION, refs=(c.element_id,))
    n_ok = sum(1 for c in checks if c.ok(tol.area_check_relative))
    diag.info("XVAL-I-SUMMARY", f"Validacao cruzada LDF x LST: {n_ok}/{len(checks)} grandezas conferem "
              f"(tol {tol.area_check_relative:.0%})", Source.VALIDATION)
    return tuple(checks), diag.as_tuple()
