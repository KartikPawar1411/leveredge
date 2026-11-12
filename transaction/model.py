"""Model orchestrator: assumptions in, a fully reconciled LBO result out.

This is the single entry point used by the UI, the exporters, the scenario
engine and the sensitivity engine. It contains no presentation logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from capital_structure.sources_uses import SourcesUses, build_sources_uses
from debt.schedule import DebtSchedule, build_debt_schedule
from financials.assumptions import LBOAssumptions
from operating_model.forecast import OperatingForecast, build_operating_forecast
from returns.exit import ExitValuation, exit_valuation
from returns.sponsor import ReturnAttribution, SponsorReturns, return_attribution, sponsor_returns
from risk.flags import RiskFlag, evaluate_flags, has_critical
from transaction.entry import EntryValuation, entry_valuation


@dataclass(frozen=True)
class LBOResult:
    assumptions: LBOAssumptions
    entry: EntryValuation
    sources_uses: SourcesUses
    operating: OperatingForecast
    debt: DebtSchedule
    exit: ExitValuation
    returns: SponsorReturns
    attribution: ReturnAttribution
    flags: tuple[RiskFlag, ...]

    @property
    def has_critical_flags(self) -> bool:
        return has_critical(list(self.flags))

    def checks(self) -> dict[str, tuple[bool, float]]:
        """Internal reconciliation checks: name -> (passed, difference)."""
        su, ds, ex, e = self.sources_uses, self.debt, self.exit, self.entry
        n = ex.exit_year
        tol = 1e-6
        out: dict[str, tuple[bool, float]] = {}
        out["Sources equal uses"] = (abs(su.imbalance) < tol, su.imbalance)
        d = e.enterprise_value - e.existing_debt + e.existing_cash - e.equity_purchase_price
        out["Entry EV-to-equity bridge"] = (abs(d) < tol, d)
        d = su.total_equity - (e.enterprise_value + su.total_fees - su.net_debt)
        out["Invested equity = EV + fees - closing net debt"] = (abs(d) < tol, d)
        mn = min(float(t.closing.min()) for t in ds.tranches.values())
        out["No negative debt balances"] = (mn > -tol, mn)
        rev = ds.tranches["revolver"]
        d = float(rev.closing.max()) - self.assumptions.debt.revolver_commitment
        out["Revolver within commitment"] = (d < tol, max(d, 0.0))
        roll = 0.0
        for tr in ds.tranches.values():
            roll += float(abs(tr.opening[1:] + tr.additions[1:] - tr.mandatory[1:] - tr.optional[1:] - tr.closing[1:]).max())
        out["Debt roll-forward"] = (roll < tol, roll)
        cash = float(abs(
            ds.cash_opening[1:] + ds.cfads[1:] - ds.total_interest[1:] - ds.total_mandatory[1:]
            - ds.total_optional[1:] + rev.additions[1:] + ds.recap_proceeds[1:] - ds.recap_fees[1:]
            - ds.dividends[1:] - ds.cash_closing[1:]).max())
        out["Cash roll-forward"] = (cash < tol, cash)
        d = ex.enterprise_value - ex.net_debt - ex.exit_fees - ex.equity_value_unfloored
        out["Exit EV-to-equity bridge"] = (abs(d) < tol, d)
        d = self.attribution.residual
        out["Return attribution sums to value creation"] = (abs(d) < tol, d)
        short = float(ds.liquidity_shortfall[1:n + 1].sum())
        out["Minimum cash maintained through exit"] = (short < tol, short)
        return out


def run_lbo(assumptions: LBOAssumptions) -> LBOResult:
    """Run the full model. Raises ValidationError for infeasible inputs."""
    a = assumptions.validate()
    entry = entry_valuation(a.company, a.transaction)
    su = build_sources_uses(a.company, a.transaction, a.debt, entry)
    op = build_operating_forecast(a.company, a.operating)
    ds = build_debt_schedule(op, su, a.debt, a.operating.tax_rate, a.transaction.min_cash,
                             a.transaction.financing_fee_pct, a.advanced)
    ex = exit_valuation(op, ds, su, a.exit)
    ret = sponsor_returns(entry, su, ds, ex, a.transaction.close_date, a.advanced)
    attr = return_attribution(entry, su, ds, ex)
    flags = evaluate_flags(su, ds, ex, a.debt, a.transaction)
    return LBOResult(a, entry, su, op, ds, ex, ret, attr, tuple(flags))
