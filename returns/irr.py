"""IRR and XIRR solvers (SciPy Brent root-finding on the NPV function)."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from scipy.optimize import brentq

from financials.errors import ModelError

_LO, _HI = -0.999999, 100.0


def npv(rate: float, cash_flows: Sequence[float]) -> float:
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cash_flows))


def _solve(f, flows: Sequence[float]) -> float:
    has_out = any(cf < 0 for cf in flows)
    has_in = any(cf > 0 for cf in flows)
    if not has_out:
        raise ModelError("IRR is undefined: no investment (negative) cash flow.")
    if not has_in:
        return -1.0  # total loss of invested capital
    lo, hi = f(_LO), f(_HI)
    if lo * hi > 0:
        raise ModelError("IRR could not be bracketed for the supplied cash flows.")
    return float(brentq(f, _LO, _HI, xtol=1e-12, rtol=1e-12, maxiter=500))


def irr(cash_flows: Sequence[float]) -> float:
    """Periodic IRR for equally spaced (annual) cash flows; index = period."""
    flows = [float(c) for c in cash_flows]
    return _solve(lambda r: npv(r, flows), flows)


def xirr(dates: Sequence[date], cash_flows: Sequence[float]) -> float:
    """Dated IRR, actual/365 day count (same convention as Excel XIRR)."""
    if len(dates) != len(cash_flows):
        raise ModelError("XIRR needs one date per cash flow.")
    flows = [float(c) for c in cash_flows]
    d0 = dates[0]
    yrs = [(d - d0).days / 365.0 for d in dates]
    return _solve(lambda r: sum(cf / (1.0 + r) ** y for cf, y in zip(flows, yrs, strict=True)), flows)


def anniversary(d: date, years: int) -> date:
    """Date `years` after d (29 Feb falls back to 28 Feb)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)
