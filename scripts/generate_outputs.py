"""Build the demo Excel model and IC report without launching the UI.

    python scripts/generate_outputs.py [assumptions.json]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exporters.excel import write_workbook  # noqa: E402
from exporters.pdf import write_ic_report  # noqa: E402
from financials.company import demo_assumptions, load_assumptions_json  # noqa: E402
from transaction.model import run_lbo  # noqa: E402


def main() -> None:
    a = load_assumptions_json(sys.argv[1]) if len(sys.argv) > 1 else demo_assumptions()
    r = run_lbo(a)
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    tag = "demo" if a.company.is_synthetic else "model"
    xlsx = write_workbook(r, str(out / f"LeverEdge_LBO_Model_{tag}.xlsx"))
    pdf = write_ic_report(r, str(out / f"LeverEdge_IC_Report_{tag}.pdf"))
    print(f"MOIC {r.returns.moic:.2f}x | gross IRR {r.returns.irr:.1%} | checks "
          f"{sum(ok for ok, _ in r.checks().values())}/{len(r.checks())}")
    print(xlsx)
    print(pdf)


if __name__ == "__main__":
    main()
