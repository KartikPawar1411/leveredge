# LeverEdge — Technical Interview Questions and Answers

Numbers quoted are from the synthetic demo transaction (illustrative only).

## LBO mechanics

**1. Walk me through an LBO model.**
Set the purchase price (entry multiple × LTM EBITDA). Build Sources and Uses: uses are the equity purchase, debt refinancing, fees and any cash top-up; sources are the debt tranches, any target cash used and rollover, with sponsor equity as the plug. Project operations to get EBITDA, then cash flow available for debt service. Run the debt schedule: interest, mandatory amortisation, then sweep excess cash. At exit, apply a multiple to exit EBITDA, subtract net debt and costs to get equity, and compute MOIC and IRR on the sponsor's cash flows.

**2. Why is enterprise value not the amount the sponsor pays the sellers?**
EV values the operations for all capital providers. Sellers own only the equity, so they receive EV less net debt. In the demo EV is 1,000 but the equity purchase price is 835 (1,000 − 180 debt + 15 cash). The buyer separately repays the 180 of debt.

**3. How is target cash treated, and how do you avoid double counting it?**
The seller is paid for cash once, in the equity price. After closing the buyer owns it. If cash exceeds the operating minimum, the excess can be a source; if it is below, the gap is a use. The model enforces that these are mutually exclusive and tests that invested equity equals EV plus fees less closing net debt.

**4. Why is sponsor equity the plug?**
Debt capacity is set by lenders (multiples of EBITDA) and the price by negotiation. Whatever the debt, rollover and available cash do not cover must be equity. If the plug comes out non-positive the structure is infeasible, and the engine raises an error instead of returning a number.

**5. What are the three sources of return in an LBO?**
EBITDA growth, multiple expansion and deleveraging (cash generation used to repay debt). In the demo, value creation of 640 splits into 486 from EBITDA growth, 0 from multiple expansion (10.0x in, 10.0x out), 210 from debt paydown, less 56 of transaction costs. The model proves the decomposition sums exactly.

**6. Does adding debt increase the company's earnings?**
No. Debt is a financing source; EBITDA is identical at any leverage. Leverage lifts equity returns only when the asset's unlevered return exceeds the after-tax cost of debt. The demo's unlevered IRR is 14.0% against senior debt costing 8.5% pre-tax, so leverage lifts sponsor IRR to 18.1%. If the asset return fell below the cost of debt, the same leverage would reduce returns.

**7. How does interest create circularity, and how did you handle it?**
Interest on average balances depends on repayments; repayments depend on cash flow; cash flow depends on interest and on taxes, which also depend on interest. The default here charges interest on opening balances, which breaks the loop and keeps every cell traceable. An optional average-balance method solves the loop by fixed-point iteration. The Excel export uses the opening-balance method so the workbook has no circular reference.

**8. Explain the cash sweep and repayment priority.**
After interest and mandatory amortisation, cash above the minimum balance is "excess". It first repays the revolver in full, then the sweep percentage of the remainder prepays the senior term loan, then subordinated debt if it is prepayable. Subordinated debt often carries call protection, so it defaults to non-prepayable. Each repayment is capped at the balance so nothing goes negative.

**9. What happens when cash flow cannot cover debt service?**
The revolver is drawn up to its commitment to restore minimum cash. If that is not enough, the model records a liquidity shortfall, lets cash fall below the minimum, and raises critical flags (and an unfunded-maturity flag if a tranche was due). It does not plug the gap.

**10. Why can a dividend recap raise IRR but lower MOIC?**
It returns cash earlier, which IRR rewards, but adds financing fees and interest and leaves more debt at exit, so total proceeds fall. The model reconciles `debt raised = fees + dividend` and shows both effects side by side.

**11. IRR versus MOIC: when do they disagree?**
MOIC ignores time; IRR penalises it. In the exit-timing table, MOIC rises every year you hold, while IRR typically peaks early and decays. A sponsor optimising IRR sells sooner; one optimising absolute gain holds longer.

**12. Gross versus net IRR?**
Gross is investment-level, before fund costs. Net is what LPs receive after management fees and carried interest. LeverEdge reports net IRR only when those costs are explicitly modelled, and labels it a deal-level approximation.

**13. Which assumption is the model most sensitive to?**
Entry and exit multiples. Each 0.5x of entry multiple moves demo IRR by roughly 2 to 3 points, and one turn of exit contraction takes IRR from 18.1% to about 14.9%. The break-even entry multiple for a 20% IRR is 9.62x versus 10.0x assumed.

**14. How do covenants enter the analysis?**
As tests on the projected ratios: maximum total debt / EBITDA and minimum EBITDA / cash interest. The demo passes in the base case, but the bear case breaches interest coverage in years 1 to 3, which is why the assessment asks for covenant headroom.

**15. How is the management incentive pool modelled?**
As options struck at entry equity value: management receives a percentage of exit equity above total invested equity. It dilutes the sponsor only when value is created.

## Modelling judgement

**16. What simplifications would you remove first for a live deal?**
A floating-rate curve with hedging, NOLs and interest-deductibility limits, amortisation of financing fees, quarterly periods with a mid-year exit, a revolver maturity, PIK toggles, and a proper fund waterfall.

**17. Why floor exit equity at zero?**
Limited liability: equity holders cannot lose more than they invested. The model keeps the unfloored figure too and flags the wipe-out, so the size of the lender shortfall stays visible.

**18. Why is DSCR below 1.0x in a maturity year not automatically a default?**
A bullet maturity is normally refinanced, not repaid from one year's cash flow. The model flags it and shows whether the revolver can bridge it; the user decides whether a refinancing is credible.

## Software design

**19. How is the code structured, and why?**
Pure-Python packages by domain (entry, capital structure, operating model, debt, returns, scenarios, risk, exporters) with no UI imports. One function, `run_lbo`, takes immutable, validated assumptions and returns an immutable result. The UI, exporters, scenarios and sensitivities all call it, so there is one source of truth.

**20. How do you know the sensitivity tables are right?**
Each cell calls `run_lbo` with modified assumptions; nothing is scaled from the base IRR. Tests recompute sampled cells independently, check the centre cell equals the base case, and check monotonicity (higher price lowers IRR). Infeasible cells return `n/a`.

**21. How did you test financial correctness?**
A fully hand-calculated three-year deal with exact expected balances, taxes, CFADS, MOIC and IRR; closed-form IRR and XIRR cases; reconciliation identities; property-style sweeps over 81 parameter combinations asserting no negative balances and that minimum cash is either held or flagged; and directional tests (higher price lowers returns, higher rates reduce repayment).

**22. How do you know the Excel export matches the Python engine?**
The workbook re-implements the model in formulas. A script opens it in desktop Excel, recalculates, and reads the Model Checks sheet, which ties IRR, MOIC, sponsor equity, exit proceeds, net debt and unlevered IRR to the engine. Four configurations matched to six decimals.

**23. Why are the Excel sensitivity sheets static?**
XlsxWriter cannot create Excel data tables. Writing a hand-scaled approximation would violate the "every cell is a full rerun" rule, so the sheets hold engine-computed values, are labelled static, and carry a live tie-out cell that shows when the workbook has moved off the exported base case.

**24. How is the investment conclusion generated?**
From the numbers: base IRR and MOIC against user-set targets, bear-case capital preservation, critical flags, value-creation mix, and the break-even entry multiple. Tests confirm the verdict changes when the price or covenants change.

**25. What would you do differently at scale?**
Vectorise the model across scenarios for Monte Carlo runs, add a persistence layer for deal versions, and expose the engine as an API so the Excel add-in and the web UI share one calculation service.
