"""Investment Committee report (ReportLab). All text conclusions are generated
from model outputs; nothing in the assessment is a fixed statement."""
from __future__ import annotations

import io
import math
from collections.abc import Mapping

import pandas as pd
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from exporters.fmt import format_frame, money, mult, pct
from risk.assessment import Assessment, InvestmentCriteria, assess
from risk.flags import covenant_table
from scenarios.scenarios import ScenarioOutcome, run_scenarios, scenario_summary
from scenarios.sensitivity import HEATMAPS, PARAMETERS, sensitivity_1d, sensitivity_2d
from transaction.model import LBOResult

NAVY = colors.HexColor("#1F3864")
BLUE = colors.HexColor("#2E75B6")
TEAL = colors.HexColor("#5B9BD5")
SAND = colors.HexColor("#C9A227")
GREY = colors.HexColor("#595959")
LIGHT = colors.HexColor("#EEF2F7")

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_ss["Heading1"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, spaceBefore=10, spaceAfter=5)
BODY = ParagraphStyle("Body", parent=_ss["BodyText"], fontName="Helvetica", fontSize=9, leading=12.5)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=7.5, textColor=GREY, leading=10)
BUL = ParagraphStyle("Bul", parent=BODY, leftIndent=10, bulletIndent=0, spaceAfter=2)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=7.8, leading=9.5)
CELL_R = ParagraphStyle("CellR", parent=CELL, alignment=TA_RIGHT)


def _table(rows: list[list[str]], widths: list[float], header: bool = True, bold_rows: tuple[int, ...] = (),
           extra: list | None = None) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, NAVY),
    ]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    for r in bold_rows:
        st += [("FONTNAME", (0, r), (-1, r), "Helvetica-Bold"), ("LINEABOVE", (0, r), (-1, r), 0.5, GREY)]
    t.setStyle(TableStyle(st + (extra or [])))
    return t


def _frame_table(df: pd.DataFrame, first: float = 62 * mm, total: float = 174 * mm, bold: tuple[str, ...] = ()) -> Table:
    f = format_frame(df)
    rows = [[""] + [str(c) for c in f.columns]] + [[str(i)] + list(f.loc[i]) for i in f.index]
    w = (total - first) / max(len(f.columns), 1)
    bold_rows = tuple(1 + list(f.index).index(b) for b in bold if b in f.index)
    return _table(rows, [first] + [w] * len(f.columns), bold_rows=bold_rows)


def _kv_table(pairs: list[tuple[str, str]], widths=(70 * mm, 32 * mm), bold: tuple[int, ...] = ()) -> Table:
    t = _table([[k, v] for k, v in pairs], list(widths), header=False, bold_rows=bold)
    t.hAlign = "LEFT"
    return t


def _bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(i, BUL, bulletText="•") for i in items]


def _bar_chart(title: str, cats: list[str], series: list[tuple[str, list[float], colors.Color]],
               stacked: bool = False, w: float = 84 * mm, h: float = 52 * mm) -> Drawing:
    d = Drawing(w, h)
    d.add(String(0, h - 9, title, fontName="Helvetica-Bold", fontSize=8, fillColor=NAVY))
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 30, 22, w - 38, h - 44
    bc.data = [s[1] for s in series]
    bc.categoryAxis.categoryNames = cats
    bc.categoryAxis.labels.fontSize = 6.5
    bc.valueAxis.labels.fontSize = 6.5
    bc.valueAxis.valueMin = 0
    bc.valueAxis.gridStrokeColor = colors.HexColor("#DDDDDD")
    bc.valueAxis.visibleGrid = True
    bc.valueAxis.strokeColor = colors.white
    bc.valueAxis.labelTextFormat = lambda v: f"{v:,.0f}"
    bc.categoryAxis.style = "stacked" if stacked else "parallel"
    bc.barSpacing = 0.5
    for i, s in enumerate(series):
        bc.bars[i].fillColor = s[2]
        bc.bars[i].strokeColor = None
    d.add(bc)
    if len(series) > 1:
        lg = Legend()
        lg.x, lg.y = 30, 8
        lg.fontSize, lg.dx, lg.dy, lg.deltax, lg.columnMaximum = 6.5, 6, 6, 50, 1
        lg.alignment = "right"
        lg.colorNamePairs = [(s[2], s[0]) for s in series]
        lg.strokeWidth = 0
        d.add(lg)
    return d


def _heat_colour(v: float, target: float) -> colors.Color:
    """Diverging blue (above target) / orange (below) scale centred on the hurdle."""
    if math.isnan(v):
        return colors.HexColor("#F2F2F2")
    x = max(-1.0, min(1.0, (v - target) / 0.12))
    if x >= 0:
        return colors.Color(1 - 0.55 * x, 1 - 0.32 * x, 1 - 0.08 * x)
    return colors.Color(1 - 0.04 * -x, 1 - 0.30 * -x, 1 - 0.55 * -x)


def _heat_table(df: pd.DataFrame, target: float, kind: str = "pct") -> Table:
    f = pct if kind == "pct" else mult
    rows = [[f"{df.index.name} \\ {df.columns.name}"] + list(df.columns)]
    extra = []
    for i, idx in enumerate(df.index):
        rows.append([str(idx)] + [f(float(v)) for v in df.iloc[i]])
        if kind == "pct":
            for j, v in enumerate(df.iloc[i]):
                extra.append(("BACKGROUND", (j + 1, i + 1), (j + 1, i + 1), _heat_colour(float(v), target)))
    w = (174 * mm - 58 * mm) / len(df.columns)
    return _table(rows, [58 * mm] + [w] * len(df.columns), extra=extra)


def _thesis(r: LBOResult) -> list[str]:
    n = r.exit.exit_year
    op, ds, at, ret = r.operating, r.debt, r.attribution, r.returns
    rev_cagr = (op.revenue[n] / op.revenue[0]) ** (1 / n) - 1
    ebitda_cagr = (op.ebitda[n] / op.ebitda[0]) ** (1 / n) - 1
    conv = ds.cfads[1:n + 1].sum() / op.ebitda[1:n + 1].sum()
    out = [
        f"<b>Earnings growth.</b> Revenue compounds at {rev_cagr:.1%} and EBITDA at {ebitda_cagr:.1%} over the {n}-year hold, "
        f"with margin moving from {op.margin[0]:.1%} to {op.margin[n]:.1%}; EBITDA rises from {money(op.ebitda[0])} to {money(op.ebitda[n])}.",
        f"<b>Cash conversion and deleveraging.</b> {conv:.0%} of EBITDA converts to cash flow available for debt service "
        f"({money(float(ds.cfads[1:n + 1].sum()))} cumulative); {money(ret.debt_repaid)} of debt is repaid and leverage falls "
        f"from {mult(float(ds.leverage[0]), 1)} to {mult(float(ds.leverage[n]), 1)}.",
    ]
    tv = at.total_value_creation
    if tv > 0:
        parts = ", ".join(f"{k[0].lower() + k[1:] if not k.startswith('EBITDA') else k} {v / tv:.0%}" for k, v in at.components.items())
        out.append(f"<b>Sources of value.</b> Equity value creation of {money(tv)} is attributable to: {parts}.")
    else:
        out.append(f"<b>Value creation is negative</b> ({money(tv)}): the modelled plan does not earn back the invested equity.")
    d = ret.leverage_uplift
    out.append(
        f"<b>Effect of leverage.</b> The business itself earns an unlevered IRR of {ret.unlevered_irr:.1%}; financing "
        f"{r.sources_uses.debt_to_capitalisation:.0%} of the capitalisation with debt {'lifts' if d >= 0 else 'reduces'} the sponsor IRR "
        f"to {ret.irr:.1%} ({d * 100:+.1f} pts). Debt adds no operating income: it changes who funds the purchase and who has "
        f"first claim on cash flow, which magnifies equity gains and losses alike.")
    return out


def build_ic_report(
    result: LBOResult,
    scenarios: Mapping[str, ScenarioOutcome] | None = None,
    assessment: Assessment | None = None,
    criteria: InvestmentCriteria = InvestmentCriteria(),
) -> bytes:
    r = result
    a = r.assumptions
    co, su, ex, ret, ds, op = a.company, r.sources_uses, r.exit, r.returns, r.debt, r.operating
    n = ex.exit_year
    yrs = max(n, 5)
    scenarios = scenarios if scenarios is not None else run_scenarios(a)
    assessment = assessment or assess(r, scenarios, criteria)
    illustrative = co.is_synthetic

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                            bottomMargin=18 * mm, title=f"Investment Committee Memorandum - {co.name}", author="LeverEdge")

    def decorate(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 12 * mm, A4[0], 12 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(18 * mm, A4[1] - 8 * mm, "LeverEdge  |  Investment Committee Memorandum")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 8 * mm, co.name[:60])
        canvas.setFillColor(GREY)
        canvas.setFont("Helvetica", 7)
        foot = ("ILLUSTRATIVE ONLY - synthetic demonstration company; not investment advice."
                if illustrative else "Model output based on user-supplied assumptions; not investment advice.")
        canvas.drawString(18 * mm, 10 * mm, foot)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    S: list = []
    add = S.append

    # 1 ------------------------------------------------------------ overview
    add(Paragraph(f"Proposed leveraged buyout of {co.name}", ParagraphStyle("T", parent=H1, fontSize=16, spaceAfter=3)))
    if illustrative:
        add(Paragraph("<b>ILLUSTRATIVE OUTPUT.</b> The target is a fictional company created for demonstration. No figure in "
                      "this report describes a real business or constitutes investment advice.",
                      ParagraphStyle("W", parent=BODY, textColor=colors.HexColor("#9C2B00"), backColor=colors.HexColor("#FDF1E7"),
                                     borderPadding=5, spaceBefore=4, spaceAfter=8)))
    add(Paragraph("1. Transaction overview", H1))
    add(Paragraph(
        f"The sponsor proposes to acquire {co.name} at an enterprise value of {money(r.entry.enterprise_value)} "
        f"({mult(r.entry.entry_multiple, 1)} LTM EBITDA of {money(r.entry.entry_ebitda)}), funded with {money(su.total_debt)} of debt "
        f"({mult(su.total_leverage, 1)} EBITDA) and {money(su.sponsor_equity)} of sponsor equity "
        f"({su.sponsor_equity / su.total_sources:.0%} of total sources). The base case assumes an exit in year {n} at "
        f"{mult(ex.exit_multiple, 1)} EBITDA, returning {money(ex.sponsor_proceeds)} to the sponsor: "
        f"<b>{mult(ret.moic)} MOIC and {pct(ret.irr)} gross IRR</b>. Closing date {a.transaction.close_date:%d %B %Y}. "
        f"Amounts in currency millions.", BODY))
    add(Spacer(1, 4))
    add(_table([["Enterprise value", "Entry multiple", "Leverage", "Sponsor equity", "MOIC", "Gross IRR", "Assessment"],
                [money(r.entry.enterprise_value), mult(r.entry.entry_multiple, 1), mult(su.total_leverage, 1),
                 money(su.sponsor_equity), mult(ret.moic), pct(ret.irr), Paragraph(f"<b>{assessment.verdict.title()}</b>", CELL_R)]],
               [24 * mm, 22 * mm, 18 * mm, 24 * mm, 16 * mm, 18 * mm, 52 * mm],
               extra=[("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("FONTSIZE", (0, 1), (-1, 1), 9.5)]))

    # 2 ------------------------------------------------------------ company
    add(Paragraph("2. Target company", H1))
    add(Paragraph(f"<b>{co.name}</b> - {co.industry}. {co.description}", BODY))
    add(Paragraph(f"LTM revenue {money(co.ltm_revenue)}; LTM EBITDA {money(co.ltm_ebitda)} ({co.ltm_ebitda / co.ltm_revenue:.1%} margin); "
                  f"existing debt {money(co.existing_debt)}; cash {money(co.cash)}; net working capital {money(co.net_working_capital)}.", BODY))
    if co.historicals:
        from financials.company import historicals_frame
        add(Spacer(1, 4))
        add(_frame_table(historicals_frame(co.historicals), bold=("EBITDA",)))

    # 3 ------------------------------------------------------------ thesis
    add(Paragraph("3. Investment thesis (as implied by the model)", H1))
    S.extend(_bullets(_thesis(r)))

    # 4-6 ---------------------------------------------------------- structure
    add(Paragraph("4. Acquisition structure and entry valuation", H1))
    add(Paragraph(
        "The purchase is priced on enterprise value. Sellers receive the equity purchase price (enterprise value less "
        "existing debt plus existing cash); existing debt is refinanced as a separate use of funds, so net debt is counted once.", BODY))
    add(Spacer(1, 3))
    add(_kv_table([(k, money(v)) for k, v in r.entry.bridge()], bold=(3,)))
    add(Paragraph("5. Sources and uses / financing structure", H1))
    src = [(k, v) for k, v in su.sources.items() if abs(v) > 1e-9]
    use = [(k, v) for k, v in su.uses.items() if abs(v) > 1e-9]
    m = max(len(src), len(use))
    rows = [["Sources", "Amount", "%", "x EBITDA", "Uses", "Amount", "%"]]
    for i in range(m):
        sk, sv = src[i] if i < len(src) else ("", None)
        uk, uv = use[i] if i < len(use) else ("", None)
        rows.append([sk, money(sv) if sv is not None else "", pct(sv / su.total_sources) if sv is not None else "",
                     mult(sv / su.entry_ebitda) if sv is not None else "",
                     uk, money(uv) if uv is not None else "", pct(uv / su.total_uses) if uv is not None else ""])
    rows.append(["Total sources", money(su.total_sources), "100.0%", mult(su.total_sources / su.entry_ebitda),
                 "Total uses", money(su.total_uses), "100.0%"])
    add(_table(rows, [44 * mm, 20 * mm, 14 * mm, 17 * mm, 45 * mm, 20 * mm, 14 * mm], bold_rows=(len(rows) - 1,),
               extra=[("ALIGN", (4, 0), (4, -1), "LEFT")]))
    add(Paragraph(f"Sources less uses: {su.imbalance:.4f} (reconciled). Sponsor equity is the residual. Closing cash "
                  f"{money(su.closing_cash)}; net debt at close {money(su.net_debt)}; sponsor ownership {pct(su.sponsor_ownership)}.", SMALL))
    d = a.debt
    add(Spacer(1, 4))
    add(_table([["Tranche", "Amount", "x EBITDA", "Rate", "Amortisation", "Maturity", "Sweep"],
                ["Revolving credit facility", f"{money(su.revolver_draw)} of {money(d.revolver_commitment)}",
                 mult(su.revolver_draw / su.entry_ebitda), pct(d.revolver_rate, 2), "-", "-", "First"],
                ["Senior term loan", money(su.senior_debt), mult(d.senior_leverage), pct(d.senior_rate, 2),
                 f"{d.senior_amort_pct:.1%} p.a.", f"Year {d.senior_maturity}", f"{d.cash_sweep_pct:.0%}"],
                ["Subordinated debt", money(su.sub_debt), mult(d.sub_leverage), pct(d.sub_rate, 2),
                 f"{d.sub_amort_pct:.1%} p.a." if d.sub_amort_pct else "Bullet", f"Year {d.sub_maturity}",
                 "After senior" if d.sub_prepayable else "None"]],
               [42 * mm, 30 * mm, 18 * mm, 18 * mm, 24 * mm, 20 * mm, 22 * mm]))

    # 7-8 ---------------------------------------------------------- operating
    add(PageBreak())
    add(Paragraph("6. Operating assumptions and financial projections", H1))
    o = a.operating
    add(Paragraph(
        f"Projection basis: {'detailed operating statement (COGS, SG&A, fixed costs)' if o.mode == 'detailed' else 'driver-based (revenue growth and EBITDA margin)'}. "
        f"D&A {o.da_pct_revenue:.1%} of revenue; capex {o.capex_pct_revenue:.1%}; net working capital {o.nwc_pct_revenue:.1%}; "
        f"cash tax rate {o.tax_rate:.0%} on EBIT less cash interest. Interest is charged on "
        f"{'beginning-of-year balances (non-circular)' if ds.interest_method == 'beginning' else 'average balances (iteratively solved)'}.", BODY))
    add(Spacer(1, 4))
    add(_frame_table(op.to_frame(yrs), bold=("EBITDA", "EBIT")))
    add(Spacer(1, 6))
    add(_frame_table(ds.cash_flow_frame(op, yrs),
                     bold=("Cash flow available for debt service", "Free cash flow after debt service", "Closing cash")))

    # 9 ------------------------------------------------------------ debt
    add(Paragraph("7. Debt repayment analysis", H1))
    cats = ["Close"] + [f"Y{t}" for t in range(1, yrs + 1)]
    charts = Table([[
        _bar_chart("EBITDA", cats, [("EBITDA", [float(v) for v in op.ebitda[:yrs + 1]], BLUE)]),
        _bar_chart("Debt outstanding by tranche", cats,
                   [("Senior", [float(v) for v in ds.tranches["senior"].closing[:yrs + 1]], NAVY),
                    ("Subordinated", [float(v) for v in ds.tranches["sub"].closing[:yrs + 1]], TEAL),
                    ("Revolver", [float(v) for v in ds.tranches["revolver"].closing[:yrs + 1]], SAND)], stacked=True),
    ]], colWidths=[87 * mm, 87 * mm])
    add(charts)
    add(_frame_table(ds.credit_stats_frame(yrs), bold=("Total debt",)))
    cov = covenant_table(ds, a.debt, n)
    if not cov.empty:
        add(Spacer(1, 5))
        add(_frame_table(cov))
    else:
        add(Paragraph("No financial covenants are modelled.", SMALL))

    # 10-11 -------------------------------------------------------- exit / returns
    add(PageBreak())
    add(Paragraph("8. Exit assumptions and valuation", H1))
    add(_kv_table([("Exit year", str(n)), ("Exit-year EBITDA", money(ex.exit_ebitda)),
                   ("Exit EV / EBITDA", mult(ex.exit_multiple, 1))] + [(k, money(v)) for k, v in ex.bridge()], bold=(7, 10)))
    add(Paragraph("9. Sponsor returns", H1))
    cf = pd.DataFrame({"Sponsor cash flow": list(ret.cash_flows)},
                      index=[f"{dt:%b-%y}" for dt in ret.dates]).T
    add(_frame_table(cf, first=40 * mm))
    add(Spacer(1, 4))
    pairs = [("Sponsor equity invested", money(ret.equity_invested)), ("Total proceeds", money(ret.total_proceeds)),
             ("Equity profit", money(ret.profit)), ("MOIC", mult(ret.moic)),
             ("Gross IRR (annual periods)", pct(ret.irr)), ("Gross IRR (dated, act/365)", pct(ret.xirr)),
             ("Unlevered company-level IRR", pct(ret.unlevered_irr)), ("Debt repaid over the hold", money(ret.debt_repaid))]
    if ret.net_irr is not None:
        pairs += [("Fund management fees", money(ret.fund_fees)), ("Carried interest", money(ret.carried_interest)),
                  ("Net IRR (after modelled fund costs)", pct(ret.net_irr)), ("Net MOIC", mult(ret.net_moic))]
    else:
        pairs.append(("Net IRR", "Not reported: fund-level fees not modelled"))
    add(_kv_table(pairs, widths=(70 * mm, 60 * mm)))
    add(Paragraph("Gross returns are at the investment level, before fund management fees and carried interest. They are "
                  "returns to the sponsor's equity, not to the enterprise; the unlevered IRR shows the company-level return.", SMALL))

    # 12 ----------------------------------------------------------- sensitivity
    add(Paragraph("10. Sensitivity analysis", H1))
    add(Paragraph(f"Every cell is a complete rerun of the model. Shading is relative to the {criteria.target_irr:.0%} target IRR "
                  "(blue above, orange below). 'n/a' marks an infeasible structure.", SMALL))
    for rp, cp in HEATMAPS:
        g_irr, _ = sensitivity_2d(a, rp, cp)
        add(KeepTogether([Paragraph(f"<b>Gross IRR: {PARAMETERS[rp]['label']} vs {PARAMETERS[cp]['label']}</b>", BODY),
                          _heat_table(g_irr, criteria.target_irr), Spacer(1, 6)]))
    _, g_moic = sensitivity_2d(a, "entry_multiple", "exit_multiple")
    add(KeepTogether([Paragraph("<b>MOIC: Entry EV/EBITDA vs Exit EV/EBITDA</b>", BODY), _heat_table(g_moic, 0, "mult")]))

    # 13 ----------------------------------------------------------- downside
    add(PageBreak())
    add(Paragraph("11. Scenario analysis and downside risks", H1))
    summ = scenario_summary(scenarios)
    add(_frame_table(summ, first=58 * mm, bold=("MOIC (x)", "Gross IRR")))
    add(Spacer(1, 5))
    risks: list[str] = []
    one = sensitivity_1d(a, "exit_multiple", [ex.exit_multiple - 1.0, ex.exit_multiple])
    if one["IRR"].notna().all():
        risks.append(f"<b>Exit valuation.</b> One turn of exit multiple contraction reduces IRR from {pct(one['IRR'].iloc[1])} to "
                     f"{pct(one['IRR'].iloc[0])} and MOIC to {mult(one['MOIC'].iloc[0])}.")
    rt = sensitivity_1d(a, "rates", [0.0, 0.01])
    if rt["IRR"].notna().all():
        risks.append(f"<b>Interest rates.</b> A 100 bp increase across all tranches cuts debt repaid over the hold from "
                     f"{money(rt['Debt repaid'].iloc[0])} to {money(rt['Debt repaid'].iloc[1])} and IRR to {pct(rt['IRR'].iloc[1])}.")
    mg = sensitivity_1d(a, "margin", [-0.02, 0.0])
    if mg["IRR"].notna().all():
        risks.append(f"<b>Margins.</b> A 200 bp EBITDA margin shortfall lowers IRR to {pct(mg['IRR'].iloc[0])} and leaves exit leverage at "
                     f"{mult(mg['Exit leverage (x)'].iloc[0], 1)}.")
    risks.append(f"<b>Liquidity.</b> Minimum base-case DSCR is {mult(float(ds.dscr[1:n + 1].min()))}; the revolver "
                 f"({money(a.debt.revolver_commitment)}) is the only liquidity backstop above the {money(a.transaction.min_cash)} minimum cash balance.")
    for name, oc in scenarios.items():
        if oc.result is not None:
            for f in [f for f in oc.result.flags if f.severity == "critical"][:4]:
                risks.append(f"<b>{name} case, {'year ' + str(f.year) if f.year else 'close'}.</b> {f.message}")
    S.extend(_bullets(risks))

    # 14 ----------------------------------------------------------- assessment
    add(Paragraph("12. Conditional investment assessment", H1))
    add(Paragraph(f"<b>{assessment.verdict}</b>", ParagraphStyle("V", parent=BODY, fontSize=11, textColor=NAVY, spaceAfter=4)))
    add(Paragraph(assessment.summary, BODY))
    add(Paragraph(f"Criteria applied: target gross IRR {criteria.target_irr:.0%}, target MOIC {mult(criteria.target_moic, 1)}, "
                  f"downside MOIC of at least {mult(criteria.min_downside_moic, 1)}, and no critical liquidity or covenant flags.", SMALL))
    for title, items in (("Supporting factors", assessment.supports), ("Concerns", assessment.concerns),
                         ("Conditions and mitigants", assessment.conditions)):
        if items:
            add(Paragraph(f"<b>{title}</b>", BODY))
            S.extend(_bullets(items))
    add(Spacer(1, 6))
    add(Paragraph("This assessment is generated mechanically from the model outputs above and is conditional on the stated "
                  "assumptions. It changes when the assumptions change and is not a substitute for commercial, financial "
                  "and legal due diligence.", SMALL))
    checks = r.checks()
    failed = [k for k, (ok, _) in checks.items() if not ok]
    add(Paragraph(f"Model integrity: {len(checks) - len(failed)} of {len(checks)} reconciliation checks pass"
                  + (f"; failing: {', '.join(failed)}." if failed else "."), SMALL))

    doc.build(S, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def write_ic_report(result: LBOResult, path: str, **kw) -> str:
    with open(path, "wb") as fh:
        fh.write(build_ic_report(result, **kw))
    return path
