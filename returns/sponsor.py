"""Sponsor (investor-level) returns, company-level returns and attribution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from capital_structure.sources_uses import SourcesUses
from debt.schedule import DebtSchedule
from financials.assumptions import AdvancedAssumptions
from returns.exit import ExitValuation
from returns.irr import anniversary, irr, xirr
from transaction.entry import EntryValuation


@dataclass(frozen=True)
class SponsorReturns:
    equity_invested: float
    dates: tuple[date, ...]
    cash_flows: tuple[float, ...]        # year 0 .. exit year, sponsor perspective
    distributions: tuple[float, ...]     # interim sponsor dividends by year
    exit_proceeds: float
    total_proceeds: float
    profit: float
    moic: float
    irr: float                           # gross IRR, annual periods
    xirr: float                          # gross IRR, dated cash flows (act/365)
    unlevered_irr: float                 # company-level (debt-free) return
    unlevered_moic: float
    debt_repaid: float                   # gross principal repaid over the hold
    net_debt_reduction: float
    net_irr: float | None = None      # only when fund-level fees are modelled
    net_moic: float | None = None
    fund_fees: float = 0.0
    carried_interest: float = 0.0

    @property
    def leverage_uplift(self) -> float:
        """Gross sponsor IRR minus the company-level unlevered IRR."""
        return self.irr - self.unlevered_irr


def sponsor_returns(
    entry: EntryValuation,
    su: SourcesUses,
    ds: DebtSchedule,
    ex: ExitValuation,
    close_date: date,
    advanced: AdvancedAssumptions | None = None,
) -> SponsorReturns:
    adv = advanced or AdvancedAssumptions()
    n = ex.exit_year
    own = su.sponsor_ownership
    dists = [float(ds.dividends[t]) * own for t in range(0, n + 1)]
    flows = [-su.sponsor_equity] + [dists[t] for t in range(1, n + 1)]
    flows[n] += ex.sponsor_proceeds
    dates = tuple(anniversary(close_date, t) for t in range(n + 1))
    total = sum(dists) + ex.sponsor_proceeds

    # Company-level return: buy the whole enterprise with no debt, collect
    # unlevered free cash flow, sell at the same exit EV. No financing fees.
    u_flows = [-(entry.enterprise_value + su.transaction_fees)]
    u_flows += [float(ds.unlevered_fcf[t]) for t in range(1, n + 1)]
    u_flows[n] += ex.enterprise_value - ex.exit_fees
    u_moic = sum(u_flows[1:]) / -u_flows[0]

    sl = slice(1, n + 1)
    repaid = float(ds.total_mandatory[sl].sum() + ds.total_optional[sl].sum())

    net_irr = net_moic = None
    fees_total = carry = 0.0
    if adv.model_net_returns:
        # Deal-level approximation of LP economics: annual management fee on
        # invested equity, then carried interest on profit after fees with a
        # full catch-up, payable only if the pre-carry return clears the hurdle.
        fee = adv.mgmt_fee_pct * su.sponsor_equity
        pre = list(flows)
        for t in range(1, n + 1):
            pre[t] -= fee
        fees_total = fee * n
        profit_after_fees = sum(pre)
        if profit_after_fees > 0 and irr(pre) >= adv.hurdle_rate:
            carry = adv.carry_pct * profit_after_fees
        pre[n] -= carry
        # fees are additional LP outlays; treat them as invested capital for MOIC
        outlay = su.sponsor_equity + fees_total
        net_irr = irr(pre)
        net_moic = (total - carry) / outlay

    return SponsorReturns(
        equity_invested=su.sponsor_equity, dates=dates, cash_flows=tuple(flows),
        distributions=tuple(dists), exit_proceeds=ex.sponsor_proceeds, total_proceeds=total,
        profit=total - su.sponsor_equity, moic=total / su.sponsor_equity,
        irr=irr(flows), xirr=xirr(dates, flows),
        unlevered_irr=irr(u_flows), unlevered_moic=u_moic,
        debt_repaid=repaid, net_debt_reduction=float(ds.net_debt[0] - ds.net_debt[n]),
        net_irr=net_irr, net_moic=net_moic, fund_fees=fees_total, carried_interest=carry,
    )


@dataclass(frozen=True)
class ReturnAttribution:
    """Decomposition of total equity value creation (all shareholders).

    exit equity + dividends - invested equity
        = EBITDA growth + multiple expansion + deleveraging - fees (+ floor adj.)
    """
    ebitda_growth: float
    multiple_expansion: float
    deleveraging: float           # net debt reduction plus cash distributed
    fees: float                   # entry + exit transaction costs (negative)
    floor_adjustment: float       # non-zero only when equity is wiped out
    total_value_creation: float

    @property
    def components(self) -> dict[str, float]:
        d = {
            "EBITDA growth": self.ebitda_growth,
            "Multiple expansion": self.multiple_expansion,
            "Debt paydown & cash generation": self.deleveraging,
            "Transaction costs": self.fees,
        }
        if abs(self.floor_adjustment) > 1e-9:
            d["Limited-liability floor"] = self.floor_adjustment
        return d

    @property
    def residual(self) -> float:
        return self.total_value_creation - sum(self.components.values())


def return_attribution(entry: EntryValuation, su: SourcesUses, ds: DebtSchedule, ex: ExitValuation) -> ReturnAttribution:
    n = ex.exit_year
    dividends = float(np.sum(ds.dividends[1:n + 1]))
    ebitda_growth = (ex.exit_ebitda - entry.entry_ebitda) * entry.entry_multiple
    multiple = (ex.exit_multiple - entry.entry_multiple) * ex.exit_ebitda
    delever = (su.net_debt - ex.net_debt) + dividends
    fees = -(su.total_fees + ex.exit_fees)
    floor_adj = ex.equity_value - ex.equity_value_unfloored
    total = ex.equity_value + dividends - su.total_equity
    return ReturnAttribution(ebitda_growth, multiple, delever, fees, floor_adj, total)
