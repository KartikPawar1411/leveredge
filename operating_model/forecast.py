"""Operating forecast (pre-financing): revenue through EBIT, capex and NWC.

Nothing here depends on the capital structure. Debt proceeds are a financing
item and never appear as operating income; interest and cash taxes are added
in the debt schedule, where the financing structure is known.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from financials.assumptions import HORIZON, CompanyData, OperatingAssumptions


@dataclass(frozen=True)
class OperatingForecast:
    """Arrays are length HORIZON + 1; index 0 is the LTM / closing period."""
    revenue: np.ndarray
    growth: np.ndarray
    cogs: np.ndarray          # zero in driver mode
    sga: np.ndarray           # zero in driver mode
    other_opex: np.ndarray    # zero in driver mode
    ebitda: np.ndarray
    margin: np.ndarray
    da: np.ndarray
    ebit: np.ndarray
    capex: np.ndarray
    nwc: np.ndarray
    delta_nwc: np.ndarray     # positive = investment in working capital (cash outflow)
    mode: str

    def to_frame(self, years: int = HORIZON) -> pd.DataFrame:
        cols = ["LTM"] + [f"Year {t}" for t in range(1, years + 1)]
        sl = slice(0, years + 1)
        rows: dict[str, np.ndarray] = {"Revenue": self.revenue[sl], "Revenue growth": self.growth[sl]}
        if self.mode == "detailed":
            rows["Cost of goods sold"] = self.cogs[sl]
            rows["SG&A"] = self.sga[sl]
            rows["Other operating costs"] = self.other_opex[sl]
        rows.update({
            "EBITDA": self.ebitda[sl], "EBITDA margin": self.margin[sl],
            "D&A": self.da[sl], "EBIT": self.ebit[sl],
            "Capital expenditure": self.capex[sl],
            "Net working capital": self.nwc[sl],
            "Increase in NWC": self.delta_nwc[sl],
        })
        return pd.DataFrame(rows, index=cols).T


def build_operating_forecast(company: CompanyData, op: OperatingAssumptions) -> OperatingForecast:
    n = HORIZON + 1
    nan = float("nan")
    revenue = np.zeros(n)
    growth = np.full(n, nan)
    revenue[0] = company.ltm_revenue
    for t in range(1, n):
        growth[t] = op.revenue_growth[t - 1]
        revenue[t] = revenue[t - 1] * (1.0 + growth[t])

    cogs, sga, other = np.zeros(n), np.zeros(n), np.zeros(n)
    ebitda = np.zeros(n)
    ebitda[0] = company.ltm_ebitda
    if op.mode == "detailed":
        cogs[1:] = revenue[1:] * np.array(op.cogs_pct)
        sga[1:] = revenue[1:] * np.array(op.sga_pct)
        other[1:] = op.other_opex
        ebitda[1:] = revenue[1:] - cogs[1:] - sga[1:] - other[1:]
    else:
        ebitda[1:] = revenue[1:] * np.array(op.ebitda_margin)

    da = np.zeros(n)
    da[1:] = revenue[1:] * op.da_pct_revenue
    capex = np.zeros(n)
    capex[1:] = revenue[1:] * op.capex_pct_revenue
    nwc = np.zeros(n)
    nwc[0] = company.net_working_capital
    nwc[1:] = revenue[1:] * op.nwc_pct_revenue
    delta_nwc = np.zeros(n)
    delta_nwc[1:] = np.diff(nwc)

    ebit = ebitda - da
    ebit[0] = nan
    da[0] = capex[0] = delta_nwc[0] = nan
    return OperatingForecast(
        revenue=revenue, growth=growth, cogs=cogs, sga=sga, other_opex=other,
        ebitda=ebitda, margin=ebitda / revenue, da=da, ebit=ebit,
        capex=capex, nwc=nwc, delta_nwc=delta_nwc, mode=op.mode,
    )
