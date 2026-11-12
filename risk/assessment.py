"""Rule-based investment assessment derived from model outputs.

The verdict is computed from base-case returns, downside resilience and risk
flags. It is never a fixed statement, and it is conditional on the inputs.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from scipy.optimize import brentq

from financials.assumptions import LBOAssumptions
from financials.errors import LeverEdgeError
from scenarios.scenarios import ScenarioOutcome
from transaction.model import LBOResult, run_lbo


@dataclass(frozen=True)
class InvestmentCriteria:
    target_irr: float = 0.20
    target_moic: float = 2.0
    min_downside_moic: float = 1.0


@dataclass(frozen=True)
class Assessment:
    verdict: str
    summary: str
    supports: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    breakeven_entry_multiple: float | None = None


def breakeven_entry_multiple(a: LBOAssumptions, target_irr: float) -> float | None:
    """Highest entry multiple at which the base case still earns target_irr.

    Debt quantum is held constant (leverage is a multiple of EBITDA), so a
    higher price is funded entirely with sponsor equity.
    """
    def gap(m: float) -> float:
        r = run_lbo(replace(a, transaction=replace(a.transaction, entry_multiple=m)))
        return r.returns.irr - target_irr

    base = a.transaction.entry_multiple
    lo, hi = None, None
    try:
        # find a feasible lower bound where IRR > target and an upper bound where IRR < target
        m = base
        for _ in range(40):
            try:
                if gap(m) > 0:
                    lo = m
                    break
            except LeverEdgeError:
                break
            m *= 0.93
        m = base
        for _ in range(40):
            if gap(m) < 0:
                hi = m
                break
            m *= 1.07
        if lo is None or hi is None:
            return None
        return float(brentq(gap, lo, hi, xtol=1e-6))
    except (LeverEdgeError, ValueError):
        return None


def assess(
    base: LBOResult,
    scenarios: Mapping[str, ScenarioOutcome],
    criteria: InvestmentCriteria = InvestmentCriteria(),
) -> Assessment:
    ret, n = base.returns, base.exit.exit_year
    supports: list[str] = []
    concerns: list[str] = []
    conditions: list[str] = []

    irr_ok = ret.irr >= criteria.target_irr
    moic_ok = ret.moic >= criteria.target_moic
    (supports if irr_ok else concerns).append(
        f"Base-case gross IRR of {ret.irr:.1%} is {'above' if irr_ok else 'below'} the {criteria.target_irr:.0%} target.")
    (supports if moic_ok else concerns).append(
        f"Base-case MOIC of {ret.moic:.2f}x is {'above' if moic_ok else 'below'} the {criteria.target_moic:.1f}x target.")

    attr = base.attribution
    if attr.total_value_creation > 0:
        op_share = attr.ebitda_growth / attr.total_value_creation
        mult_share = attr.multiple_expansion / attr.total_value_creation
        if mult_share > 0.35:
            concerns.append(f"{mult_share:.0%} of value creation relies on multiple expansion, which the sponsor does not control.")
        elif op_share >= 0.4:
            supports.append(f"{op_share:.0%} of value creation comes from EBITDA growth rather than multiple expansion.")
    delever = float(base.debt.leverage[0] - base.debt.leverage[n])
    if delever > 0:
        supports.append(f"Leverage falls from {base.debt.leverage[0]:.1f}x to {base.debt.leverage[n]:.1f}x by exit "
                        f"with {ret.debt_repaid:,.0f} of debt repaid from operating cash flow.")
    if ret.leverage_uplift < 0:
        concerns.append(f"Leverage is dilutive: the unlevered company-level IRR ({ret.unlevered_irr:.1%}) exceeds the "
                        f"sponsor IRR, i.e. the cost of debt is above the asset return.")

    base_crit = [f for f in base.flags if f.severity == "critical"]
    for f in base_crit:
        concerns.append(f"Base case: {f.message}" + (f" (Year {f.year})" if f.year else ""))
    for f in base.flags:
        if f.severity == "warning":
            conditions.append(f"Resolve: {f.message}")

    bear = scenarios.get("Bear")
    bear_ok = True
    if bear is not None:
        if bear.result is None:
            bear_ok = False
            concerns.append(f"Bear case is infeasible: {bear.error}")
        else:
            b = bear.result
            b_crit = [f for f in b.flags if f.severity == "critical"]
            if b.returns.moic < criteria.min_downside_moic:
                bear_ok = False
                concerns.append(f"Bear case loses capital: MOIC {b.returns.moic:.2f}x, IRR {b.returns.irr:.1%}.")
            else:
                supports.append(f"Bear case preserves capital: MOIC {b.returns.moic:.2f}x, IRR {b.returns.irr:.1%}.")
            if b_crit:
                bear_ok = False
                codes = sorted({f.code.replace('_', ' ').lower() for f in b_crit})
                concerns.append(f"Bear case triggers critical flags ({', '.join(codes)}).")
                conditions.append("Negotiate covenant headroom / liquidity (larger revolver, looser covenants or lower "
                                  "leverage) sufficient to withstand the bear case.")

    be = breakeven_entry_multiple(base.assumptions, criteria.target_irr)
    entry = base.entry.entry_multiple
    if be is not None:
        if be >= entry:
            supports.append(f"Price cushion: the base case still achieves a {criteria.target_irr:.0%} IRR up to an entry "
                            f"multiple of {be:.2f}x versus {entry:.2f}x assumed.")
        else:
            conditions.append(f"Reduce the purchase price to about {be:.2f}x EBITDA (from {entry:.2f}x) to reach a "
                              f"{criteria.target_irr:.0%} base-case IRR, all else equal.")

    if base_crit:
        verdict = "DO NOT PROCEED ON CURRENT TERMS"
        summary = ("The base case itself breaches liquidity, covenant or solvency tests; the capital structure "
                   "is not financeable as modelled.")
    elif irr_ok and moic_ok and bear_ok:
        verdict = "PROCEED TO NEXT STAGE"
        summary = ("Base-case returns clear both hurdles and the downside case neither loses capital nor triggers "
                   "critical credit flags. Recommendation is conditional on diligence confirming the operating assumptions.")
    elif irr_ok and not bear_ok:
        verdict = "PROCEED WITH CONDITIONS"
        summary = ("Base-case returns are attractive but the structure is fragile in the downside case. "
                   "Approval should be conditional on the mitigants listed.")
    elif ret.irr >= criteria.target_irr - 0.05 and ret.moic >= 1.0:
        verdict = "MARGINAL - RENEGOTIATE TERMS"
        summary = ("Returns fall short of the fund's hurdle by a margin that price or structure could plausibly close; "
                   "not approvable at the modelled terms.")
    else:
        verdict = "DECLINE AT CURRENT TERMS"
        summary = "Modelled returns are materially below the fund's hurdle; the price and structure do not compensate for the risk."
    return Assessment(verdict, summary, supports, concerns, conditions, be)
