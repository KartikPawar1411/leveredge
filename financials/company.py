"""Target-company data loading: the synthetic demo company and user uploads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import IO

import pandas as pd

from .assumptions import HistoricalYear, LBOAssumptions
from .errors import ValidationError

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEMO_FILE = DATA_DIR / "demo_transaction.json"
HIST_COLUMNS = ["year", "revenue", "ebitda", "da", "capex", "nwc", "taxes"]


def demo_assumptions() -> LBOAssumptions:
    """The single, clearly-labelled synthetic demonstration transaction."""
    with open(DEMO_FILE, encoding="utf-8") as fh:
        return LBOAssumptions.from_dict(json.load(fh))


def load_assumptions_json(source: str | Path | IO) -> LBOAssumptions:
    """Load a full assumption set previously saved by the app."""
    try:
        if hasattr(source, "read"):
            raw = source.read()
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        else:
            with open(source, encoding="utf-8") as fh:
                data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"Could not read assumptions file: {exc}") from exc
    return LBOAssumptions.from_dict(data)


def parse_historicals(source: str | Path | IO) -> tuple[HistoricalYear, ...]:
    """Parse a historical-financials CSV.

    Required columns: year, revenue, ebitda, da, capex, nwc, taxes
    (EBIT is derived as EBITDA - D&A so the statement always foots).
    """
    try:
        df = pd.read_csv(source)
    except Exception as exc:  # pandas raises several unrelated types
        raise ValidationError(f"Could not parse historical financials CSV: {exc}") from exc
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in HIST_COLUMNS if c not in df.columns]
    if missing:
        raise ValidationError(f"Historical CSV is missing columns: {', '.join(missing)}")
    if df.empty:
        raise ValidationError("Historical CSV has no rows.")
    num = df[HIST_COLUMNS[1:]].apply(pd.to_numeric, errors="coerce")
    if num.isna().any().any():
        raise ValidationError("Historical CSV contains non-numeric or blank values.")
    if (num["revenue"] <= 0).any():
        raise ValidationError("Historical revenue must be positive in every year.")
    return tuple(
        HistoricalYear(year=str(df.loc[i, "year"]), **{c: float(num.loc[i, c]) for c in HIST_COLUMNS[1:]})
        for i in df.index
    )


def historicals_frame(hist: tuple[HistoricalYear, ...]) -> pd.DataFrame:
    if not hist:
        return pd.DataFrame()
    rows = {
        "Revenue": [h.revenue for h in hist],
        "Revenue growth": [float("nan")] + [hist[i].revenue / hist[i - 1].revenue - 1 for i in range(1, len(hist))],
        "EBITDA": [h.ebitda for h in hist],
        "EBITDA margin": [h.ebitda / h.revenue for h in hist],
        "D&A": [h.da for h in hist],
        "EBIT": [h.ebit for h in hist],
        "Taxes": [h.taxes for h in hist],
        "Capital expenditure": [h.capex for h in hist],
        "Net working capital": [h.nwc for h in hist],
    }
    return pd.DataFrame(rows, index=[h.year for h in hist]).T
