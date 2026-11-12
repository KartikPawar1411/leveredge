from __future__ import annotations

import numpy as np
import pytest

from risk.assessment import InvestmentCriteria, assess, breakeven_entry_multiple
from scenarios.scenarios import ScenarioSpec, apply_scenario, exit_timing, run_scenarios, scenario_summary
from scenarios.sensitivity import HEATMAPS, PARAMETERS, default_grid, run_with, sensitivity_1d, sensitivity_2d
from tests.conftest import with_
from transaction.model import run_lbo


def test_base_scenario_equals_base_model(demo):
    out = run_scenarios(demo)
    assert out["Base"].result.returns.irr == pytest.approx(run_lbo(demo).returns.irr)


def test_scenarios_fully_recompute(demo):
    out = run_scenarios(demo)
    bull, base, bear = (out[k].result for k in ("Bull", "Base", "Bear"))
    assert bull.returns.irr > base.returns.irr > bear.returns.irr
    assert bull.returns.moic > base.returns.moic > bear.returns.moic
    assert bull.returns.debt_repaid > base.returns.debt_repaid > bear.returns.debt_repaid
    assert bull.debt.cfads[1:6].sum() > base.debt.cfads[1:6].sum() > bear.debt.cfads[1:6].sum()
    assert bear.debt.total_interest[1:6].sum() > base.debt.total_interest[1:6].sum()  # higher rates and slower paydown
    assert bear.debt.leverage[5] > base.debt.leverage[5] > bull.debt.leverage[5]
    # every scenario is internally reconciled
    for r in (bull, base, bear):
        c = r.checks()
        assert c["Sources equal uses"][0] and c["Debt roll-forward"][0] and c["Cash roll-forward"][0]


def test_scenario_matches_manual_application(demo):
    spec = ScenarioSpec("Custom", growth_delta=-0.01, margin_delta=0.005, rate_delta=0.01, leverage_delta=0.5,
                        exit_multiple_delta=-0.5, capex_delta=0.01, nwc_delta=0.01)
    manual = with_(
        demo,
        operating=dict(revenue_growth=tuple(g - 0.01 for g in demo.operating.revenue_growth),
                       ebitda_margin=tuple(m + 0.005 for m in demo.operating.ebitda_margin),
                       capex_pct_revenue=0.045, nwc_pct_revenue=0.13),
        debt=dict(senior_leverage=4.5, senior_rate=0.095, sub_rate=0.13, revolver_rate=0.09),
        exit=dict(exit_multiple=9.5),
    )
    a, b = run_lbo(apply_scenario(demo, spec)), run_lbo(manual)
    assert a.returns.irr == pytest.approx(b.returns.irr)
    assert a.sources_uses.sponsor_equity == pytest.approx(b.sources_uses.sponsor_equity)
    assert a.sources_uses.senior_debt == pytest.approx(450.0)


def test_bear_case_flags_are_raised_not_hidden(demo):
    bear = run_scenarios(demo)["Bear"].result
    assert bear.has_critical_flags
    summary = scenario_summary(run_scenarios(demo))
    assert summary.loc["Status", "Bear"] == "Critical flags"
    assert summary.loc["Status", "Base"] == "Clean"


def test_infeasible_scenario_reported(demo):
    out = run_scenarios(demo, {"Silly": ScenarioSpec("Silly", leverage_delta=8.0)})
    assert out["Silly"].result is None and out["Silly"].error


@pytest.mark.parametrize("rows,cols", HEATMAPS)
def test_heatmap_cells_equal_independent_full_reruns(demo, rows, cols):
    irr_g, moic_g = sensitivity_2d(demo, rows, cols)
    rv, cv = default_grid(demo, rows), default_grid(demo, cols)
    assert irr_g.shape == (len(rv), len(cv))
    for i, j in ((0, 0), (len(rv) // 2, len(cv) // 2), (len(rv) - 1, 1), (2, len(cv) - 1)):
        r = run_with(demo, {rows: rv[i], cols: cv[j]})
        assert irr_g.iloc[i, j] == pytest.approx(r.returns.irr)
        assert moic_g.iloc[i, j] == pytest.approx(r.returns.moic)
    # centre cell is the base case
    base = run_lbo(demo)
    assert irr_g.iloc[len(rv) // 2, len(cv) // 2] == pytest.approx(base.returns.irr)


def test_entry_exit_grid_is_monotonic(demo):
    irr_g, _ = sensitivity_2d(demo, "entry_multiple", "exit_multiple")
    v = irr_g.values
    assert np.all(np.diff(v, axis=0) < 0)   # paying more lowers IRR
    assert np.all(np.diff(v, axis=1) > 0)   # selling for more raises IRR


def test_leverage_sensitivity_reruns_debt_schedule(demo):
    lo = run_with(demo, {"leverage": 3.5})
    hi = run_with(demo, {"leverage": 6.0})
    assert hi.sources_uses.total_debt == pytest.approx(600.0)
    assert hi.sources_uses.sponsor_equity < lo.sources_uses.sponsor_equity
    assert hi.debt.total_interest[1] > lo.debt.total_interest[1]
    # senior / sub mix is preserved
    assert hi.sources_uses.senior_debt / hi.sources_uses.total_debt == pytest.approx(4.0 / 5.5)


@pytest.mark.parametrize("param", list(PARAMETERS))
def test_one_dimensional_sensitivities(demo, param):
    df = sensitivity_1d(demo, param)
    assert len(df) >= 5 and df["IRR"].notna().all()
    assert df["IRR"].nunique() > 1   # the parameter actually drives the model


def test_sensitivity_directions(demo):
    assert sensitivity_1d(demo, "rates")["IRR"].is_monotonic_decreasing
    assert sensitivity_1d(demo, "growth")["IRR"].is_monotonic_increasing
    assert sensitivity_1d(demo, "margin")["IRR"].is_monotonic_increasing
    assert sensitivity_1d(demo, "exit_multiple")["MOIC"].is_monotonic_increasing


def test_infeasible_cells_are_nan_not_fabricated(demo):
    irr_g, _ = sensitivity_2d(demo, "leverage", "exit_multiple", row_values=[5.5, 12.0], col_values=[10.0])
    assert np.isnan(irr_g.iloc[1, 0]) and not np.isnan(irr_g.iloc[0, 0])


def test_exit_timing_table(demo):
    df = exit_timing(demo)
    assert list(df.index) == [3, 4, 5, 6, 7]
    assert df.loc[5, "IRR"] == pytest.approx(run_lbo(demo).returns.irr)
    assert df["MOIC"].is_monotonic_increasing


def test_breakeven_multiple_hits_target_irr(demo):
    be = breakeven_entry_multiple(demo, 0.20)
    r = run_lbo(with_(demo, transaction=dict(entry_multiple=be)))
    assert r.returns.irr == pytest.approx(0.20, abs=1e-5)


def test_assessment_follows_the_numbers(demo):
    base = run_lbo(demo)
    verdict_now = assess(base, run_scenarios(demo)).verdict
    cheap = with_(demo, transaction=dict(entry_multiple=7.0))
    verdict_cheap = assess(run_lbo(cheap), run_scenarios(cheap)).verdict
    dear = with_(demo, transaction=dict(entry_multiple=14.0))
    verdict_dear = assess(run_lbo(dear), run_scenarios(dear)).verdict
    assert verdict_dear.startswith("DECLINE")
    assert verdict_cheap.startswith("PROCEED")
    assert len({verdict_now, verdict_cheap, verdict_dear}) >= 2
    # criteria are inputs, not constants
    easy = assess(base, {}, InvestmentCriteria(target_irr=0.10, target_moic=1.5))
    assert easy.verdict == "PROCEED TO NEXT STAGE"
    broken = with_(demo, debt=dict(covenant_max_leverage=4.0))
    assert assess(run_lbo(broken), {}).verdict.startswith("DO NOT PROCEED")


def test_detailed_operating_mode(demo):
    a = with_(demo, operating=dict(mode="detailed"))
    r = run_lbo(a)
    op = r.operating
    assert op.ebitda[1] == pytest.approx(op.revenue[1] - op.cogs[1] - op.sga[1] - op.other_opex[1])
    assert op.ebitda[1] == pytest.approx(540.0 * (1 - 0.60 - 0.175) - 10.0)
    assert all(ok for ok, _ in r.checks().values())
    up = run_with(a, {"margin": 0.01})
    assert up.operating.ebitda[1] == pytest.approx(op.ebitda[1] + 5.4)
