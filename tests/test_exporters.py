"""Exporter tests: workbook structure / live formulas and PDF generation."""
from __future__ import annotations

import io

import openpyxl
import pytest

from exporters.excel import SHEETS, build_workbook
from exporters.pdf import build_ic_report
from tests.conftest import with_
from transaction.model import run_lbo


@pytest.fixture(scope="module")
def workbook():
    from financials.company import demo_assumptions
    data = build_workbook(run_lbo(demo_assumptions()))
    return openpyxl.load_workbook(io.BytesIO(data))


def _find(ws, label):
    for row in ws.iter_rows(min_col=1, max_col=1):
        if row[0].value == label:
            return row[0].row
    raise AssertionError(f"label not found: {label}")


def test_workbook_has_all_required_sheets_in_order(workbook):
    assert workbook.sheetnames == SHEETS


def test_returns_are_formulas_not_hardcoded(workbook):
    ws = workbook["Sponsor Returns"]
    irr = ws.cell(_find(ws, "Gross IRR"), 3).value
    moic = ws.cell(_find(ws, "MOIC"), 3).value
    assert isinstance(irr, str) and irr.startswith("=") and "IRR(" in irr
    assert isinstance(moic, str) and moic.startswith("=")
    su = workbook["Sources and Uses"]
    sponsor = su.cell(_find(su, "Sponsor equity (residual)"), 3).value
    assert sponsor.startswith("=") and sponsor.count("-") >= 5   # total uses less every other source


def test_model_sheets_are_formula_driven(workbook):
    for name in ("Sources and Uses", "Operating Model", "Debt Schedule", "Cash Flow", "Exit Valuation", "Sponsor Returns"):
        ws = workbook[name]
        numeric = [c for row in ws.iter_rows(min_col=3) for c in row if isinstance(c.value, (int, float)) and c.value != 0]
        formulas = [c for row in ws.iter_rows(min_col=3) for c in row if isinstance(c.value, str) and c.value.startswith("=")]
        assert len(formulas) >= 10, name
        assert not numeric, f"{name} contains hardcoded numbers: {[c.coordinate for c in numeric][:5]}"


def test_inputs_are_blue_and_calculations_are_not(workbook):
    ws = workbook["Assumptions"]
    cell = ws.cell(_find(ws, "Entry EV / EBITDA multiple"), 3)
    assert cell.value == 10.0 and cell.font.color.rgb.endswith("0000FF")
    op = workbook["Operating Model"]
    assert not op.cell(_find(op, "EBITDA"), 4).font.color.rgb.endswith("0000FF")


def test_sensitivity_sheets_are_labelled_static_with_live_tie_out(workbook):
    ws = workbook["IRR Sensitivity"]
    assert "STATIC ENGINE OUTPUT" in ws["A2"].value
    assert ws["C4"].value.startswith("=")           # live link to the model IRR
    assert ws["C5"].value == pytest.approx(0.180795, abs=1e-6)


def test_workbook_exports_for_advanced_and_average_interest_cases(demo):
    a = with_(demo, advanced=dict(recap_year=2, recap_turns=1.0), transaction=dict(rollover_pct=0.1),
              debt=dict(interest_method="average"), operating=dict(mode="detailed"))
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(run_lbo(a))))
    ws = wb["Assumptions"]
    assert ws.cell(_find(ws, "Dividend recapitalisation year (before exit)"), 3).value == 2


def test_pdf_report_builds(demo):
    for a in (demo, with_(demo, exit=dict(exit_multiple=4.0)), with_(demo, advanced=dict(model_net_returns=True), exit=dict(exit_year=7))):
        pdf = build_ic_report(run_lbo(a))
        assert pdf[:5] == b"%PDF-" and len(pdf) > 20_000
