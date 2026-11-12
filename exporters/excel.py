"""Editable Excel LBO model with live formulas (XlsxWriter).

The workbook is a self-contained re-implementation of the baseline engine in
Excel formulas: change a blue input and the whole model recalculates.

Conventions
  * Blue font on yellow fill  = hardcoded input
  * Black font                = formula
  * Green font                = direct link to another sheet
  * Purple italic             = static output of the Python engine (cannot be
                                a formula: sensitivity grids and scenarios)

Scope notes
  * Interest is calculated on BEGINNING balances (non-circular). If the app is
    set to average-balance interest, the workbook still uses beginning balances
    and the engine reference figures on Model Checks are computed the same way.
  * In "detailed" operating mode the workbook is driven by the EBITDA margins
    implied by the detailed statement.
  * XlsxWriter cannot author Excel data tables, so the sensitivity sheets hold
    engine-computed values (each a full model rerun) with a live tie-out cell.
"""
from __future__ import annotations

import io
import math
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from financials.assumptions import HORIZON, LBOAssumptions
from scenarios.scenarios import DEFAULT_SCENARIOS, ScenarioSpec, run_scenarios, scenario_summary
from scenarios.sensitivity import HEATMAPS, PARAMETERS, sensitivity_2d
from transaction.model import LBOResult, run_lbo

C0 = 2  # column C holds scalars and the closing-date (year 0) value
NUMFMT = {
    "num": '#,##0.0_);(#,##0.0);"-"_)',
    "pct": '0.0%_);(0.0%);"-"_)',
    "pct2": "0.00%",
    "mult": '0.00"x"',
    "int": "0",
    "text": "General",
}
FONT = {"calc": "#000000", "input": "#0000FF", "link": "#006100", "static": "#7030A0"}

SHEETS = ["Transaction Overview", "Assumptions", "Historical Financials", "Sources and Uses", "Operating Model",
          "Debt Schedule", "Cash Flow", "Exit Valuation", "Sponsor Returns", "IRR Sensitivity",
          "MOIC Sensitivity", "Scenarios", "Model Checks"]


def ycol(t: int) -> str:
    return xl_col_to_name(C0 + t)


class _Book:
    def __init__(self, target: Any) -> None:
        self.wb = xlsxwriter.Workbook(target, {"in_memory": True})
        self.loc: dict[str, tuple[str, int]] = {}
        self._fmt: dict[tuple, Any] = {}
        self.sheets: dict[str, _Sheet] = {}

    def fmt(self, kind: str = "num", style: str = "calc", bold: bool = False, top: bool = False, **extra: Any):
        key = (kind, style, bold, top, tuple(sorted(extra.items())))
        if key not in self._fmt:
            p: dict[str, Any] = {"font_name": "Calibri", "font_size": 10, "num_format": NUMFMT.get(kind, kind),
                                 "font_color": FONT[style], "bold": bold}
            if style == "input":
                p["bg_color"] = "#FFF2CC"
            if style == "static":
                p["italic"] = True
            if top:
                p["top"] = 1
            if kind == "text":
                p["align"] = "right"
            p.update(extra)
            self._fmt[key] = self.wb.add_format(p)
        return self._fmt[key]

    # ---- references -------------------------------------------------------
    def S(self, key: str) -> str:
        sh, r = self.loc[key]
        return f"'{sh}'!$C${r + 1}"

    def Y(self, key: str, t: int) -> str:
        sh, r = self.loc[key]
        return f"'{sh}'!{ycol(t)}${r + 1}"

    def R(self, key: str, t1: int = 1, t2: int = HORIZON) -> str:
        sh, r = self.loc[key]
        return f"'{sh}'!${ycol(t1)}${r + 1}:${ycol(t2)}${r + 1}"

    def sheet(self, name: str) -> _Sheet:
        s = _Sheet(self, name)
        self.sheets[name] = s
        return s


class _Sheet:
    def __init__(self, book: _Book, name: str) -> None:
        self.b, self.name = book, name
        self.ws = book.wb.add_worksheet(name)
        self.ws.hide_gridlines(2)
        self.ws.set_column(0, 0, 46)
        self.ws.set_column(1, 1, 9)
        self.ws.set_column(2, 2 + HORIZON, 12.5)
        self.specs: list[dict] = []
        self.r = 0

    def _add(self, **spec: Any) -> int:
        spec["row"] = self.r
        self.specs.append(spec)
        self.r += 1
        return spec["row"]

    def title(self, text: str, sub: str = "") -> None:
        self._add(t="title", text=text)
        if sub:
            self._add(t="note", text=sub)
        self._add(t="blank")

    def note(self, text: str) -> None:
        self._add(t="note", text=text)

    def blank(self) -> None:
        self._add(t="blank")

    def section(self, text: str, years: bool = False, cols: list[str] | None = None) -> None:
        self._add(t="section", text=text, years=years, cols=cols)

    def scalar(self, key: str | None, label: str, value: Any, kind: str = "num", style: str = "calc",
               unit: str = "", bold: bool = False, top: bool = False, extra: list[tuple] | None = None) -> None:
        row = self._add(t="scalar", label=label, value=value, kind=kind, style=style, unit=unit, bold=bold,
                        top=top, extra=extra or [])
        if key:
            self.b.loc[key] = (self.name, row)

    def years(self, key: str | None, label: str, fn: Callable[[int], Any], kind: str = "num", style: str = "calc",
              t0: Any = None, unit: str = "", bold: bool = False, top: bool = False) -> None:
        row = self._add(t="years", label=label, fn=fn, kind=kind, style=style, t0=t0, unit=unit, bold=bold, top=top)
        if key:
            self.b.loc[key] = (self.name, row)

    # ---- writing ----------------------------------------------------------
    def _put(self, r: int, c: int, v: Any, f: Any) -> None:
        if callable(v):
            v = v()
        if v is None:
            self.ws.write_blank(r, c, None, f)
        elif isinstance(v, str) and v.startswith("="):
            self.ws.write_formula(r, c, v, f)
        elif isinstance(v, bool):
            self.ws.write_number(r, c, int(v), f)
        elif isinstance(v, (int, float)):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                self.ws.write_string(r, c, "n/a", f)
            else:
                self.ws.write_number(r, c, v, f)
        else:
            self.ws.write_string(r, c, str(v), f)

    def finalize(self) -> None:
        b, ws = self.b, self.ws
        lab = b.fmt("text", align="left")
        for s in self.specs:
            r = s["row"]
            if s["t"] == "title":
                ws.write_string(r, 0, s["text"], b.fmt("text", bold=True, font_size=14, align="left", font_color="#1F3864"))
            elif s["t"] == "note":
                ws.write_string(r, 0, s["text"], b.fmt("text", align="left", italic=True, font_color="#595959"))
            elif s["t"] == "section":
                hf = b.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="left")
                hr = b.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="right")
                ws.write_string(r, 0, s["text"], hf)
                heads = s["cols"] or (["Close"] + [f"Year {t}" for t in range(1, HORIZON + 1)] if s["years"] else [])
                for c in range(1, C0 + HORIZON + 1):
                    i = c - C0
                    ws.write_string(r, c, heads[i] if 0 <= i < len(heads) else "", hr)
            elif s["t"] == "scalar":
                ws.write_string(r, 0, s["label"], b.fmt("text", bold=s["bold"], top=s["top"], align="left"))
                ws.write_string(r, 1, s["unit"], b.fmt("text", top=s["top"], align="left", font_color="#7F7F7F"))
                self._put(r, C0, s["value"], b.fmt(s["kind"], s["style"], s["bold"], s["top"]))
                for (c, v, k, st) in s["extra"]:
                    self._put(r, c, v, b.fmt(k, st, s["bold"], s["top"]))
            elif s["t"] == "years":
                ws.write_string(r, 0, s["label"], b.fmt("text", bold=s["bold"], top=s["top"], align="left"))
                ws.write_string(r, 1, s["unit"], b.fmt("text", top=s["top"], align="left", font_color="#7F7F7F"))
                f = b.fmt(s["kind"], s["style"], s["bold"], s["top"])
                if s["t0"] is not None:
                    self._put(r, C0, s["t0"], f)
                elif s["top"]:
                    ws.write_blank(r, C0, None, f)
                for t in range(1, HORIZON + 1):
                    self._put(r, C0 + t, s["fn"](t), f)
        del lab


# =========================================================================== #
def build_workbook(result: LBOResult, scenario_specs: dict[str, ScenarioSpec] | None = None) -> bytes:
    """Return the .xlsx file as bytes."""
    a0 = result.assumptions
    a = replace(a0, debt=replace(a0.debt, interest_method="beginning"))
    ref = result if a0.debt.interest_method == "beginning" else run_lbo(a)
    co, tx, op, db, ex, adv = a.company, a.transaction, a.operating, a.debt, a.exit, a.advanced
    margins = [float(ref.operating.margin[t]) for t in range(1, HORIZON + 1)]

    buf = io.BytesIO()
    B = _Book(buf)
    S, Y, R = B.S, B.Y, B.R
    sh = {name: B.sheet(name) for name in SHEETS}
    demo_tag = "ILLUSTRATIVE - SYNTHETIC DEMONSTRATION DATA. " if co.is_synthetic else ""

    # ------------------------------------------------------------ Assumptions
    s = sh["Assumptions"]
    s.title("Assumptions", f"{demo_tag}Blue cells are inputs. Amounts in currency millions; rates as decimals.")
    s.section("Target company")
    s.scalar("company_name", "Company name", co.name, "text", "input")
    s.scalar("industry", "Industry", co.industry, "text", "input")
    s.scalar("ltm_revenue", "LTM revenue", co.ltm_revenue, "num", "input")
    s.scalar("ltm_ebitda", "LTM EBITDA (entry EBITDA)", co.ltm_ebitda, "num", "input")
    s.scalar("target_cash", "Existing cash", co.cash, "num", "input")
    s.scalar("existing_debt", "Existing debt (refinanced at close)", co.existing_debt, "num", "input")
    s.scalar("nwc0", "Net working capital at close", co.net_working_capital, "num", "input")
    s.blank()
    s.section("Transaction")
    s.scalar("entry_multiple", "Entry EV / EBITDA multiple", tx.entry_multiple, "mult", "input")
    s.scalar("txn_fee_pct", "Transaction fees", tx.transaction_fee_pct, "pct2", "input", "% of EV")
    s.scalar("fin_fee_pct", "Financing fees", tx.financing_fee_pct, "pct2", "input", "% debt")
    s.scalar("min_cash", "Minimum cash balance", tx.min_cash, "num", "input")
    s.scalar("use_excess_cash", "Use target cash above minimum as a source", int(tx.use_excess_cash), "int", "input", "1 / 0")
    s.scalar("rollover_pct", "Management rollover", tx.rollover_pct, "pct", "input", "% seller eq.")
    s.scalar("min_equity_pct", "Minimum equity contribution", tx.min_equity_contribution_pct, "pct", "input", "% sources")
    s.scalar("close_date", "Closing date", tx.close_date.isoformat(), "text", "input")
    s.blank()
    s.section("Debt financing")
    s.scalar("senior_lev", "Senior term loan", db.senior_leverage, "mult", "input", "x EBITDA")
    s.scalar("senior_rate", "Senior interest rate", db.senior_rate, "pct2", "input")
    s.scalar("senior_amort", "Senior mandatory amortisation", db.senior_amort_pct, "pct2", "input", "% orig.")
    s.scalar("senior_mat", "Senior maturity", db.senior_maturity, "int", "input", "years")
    s.scalar("sub_lev", "Subordinated debt", db.sub_leverage, "mult", "input", "x EBITDA")
    s.scalar("sub_rate", "Subordinated interest rate", db.sub_rate, "pct2", "input")
    s.scalar("sub_amort", "Subordinated mandatory amortisation", db.sub_amort_pct, "pct2", "input", "% orig.")
    s.scalar("sub_mat", "Subordinated maturity", db.sub_maturity, "int", "input", "years")
    s.scalar("sub_prepay", "Subordinated debt prepayable (cash sweep)", int(db.sub_prepayable), "int", "input", "1 / 0")
    s.scalar("rev_commit", "Revolver commitment", db.revolver_commitment, "num", "input")
    s.scalar("rev_draw0", "Revolver drawn at close", db.revolver_draw_at_close, "num", "input")
    s.scalar("rev_rate", "Revolver interest rate", db.revolver_rate, "pct2", "input")
    s.scalar("rev_fee", "Revolver undrawn commitment fee", db.revolver_undrawn_fee, "pct2", "input")
    s.scalar("sweep_pct", "Cash sweep", db.cash_sweep_pct, "pct", "input", "% excess")
    s.scalar("max_lev", "Maximum closing leverage (structuring limit)", db.max_total_leverage, "mult", "input")
    s.scalar("cov_lev", "Covenant: maximum total leverage (0 = not tested)", db.covenant_max_leverage or 0.0, "mult", "input")
    s.scalar("cov_icr", "Covenant: minimum interest coverage (0 = not tested)", db.covenant_min_interest_coverage or 0.0, "mult", "input")
    s.blank()
    s.section("Operating drivers")
    s.scalar("da_pct", "D&A", op.da_pct_revenue, "pct2", "input", "% revenue")
    s.scalar("capex_pct", "Capital expenditure", op.capex_pct_revenue, "pct2", "input", "% revenue")
    s.scalar("nwc_pct", "Net working capital", op.nwc_pct_revenue, "pct2", "input", "% revenue")
    s.scalar("tax_rate", "Cash tax rate", op.tax_rate, "pct", "input")
    s.blank()
    s.section("Exit")
    s.scalar("exit_year", "Exit year (3 to 7)", ex.exit_year, "int", "input")
    s.scalar("exit_multiple", "Exit EV / EBITDA multiple", ex.exit_multiple, "mult", "input")
    s.scalar("exit_fee_pct", "Exit transaction expenses", ex.exit_fee_pct, "pct2", "input", "% of EV")
    s.blank()
    s.section("Advanced features (optional; zero = off)")
    s.scalar("mip_pct", "Management incentive pool (share of equity gain)", ex.mip_pct, "pct", "input")
    s.scalar("recap_year", "Dividend recapitalisation year (before exit)", adv.recap_year or 0, "int", "input")
    s.scalar("recap_turns", "Recap: incremental senior debt", adv.recap_turns if adv.recap_year else 0.0, "mult", "input", "x EBITDA")
    s.blank()
    s.section("Annual operating drivers", years=True)
    s.years("growth_in", "Revenue growth", lambda t: op.revenue_growth[t - 1], "pct", "input")
    s.years("margin_in", "EBITDA margin" + (" (implied by detailed statement)" if op.mode == "detailed" else ""),
            lambda t: margins[t - 1], "pct", "input")

    # ------------------------------------------------------- Sources and Uses
    s = sh["Sources and Uses"]
    s.title("Sources and Uses of Funds", f"{demo_tag}Sponsor equity is the residual source. Sources must equal uses.")
    s.section("Entry valuation: enterprise value to equity purchase price")
    s.scalar(None, "Entry EBITDA (LTM)", lambda: f"={S('ltm_ebitda')}", "num", "link")
    s.scalar(None, "Entry EV / EBITDA multiple", lambda: f"={S('entry_multiple')}", "mult", "link")
    s.scalar("ev", "Purchase enterprise value", lambda: f"={S('ltm_ebitda')}*{S('entry_multiple')}", bold=True, top=True)
    s.scalar(None, "Less: existing debt", lambda: f"=-{S('existing_debt')}")
    s.scalar(None, "Plus: existing cash", lambda: f"={S('target_cash')}")
    s.scalar("eq_price", "Equity purchase price (paid to sellers)",
             lambda: f"={S('ev')}-{S('existing_debt')}+{S('target_cash')}", bold=True, top=True)
    s.blank()
    pu = lambda k: [(3, lambda: f"={S(k)}/{S('u_total')}", "pct", "calc")]  # noqa: E731
    ps = lambda k: [(3, lambda: f"={S(k)}/{S('s_total')}", "pct", "calc"),  # noqa: E731
                    (4, lambda: f"={S(k)}/{S('ltm_ebitda')}", "mult", "calc")]
    s.section("Uses", cols=["Amount", "% total"])
    s.scalar("u_equity", "Purchase of seller equity", lambda: f"={S('eq_price')}", extra=pu("u_equity"))
    s.scalar("u_refi", "Refinance existing debt", lambda: f"={S('existing_debt')}", extra=pu("u_refi"))
    s.scalar("u_txn", "Transaction fees", lambda: f"={S('txn_fee_pct')}*{S('ev')}", extra=pu("u_txn"))
    s.scalar("u_fin", "Financing fees",
             lambda: f"={S('fin_fee_pct')}*({S('s_senior')}+{S('s_sub')}+{S('s_rev')})", extra=pu("u_fin"))
    s.scalar("u_cash", "Cash funded to balance sheet (to reach minimum)",
             lambda: f"=MAX({S('min_cash')}-{S('target_cash')},0)", extra=pu("u_cash"))
    s.scalar("u_total", "Total uses", lambda: f"=SUM({S('u_equity')}:{S('u_cash')})", bold=True, top=True, extra=pu("u_total"))
    s.blank()
    s.section("Sources", cols=["Amount", "% total", "x EBITDA"])
    s.scalar("s_rev", "Revolving credit facility", lambda: f"={S('rev_draw0')}", extra=ps("s_rev"))
    s.scalar("s_senior", "Senior term loan", lambda: f"={S('senior_lev')}*{S('ltm_ebitda')}", extra=ps("s_senior"))
    s.scalar("s_sub", "Subordinated debt", lambda: f"={S('sub_lev')}*{S('ltm_ebitda')}", extra=ps("s_sub"))
    s.scalar("s_cash", "Existing target cash used",
             lambda: f"={S('use_excess_cash')}*MAX({S('target_cash')}-{S('min_cash')},0)", extra=ps("s_cash"))
    s.scalar("s_roll", "Management rollover equity", lambda: f"={S('rollover_pct')}*{S('eq_price')}", extra=ps("s_roll"))
    s.scalar("s_sponsor", "Sponsor equity (residual)",
             lambda: f"={S('u_total')}-{S('s_rev')}-{S('s_senior')}-{S('s_sub')}-{S('s_cash')}-{S('s_roll')}", extra=ps("s_sponsor"))
    s.scalar("s_total", "Total sources", lambda: f"=SUM({S('s_rev')}:{S('s_sponsor')})", bold=True, top=True, extra=ps("s_total"))
    s.blank()
    s.scalar("su_check", "Check: sources less uses (must be zero)", lambda: f"={S('s_total')}-{S('u_total')}")
    s.blank()
    s.section("Closing capital structure")
    s.scalar("closing_cash", "Closing cash balance", lambda: f"={S('target_cash')}-{S('s_cash')}+{S('u_cash')}")
    s.scalar("debt0", "Total debt at close", lambda: f"={S('s_rev')}+{S('s_senior')}+{S('s_sub')}")
    s.scalar("netdebt0", "Net debt at close", lambda: f"={S('debt0')}-{S('closing_cash')}")
    s.scalar("total_equity", "Total equity (sponsor + rollover)", lambda: f"={S('s_sponsor')}+{S('s_roll')}")
    s.scalar("sponsor_pct", "Sponsor ownership", lambda: f"={S('s_sponsor')}/{S('total_equity')}", "pct")
    s.scalar("lev0", "Total debt / EBITDA", lambda: f"={S('debt0')}/{S('ltm_ebitda')}", "mult")
    s.scalar("eq_contrib", "Equity contribution (% of total sources)", lambda: f"={S('total_equity')}/{S('s_total')}", "pct")
    s.scalar(None, "Leverage within structuring limit?", lambda: f'=IF({S("lev0")}<={S("max_lev")}+0.000001,"OK","EXCEEDS LIMIT")', "text")
    s.scalar(None, "Minimum equity contribution met?", lambda: f'=IF({S("eq_contrib")}>={S("min_equity_pct")}-0.000001,"OK","BELOW MINIMUM")', "text")

    # -------------------------------------------------------- Operating Model
    s = sh["Operating Model"]
    s.title("Operating Model", f"{demo_tag}Pre-financing forecast. Debt proceeds never enter operating income.")
    s.section("Income statement to EBIT", years=True)
    s.years("revenue", "Revenue", lambda t: f"={Y('revenue', t - 1)}*(1+{Y('growth_in', t)})", t0=lambda: f"={S('ltm_revenue')}", bold=True)
    s.years(None, "Revenue growth", lambda t: f"={Y('revenue', t)}/{Y('revenue', t - 1)}-1", "pct")
    s.years("ebitda", "EBITDA", lambda t: f"={Y('revenue', t)}*{Y('margin_in', t)}", t0=lambda: f"={S('ltm_ebitda')}", bold=True)
    s.years(None, "EBITDA margin", lambda t: f"={Y('ebitda', t)}/{Y('revenue', t)}", "pct", t0=lambda: f"={Y('ebitda', 0)}/{Y('revenue', 0)}")
    s.years(None, "EBITDA growth", lambda t: f"={Y('ebitda', t)}/{Y('ebitda', t - 1)}-1", "pct")
    s.years("da", "Less: depreciation and amortisation", lambda t: f"={Y('revenue', t)}*{S('da_pct')}")
    s.years("ebit", "EBIT", lambda t: f"={Y('ebitda', t)}-{Y('da', t)}", bold=True, top=True)
    s.blank()
    s.section("Investment", years=True)
    s.years("capex", "Capital expenditure", lambda t: f"={Y('revenue', t)}*{S('capex_pct')}")
    s.years("nwc", "Net working capital", lambda t: f"={Y('revenue', t)}*{S('nwc_pct')}", t0=lambda: f"={S('nwc0')}")
    s.years("dnwc", "Increase in net working capital", lambda t: f"={Y('nwc', t)}-{Y('nwc', t - 1)}")

    # ---------------------------------------------------------- Debt Schedule
    s = sh["Debt Schedule"]
    s.title("Debt Schedule", f"{demo_tag}Interest on beginning balances (non-circular). Priority: revolver, senior, subordinated.")
    s.section("Revolving credit facility", years=True)
    s.years("rev_open", "Opening balance", lambda t: f"={Y('rev_close', t - 1)}")
    s.years("rev_draw", "Draw to maintain minimum cash",
            lambda t: f"=MIN(MAX({S('min_cash')}-{Y('cash_pre', t)},0),MAX({S('rev_commit')}-{Y('rev_open', t)},0))")
    s.years("rev_repay", "Less: repayment from excess cash", lambda t: f"=MIN({Y('rev_open', t)},{Y('excess', t)})")
    s.years("rev_close", "Closing balance", lambda t: f"={Y('rev_open', t)}+{Y('rev_draw', t)}-{Y('rev_repay', t)}",
            t0=lambda: f"={S('s_rev')}", bold=True, top=True)
    s.years("rev_int", "Cash interest and commitment fee",
            lambda t: f"={Y('rev_open', t)}*{S('rev_rate')}+MAX({S('rev_commit')}-{Y('rev_open', t)},0)*{S('rev_fee')}")
    s.blank()
    s.section("Senior term loan", years=True)
    s.years("sen_open", "Opening balance", lambda t: f"={Y('sen_close', t - 1)}")
    s.years("sen_mand", "Less: mandatory repayment",
            lambda t: f"=IF({t}>={S('senior_mat')},{Y('sen_open', t)},MIN({Y('sen_open', t)},{S('senior_amort')}*{S('s_senior')}))")
    s.years("sen_sweep", "Less: optional repayment (cash sweep)",
            lambda t: f"=MIN({Y('sen_open', t)}-{Y('sen_mand', t)},{S('sweep_pct')}*{Y('sweepable', t)})")
    s.years("sen_recap", "Plus: dividend recap borrowing", lambda t: f"=IF({t}={S('recap_year')},{S('recap_turns')}*{Y('ebitda', t)},0)")
    s.years("sen_close", "Closing balance",
            lambda t: f"={Y('sen_open', t)}-{Y('sen_mand', t)}-{Y('sen_sweep', t)}+{Y('sen_recap', t)}",
            t0=lambda: f"={S('s_senior')}", bold=True, top=True)
    s.years("sen_int", "Cash interest", lambda t: f"={Y('sen_open', t)}*{S('senior_rate')}")
    s.blank()
    s.section("Subordinated debt", years=True)
    s.years("sub_open", "Opening balance", lambda t: f"={Y('sub_close', t - 1)}")
    s.years("sub_mand", "Less: mandatory repayment",
            lambda t: f"=IF({t}>={S('sub_mat')},{Y('sub_open', t)},MIN({Y('sub_open', t)},{S('sub_amort')}*{S('s_sub')}))")
    s.years("sub_sweep", "Less: optional repayment (cash sweep)",
            lambda t: f"={S('sub_prepay')}*MIN({Y('sub_open', t)}-{Y('sub_mand', t)},MAX({S('sweep_pct')}*{Y('sweepable', t)}-{Y('sen_sweep', t)},0))")
    s.years("sub_close", "Closing balance", lambda t: f"={Y('sub_open', t)}-{Y('sub_mand', t)}-{Y('sub_sweep', t)}",
            t0=lambda: f"={S('s_sub')}", bold=True, top=True)
    s.years("sub_int", "Cash interest", lambda t: f"={Y('sub_open', t)}*{S('sub_rate')}")
    s.blank()
    s.section("Totals and credit statistics", years=True)
    tot = lambda t: f"={Y('rev_close', t)}+{Y('sen_close', t)}+{Y('sub_close', t)}"  # noqa: E731
    s.years("tot_debt", "Total debt", tot, t0=lambda: tot(0), bold=True)
    s.years("tot_int", "Total cash interest", lambda t: f"={Y('rev_int', t)}+{Y('sen_int', t)}+{Y('sub_int', t)}")
    s.years("tot_mand", "Total mandatory repayments", lambda t: f"={Y('sen_mand', t)}+{Y('sub_mand', t)}")
    s.years("tot_opt", "Total optional repayments (incl. revolver)", lambda t: f"={Y('rev_repay', t)}+{Y('sen_sweep', t)}+{Y('sub_sweep', t)}")
    s.years("net_debt", "Net debt", lambda t: f"={Y('tot_debt', t)}-{Y('cash_close', t)}", t0=lambda: f"={Y('tot_debt', 0)}-{Y('cash_close', 0)}")
    lev = lambda t: f"={Y('tot_debt', t)}/{Y('ebitda', t)}"  # noqa: E731
    s.years("lev", "Total debt / EBITDA", lev, "mult", t0=lambda: lev(0))
    slev = lambda t: f"=({Y('rev_close', t)}+{Y('sen_close', t)})/{Y('ebitda', t)}"  # noqa: E731
    s.years("sen_lev", "Senior debt / EBITDA", slev, "mult", t0=lambda: slev(0))
    s.years("icr", "EBITDA / cash interest", lambda t: f'=IF({Y("tot_int", t)}>0,{Y("ebitda", t)}/{Y("tot_int", t)},"n/a")', "mult")
    s.years("dscr", "DSCR: CFADS / (interest + mandatory)",
            lambda t: f'=IF({Y("tot_int", t)}+{Y("tot_mand", t)}>0,{Y("cfads", t)}/({Y("tot_int", t)}+{Y("tot_mand", t)}),"n/a")', "mult")
    s.blank()
    s.section("Covenant tests", years=True)
    s.years("cov_lev_t", "Maximum leverage covenant",
            lambda t: f'=IF({S("cov_lev")}=0,"n/a",IF({Y("lev", t)}>{S("cov_lev")}+0.000001,"BREACH","OK"))', "text")
    s.years("cov_icr_t", "Minimum interest coverage covenant",
            lambda t: f'=IF({S("cov_icr")}=0,"n/a",IF(N({Y("icr", t)})<{S("cov_icr")}-0.000001,IF({Y("tot_int", t)}>0,"BREACH","OK"),"OK"))', "text")

    # -------------------------------------------------------------- Cash Flow
    s = sh["Cash Flow"]
    s.title("Cash Flow and Cash Waterfall", f"{demo_tag}CFADS = EBITDA - cash taxes - capex - increase in NWC.")
    s.section("Cash flow available for debt service", years=True)
    s.years(None, "EBITDA", lambda t: f"={Y('ebitda', t)}", style="link")
    s.years("cf_tax", "Less: cash taxes", lambda t: f"={S('tax_rate')}*MAX({Y('ebit', t)}-{Y('tot_int', t)},0)")
    s.years(None, "Less: capital expenditure", lambda t: f"={Y('capex', t)}", style="link")
    s.years(None, "Less: increase in net working capital", lambda t: f"={Y('dnwc', t)}", style="link")
    s.years("cfads", "Cash flow available for debt service (CFADS)",
            lambda t: f"={Y('ebitda', t)}-{Y('cf_tax', t)}-{Y('capex', t)}-{Y('dnwc', t)}", bold=True, top=True)
    s.years(None, "Less: cash interest", lambda t: f"={Y('tot_int', t)}", style="link")
    s.years(None, "Less: mandatory repayments", lambda t: f"={Y('tot_mand', t)}", style="link")
    s.years("fcf_post", "Free cash flow after debt service",
            lambda t: f"={Y('cfads', t)}-{Y('tot_int', t)}-{Y('tot_mand', t)}", bold=True, top=True)
    s.blank()
    s.section("Cash waterfall", years=True)
    s.years("cash_open", "Opening cash", lambda t: f"={Y('cash_close', t - 1)}")
    s.years("cash_pre", "Cash before revolver and sweep", lambda t: f"={Y('cash_open', t)}+{Y('fcf_post', t)}")
    s.years("excess", "Excess cash above minimum", lambda t: f"=MAX({Y('cash_pre', t)}-{S('min_cash')},0)")
    s.years(None, "Plus: revolver draw", lambda t: f"={Y('rev_draw', t)}", style="link")
    s.years(None, "Less: revolver repayment", lambda t: f"={Y('rev_repay', t)}", style="link")
    s.years("sweepable", "Cash available for sweep (after revolver)", lambda t: f"={Y('excess', t)}-{Y('rev_repay', t)}")
    s.years("cf_sweep", "Less: optional term debt prepayments", lambda t: f"={Y('sen_sweep', t)}+{Y('sub_sweep', t)}")
    s.years(None, "Plus: dividend recap borrowing", lambda t: f"={Y('sen_recap', t)}", style="link")
    s.years("recap_fee", "Less: recap financing fees", lambda t: f"={S('fin_fee_pct')}*{Y('sen_recap', t)}")
    s.years("dividend", "Less: dividend to shareholders", lambda t: f"={Y('sen_recap', t)}-{Y('recap_fee', t)}")
    s.years("cash_close", "Closing cash",
            lambda t: (f"={Y('cash_pre', t)}+{Y('rev_draw', t)}-{Y('rev_repay', t)}-{Y('cf_sweep', t)}"
                       f"+{Y('sen_recap', t)}-{Y('recap_fee', t)}-{Y('dividend', t)}"),
            t0=lambda: f"={S('closing_cash')}", bold=True, top=True)
    s.years("shortfall", "Liquidity shortfall vs minimum cash", lambda t: f"=MAX({S('min_cash')}-{Y('cash_close', t)},0)")
    s.years("hold", "Within holding period (1 = yes)", lambda t: f"=IF({t}<={S('exit_year')},1,0)", "int")
    s.blank()
    s.section("Company-level (unlevered) cash flow", years=True)
    s.years("ufcf", "Unlevered free cash flow",
            lambda t: f"={Y('ebitda', t)}-{S('tax_rate')}*MAX({Y('ebit', t)},0)-{Y('capex', t)}-{Y('dnwc', t)}")

    # --------------------------------------------------------- Exit Valuation
    s = sh["Exit Valuation"]
    s.title("Exit Valuation", f"{demo_tag}Exit enterprise value to sponsor proceeds.")
    idx = lambda k: f"INDEX({R(k)},{S('exit_year')})"  # noqa: E731
    s.section("Exit enterprise value to equity value")
    s.scalar(None, "Exit year", lambda: f"={S('exit_year')}", "int", "link")
    s.scalar("x_ebitda", "Exit-year EBITDA", lambda: f"={idx('ebitda')}")
    s.scalar(None, "Exit EV / EBITDA multiple", lambda: f"={S('exit_multiple')}", "mult", "link")
    s.scalar("x_ev", "Exit enterprise value", lambda: f"={S('x_ebitda')}*{S('exit_multiple')}", bold=True, top=True)
    s.scalar("x_debt", "Less: debt outstanding at exit", lambda: f"={idx('tot_debt')}")
    s.scalar("x_cash", "Plus: cash at exit", lambda: f"={idx('cash_close')}")
    s.scalar("x_netdebt", "Net debt at exit", lambda: f"={S('x_debt')}-{S('x_cash')}")
    s.scalar("x_fees", "Less: exit transaction expenses", lambda: f"={S('exit_fee_pct')}*{S('x_ev')}")
    s.scalar("x_equity_raw", "Equity value before limited-liability floor", lambda: f"={S('x_ev')}-{S('x_netdebt')}-{S('x_fees')}")
    s.scalar("x_equity", "Equity value at exit", lambda: f"=MAX({S('x_equity_raw')},0)", bold=True, top=True)
    s.blank()
    s.section("Distribution of exit equity")
    s.scalar("x_mip", "Less: management incentive pool",
             lambda: f"={S('mip_pct')}*MAX({S('x_equity')}-{S('total_equity')},0)")
    s.scalar("x_dist", "Equity available to investors", lambda: f"={S('x_equity')}-{S('x_mip')}", top=True)
    s.scalar(None, "Sponsor ownership", lambda: f"={S('sponsor_pct')}", "pct", "link")
    s.scalar("x_sponsor", "Sponsor equity proceeds", lambda: f"={S('x_dist')}*{S('sponsor_pct')}", bold=True)
    s.scalar("x_roll", "Rollover holders' proceeds", lambda: f"={S('x_dist')}-{S('x_sponsor')}")
    s.scalar(None, "Exit leverage (total debt / EBITDA)", lambda: f"={S('x_debt')}/{S('x_ebitda')}", "mult")

    # -------------------------------------------------------- Sponsor Returns
    s = sh["Sponsor Returns"]
    s.title("Sponsor Returns", f"{demo_tag}Gross investment returns on annual periods (Excel IRR). Investor-level, not company-level.")
    s.section("Sponsor cash flows", years=True)
    s.years("r_inv", "Equity invested", lambda t: 0, t0=lambda: f"=-{S('s_sponsor')}")
    s.years("r_div", "Interim distributions", lambda t: f"={Y('hold', t)}*{Y('dividend', t)}*{S('sponsor_pct')}", t0=0)
    s.years("r_exit", "Exit proceeds", lambda t: f"=IF({t}={S('exit_year')},{S('x_sponsor')},0)", t0=0)
    net = lambda t: f"={Y('r_inv', t)}+{Y('r_div', t)}+{Y('r_exit', t)}"  # noqa: E731
    s.years("r_net", "Net sponsor cash flow", net, t0=lambda: net(0), bold=True, top=True)
    s.blank()
    s.section("Returns")
    s.scalar("r_equity", "Sponsor equity invested", lambda: f"={S('s_sponsor')}")
    s.scalar("r_proceeds", "Total proceeds (distributions + exit)", lambda: f"=SUM({R('r_div')})+SUM({R('r_exit')})")
    s.scalar("r_profit", "Equity profit", lambda: f"={S('r_proceeds')}-{S('r_equity')}")
    s.scalar("moic", "MOIC", lambda: f"={S('r_proceeds')}/{S('r_equity')}", "mult", bold=True)
    s.scalar("irr", "Gross IRR", lambda: f"=IFERROR(IRR({R('r_net', 0)}),-1)", "pct", bold=True)
    s.scalar("r_repaid", "Debt repaid during holding period",
             lambda: f"=SUMPRODUCT({R('hold')},{R('tot_mand')})+SUMPRODUCT({R('hold')},{R('tot_opt')})")
    s.blank()
    s.section("Company-level (unlevered) return", years=True)
    s.years("u_cf", "Unlevered cash flow incl. purchase and sale",
            lambda t: f"={Y('hold', t)}*{Y('ufcf', t)}+IF({t}={S('exit_year')},{S('x_ev')}-{S('x_fees')},0)",
            t0=lambda: f"=-({S('ev')}+{S('u_txn')})")
    s.scalar("u_irr", "Unlevered IRR (no debt, same entry and exit)", lambda: f"=IFERROR(IRR({R('u_cf', 0)}),-1)", "pct")
    s.scalar(None, "IRR uplift from leverage", lambda: f"={S('irr')}-{S('u_irr')}", "pct")
    s.blank()
    s.section("Return attribution (total equity value creation)")
    s.scalar("a_growth", "EBITDA growth", lambda: f"=({S('x_ebitda')}-{S('ltm_ebitda')})*{S('entry_multiple')}")
    s.scalar("a_mult", "Multiple expansion", lambda: f"=({S('exit_multiple')}-{S('entry_multiple')})*{S('x_ebitda')}")
    s.scalar("a_delever", "Debt paydown and cash generation",
             lambda: f"={S('netdebt0')}-{S('x_netdebt')}+SUMPRODUCT({R('hold')},{R('dividend')})")
    s.scalar("a_fees", "Transaction costs (entry and exit)", lambda: f"=-({S('u_txn')}+{S('u_fin')}+{S('x_fees')})")
    s.scalar("a_floor", "Limited-liability floor adjustment", lambda: f"={S('x_equity')}-{S('x_equity_raw')}")
    s.scalar("a_total", "Total value creation", lambda: f"=SUM({S('a_growth')}:{S('a_floor')})", bold=True, top=True)
    s.scalar("a_direct", "Exit equity + dividends - invested equity",
             lambda: f"={S('x_equity')}+SUMPRODUCT({R('hold')},{R('dividend')})-{S('total_equity')}")

    # ----------------------------------------------------------- Model Checks
    s = sh["Model Checks"]
    s.title("Model Checks", "Every check must show PASS. Engine reference values are static outputs of the Python model.")
    s.section("Check", cols=["Value", "Status"])
    zero = lambda r: [(3, lambda: f'=IF(ABS({ycol(0)}{r + 1})<0.001,"PASS","FAIL")', "text", "calc")]  # noqa: E731
    first_check = s.r

    def check(label: str, value: Callable[[], str], kind: str = "num") -> None:
        s.scalar(None, label, value, kind, extra=zero(s.r))

    check("Sources less uses", lambda: f"={S('su_check')}")
    check("Entry bridge: EV - debt + cash - equity price", lambda: f"={S('ev')}-{S('existing_debt')}+{S('target_cash')}-{S('eq_price')}")
    check("Invested equity - (EV + fees - closing net debt)",
          lambda: f"={S('total_equity')}-({S('ev')}+{S('u_txn')}+{S('u_fin')}-{S('netdebt0')})")
    check("Negative debt balances (sum of any negatives)",
          lambda: f"=SUMIF({R('rev_close', 0)},\"<0\")+SUMIF({R('sen_close', 0)},\"<0\")+SUMIF({R('sub_close', 0)},\"<0\")")
    check("Revolver balance above commitment", lambda: f"=MAX(MAX({R('rev_close', 0)})-{S('rev_commit')},0)")
    check("Liquidity shortfall during holding period", lambda: f"=SUMPRODUCT({R('hold')},{R('shortfall')})")
    check("Exit bridge: EV - net debt - fees - equity (pre-floor)",
          lambda: f"={S('x_ev')}-{S('x_netdebt')}-{S('x_fees')}-{S('x_equity_raw')}")
    check("Attribution total - direct value creation", lambda: f"={S('a_total')}-{S('a_direct')}")
    check("Exit year outside 3 to 7 (0 = valid)", lambda: f"=IF(AND({S('exit_year')}>=3,{S('exit_year')}<=7),0,1)", "int")
    check("Recap year not before exit (0 = valid)",
          lambda: f"=IF(OR({S('recap_year')}=0,{S('recap_year')}<{S('exit_year')}),0,1)", "int")
    last_check = s.r - 1
    s.blank()
    s.scalar("checks_ok", "Overall status",
             lambda: f'=IF(COUNTIF(D{first_check + 1}:D{last_check + 1},"FAIL")=0,"ALL CHECKS PASS","CHECK FAILURES")', "text", bold=True)
    s.blank()
    s.section("Tie-out to Python engine (valid for the inputs as exported)", cols=["Workbook", "Engine", "Difference"])
    tie = lambda key, val, k: [(3, val, k, "static"), (4, lambda: f"={S(key)}-D{B.loc[key + '_tie'][1] + 1}", "0.000000", "calc")]  # noqa: E731
    s.scalar("irr_tie", "Gross IRR", lambda: f"={S('irr')}", "pct2", "link", extra=tie("irr", ref.returns.irr, "pct2"))
    s.scalar("moic_tie", "MOIC", lambda: f"={S('moic')}", "mult", "link", extra=tie("moic", ref.returns.moic, "mult"))
    s.scalar("s_sponsor_tie", "Sponsor equity invested", lambda: f"={S('s_sponsor')}", "num", "link",
             extra=tie("s_sponsor", ref.sources_uses.sponsor_equity, "num"))
    s.scalar("x_sponsor_tie", "Sponsor exit proceeds", lambda: f"={S('x_sponsor')}", "num", "link",
             extra=tie("x_sponsor", ref.exit.sponsor_proceeds, "num"))
    s.scalar("x_netdebt_tie", "Net debt at exit", lambda: f"={S('x_netdebt')}", "num", "link",
             extra=tie("x_netdebt", ref.exit.net_debt, "num"))
    s.scalar("u_irr_tie", "Unlevered IRR", lambda: f"={S('u_irr')}", "pct2", "link",
             extra=tie("u_irr", ref.returns.unlevered_irr, "pct2"))

    # --------------------------------------------------- Transaction Overview
    s = sh["Transaction Overview"]
    s.title("LeverEdge - LBO Transaction Overview",
            ("ILLUSTRATIVE OUTPUT: the target is a synthetic demonstration company; no figure describes a real business."
             if co.is_synthetic else "Model output based on user-supplied assumptions."))
    s.section("Target")
    s.scalar(None, "Company", lambda: f"={S('company_name')}", "text", "link")
    s.scalar(None, "Industry", lambda: f"={S('industry')}", "text", "link")
    s.scalar(None, "LTM revenue", lambda: f"={S('ltm_revenue')}", "num", "link")
    s.scalar(None, "LTM EBITDA", lambda: f"={S('ltm_ebitda')}", "num", "link")
    s.blank()
    s.section("Entry")
    s.scalar(None, "Purchase enterprise value", lambda: f"={S('ev')}", "num", "link")
    s.scalar(None, "Entry EV / EBITDA", lambda: f"={S('entry_multiple')}", "mult", "link")
    s.scalar(None, "Equity purchase price", lambda: f"={S('eq_price')}", "num", "link")
    s.scalar(None, "Total uses of funds", lambda: f"={S('u_total')}", "num", "link")
    s.scalar(None, "Total debt at close", lambda: f"={S('debt0')}", "num", "link")
    s.scalar(None, "Total debt / EBITDA at close", lambda: f"={S('lev0')}", "mult", "link")
    s.scalar(None, "Sponsor equity invested", lambda: f"={S('s_sponsor')}", "num", "link")
    s.scalar(None, "Equity contribution", lambda: f"={S('eq_contrib')}", "pct", "link")
    s.blank()
    s.section("Exit and returns")
    s.scalar(None, "Exit year", lambda: f"={S('exit_year')}", "int", "link")
    s.scalar(None, "Exit EBITDA", lambda: f"={S('x_ebitda')}", "num", "link")
    s.scalar(None, "Exit enterprise value", lambda: f"={S('x_ev')}", "num", "link")
    s.scalar(None, "Net debt at exit", lambda: f"={S('x_netdebt')}", "num", "link")
    s.scalar(None, "Sponsor equity proceeds", lambda: f"={S('x_sponsor')}", "num", "link")
    s.scalar(None, "MOIC", lambda: f"={S('moic')}", "mult", "link", bold=True)
    s.scalar(None, "Gross IRR", lambda: f"={S('irr')}", "pct", "link", bold=True)
    s.scalar(None, "Unlevered (company-level) IRR", lambda: f"={S('u_irr')}", "pct", "link")
    s.scalar(None, "Model checks", lambda: f"={S('checks_ok')}", "text", "link", bold=True)
    s.blank()
    s.section("Reading this workbook")
    for line in (
        "Blue on yellow = input; black = formula; green = cross-sheet link; purple italic = static engine output.",
        "Interest is charged on beginning-of-year balances: there is no circular reference to enable.",
        "Cash taxes = tax rate x max(EBIT - cash interest, 0). No NOL carry-forward; financing fees are not amortised for tax.",
        "Sweep order: revolver, then senior term loan, then subordinated debt if flagged prepayable.",
        "Sensitivity and scenario sheets are static engine outputs; every cell is a full rerun of the Python model.",
    ):
        s.note(line)

    for name in ("Transaction Overview", "Assumptions", "Sources and Uses", "Operating Model", "Debt Schedule",
                 "Cash Flow", "Exit Valuation", "Sponsor Returns", "Model Checks"):
        sh[name].finalize()
        sh[name].ws.freeze_panes(3, 1) if name not in ("Transaction Overview",) else None
    for name in ("Transaction Overview", "Assumptions", "Sources and Uses", "Exit Valuation", "Model Checks"):
        sh[name].ws.set_column(2, 2, 34 if name in ("Transaction Overview", "Assumptions") else 16)
        sh[name].ws.set_column(3, 4, 14)

    _historicals(B, sh["Historical Financials"].ws, a, demo_tag)
    _sensitivity(B, sh["IRR Sensitivity"].ws, sh["MOIC Sensitivity"].ws, a, ref)
    _scenarios(B, sh["Scenarios"].ws, a, scenario_specs or DEFAULT_SCENARIOS)

    sh["Transaction Overview"].ws.activate()
    B.wb.close()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
def _historicals(B: _Book, ws: Any, a: LBOAssumptions, tag: str) -> None:
    hist = a.company.historicals
    ws.write_string(0, 0, "Historical Financials", B.fmt("text", bold=True, font_size=14, align="left", font_color="#1F3864"))
    ws.write_string(1, 0, f"{tag}Inputs as supplied; EBIT, growth and margins are formulas.",
                    B.fmt("text", align="left", italic=True, font_color="#595959"))
    if not hist:
        ws.write_string(3, 0, "No historical financial statements were supplied.", B.fmt("text", align="left"))
        return
    hf = B.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="left")
    hr = B.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="right")
    ws.write_string(3, 0, "Fiscal year", hf)
    ws.write_string(3, 1, "", hf)
    n = len(hist)
    for j, h in enumerate(hist):
        ws.write_string(3, C0 + j, h.year, hr)
    rows = [("Revenue", "revenue"), ("EBITDA", "ebitda"), ("Depreciation and amortisation", "da"),
            ("Taxes", "taxes"), ("Capital expenditure", "capex"), ("Net working capital", "nwc")]
    at = {}
    for i, (label, field) in enumerate(rows):
        r = 4 + i
        at[field] = r
        ws.write_string(r, 0, label, B.fmt("text", align="left"))
        for j, h in enumerate(hist):
            ws.write_number(r, C0 + j, getattr(h, field), B.fmt("num", "input"))
    r0 = 4 + len(rows) + 1
    ws.write_string(r0, 0, "Derived", hf)
    for c in range(1, C0 + n):
        ws.write_string(r0, c, "", hf)
    derived = [
        ("EBIT", "num", lambda c, p: f"={c}{at['ebitda'] + 1}-{c}{at['da'] + 1}"),
        ("Revenue growth", "pct", lambda c, p: f"={c}{at['revenue'] + 1}/{p}{at['revenue'] + 1}-1" if p else None),
        ("EBITDA margin", "pct", lambda c, p: f"={c}{at['ebitda'] + 1}/{c}{at['revenue'] + 1}"),
        ("D&A % revenue", "pct", lambda c, p: f"={c}{at['da'] + 1}/{c}{at['revenue'] + 1}"),
        ("Capex % revenue", "pct", lambda c, p: f"={c}{at['capex'] + 1}/{c}{at['revenue'] + 1}"),
        ("NWC % revenue", "pct", lambda c, p: f"={c}{at['nwc'] + 1}/{c}{at['revenue'] + 1}"),
    ]
    for i, (label, kind, fn) in enumerate(derived):
        r = r0 + 1 + i
        ws.write_string(r, 0, label, B.fmt("text", align="left"))
        for j in range(n):
            f = fn(xl_col_to_name(C0 + j), xl_col_to_name(C0 + j - 1) if j else None)
            if f:
                ws.write_formula(r, C0 + j, f, B.fmt(kind))


def _grid(B: _Book, ws: Any, r: int, title: str, df: Any, kind: str) -> int:
    hf = B.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="left")
    hr = B.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="right")
    ws.write_string(r, 0, title, hf)
    ws.write_string(r, 1, "", hf)
    for j, c in enumerate(df.columns):
        ws.write_string(r, C0 + j, str(c), hr)
    ws.write_string(r + 1, 0, f"Rows: {df.index.name}  |  Columns: {df.columns.name}",
                    B.fmt("text", align="left", italic=True, font_color="#595959"))
    for i, idx in enumerate(df.index):
        rr = r + 2 + i
        ws.write_string(rr, 0, str(idx), B.fmt("text", bold=True, align="left"))
        for j in range(df.shape[1]):
            v = float(df.iloc[i, j])
            if math.isnan(v):
                ws.write_string(rr, C0 + j, "n/a", B.fmt("text", "static"))
            else:
                ws.write_number(rr, C0 + j, v, B.fmt(kind, "static"))
    ws.conditional_format(r + 2, C0, r + 1 + len(df.index), C0 + df.shape[1] - 1,
                          {"type": "3_color_scale", "min_color": "#F4B183", "mid_color": "#FFFFFF", "max_color": "#9DC3E6"})
    return r + 2 + len(df.index) + 2


def _sensitivity(B: _Book, ws_irr: Any, ws_moic: Any, a: LBOAssumptions, ref: LBOResult) -> None:
    grids = [(rp, cp, *sensitivity_2d(a, rp, cp)) for rp, cp in HEATMAPS]
    for ws, which, kind, live_key, base in ((ws_irr, 2, "pct", "irr", ref.returns.irr),
                                            (ws_moic, 3, "mult", "moic", ref.returns.moic)):
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 46)
        ws.set_column(1, 1, 9)
        ws.set_column(2, 12, 11)
        name = "IRR" if which == 2 else "MOIC"
        ws.write_string(0, 0, f"{name} Sensitivity", B.fmt("text", bold=True, font_size=14, align="left", font_color="#1F3864"))
        ws.write_string(1, 0, "STATIC ENGINE OUTPUT: each cell is a full rerun of the Python model (sources and uses, debt "
                              "schedule, exit, returns). Cells do not update when workbook inputs change.",
                        B.fmt("text", align="left", italic=True, font_color="#7030A0"))
        ws.write_string(3, 0, f"Live workbook {name} (formula)", B.fmt("text", align="left"))
        ws.write_formula(3, C0, f"={B.S(live_key)}", B.fmt(kind, "link"))
        ws.write_string(4, 0, f"Engine base-case {name} at export (static)", B.fmt("text", align="left"))
        ws.write_number(4, C0, base, B.fmt(kind, "static"))
        ws.write_string(5, 0, "Difference (non-zero once inputs are edited)", B.fmt("text", align="left"))
        ws.write_formula(5, C0, f"={ycol(0)}4-{ycol(0)}5", B.fmt("0.000000"))
        r = 7
        for g in grids:
            rp, cp = g[0], g[1]
            r = _grid(B, ws, r, f"{name}: {PARAMETERS[rp]['label']} vs {PARAMETERS[cp]['label']}", g[which], kind)


def _scenarios(B: _Book, ws: Any, a: LBOAssumptions, specs: dict[str, ScenarioSpec]) -> None:
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 46)
    ws.set_column(1, 1, 9)
    ws.set_column(2, 8, 16)
    ws.write_string(0, 0, "Scenario Analysis", B.fmt("text", bold=True, font_size=14, align="left", font_color="#1F3864"))
    ws.write_string(1, 0, "STATIC ENGINE OUTPUT: each scenario is a full rerun of the Python model on adjusted assumptions.",
                    B.fmt("text", align="left", italic=True, font_color="#7030A0"))
    hf = B.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="left")
    hr = B.fmt("text", bold=True, bg_color="#1F3864", font_color="#FFFFFF", align="right")
    names = list(specs)
    ws.write_string(3, 0, "Scenario adjustments versus base assumptions", hf)
    ws.write_string(3, 1, "", hf)
    for j, n in enumerate(names):
        ws.write_string(3, C0 + j, n, hr)
    adj = [("Revenue growth (pts per year)", "growth_delta", "pct2"), ("EBITDA margin (pts)", "margin_delta", "pct2"),
           ("Interest rates (pts)", "rate_delta", "pct2"), ("Senior leverage (turns)", "leverage_delta", "mult"),
           ("Exit multiple (turns)", "exit_multiple_delta", "mult"), ("Capex % revenue (pts)", "capex_delta", "pct2"),
           ("NWC % revenue (pts)", "nwc_delta", "pct2")]
    for i, (label, field, kind) in enumerate(adj):
        ws.write_string(4 + i, 0, label, B.fmt("text", align="left"))
        for j, n in enumerate(names):
            ws.write_number(4 + i, C0 + j, getattr(specs[n], field), B.fmt(kind, "static"))
    df = scenario_summary(run_scenarios(a, specs))
    r0 = 4 + len(adj) + 1
    ws.write_string(r0, 0, "Scenario outputs", hf)
    ws.write_string(r0, 1, "", hf)
    for j, n in enumerate(names):
        ws.write_string(r0, C0 + j, n, hr)
    for i, label in enumerate(df.index):
        r = r0 + 1 + i
        ws.write_string(r, 0, str(label), B.fmt("text", align="left"))
        kind = "pct" if "IRR" in label else ("mult" if "(x)" in label else ("int" if "flags" in label else "num"))
        for j, n in enumerate(names):
            v = df.loc[label, n] if n in df.columns else None
            if isinstance(v, str):
                ws.write_string(r, C0 + j, v, B.fmt("text", "static"))
            elif v is None or (isinstance(v, float) and math.isnan(v)):
                ws.write_string(r, C0 + j, "n/a", B.fmt("text", "static"))
            else:
                ws.write_number(r, C0 + j, float(v), B.fmt(kind, "static"))


def write_workbook(result: LBOResult, path: str, scenario_specs: dict[str, ScenarioSpec] | None = None) -> str:
    with open(path, "wb") as fh:
        fh.write(build_workbook(result, scenario_specs))
    return path
