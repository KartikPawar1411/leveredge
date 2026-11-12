from __future__ import annotations

import itertools

import numpy as np
import pytest

from tests.conftest import with_
from transaction.model import run_lbo


def test_hand_case_debt_schedule(simple):
    ds = run_lbo(simple).debt
    sen = ds.tranches["senior"]
    assert sen.interest[1:4] == pytest.approx([80.0, 72.5, 64.4375])
    assert ds.cash_taxes[1:4] == pytest.approx([25.0, 26.875, 28.890625])
    assert ds.cfads[1:4] == pytest.approx([155.0, 153.125, 151.109375])
    assert sen.optional[1:4] == pytest.approx([75.0, 80.625, 86.671875])
    assert sen.closing[0:4] == pytest.approx([800.0, 725.0, 644.375, 557.703125])
    assert ds.cash_closing[0:4] == pytest.approx([50.0, 50.0, 50.0, 50.0])


def test_interest_uses_beginning_balances(demo):
    r = run_lbo(demo)
    d = demo.debt
    for key, rate in (("senior", d.senior_rate), ("sub", d.sub_rate)):
        tr = r.debt.tranches[key]
        assert tr.interest[1:] == pytest.approx(tr.opening[1:] * rate)
    rev = r.debt.tranches["revolver"]
    expected = rev.opening[1:] * d.revolver_rate + (d.revolver_commitment - rev.opening[1:]) * d.revolver_undrawn_fee
    assert rev.interest[1:] == pytest.approx(expected)


def test_average_interest_method_is_self_consistent(demo):
    r = run_lbo(with_(demo, debt=dict(interest_method="average")))
    sen = r.debt.tranches["senior"]
    # recap is off, so closing balance is the post-sweep balance used in the average
    assert sen.interest[1:] == pytest.approx((sen.opening[1:] + sen.closing[1:]) / 2 * demo.debt.senior_rate, abs=1e-8)
    assert all(ok for ok, _ in r.checks().values())
    # paying down during the year means less interest than the beginning-balance method
    assert r.debt.total_interest[1:6].sum() < run_lbo(demo).debt.total_interest[1:6].sum()


def test_cash_sweep_respects_sweep_percentage(simple):
    r = run_lbo(with_(simple, debt=dict(cash_sweep_pct=0.5)))
    ds = r.debt
    # year 1: excess cash 75.0 -> 37.5 swept, 37.5 retained
    assert ds.tranches["senior"].optional[1] == pytest.approx(37.5)
    assert ds.cash_closing[1] == pytest.approx(87.5)
    assert ds.tranches["senior"].closing[1] == pytest.approx(762.5)


def test_mandatory_amortisation_and_priority(simple):
    r = run_lbo(with_(simple, debt=dict(senior_amort_pct=0.05, sub_leverage=1.0, sub_rate=0.12, sub_prepayable=True)))
    ds = r.debt
    sen, sub = ds.tranches["senior"], ds.tranches["sub"]
    assert sen.mandatory[1] == pytest.approx(40.0)  # 5% x 800 original principal
    # senior is swept before subordinated debt: sub only repaid once senior is fully retired
    for t in range(1, 8):
        if sub.optional[t] > 1e-9:
            assert sen.closing[t] == pytest.approx(0.0, abs=1e-9)


def test_non_prepayable_sub_debt_is_not_swept(demo):
    r = run_lbo(demo)
    assert np.all(r.debt.tranches["sub"].optional == 0.0)
    assert r.debt.tranches["sub"].closing[5] == pytest.approx(150.0)


def test_maturity_forces_full_repayment(simple):
    r = run_lbo(with_(simple, debt=dict(senior_maturity=3, revolver_commitment=1000.0, revolver_rate=0.09)))
    sen = r.debt.tranches["senior"]
    assert sen.mandatory[3] == pytest.approx(sen.opening[3])
    assert sen.closing[3] == pytest.approx(0.0)
    # refinanced onto the revolver, which stays within its commitment
    assert r.debt.tranches["revolver"].closing[3] > 0
    assert "MATURITY_ON_REVOLVER" in {f.code for f in r.flags}


def test_unfunded_maturity_and_liquidity_shortfall_are_flagged(simple):
    r = run_lbo(with_(simple, debt=dict(senior_maturity=3, revolver_commitment=100.0, revolver_rate=0.09)))
    ds = r.debt
    assert ds.tranches["revolver"].closing[3] == pytest.approx(100.0)  # capped at commitment
    assert ds.liquidity_shortfall[3] > 0
    assert ds.cash_closing[3] < 0
    codes = {f.code for f in r.flags}
    assert {"UNFUNDED_MATURITY", "LIQUIDITY_SHORTFALL", "NEGATIVE_CASH"} <= codes
    assert r.has_critical_flags
    ok, _ = r.checks()["Minimum cash maintained through exit"]
    assert not ok


def test_revolver_funds_shortfall_then_is_repaid_first(demo):
    # heavy year-1 working-capital build forces a draw
    a = with_(demo, operating=dict(nwc_pct_revenue=0.20))
    ds = run_lbo(a).debt
    rev = ds.tranches["revolver"]
    assert rev.additions[1] > 0
    assert ds.cash_closing[1] == pytest.approx(demo.transaction.min_cash)
    assert ds.tranches["senior"].optional[1] == 0.0
    # next year excess cash goes to the revolver before the term loan
    assert rev.optional[2] > 0
    if rev.closing[2] > 1e-9:
        assert ds.tranches["senior"].optional[2] == 0.0


def test_balances_never_negative_and_min_cash_holds_across_a_wide_grid(demo):
    growth = (-0.10, 0.0, 0.08)
    margin = (-0.08, 0.0, 0.05)
    lev = (2.0, 4.0, 5.5)
    sweep = (0.0, 0.5, 1.0)
    for g, m, lv, s in itertools.product(growth, margin, lev, sweep):
        a = with_(demo, debt=dict(senior_leverage=lv, cash_sweep_pct=s, sub_prepayable=True))
        a = with_(a, operating=dict(revenue_growth=tuple(x + g for x in demo.operating.revenue_growth),
                                    ebitda_margin=tuple(x + m for x in demo.operating.ebitda_margin)))
        r = run_lbo(a)
        ds = r.debt
        for tr in ds.tranches.values():
            assert tr.closing.min() >= -1e-9
            assert tr.mandatory.min() >= -1e-9 and tr.optional.min() >= -1e-9
        assert ds.tranches["revolver"].closing.max() <= demo.debt.revolver_commitment + 1e-9
        # minimum cash is either respected or explicitly reported as a shortfall
        for t in range(1, 8):
            below = ds.cash_closing[t] < demo.transaction.min_cash - 1e-9
            assert below == (ds.liquidity_shortfall[t] > 1e-9)
        checks = r.checks()
        assert checks["Debt roll-forward"][0] and checks["Cash roll-forward"][0]


def test_higher_interest_rates_reduce_debt_repayment_capacity(demo):
    base = run_lbo(demo)
    high = run_lbo(with_(demo, debt=dict(senior_rate=demo.debt.senior_rate + 0.03, sub_rate=demo.debt.sub_rate + 0.03)))
    assert high.debt.total_interest[1:6].sum() > base.debt.total_interest[1:6].sum()
    assert high.returns.debt_repaid < base.returns.debt_repaid
    assert high.exit.net_debt > base.exit.net_debt
    assert high.returns.irr < base.returns.irr


def test_covenant_breach_flagged(demo):
    r = run_lbo(with_(demo, debt=dict(covenant_max_leverage=4.5)))
    breaches = [f for f in r.flags if f.code == "COVENANT_LEVERAGE"]
    assert [f.year for f in breaches] == [1]  # 4.75x in year 1, 4.06x in year 2
    assert not [f for f in run_lbo(demo).flags if f.code.startswith("COVENANT")]
