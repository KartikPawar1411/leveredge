"""LeverEdge Streamlit dashboard. UI only: every number comes from the engine.

    streamlit run app/main.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import charts  # noqa: E402
from exporters.excel import build_workbook  # noqa: E402
from exporters.fmt import format_frame, money, mult, pct  # noqa: E402
from exporters.pdf import build_ic_report  # noqa: E402
from financials.assumptions import (  # noqa: E402
    HORIZON,
    AdvancedAssumptions,
    CompanyData,
    DebtAssumptions,
    ExitAssumptions,
    LBOAssumptions,
    OperatingAssumptions,
    TransactionAssumptions,
)
from financials.company import HIST_COLUMNS, demo_assumptions, historicals_frame, load_assumptions_json, parse_historicals  # noqa: E402
from financials.errors import LeverEdgeError  # noqa: E402
from risk.assessment import InvestmentCriteria, assess  # noqa: E402
from risk.flags import covenant_table, flags_frame  # noqa: E402
from scenarios.scenarios import DEFAULT_SCENARIOS, ScenarioSpec, exit_timing, run_scenarios, scenario_summary  # noqa: E402
from scenarios.sensitivity import HEATMAPS, PARAMETERS, sensitivity_1d, sensitivity_2d  # noqa: E402
from transaction.model import run_lbo  # noqa: E402

st.set_page_config(page_title="LeverEdge | LBO Modeling Platform", page_icon=":material/account_balance:", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; max-width: 1500px;}
  [data-testid="stMetricValue"] {font-size: 1.55rem;}
  [data-testid="stMetric"] {background: #fcfcfb; border: 1px solid rgba(11,11,11,.10); border-radius: 8px; padding: 10px 14px;}
  .le-banner {background:#FDF1E7; border-left:4px solid #C55A11; padding:8px 14px; border-radius:4px; color:#7A3A0A; font-size:.9rem; margin-bottom:.6rem;}
  .le-verdict {background:#1F3864; color:white; padding:12px 16px; border-radius:6px; margin:.4rem 0 .8rem 0;}
  .le-verdict b {font-size:1.05rem;}
</style>""", unsafe_allow_html=True)

YEARS = [f"Year {t}" for t in range(1, HORIZON + 1)]


def show(df: pd.DataFrame, raw: bool = False) -> None:
    st.dataframe(df if raw else format_frame(df), width="stretch")


_chart_n = iter(range(10_000))


def plot(fig) -> None:
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"chart_{next(_chart_n)}")


# --------------------------------------------------------------------------- #
# Session state: `loaded` holds the assumption set that seeds the widgets.
# --------------------------------------------------------------------------- #
ss = st.session_state
if "loaded" not in ss:
    ss.loaded, ss.load_id, ss.file_sig = demo_assumptions(), 0, None


def set_loaded(a: LBOAssumptions) -> None:
    ss.loaded = a
    ss.load_id += 1
    for k in ("xlsx", "pdf"):
        ss.pop(k, None)


with st.sidebar:
    st.markdown("## LeverEdge")
    st.caption("Leveraged buyout and private equity investment modeling")
    with st.expander("Data source", expanded=False):
        st.caption("Start from the synthetic demo company, load a saved assumption set, or upload historical financials.")
        if st.button("Reset to synthetic demo company", width="stretch"):
            ss.file_sig = None
            set_loaded(demo_assumptions())
            st.rerun()
        up = st.file_uploader("Assumptions file (.json exported from this app)", type=["json"])
        if up is not None and ss.file_sig != ("json", up.name, up.size):
            try:
                set_loaded(load_assumptions_json(up))
                ss.file_sig = ("json", up.name, up.size)
                st.rerun()
            except LeverEdgeError as exc:
                st.error(str(exc))
        hist_up = st.file_uploader("Historical financials (.csv)", type=["csv"],
                                   help="Columns: " + ", ".join(HIST_COLUMNS) + ". The last row seeds LTM revenue, EBITDA and NWC.")
        if hist_up is not None and ss.file_sig != ("csv", hist_up.name, hist_up.size):
            try:
                hist = parse_historicals(hist_up)
                last = hist[-1]
                co = replace(ss.loaded.company, historicals=hist, ltm_revenue=last.revenue, ltm_ebitda=last.ebitda,
                             net_working_capital=last.nwc, is_synthetic=False,
                             name=ss.loaded.company.name.replace(" (SYNTHETIC DEMO)", ""), description="User-supplied company data.")
                set_loaded(replace(ss.loaded, company=co))
                ss.file_sig = ("csv", hist_up.name, hist_up.size)
                st.rerun()
            except LeverEdgeError as exc:
                st.error(str(exc))

    L: LBOAssumptions = ss.loaded
    K = f"_{ss.load_id}"   # widget keys change when a new assumption set is loaded

    def num(label: str, value: float, key: str, step: float = 1.0, fmt: str = "%.1f", lo: float | None = 0.0,
            hi: float | None = None, help: str | None = None) -> float:
        return st.number_input(label, value=float(value), step=step, format=fmt, min_value=lo, max_value=hi, key=key + K, help=help)

    def pctin(label: str, value: float, key: str, step: float = 0.25, lo: float = 0.0, hi: float = 100.0, help: str | None = None) -> float:
        return st.number_input(label + " (%)", value=round(value * 100, 4), step=step, format="%.2f",
                               min_value=lo, max_value=hi, key=key + K, help=help) / 100.0

    with st.expander("Target company", expanded=False):
        name = st.text_input("Company name", L.company.name, key="name" + K)
        industry = st.text_input("Industry", L.company.industry, key="ind" + K)
        description = st.text_area("Description", L.company.description, key="desc" + K, height=80)
        ltm_rev = num("LTM revenue", L.company.ltm_revenue, "rev", 10.0)
        ltm_ebitda = num("LTM EBITDA", L.company.ltm_ebitda, "ebitda", 5.0)
        cash = num("Cash", L.company.cash, "cash")
        existing_debt = num("Existing debt (refinanced at close)", L.company.existing_debt, "xdebt", 5.0)
        nwc0 = num("Net working capital", L.company.net_working_capital, "nwc0", 5.0, lo=None)
        is_synth = L.company.is_synthetic and name == L.company.name

    with st.expander("Entry valuation and fees", expanded=True):
        entry_mult = num("Entry EV / EBITDA (x)", L.transaction.entry_multiple, "em", 0.25, "%.2f", lo=0.1)
        txn_fee = pctin("Transaction fees, % of EV", L.transaction.transaction_fee_pct, "tf", hi=20.0)
        fin_fee = pctin("Financing fees, % of debt raised", L.transaction.financing_fee_pct, "ff", hi=20.0)
        min_cash = num("Minimum cash balance", L.transaction.min_cash, "mc")
        use_cash = st.checkbox("Use target cash above minimum to fund the deal", L.transaction.use_excess_cash, key="uc" + K)
        close_date = st.date_input("Closing date", L.transaction.close_date, key="cd" + K)

    with st.expander("Debt financing", expanded=True):
        sen_lev = num("Senior term loan (x EBITDA)", L.debt.senior_leverage, "sl", 0.25, "%.2f")
        sen_rate = pctin("Senior interest rate", L.debt.senior_rate, "sr", hi=50.0)
        sen_am = pctin("Senior amortisation, % of original p.a.", L.debt.senior_amort_pct, "sa", 0.5)
        sen_mat = int(st.number_input("Senior maturity (years)", 1, 15, L.debt.senior_maturity, key="sm" + K))
        sub_lev = num("Subordinated debt (x EBITDA)", L.debt.sub_leverage, "bl", 0.25, "%.2f")
        sub_rate = pctin("Subordinated interest rate", L.debt.sub_rate, "br", hi=50.0)
        sub_mat = int(st.number_input("Subordinated maturity (years)", 1, 15, L.debt.sub_maturity, key="bm" + K))
        sub_prepay = st.checkbox("Subordinated debt prepayable from sweep", L.debt.sub_prepayable, key="bp" + K)
        rev_commit = num("Revolver commitment", L.debt.revolver_commitment, "rc", 5.0)
        rev_draw = num("Revolver drawn at close", L.debt.revolver_draw_at_close, "rd", 5.0)
        rev_rate = pctin("Revolver interest rate", L.debt.revolver_rate, "rr", hi=50.0)
        rev_fee = pctin("Revolver undrawn fee", L.debt.revolver_undrawn_fee, "rf", 0.125, hi=10.0)
        sweep = pctin("Cash sweep, % of excess cash", L.debt.cash_sweep_pct, "sw", 5.0)
        max_lev = num("Maximum closing leverage (x)", L.debt.max_total_leverage, "ml", 0.25, "%.2f")
        min_eq = pctin("Minimum equity contribution", L.transaction.min_equity_contribution_pct, "me", 1.0)
        method = st.radio("Interest calculation", ["beginning", "average"], horizontal=True,
                          index=0 if L.debt.interest_method == "beginning" else 1, key="im" + K,
                          format_func=lambda m: "Beginning balance" if m == "beginning" else "Average balance (iterative)",
                          help="Default is the transparent non-circular method: opening balance x rate. The average-balance "
                               "option is solved by fixed-point iteration.")
        use_cov = st.checkbox("Model financial covenants", L.debt.covenant_max_leverage is not None, key="cov" + K)
        cov_lev = num("Max total debt / EBITDA (x)", L.debt.covenant_max_leverage or 6.75, "cl", 0.25, "%.2f") if use_cov else None
        cov_icr = num("Min EBITDA / interest (x)", L.debt.covenant_min_interest_coverage or 1.75, "ci", 0.25, "%.2f") if use_cov else None

    with st.expander("Operating forecast", expanded=False):
        mode = st.radio("Projection method", ["driver", "detailed"], horizontal=True,
                        index=0 if L.operating.mode == "driver" else 1, key="mode" + K,
                        format_func=lambda m: "Drivers" if m == "driver" else "Detailed statement")
        rows = {"Revenue growth %": [g * 100 for g in L.operating.revenue_growth]}
        cogs0 = L.operating.cogs_pct or tuple(0.6 for _ in range(HORIZON))
        sga0 = L.operating.sga_pct or tuple(0.18 for _ in range(HORIZON))
        if mode == "driver":
            rows["EBITDA margin %"] = [m * 100 for m in L.operating.ebitda_margin]
        else:
            rows["COGS % revenue"] = [c * 100 for c in cogs0]
            rows["SG&A % revenue"] = [c * 100 for c in sga0]
        drv = st.data_editor(pd.DataFrame(rows, index=YEARS).T.round(3), key=f"drv_{mode}{K}", width="stretch",
                             column_config={y: st.column_config.NumberColumn(y.replace("Year ", "Y"), format="%.1f") for y in YEARS})
        other_opex = num("Other fixed operating costs p.a.", L.operating.other_opex, "oo", 1.0) if mode == "detailed" else L.operating.other_opex
        da_pct = pctin("D&A, % of revenue", L.operating.da_pct_revenue, "da")
        capex_pct = pctin("Capital expenditure, % of revenue", L.operating.capex_pct_revenue, "cx")
        nwc_pct = pctin("Net working capital, % of revenue", L.operating.nwc_pct_revenue, "nw", 0.5, lo=-50.0)
        tax = pctin("Cash tax rate", L.operating.tax_rate, "tx", 1.0, hi=99.0)

    with st.expander("Exit", expanded=True):
        exit_year = st.slider("Exit year", 3, 7, L.exit.exit_year, key="ey" + K)
        exit_mult = num("Exit EV / EBITDA (x)", L.exit.exit_multiple, "xm", 0.25, "%.2f", lo=0.1)
        exit_fee = pctin("Exit transaction expenses, % of EV", L.exit.exit_fee_pct, "xf", hi=20.0)

    with st.expander("Advanced features (optional)", expanded=False):
        st.caption("All off in the baseline model.")
        rollover = pctin("Management rollover, % of seller equity", L.transaction.rollover_pct, "ro", 1.0, hi=99.0)
        mip = pctin("Management incentive pool, % of equity gain", L.exit.mip_pct, "mip", 1.0, hi=99.0,
                    help="Options struck at entry equity value: management receives this share of exit equity above total invested equity.")
        do_recap = st.checkbox("Dividend recapitalisation", L.advanced.recap_year is not None, key="rcp" + K)
        recap_year = recap_turns = None
        if do_recap:
            recap_year = st.slider("Recap year", 1, 6, min(L.advanced.recap_year or 3, 6), key="ry" + K)
            recap_turns = num("Incremental senior debt (x that year's EBITDA)", L.advanced.recap_turns or 1.0, "rt", 0.25, "%.2f")
        net = st.checkbox("Model fund-level fees and carry (net IRR)", L.advanced.model_net_returns, key="net" + K)
        mgmt_fee = pctin("Management fee on invested equity p.a.", L.advanced.mgmt_fee_pct, "mf", hi=10.0) if net else L.advanced.mgmt_fee_pct
        carry = pctin("Carried interest", L.advanced.carry_pct, "cy", 1.0, hi=50.0) if net else L.advanced.carry_pct
        hurdle = pctin("Preferred return hurdle", L.advanced.hurdle_rate, "hr", 1.0, hi=50.0) if net else L.advanced.hurdle_rate

    with st.expander("Investment criteria", expanded=False):
        crit = InvestmentCriteria(
            target_irr=pctin("Target gross IRR", 0.20, "tirr", 1.0),
            target_moic=num("Target MOIC (x)", 2.0, "tmoic", 0.1, "%.1f"),
            min_downside_moic=num("Minimum bear-case MOIC (x)", 1.0, "dmoic", 0.1, "%.1f"),
        )

# --------------------------------------------------------------------------- #
# Build assumptions from the widgets and run the engine
# --------------------------------------------------------------------------- #
try:
    vec = lambda label: tuple(float(v) / 100.0 for v in drv.loc[label].fillna(0.0))  # noqa: E731
    A = LBOAssumptions(
        company=CompanyData(name=name, industry=industry, description=description, ltm_revenue=ltm_rev,
                            ltm_ebitda=ltm_ebitda, cash=cash, existing_debt=existing_debt, net_working_capital=nwc0,
                            is_synthetic=is_synth, historicals=L.company.historicals),
        transaction=TransactionAssumptions(entry_multiple=entry_mult, transaction_fee_pct=txn_fee, financing_fee_pct=fin_fee,
                                           min_cash=min_cash, use_excess_cash=use_cash, rollover_pct=rollover,
                                           min_equity_contribution_pct=min_eq,
                                           close_date=close_date if isinstance(close_date, date) else L.transaction.close_date),
        operating=OperatingAssumptions(
            mode=mode, revenue_growth=vec("Revenue growth %"),
            ebitda_margin=vec("EBITDA margin %") if mode == "driver" else L.operating.ebitda_margin,
            cogs_pct=vec("COGS % revenue") if mode == "detailed" else L.operating.cogs_pct,
            sga_pct=vec("SG&A % revenue") if mode == "detailed" else L.operating.sga_pct,
            other_opex=other_opex, da_pct_revenue=da_pct, capex_pct_revenue=capex_pct, nwc_pct_revenue=nwc_pct, tax_rate=tax),
        debt=DebtAssumptions(senior_leverage=sen_lev, senior_rate=sen_rate, senior_amort_pct=sen_am, senior_maturity=sen_mat,
                             sub_leverage=sub_lev, sub_rate=sub_rate, sub_amort_pct=L.debt.sub_amort_pct, sub_maturity=sub_mat,
                             sub_prepayable=sub_prepay, revolver_commitment=rev_commit, revolver_draw_at_close=rev_draw,
                             revolver_rate=rev_rate, revolver_undrawn_fee=rev_fee, cash_sweep_pct=sweep,
                             max_total_leverage=max_lev, interest_method=method,
                             covenant_max_leverage=cov_lev, covenant_min_interest_coverage=cov_icr),
        exit=ExitAssumptions(exit_year=exit_year, exit_multiple=exit_mult, exit_fee_pct=exit_fee, mip_pct=mip),
        advanced=AdvancedAssumptions(recap_year=recap_year, recap_turns=recap_turns or 0.0, model_net_returns=net,
                                     mgmt_fee_pct=mgmt_fee, carry_pct=carry, hurdle_rate=hurdle),
    )
    R = run_lbo(A)
except LeverEdgeError as exc:
    st.title("LeverEdge")
    st.error(f"The model cannot be run with the current inputs: {exc}")
    st.info("Adjust the highlighted assumption in the sidebar. No output is shown until the inputs are feasible.")
    st.stop()

su, ds, op, ex, ret, entry = R.sources_uses, R.debt, R.operating, R.exit, R.returns, R.entry
N = ex.exit_year
VIEW = max(N, 5)

# scenario specs live in session state so edits persist across reruns
if "scen" not in ss:
    ss.scen = {k: v for k, v in DEFAULT_SCENARIOS.items()}
SC = run_scenarios(A, ss.scen)
ASSESS = assess(R, SC, crit)

# --------------------------------------------------------------------------- #
st.title("LeverEdge")
st.caption("Leveraged Buyout and Private Equity Investment Modeling Platform  |  amounts in currency millions")
if A.company.is_synthetic:
    st.markdown('<div class="le-banner"><b>Illustrative output.</b> The target is a synthetic demonstration company. '
                'No figure describes a real business.</div>', unsafe_allow_html=True)

TAB_NAMES = ["Overview", "Entry and S&U", "Operating model", "Debt", "Exit and returns", "Sensitivity",
             "Scenarios", "Advanced", "Risk and checks", "Export"]
_want = st.query_params.get("tab", "").lower()   # deep link, e.g. ?tab=sensitivity
tabs = st.tabs(TAB_NAMES, default=next((n for n in TAB_NAMES if n.lower().startswith(_want)), None) if _want else None)

# ------------------------------------------------------------------ Overview
with tabs[0]:
    st.subheader(A.company.name)
    st.caption(f"{A.company.industry}. {A.company.description}")
    c = st.columns(6)
    c[0].metric("Enterprise value", money(entry.enterprise_value), f"{entry.entry_multiple:.1f}x EBITDA", delta_color="off", delta_arrow="off")
    c[1].metric("Total debt at close", money(su.total_debt), f"{su.total_leverage:.1f}x EBITDA", delta_color="off", delta_arrow="off")
    c[2].metric("Sponsor equity", money(su.sponsor_equity), f"{su.equity_contribution_pct:.0%} of sources", delta_color="off", delta_arrow="off")
    c[3].metric("Sponsor proceeds", money(ret.total_proceeds), f"year {N} exit at {ex.exit_multiple:.1f}x", delta_color="off", delta_arrow="off")
    c[4].metric("MOIC", mult(ret.moic), f"target {crit.target_moic:.1f}x", delta_color="off", delta_arrow="off")
    c[5].metric("Gross IRR", pct(ret.irr), f"{(ret.irr - crit.target_irr) * 100:+.1f} pts vs target")
    st.markdown(f'<div class="le-verdict"><b>{ASSESS.verdict}</b><br>{ASSESS.summary}</div>', unsafe_allow_html=True)
    a1, a2 = st.columns(2)
    with a1:
        plot(charts.ebitda_chart(op, VIEW))
    with a2:
        plot(charts.debt_balance_chart(ds, VIEW))
    b1, b2 = st.columns(2)
    with b1:
        st.markdown("**How leverage drives this return**")
        st.markdown(
            f"- The business earns an **unlevered IRR of {pct(ret.unlevered_irr)}** (bought and sold at the same prices with no debt).\n"
            f"- Funding {su.debt_to_capitalisation:.0%} of the capitalisation with debt costing "
            f"{pct(A.debt.senior_rate)} to {pct(max(A.debt.sub_rate, A.debt.senior_rate))} pre-tax "
            f"{'raises' if ret.leverage_uplift >= 0 else 'lowers'} the sponsor IRR to **{pct(ret.irr)}** "
            f"({ret.leverage_uplift * 100:+.1f} pts).\n"
            f"- Debt is a financing source, never operating income: EBITDA is identical at any leverage. Leverage helps only "
            f"while the asset return exceeds the after-tax cost of debt, and it magnifies losses in the same way.\n"
            f"- {money(ret.debt_repaid)} of debt is repaid from operating cash flow over {N} years, transferring enterprise "
            f"value from lenders to equity holders.")
    with b2:
        st.markdown("**Risk indicators**")
        crit_n = sum(f.severity == "critical" for f in R.flags)
        warn_n = sum(f.severity == "warning" for f in R.flags)
        bear = SC.get("Bear")
        r1, r2, r3 = st.columns(3)
        r1.metric("Critical flags (base)", crit_n)
        r2.metric("Minimum DSCR", mult(float(ds.dscr[1:N + 1].min())))
        r3.metric("Exit leverage", mult(float(ds.leverage[N]), 1))
        if crit_n == 0 and warn_n == 0:
            st.success("Base case: no liquidity, covenant or structuring flags.", icon=":material/check_circle:")
        for f in R.flags[:5]:
            (st.error if f.severity == "critical" else st.warning if f.severity == "warning" else st.info)(
                f"{'Year ' + str(f.year) + ': ' if f.year else ''}{f.message}")
        if bear and bear.result is not None:
            bn = sum(f.severity == "critical" for f in bear.result.flags)
            st.caption(f"Bear case: IRR {pct(bear.result.returns.irr)}, MOIC {mult(bear.result.returns.moic)}, {bn} critical flag(s). See Scenarios.")

# -------------------------------------------------------------- Entry and S&U
with tabs[1]:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("Entry valuation")
        st.caption("Enterprise value is not the equity cheque. Sellers receive EV less existing debt plus existing cash.")
        show(pd.DataFrame({"Amount": dict([("Entry EBITDA (LTM)", entry.entry_ebitda)] + entry.bridge())}))
        st.caption(f"Entry EV / EBITDA {mult(entry.entry_multiple)}  |  EV / revenue {mult(entry.ev_to_revenue)}")
    with c2:
        st.subheader("Closing capital structure")
        plot(charts.capital_structure_chart(su))
        k = st.columns(4)
        k[0].metric("Total debt / EBITDA", mult(su.total_leverage))
        k[1].metric("Senior debt / EBITDA", mult(su.senior_leverage))
        k[2].metric("Equity contribution", pct(su.equity_contribution_pct))
        k[3].metric("Sponsor ownership", pct(su.sponsor_ownership))
    st.subheader("Sources and uses")
    s1, s2 = st.columns(2)

    def su_frame(d: dict[str, float], total: float, label: str) -> pd.DataFrame:
        df = pd.DataFrame({"Amount": d})
        df.loc[label] = total
        out = pd.DataFrame({"Amount": df["Amount"].map(money), "% of total": (df["Amount"] / total).map(pct),
                            "x EBITDA": (df["Amount"] / su.entry_ebitda).map(mult)})
        return out

    with s1:
        st.markdown("**Sources**")
        show(su_frame(su.sources, su.total_sources, "Total sources"), raw=True)
    with s2:
        st.markdown("**Uses**")
        show(su_frame(su.uses, su.total_uses, "Total uses"), raw=True)
    if su.is_balanced:
        st.success(f"Sources equal uses: {money(su.total_sources)} = {money(su.total_uses)} (difference {su.imbalance:.6f}). "
                   f"Sponsor equity is the residual source.", icon=":material/check_circle:")
    else:
        st.error(f"Sources and uses do not balance: difference {su.imbalance:.4f}")
    st.caption(f"Cash treatment: target cash {money(A.company.cash)}; used as a source {money(su.existing_cash_used)}; "
               f"funded to reach minimum {money(su.cash_to_balance_sheet)}; closing cash {money(su.closing_cash)}. "
               f"Financing fees are {pct(A.transaction.financing_fee_pct)} of funded debt and are a use of funds, not netted from proceeds.")

# ------------------------------------------------------------ Operating model
with tabs[2]:
    if A.company.historicals:
        st.subheader("Historical financials")
        show(historicals_frame(A.company.historicals))
    st.subheader("Operating forecast")
    st.caption(("Detailed operating statement: revenue less COGS, SG&A and fixed costs." if op.mode == "detailed"
                else "Driver-based: revenue growth and EBITDA margin.") + " Pre-financing; identical under any capital structure.")
    show(op.to_frame(VIEW))
    st.subheader("Cash flow")
    show(ds.cash_flow_frame(op, VIEW))
    c1, c2 = st.columns(2)
    with c1:
        plot(charts.ebitda_chart(op, VIEW))
    with c2:
        plot(charts.debt_service_chart(ds, VIEW))

# ----------------------------------------------------------------------- Debt
with tabs[3]:
    st.caption("Interest method: " + ("opening balance x rate (non-circular)." if ds.interest_method == "beginning"
               else "average balance, solved by fixed-point iteration.") +
               " Repayment priority: revolver, senior term loan, then subordinated debt if prepayable.")
    c1, c2 = st.columns(2)
    with c1:
        plot(charts.debt_balance_chart(ds, VIEW))
    with c2:
        plot(charts.repayment_chart(ds, VIEW))
    for key in ("senior", "sub", "revolver"):
        tr = ds.tranches[key]
        if tr.closing.max() > 1e-9 or tr.additions.max() > 1e-9:
            st.markdown(f"**{tr.name}**" + (f" (commitment {money(A.debt.revolver_commitment)})" if key == "revolver" else ""))
            show(tr.to_frame(VIEW))
    st.subheader("Credit statistics")
    show(ds.credit_stats_frame(VIEW))
    cov = covenant_table(ds, A.debt, N)
    if not cov.empty:
        st.subheader("Covenant compliance (holding period)")
        show(cov)

# ----------------------------------------------------------- Exit and returns
with tabs[4]:
    c1, c2 = st.columns([1.15, 1])
    with c1:
        steps = [("Exit EV", ex.enterprise_value, "absolute"), ("Debt", -ex.debt_outstanding, "relative"),
                 ("Cash", ex.cash, "relative"), ("Exit costs", -ex.exit_fees, "relative")]
        if ex.mip_payout > 1e-9:
            steps.append(("MIP", -ex.mip_payout, "relative"))
        if ex.rollover_proceeds > 1e-9:
            steps.append(("Rollover holders", -ex.rollover_proceeds, "relative"))
        steps.append(("Sponsor proceeds", ex.sponsor_proceeds, "total"))
        plot(charts.waterfall(f"Exit bridge, year {N}: enterprise value to sponsor proceeds", steps))
    with c2:
        st.markdown("**Exit valuation**")
        show(pd.DataFrame({"Amount": dict([("Exit-year EBITDA", ex.exit_ebitda)] + ex.bridge())}))
        st.caption(f"Exit EV / EBITDA {mult(ex.exit_multiple)}; sponsor ownership {pct(ex.sponsor_ownership)}.")
    st.subheader("Sponsor returns")
    k = st.columns(6)
    k[0].metric("Equity invested", money(ret.equity_invested))
    k[1].metric("Total proceeds", money(ret.total_proceeds))
    k[2].metric("Equity profit", money(ret.profit))
    k[3].metric("MOIC", mult(ret.moic))
    k[4].metric("Gross IRR", pct(ret.irr))
    k[5].metric("Net IRR", pct(ret.net_irr) if ret.net_irr is not None else "n/a",
                help="Reported only when fund-level fees and carry are switched on under Advanced features.")
    c1, c2 = st.columns(2)
    with c1:
        plot(charts.sponsor_cash_flows(ret.dates, ret.cash_flows))
        st.caption(f"Gross IRR on annual periods {pct(ret.irr, 2)}; on dated cash flows (XIRR, actual/365) {pct(ret.xirr, 2)}. "
                   f"Debt repaid over the hold {money(ret.debt_repaid)}.")
    with c2:
        at = R.attribution
        steps = [(k_, v, "relative") for k_, v in at.components.items()] + [("Value created", at.total_value_creation, "total")]
        plot(charts.waterfall("Return attribution: total equity value creation", steps, height=300))
        st.caption(f"Investor-level vs company-level: sponsor IRR {pct(ret.irr)} vs unlevered company IRR {pct(ret.unlevered_irr)} "
                   f"(unlevered MOIC {mult(ret.unlevered_moic)}).")

# ---------------------------------------------------------------- Sensitivity
with tabs[5]:
    st.caption("Every cell reruns the full model: sources and uses, operating forecast, debt schedule, exit and returns. "
               "Grids are centred on the current base case; 'n/a' marks an infeasible structure. The EBITDA growth shift is applied through revenue growth at the assumed margins.")
    metric = st.radio("Metric", ["IRR", "MOIC"], horizontal=True, key="hm_metric")
    cols = st.columns(3)
    for col, (rp, cp) in zip(cols, HEATMAPS, strict=True):
        g_irr, g_moic = sensitivity_2d(A, rp, cp)
        g = g_irr if metric == "IRR" else g_moic
        centre = crit.target_irr if metric == "IRR" else crit.target_moic
        with col:
            plot(charts.heatmap(g, f"{PARAMETERS[rp]['label']} vs {PARAMETERS[cp]['label']}", metric, centre))
    st.caption(f"Colour is centred on the {'target IRR of ' + pct(crit.target_irr, 0) if metric == 'IRR' else 'target MOIC of ' + mult(crit.target_moic, 1)}: "
               "blue above, red below, grey at target.")
    st.subheader("Single-variable sensitivities")
    p = st.selectbox("Variable", list(PARAMETERS), format_func=lambda k_: PARAMETERS[k_]["label"], key="sens1d")
    one = sensitivity_1d(A, p)
    c1, c2 = st.columns([1.3, 1])
    with c1:
        disp = one.copy()
        for c_ in ("Sponsor equity", "Exit equity to sponsor", "Debt repaid"):
            disp[c_] = disp[c_].map(money)
        disp["Exit leverage (x)"] = disp["Exit leverage (x)"].map(mult)
        disp["MOIC"] = disp["MOIC"].map(mult)
        disp["IRR"] = disp["IRR"].map(pct)
        disp["Critical flags"] = disp["Critical flags"].map(lambda v: "n/a" if pd.isna(v) else f"{v:.0f}")
        show(disp, raw=True)
    with c2:
        plot(charts.simple_bars(list(one.index), one["IRR"].tolist(), f"Gross IRR: {PARAMETERS[p]['label']}", "IRR",
                                pct_axis=True, labels=[pct(v) for v in one["IRR"]]))

# ------------------------------------------------------------------ Scenarios
with tabs[6]:
    st.caption("Edit the adjustments applied on top of the current base assumptions. Each scenario is a full model rerun.")
    fields = [("Revenue growth (pts p.a.)", "growth_delta", 100), ("EBITDA margin (pts)", "margin_delta", 100),
              ("Interest rates (pts)", "rate_delta", 100), ("Entry senior leverage (turns)", "leverage_delta", 1),
              ("Exit multiple (turns)", "exit_multiple_delta", 1), ("Capex % revenue (pts)", "capex_delta", 100),
              ("NWC % revenue (pts)", "nwc_delta", 100)]
    base_tbl = pd.DataFrame({n: [getattr(DEFAULT_SCENARIOS[n], f) * s for _, f, s in fields] for n in DEFAULT_SCENARIOS},
                            index=[f[0] for f in fields])
    edited = st.data_editor(base_tbl, key="scen_editor", width="stretch",
                            column_config={n: st.column_config.NumberColumn(n, format="%.2f", step=0.05) for n in base_tbl.columns})
    new = {n: ScenarioSpec(n, **{f: float(edited.loc[lab, n]) / s for lab, f, s in fields}) for n in edited.columns}
    if new != ss.scen:
        ss.scen = new
        st.rerun()
    k = st.columns(3)
    ok = {n: o.result for n, o in SC.items() if o.result is not None}
    for n, o in SC.items():
        if o.result is None:
            st.error(f"{n} scenario is infeasible: {o.error}")
    with k[0]:
        plot(charts.scenario_bars({n: r_.returns.irr for n, r_ in ok.items()}, "Gross IRR by scenario", ".1%"))
    with k[1]:
        plot(charts.scenario_bars({n: r_.returns.moic for n, r_ in ok.items()}, "MOIC by scenario", ".2f"))
    with k[2]:
        plot(charts.scenario_lines({n: r_.debt.leverage[:N + 1] for n, r_ in ok.items()}, "Total debt / EBITDA"))
    show(scenario_summary(SC))
    st.subheader("Scenario risk flags")
    any_flag = False
    for n, r_ in ok.items():
        for f in r_.flags:
            if f.severity in ("critical", "warning"):
                any_flag = True
                (st.error if f.severity == "critical" else st.warning)(
                    f"**{n}** | {'Year ' + str(f.year) if f.year else 'Close'} | {f.message}")
    if not any_flag:
        st.success("No scenario breaches liquidity, debt-service or covenant tests.", icon=":material/check_circle:")

# ------------------------------------------------------------------- Advanced
with tabs[7]:
    st.caption("Optional features, separate from the baseline model. Each is reconciled by the checks under Risk and checks.")
    st.subheader("Exit timing comparison")
    et = exit_timing(A)
    c1, c2 = st.columns([1.2, 1])
    with c1:
        d = et.copy()
        for c_ in ("Exit EBITDA", "Exit EV", "Net debt at exit", "Sponsor proceeds"):
            d[c_] = d[c_].map(money)
        d["MOIC"], d["IRR"] = d["MOIC"].map(mult), d["IRR"].map(pct)
        show(d, raw=True)
        st.caption("MOIC typically rises with a longer hold while IRR decays as the same gain is spread over more years.")
    with c2:
        plot(charts.simple_bars([f"Year {y}" for y in et.index], et["IRR"].tolist(), "Gross IRR by exit year", "IRR",
                                pct_axis=True, labels=[pct(v) for v in et["IRR"]]))
    st.subheader("Dividend recapitalisation")
    if A.advanced.recap_year:
        base_nr = run_lbo(replace(A, advanced=replace(A.advanced, recap_year=None, recap_turns=0.0)))
        t = A.advanced.recap_year
        cmp = pd.DataFrame({
            "Without recap": {"Recap debt raised": 0.0, "Financing fees": 0.0, "Dividend to shareholders": 0.0,
                              f"Total debt / EBITDA (x), year {t}": float(base_nr.debt.leverage[t]),
                              "Net debt at exit": base_nr.exit.net_debt, "Sponsor exit proceeds": base_nr.exit.sponsor_proceeds,
                              "MOIC (x)": base_nr.returns.moic, "Gross IRR": base_nr.returns.irr},
            "With recap": {"Recap debt raised": float(ds.recap_proceeds[t]), "Financing fees": float(ds.recap_fees[t]),
                           "Dividend to shareholders": float(ds.dividends[t]),
                           f"Total debt / EBITDA (x), year {t}": float(ds.leverage[t]),
                           "Net debt at exit": ex.net_debt, "Sponsor exit proceeds": ex.sponsor_proceeds,
                           "MOIC (x)": ret.moic, "Gross IRR": ret.irr}})
        show(cmp)
        st.caption("Reconciliation: recap debt raised = financing fees + dividend. The recap moves cash earlier (higher IRR) "
                   "but adds fees and interest (lower MOIC) and re-levers the company.")
    else:
        st.info("Off. Enable under Advanced features in the sidebar to raise incremental senior debt and distribute it.")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Management rollover and incentive pool")
        show(pd.DataFrame({"Amount": {
            "Rollover equity at entry": su.rollover_equity, "Sponsor equity at entry": su.sponsor_equity,
            "Exit equity value": ex.equity_value, "Less: management incentive pool": -ex.mip_payout,
            "Rollover holders' proceeds": ex.rollover_proceeds, "Sponsor proceeds": ex.sponsor_proceeds}}))
        st.caption(f"Sponsor ownership {pct(su.sponsor_ownership)}. MIP = {pct(A.exit.mip_pct)} of exit equity above invested equity.")
    with c2:
        st.subheader("Gross to net returns")
        if ret.net_irr is not None:
            show(pd.DataFrame({"Value": {"Gross IRR": ret.irr, "Gross MOIC (x)": ret.moic, "Management fees paid": ret.fund_fees,
                                         "Carried interest": ret.carried_interest, "Net IRR": ret.net_irr, "Net MOIC (x)": ret.net_moic}}))
            st.caption("Deal-level approximation of fund economics: annual fee on invested equity; carry on profit after fees "
                       "with full catch-up, payable only if the hurdle is met.")
        else:
            st.info("Net IRR is not reported because fund-level fees and carry are not modelled. Enable them under Advanced features.")

# ------------------------------------------------------------ Risk and checks
with tabs[8]:
    st.subheader("Risk indicators (base case)")
    if R.flags:
        show(flags_frame(list(R.flags)), raw=True)
    else:
        st.success("No flags raised in the base case.", icon=":material/check_circle:")
    st.subheader("Investment assessment")
    st.markdown(f'<div class="le-verdict"><b>{ASSESS.verdict}</b><br>{ASSESS.summary}</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    for col, title, items in ((c1, "Supporting factors", ASSESS.supports), (c2, "Concerns", ASSESS.concerns),
                              (c3, "Conditions and mitigants", ASSESS.conditions)):
        with col:
            st.markdown(f"**{title}**")
            st.markdown("\n".join(f"- {i}" for i in items) if items else "- None")
    st.subheader("Model reconciliation checks")
    chk = R.checks()
    show(pd.DataFrame({"Check": list(chk), "Status": ["PASS" if ok_ else "FAIL" for ok_, _ in chk.values()],
                       "Difference": [f"{d_:.6f}" for _, d_ in chk.values()]}).set_index("Check"), raw=True)
    st.caption("A FAIL on 'Minimum cash maintained' is a genuine liquidity finding, not a model error.")

# --------------------------------------------------------------------- Export
with tabs[9]:
    st.caption("Exports are built from the current inputs. Rebuild after changing assumptions.")
    slug = "".join(ch if ch.isalnum() else "_" for ch in A.company.name)[:40].strip("_")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Excel LBO model**")
        st.caption("Thirteen sheets with live formulas, input/formula colour coding and model checks.")
        if st.button("Build Excel model", key="bx", width="stretch"):
            ss.xlsx = build_workbook(R, ss.scen)
        if ss.get("xlsx"):
            st.download_button("Download .xlsx", ss.xlsx, f"LeverEdge_LBO_{slug}.xlsx", width="stretch",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if A.debt.interest_method == "average":
            st.caption("Note: the workbook always uses beginning-balance interest; its returns will differ slightly from the app.")
    with c2:
        st.markdown("**Investment committee report**")
        st.caption("PDF memorandum; the assessment is derived from the model outputs.")
        if st.button("Build PDF report", key="bp_", width="stretch"):
            ss.pdf = build_ic_report(R, SC, ASSESS, crit)
        if ss.get("pdf"):
            st.download_button("Download .pdf", ss.pdf, f"LeverEdge_IC_{slug}.pdf", mime="application/pdf", width="stretch")
    with c3:
        st.markdown("**Assumptions and templates**")
        st.caption("Save the full assumption set, or get the historical-financials CSV template.")
        st.download_button("Download assumptions (.json)", json.dumps(A.to_dict(), indent=2), f"LeverEdge_assumptions_{slug}.json",
                           mime="application/json", width="stretch")
        st.download_button("Download historicals template (.csv)",
                           (ROOT / "data" / "historicals_template.csv").read_text(encoding="utf-8"),
                           "historicals_template.csv", mime="text/csv", width="stretch")
