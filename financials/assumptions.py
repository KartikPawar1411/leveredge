"""Typed, validated input containers for the LBO model.

All monetary amounts are in one consistent currency unit (the demo uses USD
millions). All rates and percentages are decimals (0.085 = 8.5%).

Year index convention used across the engine: index 0 is the closing date /
LTM period, indices 1..HORIZON are projection years.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

from .errors import ValidationError

HORIZON: int = 7          # projection years always computed
MIN_EXIT_YEAR: int = 3
MAX_EXIT_YEAR: int = 7
DISPLAY_YEARS: int = 5    # the "five-year operating model" default view


def as_vector(x: float | Sequence[float], n: int = HORIZON) -> tuple[float, ...]:
    """Coerce a scalar or sequence to an n-vector (pad with the last value)."""
    if isinstance(x, (int, float)):
        return tuple(float(x) for _ in range(n))
    vals = [float(v) for v in x]
    if not vals:
        raise ValidationError("Empty driver vector supplied.")
    while len(vals) < n:
        vals.append(vals[-1])
    return tuple(vals[:n])


@dataclass(frozen=True)
class HistoricalYear:
    year: str
    revenue: float
    ebitda: float
    da: float
    capex: float
    nwc: float
    taxes: float

    @property
    def ebit(self) -> float:
        return self.ebitda - self.da


@dataclass(frozen=True)
class CompanyData:
    name: str
    industry: str
    ltm_revenue: float
    ltm_ebitda: float
    cash: float
    existing_debt: float
    net_working_capital: float
    description: str = ""
    is_synthetic: bool = False
    historicals: tuple[HistoricalYear, ...] = ()


@dataclass(frozen=True)
class TransactionAssumptions:
    entry_multiple: float                 # EV / LTM EBITDA
    transaction_fee_pct: float = 0.02     # % of purchase enterprise value
    financing_fee_pct: float = 0.025      # % of funded debt raised
    min_cash: float = 0.0                 # minimum operating cash balance
    use_excess_cash: bool = True          # target cash above minimum funds the deal
    rollover_pct: float = 0.0             # % of seller equity value rolled by management
    min_equity_contribution_pct: float = 0.25  # (sponsor + rollover) / total sources
    close_date: date = date(2025, 12, 31)


@dataclass(frozen=True)
class OperatingAssumptions:
    revenue_growth: tuple[float, ...]
    ebitda_margin: tuple[float, ...]
    da_pct_revenue: float
    capex_pct_revenue: float
    nwc_pct_revenue: float
    tax_rate: float
    mode: str = "driver"                  # "driver" or "detailed"
    # detailed operating statement (used only when mode == "detailed")
    cogs_pct: tuple[float, ...] = ()
    sga_pct: tuple[float, ...] = ()
    other_opex: float = 0.0               # fixed annual operating cost


@dataclass(frozen=True)
class DebtAssumptions:
    senior_leverage: float                # x LTM EBITDA
    senior_rate: float
    senior_amort_pct: float = 0.01        # % of original principal per year
    senior_maturity: int = 7              # years from close
    sub_leverage: float = 0.0
    sub_rate: float = 0.0
    sub_amort_pct: float = 0.0
    sub_maturity: int = 8
    sub_prepayable: bool = False          # eligible for cash sweep after senior
    revolver_commitment: float = 0.0
    revolver_draw_at_close: float = 0.0
    revolver_rate: float = 0.0
    revolver_undrawn_fee: float = 0.0
    cash_sweep_pct: float = 1.0           # % of excess cash applied to prepayment
    max_total_leverage: float = 6.5       # structuring limit at close (x EBITDA)
    interest_method: str = "beginning"    # "beginning" (default) or "average"
    covenant_max_leverage: float | None = None      # total debt / EBITDA
    covenant_min_interest_coverage: float | None = None  # EBITDA / cash interest


@dataclass(frozen=True)
class ExitAssumptions:
    exit_year: int = 5
    exit_multiple: float = 10.0
    exit_fee_pct: float = 0.015           # % of exit enterprise value
    mip_pct: float = 0.0                  # management incentive pool (advanced)


@dataclass(frozen=True)
class AdvancedAssumptions:
    """Optional features, all OFF in the baseline model."""
    recap_year: int | None = None      # dividend recapitalisation year
    recap_turns: float = 0.0              # incremental senior debt, x that year's EBITDA
    model_net_returns: bool = False       # fund-level fees / carry
    mgmt_fee_pct: float = 0.02            # annual, on invested equity
    carry_pct: float = 0.20
    hurdle_rate: float = 0.08


@dataclass(frozen=True)
class LBOAssumptions:
    company: CompanyData
    transaction: TransactionAssumptions
    operating: OperatingAssumptions
    debt: DebtAssumptions
    exit: ExitAssumptions = field(default_factory=ExitAssumptions)
    advanced: AdvancedAssumptions = field(default_factory=AdvancedAssumptions)

    # ------------------------------------------------------------------ #
    def validate(self) -> LBOAssumptions:
        c, t, o, d, x, a = (self.company, self.transaction, self.operating,
                            self.debt, self.exit, self.advanced)

        def req(cond: bool, msg: str, fld: str) -> None:
            if not cond:
                raise ValidationError(msg, fld)

        req(c.ltm_revenue > 0, "LTM revenue must be positive.", "ltm_revenue")
        req(c.ltm_ebitda > 0, "LTM EBITDA must be positive to apply an EV/EBITDA multiple.", "ltm_ebitda")
        req(c.cash >= 0, "Cash cannot be negative.", "cash")
        req(c.existing_debt >= 0, "Existing debt cannot be negative.", "existing_debt")

        req(t.entry_multiple > 0, "Entry multiple must be positive.", "entry_multiple")
        req(0 <= t.transaction_fee_pct < 0.25, "Transaction fee % out of range.", "transaction_fee_pct")
        req(0 <= t.financing_fee_pct < 0.25, "Financing fee % out of range.", "financing_fee_pct")
        req(t.min_cash >= 0, "Minimum cash cannot be negative.", "min_cash")
        req(0 <= t.rollover_pct < 1, "Rollover % must be in [0, 1).", "rollover_pct")

        for name in ("revenue_growth", "ebitda_margin"):
            req(len(getattr(o, name)) == HORIZON, f"{name} needs {HORIZON} annual values.", name)
        req(all(g > -1 for g in o.revenue_growth), "Revenue growth must exceed -100%.", "revenue_growth")
        req(o.mode in ("driver", "detailed"), "Operating mode must be 'driver' or 'detailed'.", "mode")
        if o.mode == "detailed":
            req(len(o.cogs_pct) == HORIZON and len(o.sga_pct) == HORIZON,
                f"Detailed mode needs {HORIZON} COGS% and SG&A% values.", "cogs_pct")
        else:
            req(all(-1 < m < 1 for m in o.ebitda_margin), "EBITDA margins must be between -100% and 100%.", "ebitda_margin")
        req(0 <= o.tax_rate < 1, "Tax rate must be in [0, 1).", "tax_rate")
        req(o.da_pct_revenue >= 0 and o.capex_pct_revenue >= 0, "D&A and capex % must be non-negative.", "da_pct_revenue")

        req(d.senior_leverage >= 0 and d.sub_leverage >= 0, "Leverage multiples cannot be negative.", "senior_leverage")
        for r in ("senior_rate", "sub_rate", "revolver_rate", "revolver_undrawn_fee"):
            req(0 <= getattr(d, r) < 1, f"{r} must be in [0, 1).", r)
        req(0 <= d.senior_amort_pct <= 1 and 0 <= d.sub_amort_pct <= 1, "Amortisation % must be in [0, 1].", "senior_amort_pct")
        req(d.senior_maturity >= 1 and d.sub_maturity >= 1, "Maturities must be at least one year.", "senior_maturity")
        req(d.revolver_commitment >= 0, "Revolver commitment cannot be negative.", "revolver_commitment")
        req(0 <= d.revolver_draw_at_close <= d.revolver_commitment + 1e-9,
            "Revolver draw at close cannot exceed the facility commitment.", "revolver_draw_at_close")
        req(0 <= d.cash_sweep_pct <= 1, "Cash sweep % must be in [0, 1].", "cash_sweep_pct")
        req(d.interest_method in ("beginning", "average"), "Interest method must be 'beginning' or 'average'.", "interest_method")

        req(MIN_EXIT_YEAR <= x.exit_year <= MAX_EXIT_YEAR,
            f"Exit year must be between {MIN_EXIT_YEAR} and {MAX_EXIT_YEAR}.", "exit_year")
        req(x.exit_multiple > 0, "Exit multiple must be positive.", "exit_multiple")
        req(0 <= x.exit_fee_pct < 0.25, "Exit fee % out of range.", "exit_fee_pct")
        req(0 <= x.mip_pct < 1, "MIP % must be in [0, 1).", "mip_pct")

        if a.recap_year is not None:
            req(1 <= a.recap_year < x.exit_year, "Dividend recap must occur before the exit year.", "recap_year")
            req(a.recap_turns >= 0, "Recap leverage cannot be negative.", "recap_turns")
        req(0 <= a.mgmt_fee_pct < 0.2 and 0 <= a.carry_pct < 1, "Fund fee assumptions out of range.", "mgmt_fee_pct")
        return self

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["transaction"]["close_date"] = self.transaction.close_date.isoformat()
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LBOAssumptions:
        try:
            c = dict(d["company"])
            c["historicals"] = tuple(HistoricalYear(**h) for h in c.get("historicals", ()))
            t = dict(d["transaction"])
            if isinstance(t.get("close_date"), str):
                t["close_date"] = date.fromisoformat(t["close_date"])
            o = dict(d["operating"])
            o["revenue_growth"] = as_vector(o["revenue_growth"])
            o["ebitda_margin"] = as_vector(o["ebitda_margin"])
            for k in ("cogs_pct", "sga_pct"):
                o[k] = as_vector(o[k]) if o.get(k) else ()
            return LBOAssumptions(
                company=CompanyData(**c),
                transaction=TransactionAssumptions(**t),
                operating=OperatingAssumptions(**o),
                debt=DebtAssumptions(**d["debt"]),
                exit=ExitAssumptions(**d.get("exit", {})),
                advanced=AdvancedAssumptions(**d.get("advanced", {})),
            ).validate()
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"Malformed assumptions file: {exc}") from exc


def shift(values: Iterable[float], delta: float) -> tuple[float, ...]:
    """Parallel shift of a driver vector."""
    return tuple(v + delta for v in values)


__all__ = [
    "HORIZON", "MIN_EXIT_YEAR", "MAX_EXIT_YEAR", "DISPLAY_YEARS", "as_vector", "shift", "replace",
    "HistoricalYear", "CompanyData", "TransactionAssumptions", "OperatingAssumptions",
    "DebtAssumptions", "ExitAssumptions", "AdvancedAssumptions", "LBOAssumptions",
]
