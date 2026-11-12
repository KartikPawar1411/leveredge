"""Number formatting shared by the PDF report and the Streamlit app."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _bad(x: Any) -> bool:
    return x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def money(x: Any, dp: int = 1) -> str:
    if _bad(x):
        return "n/a"
    if abs(x) < 0.5 * 10 ** -dp:
        return "-"
    return f"({abs(x):,.{dp}f})" if x < 0 else f"{x:,.{dp}f}"


def pct(x: Any, dp: int = 1) -> str:
    return "n/a" if _bad(x) else f"{x:.{dp}%}"


def mult(x: Any, dp: int = 2) -> str:
    return "n/a" if _bad(x) else f"{x:.{dp}f}x"


def row_kind(label: str) -> str:
    low = label.lower()
    if "status" in low or "covenant" in low and "(x)" not in low:
        return "text"
    if any(k in low for k in ("growth", "margin", "irr", "rate", "%")):
        return "pct"
    if "(x)" in low or "/ ebitda" in low or "/ cash interest" in low or "dscr" in low or "moic" in low or "multiple" in low:
        return "mult"
    if "flags" in low or low == "exit year":
        return "int"
    return "money"


def fmt_value(v: Any, kind: str) -> str:
    if isinstance(v, str):
        return v
    if kind == "pct":
        return pct(v)
    if kind == "mult":
        return mult(v)
    if kind == "int":
        return "n/a" if _bad(v) else f"{v:.0f}"
    return money(v)


def format_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Format a statement-style frame (rows = line items) into display strings."""
    out = pd.DataFrame(index=df.index, columns=df.columns, dtype=object)
    for label in df.index:
        kind = row_kind(str(label))
        for c in df.columns:
            v = df.loc[label, c]
            out.loc[label, c] = "" if (kind != "text" and isinstance(v, float) and math.isnan(v)) else fmt_value(v, kind)
    return out
