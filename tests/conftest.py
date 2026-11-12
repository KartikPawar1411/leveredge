"""Shared fixtures. `simple` is the hand-calculated reference transaction.

HAND-CALCULATED REFERENCE CASE (see docs/FORMULAS.md, section 9)
---------------------------------------------------------------
Revenue 1,000 flat; EBITDA margin 20% -> EBITDA 200; D&A 2% -> 20; EBIT 180
Capex 2% -> 20; NWC 10% of revenue = 100, constant -> change in NWC 0; tax 25%
Entry 8.0x -> EV 1,600; existing debt 300; cash 50; minimum cash 50; no fees
Senior term loan 4.0x = 800 at 10%, no amortisation, 100% cash sweep
Equity purchase price = 1,600 - 300 + 50 = 1,350
Uses = 1,350 + 300 = 1,650; Sources = 800 debt + 850 sponsor equity

Year 1: interest 80.0000; tax 0.25 x (180 - 80) = 25.0000
        CFADS 200 - 25 - 20 = 155.0000; sweep 75.0000; debt 725.0000
Year 2: interest 72.5000; tax 0.25 x 107.5 = 26.8750
        CFADS 153.1250; sweep 80.6250; debt 644.3750
Year 3: interest 64.4375; tax 0.25 x 115.5625 = 28.890625
        CFADS 151.109375; sweep 86.671875; debt 557.703125
Exit Y3 at 8.0x: EV 1,600; net debt 557.703125 - 50 = 507.703125
        equity 1,092.296875; MOIC 1.285055147...; IRR = MOIC^(1/3) - 1
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from financials.assumptions import (
    AdvancedAssumptions,
    CompanyData,
    DebtAssumptions,
    ExitAssumptions,
    LBOAssumptions,
    OperatingAssumptions,
    TransactionAssumptions,
    as_vector,
)
from financials.company import demo_assumptions


@pytest.fixture
def demo() -> LBOAssumptions:
    return demo_assumptions()


@pytest.fixture
def simple() -> LBOAssumptions:
    return LBOAssumptions(
        company=CompanyData(name="Hand Case", industry="Test", ltm_revenue=1000.0, ltm_ebitda=200.0,
                            cash=50.0, existing_debt=300.0, net_working_capital=100.0, is_synthetic=True),
        transaction=TransactionAssumptions(entry_multiple=8.0, transaction_fee_pct=0.0, financing_fee_pct=0.0,
                                           min_cash=50.0, use_excess_cash=True, close_date=date(2025, 12, 31)),
        operating=OperatingAssumptions(revenue_growth=as_vector(0.0), ebitda_margin=as_vector(0.20),
                                       da_pct_revenue=0.02, capex_pct_revenue=0.02, nwc_pct_revenue=0.10,
                                       tax_rate=0.25),
        debt=DebtAssumptions(senior_leverage=4.0, senior_rate=0.10, senior_amort_pct=0.0, senior_maturity=7,
                             cash_sweep_pct=1.0),
        exit=ExitAssumptions(exit_year=3, exit_multiple=8.0, exit_fee_pct=0.0),
        advanced=AdvancedAssumptions(),
    )


def with_(a: LBOAssumptions, **sections) -> LBOAssumptions:
    """with_(a, debt=dict(senior_rate=0.1), exit=dict(exit_year=4))"""
    return replace(a, **{k: replace(getattr(a, k), **v) for k, v in sections.items()})
