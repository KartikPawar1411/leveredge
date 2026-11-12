"""Bull / Base / Bear scenario engine. Each scenario is a full model rerun."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import pandas as pd

from financials.assumptions import MAX_EXIT_YEAR, MIN_EXIT_YEAR, LBOAssumptions, shift
from financials.errors import LeverEdgeError
from transaction.model import LBOResult, run_lbo


@dataclass(frozen=True)
class ScenarioSpec:
    """Adjustments applied on top of the base-case assumptions."""
    name: str
    growth_delta: float = 0.0          # added to revenue growth in every year
    margin_delta: float = 0.0          # added to EBITDA margin in every year
    rate_delta: float = 0.0            # added to every debt tranche's interest rate
    leverage_delta: float = 0.0        # turns of senior debt added at entry
    exit_multiple_delta: float = 0.0   # turns added to the exit multiple
    capex_delta: float = 0.0           # added to capex % of revenue
    nwc_delta: float = 0.0             # added to NWC % of revenue


DEFAULT_SCENARIOS: dict[str, ScenarioSpec] = {
    "Bull": ScenarioSpec("Bull", growth_delta=0.02, margin_delta=0.01, rate_delta=-0.005,
                         exit_multiple_delta=1.0, capex_delta=-0.0025, nwc_delta=-0.01),
    "Base": ScenarioSpec("Base"),
    "Bear": ScenarioSpec("Bear", growth_delta=-0.04, margin_delta=-0.025, rate_delta=0.015,
                         exit_multiple_delta=-1.5, capex_delta=0.005, nwc_delta=0.02),
}


def apply_scenario(a: LBOAssumptions, s: ScenarioSpec) -> LBOAssumptions:
    o, d = a.operating, a.debt
    if o.mode == "detailed":
        op = replace(o, revenue_growth=shift(o.revenue_growth, s.growth_delta),
                     cogs_pct=shift(o.cogs_pct, -s.margin_delta))
    else:
        op = replace(o, revenue_growth=shift(o.revenue_growth, s.growth_delta),
                     ebitda_margin=shift(o.ebitda_margin, s.margin_delta))
    op = replace(op, capex_pct_revenue=max(o.capex_pct_revenue + s.capex_delta, 0.0),
                 nwc_pct_revenue=o.nwc_pct_revenue + s.nwc_delta)
    dbt = replace(d,
                  senior_leverage=max(d.senior_leverage + s.leverage_delta, 0.0),
                  senior_rate=max(d.senior_rate + s.rate_delta, 0.0),
                  sub_rate=max(d.sub_rate + s.rate_delta, 0.0),
                  revolver_rate=max(d.revolver_rate + s.rate_delta, 0.0))
    ex = replace(a.exit, exit_multiple=max(a.exit.exit_multiple + s.exit_multiple_delta, 0.1))
    return replace(a, operating=op, debt=dbt, exit=ex)


@dataclass(frozen=True)
class ScenarioOutcome:
    spec: ScenarioSpec
    result: LBOResult | None
    error: str | None = None


def run_scenarios(a: LBOAssumptions, specs: Mapping[str, ScenarioSpec] | None = None) -> dict[str, ScenarioOutcome]:
    out: dict[str, ScenarioOutcome] = {}
    for name, spec in (specs or DEFAULT_SCENARIOS).items():
        try:
            out[name] = ScenarioOutcome(spec, run_lbo(apply_scenario(a, spec)))
        except LeverEdgeError as exc:
            out[name] = ScenarioOutcome(spec, None, str(exc))
    return out


def scenario_summary(outcomes: Mapping[str, ScenarioOutcome]) -> pd.DataFrame:
    """Side-by-side comparison of the outputs that matter to an IC."""
    cols: dict[str, dict[str, float | str]] = {}
    for name, oc in outcomes.items():
        r = oc.result
        if r is None:
            cols[name] = {"Status": f"Infeasible: {oc.error}"}
            continue
        n = r.exit.exit_year
        ds = r.debt
        crit = sum(f.severity == "critical" for f in r.flags)
        cols[name] = {
            "Sponsor equity invested": r.sources_uses.sponsor_equity,
            "Closing leverage (x)": r.sources_uses.total_leverage,
            f"Exit EBITDA (Year {n})": r.exit.exit_ebitda,
            "Cumulative CFADS": float(ds.cfads[1:n + 1].sum()),
            "Cumulative cash interest": float(ds.total_interest[1:n + 1].sum()),
            "Debt repaid": r.returns.debt_repaid,
            "Exit net debt": r.exit.net_debt,
            "Exit leverage (x)": float(ds.leverage[n]),
            "Peak leverage (x)": float(ds.leverage[0:n + 1].max()),
            "Minimum DSCR (x)": float(ds.dscr[1:n + 1].min()),
            "Minimum cash balance": float(ds.cash_closing[1:n + 1].min()),
            "Exit enterprise value": r.exit.enterprise_value,
            "Exit equity to sponsor": r.exit.sponsor_proceeds,
            "MOIC (x)": r.returns.moic,
            "Gross IRR": r.returns.irr,
            "Critical risk flags": float(crit),
            "Status": "Critical flags" if crit else ("Warnings" if r.flags and any(f.severity == "warning" for f in r.flags) else "Clean"),
        }
    return pd.DataFrame(cols)


def exit_timing(a: LBOAssumptions) -> pd.DataFrame:
    """Returns for every permitted exit year (advanced: exit timing comparison)."""
    rows = []
    for y in range(MIN_EXIT_YEAR, MAX_EXIT_YEAR + 1):
        try:
            r = run_lbo(replace(a, exit=replace(a.exit, exit_year=y)))
            rows.append({"Exit year": y, "Exit EBITDA": r.exit.exit_ebitda, "Exit EV": r.exit.enterprise_value,
                         "Net debt at exit": r.exit.net_debt, "Sponsor proceeds": r.exit.sponsor_proceeds,
                         "MOIC": r.returns.moic, "IRR": r.returns.irr})
        except LeverEdgeError:
            rows.append({"Exit year": y})
    return pd.DataFrame(rows).set_index("Exit year")
