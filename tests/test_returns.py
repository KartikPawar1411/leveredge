from __future__ import annotations

from datetime import date

import pytest

from financials.errors import ModelError
from returns.irr import irr, npv, xirr
from tests.conftest import with_
from transaction.model import run_lbo


# ---------------------------------------------------------------- IRR maths
def test_irr_closed_form_cases():
    assert irr([-100, 0, 0, 0, 0, 200]) == pytest.approx(2 ** 0.2 - 1)
    assert irr([-100, 110]) == pytest.approx(0.10)
    assert irr([-100, 60, 60]) == pytest.approx(0.130662386, abs=1e-8)  # root of 100x^2 - 60x - 60, x = 1 + r
    assert irr([-100, 50]) == pytest.approx(-0.5)
    assert irr([-100, 0, 0]) == -1.0


def test_irr_makes_npv_zero():
    flows = [-493.75, 0, 30, 0, 12.5, 900]
    assert npv(irr(flows), flows) == pytest.approx(0.0, abs=1e-7)


def test_irr_undefined_without_investment():
    with pytest.raises(ModelError):
        irr([100, 50])


def test_xirr_matches_hand_calculation():
    # 100 doubling over exactly 730 days: (1 + r)^(730/365) = 2
    assert xirr([date(2025, 1, 1), date(2027, 1, 1)], [-100, 200]) == pytest.approx(2 ** 0.5 - 1)
    # irregular: 100 -> 110 after 182 days
    assert xirr([date(2025, 1, 1), date(2025, 7, 2)], [-100, 110]) == pytest.approx(1.1 ** (365 / 182) - 1)


# ---------------------------------------------------------------- model returns
def test_hand_case_exit_and_returns(simple):
    r = run_lbo(simple)
    ex, ret = r.exit, r.returns
    assert ex.exit_ebitda == pytest.approx(200.0)
    assert ex.enterprise_value == pytest.approx(1600.0)
    assert ex.debt_outstanding == pytest.approx(557.703125)
    assert ex.net_debt == pytest.approx(507.703125)
    assert ex.equity_value == pytest.approx(1092.296875)
    assert ret.moic == pytest.approx(1092.296875 / 850.0)
    assert ret.irr == pytest.approx((1092.296875 / 850.0) ** (1 / 3) - 1)
    assert ret.profit == pytest.approx(242.296875)
    assert ret.debt_repaid == pytest.approx(75.0 + 80.625 + 86.671875)


def test_exit_equity_reflects_remaining_debt(demo):
    r = run_lbo(demo)
    ex, ds, n = r.exit, r.debt, 5
    assert ex.debt_outstanding == pytest.approx(sum(t.closing[n] for t in ds.tranches.values()))
    assert ex.equity_value == pytest.approx(ex.enterprise_value - ex.debt_outstanding + ex.cash - ex.exit_fees)
    # one more unit of unpaid debt is one less unit of equity: remove the sweep and compare
    r2 = run_lbo(with_(demo, debt=dict(cash_sweep_pct=0.0)))
    d_net_debt = r2.exit.net_debt - ex.net_debt
    assert d_net_debt > 0
    assert ex.equity_value - r2.exit.equity_value == pytest.approx(d_net_debt)


def test_moic_and_irr_definitions(demo):
    ret = run_lbo(demo).returns
    assert ret.cash_flows[0] == pytest.approx(-493.75)
    assert ret.moic == pytest.approx(sum(ret.cash_flows[1:]) / 493.75)
    assert npv(ret.irr, ret.cash_flows) == pytest.approx(0.0, abs=1e-6)
    assert ret.irr == pytest.approx(ret.moic ** (1 / 5) - 1)   # single exit cash flow
    assert ret.xirr == pytest.approx(ret.irr, abs=5e-4)        # differs only by leap days
    assert ret.net_irr is None                                 # not reported unless fund costs are modelled


def test_higher_purchase_price_reduces_returns(demo):
    irrs, moics = [], []
    for m in (8.0, 9.0, 10.0, 11.0, 12.0):
        r = run_lbo(with_(demo, transaction=dict(entry_multiple=m)))
        irrs.append(r.returns.irr)
        moics.append(r.returns.moic)
    assert all(a > b for a, b in zip(irrs, irrs[1:], strict=False))
    assert all(a > b for a, b in zip(moics, moics[1:], strict=False))


def test_leverage_amplifies_returns_only_when_asset_return_exceeds_cost_of_debt(demo):
    r = run_lbo(demo)
    assert r.returns.unlevered_irr > demo.debt.senior_rate * (1 - demo.operating.tax_rate)
    assert r.returns.irr > r.returns.unlevered_irr
    # the company-level return does not depend on how the deal is financed
    r_low = run_lbo(with_(demo, debt=dict(senior_leverage=2.0, sub_leverage=0.0)))
    assert r_low.returns.unlevered_irr == pytest.approx(r.returns.unlevered_irr)
    assert r_low.returns.irr < r.returns.irr
    # operating forecast is untouched by leverage: debt is not operating income
    assert list(r_low.operating.ebitda) == list(r.operating.ebitda)


def test_equity_wipe_out_is_floored_and_flagged(demo):
    r = run_lbo(with_(demo, exit=dict(exit_multiple=1.5)))
    assert r.exit.equity_value == 0.0 and r.exit.equity_value_unfloored < 0
    assert r.returns.moic == 0.0 and r.returns.irr == -1.0
    assert "EQUITY_WIPED_OUT" in {f.code for f in r.flags}
    assert r.checks()["Return attribution sums to value creation"][0]


def test_return_attribution_reconciles(demo):
    for a in (demo, with_(demo, exit=dict(exit_multiple=12.0, exit_year=4)),
              with_(demo, advanced=dict(recap_year=3, recap_turns=1.0), transaction=dict(rollover_pct=0.1))):
        r = run_lbo(a)
        assert abs(r.attribution.residual) < 1e-8
        assert r.attribution.total_value_creation == pytest.approx(
            r.exit.equity_value + r.debt.dividends[1:r.exit.exit_year + 1].sum() - r.sources_uses.total_equity)


# ---------------------------------------------------------------- advanced features
def test_dividend_recap_reconciles(demo):
    base = run_lbo(demo)
    r = run_lbo(with_(demo, advanced=dict(recap_year=3, recap_turns=1.0)))
    ds = r.debt
    ebitda3 = r.operating.ebitda[3]
    assert ds.recap_proceeds[3] == pytest.approx(ebitda3)
    assert ds.recap_fees[3] == pytest.approx(0.025 * ebitda3)
    assert ds.dividends[3] == pytest.approx(0.975 * ebitda3)             # debt raised = fees + dividend
    assert ds.total_debt[3] - base.debt.total_debt[3] == pytest.approx(ebitda3)
    assert ds.cash_closing[3] == pytest.approx(base.debt.cash_closing[3])  # no cash leakage
    assert r.returns.cash_flows[3] == pytest.approx(0.975 * ebitda3)
    assert r.returns.moic < base.returns.moic     # fees + extra interest reduce total proceeds
    assert r.returns.irr > base.returns.irr       # but earlier cash raises IRR
    assert all(ok for ok, _ in r.checks().values())


def test_management_incentive_pool_dilutes_sponsor(demo):
    base = run_lbo(demo)
    r = run_lbo(with_(demo, exit=dict(mip_pct=0.10)))
    gain = base.exit.equity_value - base.sources_uses.total_equity
    assert r.exit.mip_payout == pytest.approx(0.10 * gain)
    assert r.exit.sponsor_proceeds == pytest.approx(base.exit.sponsor_proceeds - 0.10 * gain)


def test_rollover_holders_share_exit_proceeds_pro_rata(demo):
    r = run_lbo(with_(demo, transaction=dict(rollover_pct=0.10)))
    ex, su = r.exit, r.sources_uses
    assert ex.sponsor_proceeds + ex.rollover_proceeds == pytest.approx(ex.equity_value)
    assert ex.sponsor_proceeds / ex.equity_value == pytest.approx(su.sponsor_ownership)
    # pro-rata economics: sponsor MOIC equals total equity MOIC
    assert r.returns.moic == pytest.approx(ex.equity_value / su.total_equity)


def test_net_irr_only_with_fund_costs_and_below_gross(demo):
    r = run_lbo(with_(demo, advanced=dict(model_net_returns=True)))
    ret = r.returns
    assert ret.net_irr is not None and ret.net_irr < ret.irr
    assert ret.fund_fees == pytest.approx(0.02 * 493.75 * 5)
    profit_after_fees = ret.profit - ret.fund_fees
    assert ret.carried_interest == pytest.approx(0.20 * profit_after_fees)
    assert ret.net_moic == pytest.approx((ret.total_proceeds - ret.carried_interest) / (493.75 + ret.fund_fees))


def test_exit_year_changes_dated_cash_flows(demo):
    for y in (3, 4, 5, 6, 7):
        r = run_lbo(with_(demo, exit=dict(exit_year=y)))
        assert len(r.returns.cash_flows) == y + 1
        assert r.returns.dates[-1] == date(2025 + y, 12, 31)
        assert r.exit.exit_ebitda == pytest.approx(r.operating.ebitda[y])
