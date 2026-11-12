"""Sources and Uses of funds and the closing capital structure.

Design rules that prevent double counting:
  * Seller equity is purchased at EV - existing debt + existing cash, so the
    seller is paid for the target's cash exactly once.
  * Existing debt is refinanced as a separate use (never netted inside EV twice).
  * Target cash above the minimum balance may be used as a SOURCE; a shortfall
    versus the minimum is funded as a USE. Never both.
  * Financing fees are a use sized on funded debt; they are not deducted from
    debt proceeds as well.
  * Sponsor equity is the residual plug: total uses less all other sources.
"""
from __future__ import annotations

from dataclasses import dataclass

from financials.assumptions import CompanyData, DebtAssumptions, TransactionAssumptions
from financials.errors import ValidationError
from transaction.entry import EntryValuation

TOL = 1e-6


@dataclass(frozen=True)
class SourcesUses:
    # uses
    purchase_of_equity: float
    refinance_existing_debt: float
    transaction_fees: float
    financing_fees: float
    cash_to_balance_sheet: float
    # sources
    senior_debt: float
    sub_debt: float
    revolver_draw: float
    existing_cash_used: float
    rollover_equity: float
    sponsor_equity: float
    # derived
    closing_cash: float
    entry_ebitda: float

    @property
    def uses(self) -> dict[str, float]:
        return {
            "Purchase of seller equity": self.purchase_of_equity,
            "Refinance existing debt": self.refinance_existing_debt,
            "Transaction fees": self.transaction_fees,
            "Financing fees": self.financing_fees,
            "Cash funded to balance sheet": self.cash_to_balance_sheet,
        }

    @property
    def sources(self) -> dict[str, float]:
        return {
            "Revolving credit facility": self.revolver_draw,
            "Senior term loan": self.senior_debt,
            "Subordinated debt": self.sub_debt,
            "Existing target cash used": self.existing_cash_used,
            "Management rollover equity": self.rollover_equity,
            "Sponsor equity": self.sponsor_equity,
        }

    @property
    def total_uses(self) -> float:
        return sum(self.uses.values())

    @property
    def total_sources(self) -> float:
        return sum(self.sources.values())

    @property
    def imbalance(self) -> float:
        return self.total_sources - self.total_uses

    @property
    def is_balanced(self) -> bool:
        return abs(self.imbalance) < TOL

    @property
    def total_debt(self) -> float:
        return self.senior_debt + self.sub_debt + self.revolver_draw

    @property
    def total_equity(self) -> float:
        return self.sponsor_equity + self.rollover_equity

    @property
    def total_fees(self) -> float:
        return self.transaction_fees + self.financing_fees

    @property
    def sponsor_ownership(self) -> float:
        return self.sponsor_equity / self.total_equity

    @property
    def total_leverage(self) -> float:
        return self.total_debt / self.entry_ebitda

    @property
    def senior_leverage(self) -> float:
        return (self.senior_debt + self.revolver_draw) / self.entry_ebitda

    @property
    def net_debt(self) -> float:
        return self.total_debt - self.closing_cash

    @property
    def equity_contribution_pct(self) -> float:
        """Total equity (sponsor + rollover) as a share of total sources."""
        return self.total_equity / self.total_sources

    @property
    def debt_to_capitalisation(self) -> float:
        return self.total_debt / (self.total_debt + self.total_equity)


def build_sources_uses(
    company: CompanyData,
    txn: TransactionAssumptions,
    debt: DebtAssumptions,
    entry: EntryValuation,
) -> SourcesUses:
    senior = debt.senior_leverage * company.ltm_ebitda
    sub = debt.sub_leverage * company.ltm_ebitda
    revolver = debt.revolver_draw_at_close
    funded_debt = senior + sub + revolver

    if entry.equity_purchase_price <= 0:
        raise ValidationError(
            "Equity purchase price is not positive: existing net debt exceeds the purchase enterprise value.",
            "entry_multiple",
        )

    transaction_fees = txn.transaction_fee_pct * entry.enterprise_value
    financing_fees = txn.financing_fee_pct * funded_debt
    cash_top_up = max(txn.min_cash - company.cash, 0.0)
    cash_used = max(company.cash - txn.min_cash, 0.0) if txn.use_excess_cash else 0.0
    rollover = txn.rollover_pct * entry.equity_purchase_price

    total_uses = (entry.equity_purchase_price + company.existing_debt
                  + transaction_fees + financing_fees + cash_top_up)
    sponsor = total_uses - funded_debt - cash_used - rollover
    if sponsor <= 0:
        raise ValidationError(
            f"Debt, cash and rollover sources ({funded_debt + cash_used + rollover:,.1f}) "
            f"meet or exceed total uses ({total_uses:,.1f}); sponsor equity would be non-positive. "
            "Reduce leverage or rollover.",
            "senior_leverage",
        )

    su = SourcesUses(
        purchase_of_equity=entry.equity_purchase_price,
        refinance_existing_debt=company.existing_debt,
        transaction_fees=transaction_fees,
        financing_fees=financing_fees,
        cash_to_balance_sheet=cash_top_up,
        senior_debt=senior,
        sub_debt=sub,
        revolver_draw=revolver,
        existing_cash_used=cash_used,
        rollover_equity=rollover,
        sponsor_equity=sponsor,
        closing_cash=company.cash - cash_used + cash_top_up,
        entry_ebitda=company.ltm_ebitda,
    )
    if not su.is_balanced:  # defensive: cannot happen with a residual plug
        raise ValidationError(f"Sources and Uses do not balance (difference {su.imbalance:.6f}).")
    return su
