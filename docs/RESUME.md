# LeverEdge â€” Resume-Ready Project Description

## Paste-ready resume entry

**LeverEdge â€” LBO and Private Equity Investment Modeling Platform** | Python, pandas, SciPy, Streamlit, Excel | `github.com/KartikPawar1411/leveredge`

- Built an end-to-end leveraged buyout model (entry valuation, reconciled Sources and Uses, three-tranche debt schedule with cash sweep, exit and sponsor returns) with a Streamlit dashboard and 80 automated tests, including hand-calculated reference cases.
- Generated a 13-sheet Excel LBO model with live formulas whose IRR, MOIC and net debt match the Python engine to six decimals when recalculated in Excel, plus an Investment Committee memo whose recommendation is computed from model outputs.
- Implemented sensitivity heatmaps and Bull/Base/Bear scenarios in which every cell reruns the full model, with liquidity, maturity and covenant breaches flagged; the demo deal is rated marginal at 10.0x and approvable near 9.6x.

For a finance-first resume, lead with the first and third bullets; for a technical one, lead with the first and second.

## Before you put the link on a resume

1. The repository is public at https://github.com/KartikPawar1411/leveredge; open it logged out once to see what a recruiter sees.
2. Check the name in `LICENSE`.
3. Be ready to rebuild the hand-calculated case in `docs/FORMULAS.md` section 9 on paper; it is the most likely interview follow-up.
4. Optional: deploy the app to Streamlit Community Cloud and add the live URL beside the GitHub link.

## One line

Built LeverEdge, a Python LBO modeling platform that takes a buyout from entry valuation and Sources and Uses through a three-tranche debt schedule to sponsor IRR and MOIC, with a Streamlit dashboard, a formula-driven Excel export and an auto-generated Investment Committee report.

## Resume bullets (pick three or four)

- Built an end-to-end leveraged buyout model in Python (pandas, NumPy, SciPy): enterprise-to-equity bridge, reconciled Sources and Uses with sponsor equity as the residual, seven-year operating forecast, and exit valuation for holds of three to seven years.
- Engineered a debt schedule with revolver, senior and subordinated tranches, mandatory amortisation, maturities, prioritised cash sweep and minimum-cash constraints; liquidity shortfalls, unfunded maturities and covenant breaches are flagged, never absorbed.
- Implemented sensitivity heatmaps and Bull/Base/Bear scenarios in which every cell reruns the full model (about 1.5 ms per run), plus return attribution that reconciles exactly to equity value creation.
- Generated a 13-sheet Excel LBO model with live formulas and banking colour conventions; verified in desktop Excel that IRR, MOIC and net debt match the Python engine to six decimals across four deal configurations.
- Wrote 80 pytest tests, including a hand-calculated reference deal and property-style checks over 81 parameter combinations, covering Sources and Uses reconciliation, debt balances, cash sweep, IRR/XIRR and scenario recomputation.
- Produced an Investment Committee PDF whose recommendation is computed from model outputs: returns against hurdles, downside resilience, risk flags and the break-even entry multiple.

## Short paragraph (LinkedIn or portfolio)

LeverEdge is a private equity transaction modeling tool I built in Python. It models the acquisition, financing, operation, deleveraging and exit of a company and reports sponsor returns alongside the unlevered company-level return, so the effect of leverage is explicit. The calculation engine is independent of the Streamlit interface and is covered by 80 automated tests, including hand-calculated cases. It exports an editable Excel model with real formulas and an Investment Committee memo whose conclusion follows from the numbers. The bundled demo company is synthetic.

## Skills demonstrated

LBO and M&A modeling, leveraged finance (tranching, cash sweep, covenants), valuation bridges, IRR/XIRR, scenario and sensitivity analysis, Python, pandas, NumPy, SciPy, Streamlit, Plotly, XlsxWriter, ReportLab, pytest, modular software design.

## Talking points

- Why the default interest method is non-circular, and how the optional average-balance method is solved.
- How target cash, refinancing and fees are kept from being double counted, and the identity that proves it.
- Why the demo deal is assessed as marginal at 10.0x (18.1% IRR against a 20% hurdle) and approvable near 9.6x.
- Why the Excel sensitivity sheets are static, and how the tie-out cell keeps that honest.
