from __future__ import annotations

import pytest

from financials.errors import ValidationError
from tests.conftest import with_
from transaction.model import run_lbo


def test_entry_bridge_hand_case(simple):
    e = run_lbo(simple).entry
    assert e.enterprise_value == pytest.approx(1600.0)
    assert e.net_debt == pytest.approx(250.0)
    assert e.equity_purchase_price == pytest.approx(1350.0)
    # EV is not the equity cheque
    assert e.equity_purchase_price != e.enterprise_value


def test_sources_uses_hand_case(simple):
    su = run_lbo(simple).sources_uses
    assert su.total_uses == pytest.approx(1650.0)
    assert su.total_sources == pytest.approx(1650.0)
    assert su.senior_debt == pytest.approx(800.0)
    assert su.sponsor_equity == pytest.approx(850.0)
    assert su.existing_cash_used == 0.0 and su.cash_to_balance_sheet == 0.0
    assert su.closing_cash == pytest.approx(50.0)


def test_demo_sources_uses_hand_values(demo):
    """EV 1,000; equity price 1,000-180+15 = 835; fees 20 + 2.5% x 550 = 13.75;
    uses 1,048.75; debt 550 + excess cash 5 -> sponsor 493.75."""
    su = run_lbo(demo).sources_uses
    assert su.purchase_of_equity == pytest.approx(835.0)
    assert su.transaction_fees == pytest.approx(20.0)
    assert su.financing_fees == pytest.approx(13.75)
    assert su.total_uses == pytest.approx(1048.75)
    assert su.existing_cash_used == pytest.approx(5.0)
    assert su.sponsor_equity == pytest.approx(493.75)
    assert su.is_balanced


@pytest.mark.parametrize("cash,min_cash,use_excess", [(15, 10, True), (15, 10, False), (4, 10, True), (0, 0, True), (10, 10, True)])
def test_sources_always_equal_uses_and_cash_not_double_counted(demo, cash, min_cash, use_excess):
    a = with_(demo, company=dict(cash=float(cash)), transaction=dict(min_cash=float(min_cash), use_excess_cash=use_excess))
    r = run_lbo(a)
    su = r.sources_uses
    assert su.total_sources == pytest.approx(su.total_uses, abs=1e-9)
    # cash is either a source (excess) or a use (top-up), never both
    assert su.existing_cash_used * su.cash_to_balance_sheet == 0.0
    # closing cash = target cash - cash used + cash funded, and never below the minimum
    assert su.closing_cash == pytest.approx(cash - su.existing_cash_used + su.cash_to_balance_sheet)
    assert su.closing_cash >= min_cash - 1e-9
    # invested equity ties to EV + fees - closing net debt (no double count of cash / refinancing / fees)
    assert su.total_equity == pytest.approx(r.entry.enterprise_value + su.total_fees - su.net_debt)


def test_keeping_excess_cash_costs_sponsor_exactly_that_cash(demo):
    used = run_lbo(demo).sources_uses
    kept = run_lbo(with_(demo, transaction=dict(use_excess_cash=False))).sources_uses
    assert kept.sponsor_equity - used.sponsor_equity == pytest.approx(5.0)
    assert kept.closing_cash - used.closing_cash == pytest.approx(5.0)


def test_rollover_reduces_sponsor_cheque_and_ownership(demo):
    r = run_lbo(with_(demo, transaction=dict(rollover_pct=0.10)))
    su = r.sources_uses
    assert su.rollover_equity == pytest.approx(83.5)
    assert su.sponsor_equity == pytest.approx(493.75 - 83.5)
    assert su.sponsor_ownership == pytest.approx((493.75 - 83.5) / 493.75)
    assert su.is_balanced


def test_excess_leverage_raises_instead_of_negative_equity(demo):
    with pytest.raises(ValidationError):
        run_lbo(with_(demo, debt=dict(senior_leverage=11.0)))


def test_minimum_equity_requirement_flagged(demo):
    r = run_lbo(with_(demo, debt=dict(senior_leverage=6.5, sub_leverage=1.5)))
    assert r.sources_uses.equity_contribution_pct < 0.25
    codes = {f.code for f in r.flags}
    assert "MIN_EQUITY" in codes and "MAX_LEVERAGE" in codes


def test_revolver_draw_cannot_exceed_commitment(demo):
    with pytest.raises(ValidationError):
        run_lbo(with_(demo, debt=dict(revolver_draw_at_close=80.0)))


@pytest.mark.parametrize("section,field,value", [
    ("transaction", "entry_multiple", 0.0), ("exit", "exit_year", 2), ("exit", "exit_year", 8),
    ("debt", "cash_sweep_pct", 1.5), ("operating", "tax_rate", 1.2), ("company", "ltm_ebitda", -5.0),
    ("debt", "interest_method", "magic"),
])
def test_validation_rejects_bad_inputs(demo, section, field, value):
    with pytest.raises(ValidationError):
        run_lbo(with_(demo, **{section: {field: value}}))
