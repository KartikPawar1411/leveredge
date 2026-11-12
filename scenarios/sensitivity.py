"""Sensitivity analysis. Every cell is a complete rerun of the LBO model
(sources and uses, operating forecast, debt schedule, exit and returns);
no shortcut multipliers are applied to IRR or MOIC.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

import numpy as np
import pandas as pd

from financials.assumptions import MAX_EXIT_YEAR, MIN_EXIT_YEAR, LBOAssumptions, shift
from financials.errors import LeverEdgeError
from transaction.model import LBOResult, run_lbo

Applier = Callable[[LBOAssumptions, float], LBOAssumptions]


def _entry_multiple(a: LBOAssumptions, v: float) -> LBOAssumptions:
    return replace(a, transaction=replace(a.transaction, entry_multiple=v))


def _exit_multiple(a: LBOAssumptions, v: float) -> LBOAssumptions:
    return replace(a, exit=replace(a.exit, exit_multiple=v))


def _growth(a: LBOAssumptions, v: float) -> LBOAssumptions:
    return replace(a, operating=replace(a.operating, revenue_growth=shift(a.operating.revenue_growth, v)))


def _margin(a: LBOAssumptions, v: float) -> LBOAssumptions:
    o = a.operating
    if o.mode == "detailed":  # a margin gain is modelled as an equal reduction in COGS %
        return replace(a, operating=replace(o, cogs_pct=shift(o.cogs_pct, -v)))
    return replace(a, operating=replace(o, ebitda_margin=shift(o.ebitda_margin, v)))


def _leverage(a: LBOAssumptions, v: float) -> LBOAssumptions:
    """Set total term leverage to v turns, keeping the senior / sub mix."""
    d = a.debt
    base = d.senior_leverage + d.sub_leverage
    w = d.senior_leverage / base if base > 0 else 1.0
    return replace(a, debt=replace(d, senior_leverage=v * w, sub_leverage=v * (1 - w)))


def _rates(a: LBOAssumptions, v: float) -> LBOAssumptions:
    d = a.debt
    return replace(a, debt=replace(d, senior_rate=max(d.senior_rate + v, 0.0), sub_rate=max(d.sub_rate + v, 0.0),
                                   revolver_rate=max(d.revolver_rate + v, 0.0)))


def _exit_year(a: LBOAssumptions, v: float) -> LBOAssumptions:
    return replace(a, exit=replace(a.exit, exit_year=int(round(v))))


PARAMETERS: dict[str, dict] = {
    "entry_multiple": {"label": "Entry EV/EBITDA", "apply": _entry_multiple, "fmt": "{:.1f}x", "kind": "absolute"},
    "exit_multiple": {"label": "Exit EV/EBITDA", "apply": _exit_multiple, "fmt": "{:.1f}x", "kind": "absolute"},
    "growth": {"label": "EBITDA growth shift", "apply": _growth, "fmt": "{:+.1%}", "kind": "delta"},
    "leverage": {"label": "Total leverage at close", "apply": _leverage, "fmt": "{:.2f}x", "kind": "absolute"},
    "rates": {"label": "Interest rate shift", "apply": _rates, "fmt": "{:+.2%}", "kind": "delta"},
    "exit_year": {"label": "Holding period (years)", "apply": _exit_year, "fmt": "{:.0f}", "kind": "absolute"},
    "margin": {"label": "EBITDA margin shift", "apply": _margin, "fmt": "{:+.1%}", "kind": "delta"},
}


def base_value(a: LBOAssumptions, param: str) -> float:
    return {
        "entry_multiple": a.transaction.entry_multiple,
        "exit_multiple": a.exit.exit_multiple,
        "leverage": a.debt.senior_leverage + a.debt.sub_leverage,
        "exit_year": float(a.exit.exit_year),
    }.get(param, 0.0)


def default_grid(a: LBOAssumptions, param: str, n: int = 7) -> list[float]:
    """A grid of n values centred on the base case."""
    b = base_value(a, param)
    half = n // 2
    if param == "exit_year":
        return [float(y) for y in range(MIN_EXIT_YEAR, MAX_EXIT_YEAR + 1)]
    step = {"entry_multiple": 0.5, "exit_multiple": 0.5, "leverage": 0.5,
            "growth": 0.01, "rates": 0.005, "margin": 0.01}[param]
    vals = [round(b + (i - half) * step, 6) for i in range(n)]
    if PARAMETERS[param]["kind"] == "absolute":
        vals = [v for v in vals if v > 0] if param != "leverage" else [v for v in vals if v >= 0]
    return vals


def run_with(a: LBOAssumptions, changes: dict[str, float]) -> LBOResult | None:
    """Rerun the model with parameter overrides; None when infeasible."""
    try:
        for p, v in changes.items():
            a = PARAMETERS[p]["apply"](a, v)
        return run_lbo(a)
    except LeverEdgeError:
        return None


def sensitivity_1d(a: LBOAssumptions, param: str, values: Sequence[float] | None = None) -> pd.DataFrame:
    values = list(values) if values is not None else default_grid(a, param)
    fmt = PARAMETERS[param]["fmt"]
    rows = []
    for v in values:
        r = run_with(a, {param: v})
        nan = float("nan")
        rows.append({
            PARAMETERS[param]["label"]: fmt.format(v),
            "Sponsor equity": r.sources_uses.sponsor_equity if r else nan,
            "Exit equity to sponsor": r.exit.sponsor_proceeds if r else nan,
            "Debt repaid": r.returns.debt_repaid if r else nan,
            "Exit leverage (x)": float(r.debt.leverage[r.exit.exit_year]) if r else nan,
            "MOIC": r.returns.moic if r else nan,
            "IRR": r.returns.irr if r else nan,
            "Critical flags": sum(f.severity == "critical" for f in r.flags) if r else nan,
        })
    return pd.DataFrame(rows).set_index(PARAMETERS[param]["label"])


def sensitivity_2d(
    a: LBOAssumptions,
    row_param: str,
    col_param: str,
    row_values: Sequence[float] | None = None,
    col_values: Sequence[float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (IRR grid, MOIC grid); rows = row_param, columns = col_param."""
    rv = list(row_values) if row_values is not None else default_grid(a, row_param)
    cv = list(col_values) if col_values is not None else default_grid(a, col_param)
    irr_g = np.full((len(rv), len(cv)), np.nan)
    moic_g = np.full((len(rv), len(cv)), np.nan)
    for i, r in enumerate(rv):
        for j, c in enumerate(cv):
            res = run_with(a, {row_param: r, col_param: c})
            if res is not None:
                irr_g[i, j], moic_g[i, j] = res.returns.irr, res.returns.moic
    idx = pd.Index([PARAMETERS[row_param]["fmt"].format(v) for v in rv], name=PARAMETERS[row_param]["label"])
    cols = pd.Index([PARAMETERS[col_param]["fmt"].format(v) for v in cv], name=PARAMETERS[col_param]["label"])
    return pd.DataFrame(irr_g, idx, cols), pd.DataFrame(moic_g, idx, cols)


HEATMAPS: list[tuple[str, str]] = [
    ("entry_multiple", "exit_multiple"),
    ("leverage", "exit_multiple"),
    ("growth", "exit_multiple"),
]
