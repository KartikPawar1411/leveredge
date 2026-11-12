"""Exit valuation and the exit enterprise-to-equity bridge."""
from __future__ import annotations

from dataclasses import dataclass

from capital_structure.sources_uses import SourcesUses
from debt.schedule import DebtSchedule
from financials.assumptions import ExitAssumptions
from operating_model.forecast import OperatingForecast


@dataclass(frozen=True)
class ExitValuation:
    exit_year: int
    exit_ebitda: float
    exit_multiple: float
    enterprise_value: float
    debt_outstanding: float
    cash: float
    net_debt: float
    exit_fees: float
    equity_value_unfloored: float   # can be negative: debt + fees exceed EV
    equity_value: float             # floored at zero (limited liability)
    mip_payout: float
    equity_to_investors: float      # after MIP, shared by sponsor and rollover holders
    sponsor_ownership: float
    sponsor_proceeds: float
    rollover_proceeds: float

    @property
    def equity_wiped_out(self) -> bool:
        return self.equity_value_unfloored <= 0

    def bridge(self) -> list[tuple[str, float]]:
        return [
            ("Exit enterprise value", self.enterprise_value),
            ("Less: debt outstanding", -self.debt_outstanding),
            ("Plus: cash", self.cash),
            ("Less: exit transaction expenses", -self.exit_fees),
            ("Equity value at exit", self.equity_value),
            ("Less: management incentive pool", -self.mip_payout),
            ("Equity available to investors", self.equity_to_investors),
            ("Sponsor proceeds", self.sponsor_proceeds),
        ]


def exit_valuation(op: OperatingForecast, ds: DebtSchedule, su: SourcesUses, ex: ExitAssumptions) -> ExitValuation:
    """Exit EV = exit multiple x exit-year EBITDA.

    Equity value = EV - (debt - cash) - exit expenses.
    MIP (optional): options struck at the entry equity value, i.e. management
    receives mip_pct of equity value created above total invested equity.
    Remaining equity is split pro rata between sponsor and rollover holders.
    """
    n = ex.exit_year
    ebitda = float(op.ebitda[n])
    ev = ex.exit_multiple * ebitda
    debt = float(ds.total_debt[n])
    cash = float(ds.cash_closing[n])
    fees = ex.exit_fee_pct * ev
    raw = ev - (debt - cash) - fees
    equity = max(raw, 0.0)
    mip = ex.mip_pct * max(equity - su.total_equity, 0.0)
    dist = equity - mip
    own = su.sponsor_ownership
    return ExitValuation(
        exit_year=n, exit_ebitda=ebitda, exit_multiple=ex.exit_multiple, enterprise_value=ev,
        debt_outstanding=debt, cash=cash, net_debt=debt - cash, exit_fees=fees,
        equity_value_unfloored=raw, equity_value=equity, mip_payout=mip,
        equity_to_investors=dist, sponsor_ownership=own,
        sponsor_proceeds=dist * own, rollover_proceeds=dist * (1.0 - own),
    )
