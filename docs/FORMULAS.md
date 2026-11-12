# LeverEdge — Financial Formula Documentation

Formulas are listed in the order the engine calculates them. Year 0 is the closing date; years 1 to 7 are projection years. Amounts are in one currency unit (the demo uses millions); rates are decimals.

## 1. Entry valuation (`transaction/entry.py`)

```
Purchase enterprise value (EV) = Entry multiple × LTM EBITDA
Existing net debt              = Existing debt − Existing cash
Equity purchase price          = EV − Existing debt + Existing cash
```

EV is the price of the operations. The equity purchase price is what sellers receive. They differ by net debt, and the model never uses one in place of the other.

## 2. Sources and Uses (`capital_structure/sources_uses.py`)

**Uses**

```
Purchase of seller equity   = Equity purchase price
Refinance existing debt     = Existing debt
Transaction fees            = Transaction fee % × EV
Financing fees              = Financing fee % × (Senior + Subordinated + Revolver drawn at close)
Cash funded to balance sheet= max(Minimum cash − Existing cash, 0)
```

**Sources**

```
Senior term loan            = Senior leverage × LTM EBITDA
Subordinated debt           = Sub leverage × LTM EBITDA
Revolver                    = Draw at close (≤ commitment, validated)
Existing target cash used   = max(Existing cash − Minimum cash, 0)   if the toggle is on, else 0
Management rollover         = Rollover % × Equity purchase price
Sponsor equity              = Total uses − all other sources          (residual)
```

**Why nothing is double counted**

- Sellers are paid for target cash once, inside the equity purchase price. The buyer then owns that cash: any excess over the minimum can fund the deal (a source), or a shortfall is topped up (a use). The two are mutually exclusive.
- Existing debt appears once as a deduction in the equity bridge and once as the refinancing use. Together, `equity purchase + refinancing = EV + existing cash`.
- Financing fees are a use. Debt proceeds are recorded gross, not net of fees.
- Rollover: the full equity purchase is a use and the rolled amount is a source, so sponsor cash falls by exactly the rollover.

**Reconciliation identities** (tested)

```
Total sources = Total uses
Closing cash  = Existing cash − Cash used + Cash funded
Total equity  = EV + Transaction fees + Financing fees − (Closing debt − Closing cash)
Sponsor ownership = Sponsor equity / (Sponsor equity + Rollover equity)
Equity contribution = Total equity / Total sources      (flagged if below the minimum)
Closing leverage    = Total debt / LTM EBITDA           (flagged if above the structuring limit)
```

Sponsor equity ≤ 0 raises a validation error.

## 3. Operating forecast (`operating_model/forecast.py`)

```
Revenue_t   = Revenue_{t−1} × (1 + growth_t)
EBITDA_t    = Revenue_t × margin_t                                   (driver mode)
EBITDA_t    = Revenue_t − COGS%_t × Revenue_t − SG&A%_t × Revenue_t − Fixed costs   (detailed mode)
D&A_t       = D&A % × Revenue_t
EBIT_t      = EBITDA_t − D&A_t
Capex_t     = Capex % × Revenue_t
NWC_t       = NWC % × Revenue_t          (NWC_0 = balance at close)
ΔNWC_t      = NWC_t − NWC_{t−1}          (positive = cash outflow)
```

The forecast is independent of the capital structure. Debt proceeds are financing, not income.

## 4. Debt schedule and cash waterfall (`debt/schedule.py`)

For each year, in this order:

```
1. Cash interest (default, "beginning"):
     Senior   = Opening balance × rate
     Sub      = Opening balance × rate
     Revolver = Opening drawn × rate + (Commitment − Opening drawn) × undrawn fee
2. Cash taxes  = Tax rate × max(EBIT − Total cash interest, 0)
3. CFADS       = EBITDA − Cash taxes − Capex − ΔNWC
4. Mandatory repayment (senior, sub)
               = Opening balance                         if year ≥ maturity
               = min(Opening balance, Amort % × Original principal)   otherwise
5. Pre-revolver cash = Opening cash + CFADS − Interest − Mandatory
6. Revolver draw     = min( max(Minimum cash − Pre-revolver cash, 0), Commitment − Opening drawn )
7. Excess cash       = max(Pre-revolver cash − Minimum cash, 0)
     Revolver repayment = min(Opening drawn, Excess cash)
     Sweep budget       = Sweep % × (Excess cash − Revolver repayment)
     Senior sweep       = min(Opening − Mandatory, Sweep budget)
     Sub sweep          = min(Opening − Mandatory, Sweep budget − Senior sweep)   only if prepayable
8. Closing balance   = Opening + Additions − Mandatory − Optional
9. Closing cash      = Pre-revolver cash + Draw − Revolver repayment − Sweeps
   Liquidity shortfall = max(Minimum cash − Closing cash, 0)
```

**Interest convention.** The default charges interest on the opening balance. Interest therefore does not depend on the current year's repayments and the model has no circular reference. This slightly overstates interest when debt is repaid during the year. The optional "average" method charges interest on `(Opening + Closing) / 2`; because closing balances depend on interest (through taxes and the sweep), it is solved by fixed-point iteration to a tolerance of 1e-10.

**Guarantees.** Every repayment is capped at the outstanding balance, so balances cannot go negative. Revolver draws are capped at the undrawn commitment. If cash is still below the minimum after a full draw, the shortfall is reported and flagged; the model does not invent funding.

**Credit statistics**

```
Total leverage     = Total debt / EBITDA
Senior leverage    = (Revolver + Senior) / EBITDA
Interest coverage  = EBITDA / Total cash interest
DSCR               = CFADS / (Cash interest + Mandatory repayments)
```

## 5. Exit valuation (`returns/exit.py`)

```
Exit EV            = Exit multiple × EBITDA in the exit year
Net debt at exit   = Total debt − Cash                (closing balances of the exit year)
Exit costs         = Exit fee % × Exit EV
Equity value       = max(Exit EV − Net debt − Exit costs, 0)
MIP payout         = MIP % × max(Equity value − Total invested equity, 0)
Sponsor proceeds   = (Equity value − MIP payout) × Sponsor ownership
```

The MIP is modelled as options struck at the entry equity value: management shares only in value created above invested equity.

## 6. Sponsor returns (`returns/sponsor.py`, `returns/irr.py`)

```
Cash flow_0        = −Sponsor equity
Cash flow_t        = Sponsor share of dividends_t           (+ Sponsor proceeds in the exit year)
MOIC               = (Σ distributions + exit proceeds) / Sponsor equity
Gross IRR          = r such that Σ CF_t / (1 + r)^t = 0                (annual periods)
XIRR               = r such that Σ CF_i / (1 + r)^(days_i / 365) = 0   (dated flows, actual/365)
```

Both are solved with SciPy's Brent method. With a single exit cash flow, `IRR = MOIC^(1/N) − 1`. If nothing is returned, IRR is −100%.

**Investor-level versus company-level.** The unlevered IRR answers "what does the business earn regardless of financing":

```
CF_0 = −(EV + Transaction fees)
CF_t = EBITDA − Tax rate × max(EBIT, 0) − Capex − ΔNWC      (+ Exit EV − Exit costs in the exit year)
```

Leverage uplift = Gross IRR − Unlevered IRR. Leverage raises equity returns only while the unlevered return exceeds the after-tax cost of debt, and it magnifies losses symmetrically. It never changes EBITDA.

**Net IRR** is reported only when fund-level costs are switched on:

```
Annual fee   = Management fee % × Sponsor equity          (years 1..N)
Carry        = Carry % × (Profit after fees)              if pre-carry IRR ≥ hurdle and profit > 0, else 0
Net flows    = Gross flows − fees − carry (at exit)
```

This is a deal-level approximation with a full catch-up, not a fund waterfall.

## 7. Return attribution

Total equity value creation (all shareholders) decomposes exactly:

```
Exit equity + Dividends − Invested equity
  = (Exit EBITDA − Entry EBITDA) × Entry multiple          EBITDA growth
  + (Exit multiple − Entry multiple) × Exit EBITDA         Multiple expansion
  + (Net debt at close − Net debt at exit) + Dividends     Debt paydown and cash generation
  − (Entry fees + Exit costs)                              Transaction costs
  + Floor adjustment                                       (non-zero only if equity is wiped out)
```

## 8. Advanced features

**Dividend recapitalisation.** At the end of the recap year, incremental senior debt of `Recap turns × EBITDA` is raised. `Debt raised = Financing fees + Dividend`, so closing cash is unchanged. Interest on the new debt starts the following year. The sponsor receives its ownership share of the dividend in that year's cash flow.

**Covenants.** Optional maximum `Total debt / EBITDA` and minimum `EBITDA / cash interest`, tested each year of the holding period.

**Risk flags** (`risk/flags.py`): negative cash, liquidity shortfall, unfunded debt maturity, DSCR below 1.0x, covenant breaches, closing leverage above the limit, equity contribution below the minimum, equity wiped out at exit.

## 9. Hand-calculated reference case

Used in `tests/conftest.py` (`simple` fixture).

Inputs: revenue 1,000 flat; margin 20%; D&A 2%; capex 2%; NWC 10% (constant); tax 25%; entry 8.0x; existing debt 300; cash 50; minimum cash 50; no fees; senior 4.0x at 10%, no amortisation, 100% sweep; exit year 3 at 8.0x.

```
EV = 8.0 × 200 = 1,600        Equity price = 1,600 − 300 + 50 = 1,350
Uses = 1,350 + 300 = 1,650    Debt = 800    Sponsor equity = 850
```

| | Year 1 | Year 2 | Year 3 |
|---|---|---|---|
| Opening debt | 800.0000 | 725.0000 | 644.3750 |
| Interest (10%) | 80.0000 | 72.5000 | 64.4375 |
| Taxes = 25% × (180 − interest) | 25.0000 | 26.8750 | 28.890625 |
| CFADS = 200 − taxes − 20 | 155.0000 | 153.1250 | 151.109375 |
| Sweep = CFADS − interest | 75.0000 | 80.6250 | 86.671875 |
| Closing debt | 725.0000 | 644.3750 | 557.703125 |

```
Exit EV = 1,600    Net debt = 557.703125 − 50 = 507.703125    Equity = 1,092.296875
MOIC = 1,092.296875 / 850 = 1.28506x    IRR = 1.28506^(1/3) − 1 = 8.72%
```

## 10. Demo transaction hand checks

```
EV = 10.0 × 100 = 1,000                 Equity price = 1,000 − 180 + 15 = 835
Fees = 2% × 1,000 + 2.5% × 550 = 33.75  Uses = 835 + 180 + 33.75 = 1,048.75
Sources: debt 550 + excess cash 5       Sponsor equity = 1,048.75 − 555 = 493.75
```
