"""Entry valuation: purchase enterprise value and the EV-to-equity bridge."""
from __future__ import annotations

from dataclasses import dataclass

from financials.assumptions import CompanyData, TransactionAssumptions


@dataclass(frozen=True)
class EntryValuation:
    entry_ebitda: float
    entry_multiple: float
    enterprise_value: float
    existing_debt: float
    existing_cash: float
    net_debt: float
    equity_purchase_price: float   # consideration paid to selling shareholders
    ev_to_revenue: float

    def bridge(self) -> list[tuple[str, float]]:
        return [
            ("Purchase enterprise value", self.enterprise_value),
            ("Less: existing debt", -self.existing_debt),
            ("Plus: existing cash", self.existing_cash),
            ("Equity purchase price (seller equity value)", self.equity_purchase_price),
        ]


def entry_valuation(company: CompanyData, txn: TransactionAssumptions) -> EntryValuation:
    """Enterprise value = multiple x LTM EBITDA.

    Equity purchase price = EV - existing debt + existing cash. The seller is
    paid for the equity; the acquirer separately refinances the debt (a use of
    funds) and inherits the target's cash (which may fund part of the deal).
    """
    ev = txn.entry_multiple * company.ltm_ebitda
    net_debt = company.existing_debt - company.cash
    return EntryValuation(
        entry_ebitda=company.ltm_ebitda,
        entry_multiple=txn.entry_multiple,
        enterprise_value=ev,
        existing_debt=company.existing_debt,
        existing_cash=company.cash,
        net_debt=net_debt,
        equity_purchase_price=ev - net_debt,
        ev_to_revenue=ev / company.ltm_revenue,
    )
