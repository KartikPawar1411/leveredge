"""Risk indicators and covenant tests, evaluated over the holding period."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from capital_structure.sources_uses import SourcesUses
from debt.schedule import DebtSchedule
from financials.assumptions import DebtAssumptions, TransactionAssumptions
from returns.exit import ExitValuation

CRITICAL, WARNING, INFO = "critical", "warning", "info"
TOL = 1e-6


@dataclass(frozen=True)
class RiskFlag:
    code: str
    severity: str
    message: str
    year: int | None = None


def covenant_table(ds: DebtSchedule, debt: DebtAssumptions, years: int) -> pd.DataFrame:
    """Covenant compliance by year; empty frame when no covenants are modelled."""
    rows: dict[str, list] = {}
    idx = [f"Year {t}" for t in range(1, years + 1)]
    if debt.covenant_max_leverage is not None:
        lim = debt.covenant_max_leverage
        act = [float(ds.leverage[t]) for t in range(1, years + 1)]
        rows["Total leverage (x)"] = act
        rows["Maximum permitted (x)"] = [lim] * years
        rows["Leverage headroom (x)"] = [lim - a for a in act]
        rows["Leverage covenant"] = ["Breach" if a > lim + TOL else "Pass" for a in act]
    if debt.covenant_min_interest_coverage is not None:
        lim = debt.covenant_min_interest_coverage
        act = [float(ds.interest_coverage[t]) for t in range(1, years + 1)]
        rows["Interest coverage (x)"] = act
        rows["Minimum required (x)"] = [lim] * years
        rows["Coverage headroom (x)"] = [a - lim for a in act]
        rows["Coverage covenant"] = ["Breach" if a < lim - TOL else "Pass" for a in act]
    return pd.DataFrame(rows, index=idx).T if rows else pd.DataFrame()


def evaluate_flags(
    su: SourcesUses,
    ds: DebtSchedule,
    ex: ExitValuation,
    debt: DebtAssumptions,
    txn: TransactionAssumptions,
) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    n = ex.exit_year

    if su.total_leverage > debt.max_total_leverage + TOL:
        flags.append(RiskFlag("MAX_LEVERAGE", WARNING,
                              f"Closing leverage of {su.total_leverage:.2f}x exceeds the {debt.max_total_leverage:.2f}x structuring limit."))
    if su.equity_contribution_pct < txn.min_equity_contribution_pct - TOL:
        flags.append(RiskFlag("MIN_EQUITY", WARNING,
                              f"Equity contribution of {su.equity_contribution_pct:.1%} is below the "
                              f"{txn.min_equity_contribution_pct:.0%} minimum."))

    maturities = {"senior": debt.senior_maturity, "sub": debt.sub_maturity}
    for t in range(1, n + 1):
        cash, short = float(ds.cash_closing[t]), float(ds.liquidity_shortfall[t])
        matured = [k for k, m in maturities.items() if m == t and ds.tranches[k].opening[t] > TOL]
        if cash < -TOL:
            flags.append(RiskFlag("NEGATIVE_CASH", CRITICAL,
                                  f"Cash balance is negative ({cash:,.1f}) after exhausting the revolver.", t))
        if short > TOL:
            flags.append(RiskFlag("LIQUIDITY_SHORTFALL", CRITICAL,
                                  f"Cash is {short:,.1f} below the minimum balance with the revolver fully drawn.", t))
            if matured:
                names = " and ".join(ds.tranches[k].name.lower() for k in matured)
                flags.append(RiskFlag("UNFUNDED_MATURITY", CRITICAL,
                                      f"Maturity of the {names} cannot be funded from cash and available revolver; refinancing required.", t))
        elif matured and ds.tranches["revolver"].additions[t] > TOL:
            flags.append(RiskFlag("MATURITY_ON_REVOLVER", WARNING,
                                  "Debt maturity is partly funded by drawing the revolver.", t))
        if ds.dscr[t] < 1.0 - TOL:
            flags.append(RiskFlag("DEBT_SERVICE", CRITICAL if short > TOL else WARNING,
                                  f"Debt service coverage of {ds.dscr[t]:.2f}x: operating cash flow does not cover interest and scheduled repayments.", t))
        if debt.covenant_max_leverage is not None and ds.leverage[t] > debt.covenant_max_leverage + TOL:
            flags.append(RiskFlag("COVENANT_LEVERAGE", CRITICAL,
                                  f"Leverage covenant breach: {ds.leverage[t]:.2f}x vs {debt.covenant_max_leverage:.2f}x maximum.", t))
        if (debt.covenant_min_interest_coverage is not None
                and ds.interest_coverage[t] < debt.covenant_min_interest_coverage - TOL):
            flags.append(RiskFlag("COVENANT_COVERAGE", CRITICAL,
                                  f"Interest coverage covenant breach: {ds.interest_coverage[t]:.2f}x vs "
                                  f"{debt.covenant_min_interest_coverage:.2f}x minimum.", t))
        if ds.tranches["revolver"].closing[t] > TOL and short <= TOL:
            flags.append(RiskFlag("REVOLVER_DRAWN", INFO,
                                  f"Revolver drawn to {ds.tranches['revolver'].closing[t]:,.1f} to maintain minimum cash.", t))

    if ex.equity_wiped_out:
        flags.append(RiskFlag("EQUITY_WIPED_OUT", CRITICAL,
                              "Exit enterprise value does not cover net debt and exit costs; equity value is zero.", n))
    return flags


def has_critical(flags: list[RiskFlag]) -> bool:
    return any(f.severity == CRITICAL for f in flags)


def flags_frame(flags: list[RiskFlag]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Severity": f.severity.upper(), "Year": f.year if f.year is not None else "Close",
          "Indicator": f.code.replace("_", " ").title(), "Detail": f.message} for f in flags],
        columns=["Severity", "Year", "Indicator", "Detail"],
    )
