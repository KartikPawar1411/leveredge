"""Debt schedule, cash taxes and the cash-flow waterfall.

Annual waterfall (identical in the Excel export):

  1. Cash interest per tranche. Default method "beginning": opening balance x
     rate. This is deliberately NON-circular: interest does not depend on the
     current year's repayments. The optional "average" method charges interest
     on (opening + closing) / 2 and is solved by fixed-point iteration.
  2. Cash taxes        = tax rate x max(EBIT - cash interest, 0)
  3. CFADS             = EBITDA - cash taxes - capex - increase in NWC
  4. Mandatory repayments (scheduled amortisation; full balance at maturity).
  5. Pre-revolver cash = opening cash + CFADS - interest - mandatory
  6. Revolver draw     = min(shortfall vs minimum cash, undrawn commitment)
  7. Excess cash       = max(pre-revolver cash - minimum cash, 0)
       a. repay revolver (100% of excess, first priority)
       b. sweep % x remainder -> senior term loan
       c. any unused sweep -> subordinated debt, only if prepayable
  8. Optional dividend recap (advanced): incremental senior debt raised at
     year end, net of financing fees, distributed to shareholders.
  9. Closing cash; any amount below the minimum is a reported liquidity
     shortfall (never silently absorbed).

Repayments are capped at the outstanding balance, so balances cannot go
negative; revolver draws are capped at the commitment.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from capital_structure.sources_uses import SourcesUses
from financials.assumptions import HORIZON, AdvancedAssumptions, DebtAssumptions
from financials.errors import ModelError
from operating_model.forecast import OperatingForecast

TRANCHES = ("revolver", "senior", "sub")
TRANCHE_LABELS = {"revolver": "Revolving credit facility", "senior": "Senior term loan", "sub": "Subordinated debt"}


@dataclass(frozen=True)
class TrancheSchedule:
    name: str
    rate: float
    opening: np.ndarray
    additions: np.ndarray     # revolver draws / recap borrowing
    mandatory: np.ndarray
    optional: np.ndarray      # cash sweep / revolver repayment
    closing: np.ndarray       # index 0 = balance at close
    interest: np.ndarray

    def to_frame(self, years: int = HORIZON) -> pd.DataFrame:
        sl = slice(1, years + 1)
        return pd.DataFrame({
            "Opening balance": self.opening[sl],
            "Additional borrowing": self.additions[sl],
            "Mandatory repayment": -self.mandatory[sl],
            "Optional repayment": -self.optional[sl],
            "Closing balance": self.closing[sl],
            "Interest rate": np.full(years, self.rate),
            "Cash interest": self.interest[sl],
        }, index=[f"Year {t}" for t in range(1, years + 1)]).T


@dataclass(frozen=True)
class DebtSchedule:
    """All arrays are length HORIZON + 1 with index 0 = closing date."""
    tranches: dict[str, TrancheSchedule]
    cash_taxes: np.ndarray
    unlevered_taxes: np.ndarray
    cfads: np.ndarray
    unlevered_fcf: np.ndarray
    total_interest: np.ndarray
    total_mandatory: np.ndarray
    total_optional: np.ndarray
    fcf_after_debt_service: np.ndarray
    cash_opening: np.ndarray
    cash_pre_revolver: np.ndarray
    cash_closing: np.ndarray
    liquidity_shortfall: np.ndarray
    recap_proceeds: np.ndarray
    recap_fees: np.ndarray
    dividends: np.ndarray        # total distributions to all shareholders
    total_debt: np.ndarray
    net_debt: np.ndarray
    leverage: np.ndarray         # total debt / EBITDA
    senior_leverage: np.ndarray  # (revolver + senior) / EBITDA
    interest_coverage: np.ndarray  # EBITDA / cash interest
    dscr: np.ndarray             # CFADS / (cash interest + mandatory repayments)
    min_cash: float
    interest_method: str

    def cash_flow_frame(self, op: OperatingForecast, years: int = HORIZON) -> pd.DataFrame:
        sl = slice(1, years + 1)
        t = self.tranches
        rows = {
            "EBITDA": op.ebitda[sl],
            "Less: cash taxes": -self.cash_taxes[sl],
            "Less: capital expenditure": -op.capex[sl],
            "Less: increase in NWC": -op.delta_nwc[sl],
            "Cash flow available for debt service": self.cfads[sl],
            "Less: cash interest": -self.total_interest[sl],
            "Less: mandatory repayments": -self.total_mandatory[sl],
            "Free cash flow after debt service": self.fcf_after_debt_service[sl],
            "Opening cash": self.cash_opening[sl],
            "Revolver draw / (repayment)": t["revolver"].additions[sl] - t["revolver"].optional[sl],
            "Less: optional prepayments (sweep)": -(t["senior"].optional[sl] + t["sub"].optional[sl]),
            "Recap debt proceeds, net of fees": self.recap_proceeds[sl] - self.recap_fees[sl],
            "Less: dividends to shareholders": -self.dividends[sl],
            "Closing cash": self.cash_closing[sl],
            "Liquidity shortfall vs minimum cash": self.liquidity_shortfall[sl],
        }
        return pd.DataFrame(rows, index=[f"Year {y}" for y in range(1, years + 1)]).T

    def credit_stats_frame(self, years: int = HORIZON) -> pd.DataFrame:
        sl = slice(0, years + 1)
        return pd.DataFrame({
            "Total debt": self.total_debt[sl],
            "Net debt": self.net_debt[sl],
            "Total debt / EBITDA": self.leverage[sl],
            "Senior debt / EBITDA": self.senior_leverage[sl],
            "EBITDA / cash interest": self.interest_coverage[sl],
            "DSCR (CFADS / debt service)": self.dscr[sl],
        }, index=["Close"] + [f"Year {y}" for y in range(1, years + 1)]).T


def build_debt_schedule(
    op: OperatingForecast,
    su: SourcesUses,
    debt: DebtAssumptions,
    tax_rate: float,
    min_cash: float,
    financing_fee_pct: float = 0.0,
    advanced: AdvancedAssumptions | None = None,
) -> DebtSchedule:
    adv = advanced or AdvancedAssumptions()
    n = HORIZON + 1
    z = lambda: np.zeros(n)  # noqa: E731
    rates = {"revolver": debt.revolver_rate, "senior": debt.senior_rate, "sub": debt.sub_rate}
    opening = {k: z() for k in TRANCHES}
    additions = {k: z() for k in TRANCHES}
    mandatory = {k: z() for k in TRANCHES}
    optional = {k: z() for k in TRANCHES}
    closing = {k: z() for k in TRANCHES}
    interest = {k: z() for k in TRANCHES}
    closing["revolver"][0], closing["senior"][0], closing["sub"][0] = su.revolver_draw, su.senior_debt, su.sub_debt
    original = {"senior": su.senior_debt, "sub": su.sub_debt}
    amort = {"senior": debt.senior_amort_pct, "sub": debt.sub_amort_pct}
    maturity = {"senior": debt.senior_maturity, "sub": debt.sub_maturity}

    taxes, utaxes, cfads, ufcf = z(), z(), z(), z()
    fcf_post, cash_open, cash_pre, cash_close, shortfall = z(), z(), z(), z(), z()
    recap, recap_fees, dividends = z(), z(), z()
    cash_close[0] = su.closing_cash

    def interest_on(bal: dict[str, float]) -> dict[str, float]:
        rev = bal["revolver"]
        return {
            "revolver": rev * rates["revolver"] + max(debt.revolver_commitment - rev, 0.0) * debt.revolver_undrawn_fee,
            "senior": bal["senior"] * rates["senior"],
            "sub": bal["sub"] * rates["sub"],
        }

    for t in range(1, n):
        ob = {k: closing[k][t - 1] for k in TRANCHES}
        c0 = cash_close[t - 1]
        ebitda, ebit, capex, dnwc = op.ebitda[t], op.ebit[t], op.capex[t], op.delta_nwc[t]

        def waterfall(intr: dict[str, float]) -> dict[str, float]:
            total_int = sum(intr.values())
            tax = tax_rate * max(ebit - total_int, 0.0)
            cf = ebitda - tax - capex - dnwc
            mand = {k: (ob[k] if t >= maturity[k] else min(ob[k], amort[k] * original[k])) for k in ("senior", "sub")}
            pre = c0 + cf - total_int - mand["senior"] - mand["sub"]
            draw = min(max(min_cash - pre, 0.0), max(debt.revolver_commitment - ob["revolver"], 0.0))
            excess = max(pre - min_cash, 0.0)
            rev_repay = min(ob["revolver"], excess)
            budget = debt.cash_sweep_pct * (excess - rev_repay)
            sw_sen = min(ob["senior"] - mand["senior"], budget)
            sw_sub = min(ob["sub"] - mand["sub"], budget - sw_sen) if debt.sub_prepayable else 0.0
            return {
                "tax": tax, "cfads": cf, "pre": pre, "draw": draw, "rev_repay": rev_repay,
                "mand_sen": mand["senior"], "mand_sub": mand["sub"], "sw_sen": sw_sen, "sw_sub": sw_sub,
                "close_rev": ob["revolver"] + draw - rev_repay,
                "close_sen": ob["senior"] - mand["senior"] - sw_sen,
                "close_sub": ob["sub"] - mand["sub"] - sw_sub,
                "cash": pre + draw - rev_repay - sw_sen - sw_sub,
            }

        intr = interest_on(ob)
        w = waterfall(intr)
        if debt.interest_method == "average":
            for _ in range(200):
                avg = {"revolver": (ob["revolver"] + w["close_rev"]) / 2,
                       "senior": (ob["senior"] + w["close_sen"]) / 2,
                       "sub": (ob["sub"] + w["close_sub"]) / 2}
                new = interest_on(avg)
                w = waterfall(new)
                done = max(abs(new[k] - intr[k]) for k in TRANCHES) < 1e-10
                intr = new
                if done:
                    break
            else:
                raise ModelError(f"Average-balance interest did not converge in year {t}.")

        # optional dividend recapitalisation at year end (does not affect this year's interest)
        if adv.recap_year == t and adv.recap_turns > 0:
            recap[t] = adv.recap_turns * ebitda
            recap_fees[t] = financing_fee_pct * recap[t]
            dividends[t] = recap[t] - recap_fees[t]

        for k in TRANCHES:
            opening[k][t] = ob[k]
            interest[k][t] = intr[k]
        additions["revolver"][t], optional["revolver"][t] = w["draw"], w["rev_repay"]
        mandatory["senior"][t], optional["senior"][t], additions["senior"][t] = w["mand_sen"], w["sw_sen"], recap[t]
        mandatory["sub"][t], optional["sub"][t] = w["mand_sub"], w["sw_sub"]
        closing["revolver"][t] = w["close_rev"]
        closing["senior"][t] = w["close_sen"] + recap[t]
        closing["sub"][t] = w["close_sub"]

        taxes[t], cfads[t] = w["tax"], w["cfads"]
        utaxes[t] = tax_rate * max(ebit, 0.0)
        ufcf[t] = ebitda - utaxes[t] - capex - dnwc
        cash_open[t], cash_pre[t] = c0, w["pre"]
        fcf_post[t] = w["pre"] - c0
        cash_close[t] = w["cash"]            # recap proceeds - fees - dividend nets to zero
        shortfall[t] = max(min_cash - cash_close[t], 0.0)

    total_interest = sum(interest[k] for k in TRANCHES)
    total_mand = mandatory["senior"] + mandatory["sub"]
    total_opt = sum(optional[k] for k in TRANCHES)
    total_debt = sum(closing[k] for k in TRANCHES)
    with np.errstate(divide="ignore", invalid="ignore"):
        lev = np.where(op.ebitda > 0, total_debt / op.ebitda, np.inf)
        sen_lev = np.where(op.ebitda > 0, (closing["revolver"] + closing["senior"]) / op.ebitda, np.inf)
        icr = np.where(total_interest > 0, op.ebitda / total_interest, np.inf)
        service = total_interest + total_mand
        dscr = np.where(service > 0, cfads / service, np.inf)
    icr[0] = dscr[0] = np.nan

    tr = {k: TrancheSchedule(TRANCHE_LABELS[k], rates[k], opening[k], additions[k], mandatory[k],
                             optional[k], closing[k], interest[k]) for k in TRANCHES}
    return DebtSchedule(
        tranches=tr, cash_taxes=taxes, unlevered_taxes=utaxes, cfads=cfads, unlevered_fcf=ufcf,
        total_interest=total_interest, total_mandatory=total_mand, total_optional=total_opt,
        fcf_after_debt_service=fcf_post, cash_opening=cash_open, cash_pre_revolver=cash_pre,
        cash_closing=cash_close, liquidity_shortfall=shortfall, recap_proceeds=recap,
        recap_fees=recap_fees, dividends=dividends, total_debt=total_debt,
        net_debt=total_debt - cash_close, leverage=lev, senior_leverage=sen_lev,
        interest_coverage=icr, dscr=dscr, min_cash=min_cash, interest_method=debt.interest_method,
    )
