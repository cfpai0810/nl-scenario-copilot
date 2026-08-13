# Running the Scenario Modelling Copilot on your own data

The web app runs on sample data. To use your own figures, download the project and
run it locally with your own Anthropic API key.

The tool reads five CSV files from the `data/` folder. They work together, so getting
one period or line item out of step is the easiest way to get a wrong result. Read the
cross-file rules below before you start editing.

## The five files at a glance

All five use the period format `YYYY-MM` (zero-padded, no day, e.g. `2026-07`).

- `actuals_ytd.csv` - your locked historical P&L, month by month. Its last month
  sets the boundary between actuals and forecast.
- `driver_table.csv` - the forecast control panel: one row per P&L line, saying which
  formula drives it and the base assumption.
- `operational_actuals.csv` - historical headcount and customer counts. Supplies the
  starting headcount for the forecast.
- `headcount_schedule.csv` - the forward hiring plan, one row per forecast month.
- `customer_targets.csv` - the customer acquisition plan, one row per forecast month.

## How the files fit together

The files are not independent. Three rules must hold:

1. **Periods must line up.** `actuals_ytd.csv` is the anchor. Its latest month is the
   boundary; the forecast covers the next six months. `headcount_schedule.csv` and
   `customer_targets.csv` must each have exactly those six months.
   `operational_actuals.csv` must cover the same history, including the boundary month.
2. **Line items must match.** The names in `actuals_ytd.csv` and `driver_table.csv`
   must match and be exactly: Revenue, COGS, Personnel Cost, Marketing Spend, IT
   Infrastructure, R&D Expense.
3. **Every line needs a driver row.** `driver_table.csv` needs one row per line item,
   including the two driven by schedule files (their value is ignored, but the row
   must exist).

If you keep the same six line items and six-month horizon as the sample, these rules
hold by construction and you only need to change the numbers.

## The files in detail

### actuals_ytd.csv (your locked history)

Six rows per month (one per line item). Columns:
- `period` (`YYYY-MM`), `line_item` (one of the six), `actual` (the amount),
  `status` (must be `locked`).

Amounts are positive totals - costs are positive, not negative. The engine applies
the signs when computing profit.

```
period,line_item,actual,status
2025-01,Revenue,980000,locked
2025-01,COGS,401800,locked
2025-01,Personnel Cost,198400,locked
```

Include a full, unbroken run of months up to your boundary. A gap shifts the boundary
and produces the wrong forecast. Revenue uses a seasonal pattern read from a full
prior calendar year, so include at least twelve months of history.

### driver_table.csv (the forecast control panel)

One row per line item. Columns:
- `line_item`, `driver_type` (see below), `driver_value`, `note` (optional, ignored).

Driver types:
- `seasonal_yoy` - annual growth as a fraction (`0.12` = 12%).
- `margin_pct` - cost as a fraction of Revenue (`0.418` = 41.8%).
- `fixed` - absolute monthly amount (`45000.0`).
- `growth_pct` - month-on-month growth as a fraction (`0.06` = 6%).
- `headcount_driven` - set the value to `0.0`; the real input is
  `headcount_schedule.csv`.
- `cac_driven` - set the value to `0.0`; the real input is `customer_targets.csv`.

```
line_item,driver_type,driver_value,note
Revenue,seasonal_yoy,0.12,12% annual growth
COGS,margin_pct,0.418,41.8% of revenue
IT Infrastructure,fixed,45000.0,fixed monthly cost
```

Rates are fractions, not whole numbers. `12` means 1200%, not 12%; write `0.12`.

### operational_actuals.csv (headcount and customer history)

Two rows per month: one for `headcount`, one for `new_customers`. Columns:
- `period` (`YYYY-MM`), `metric`, `value` (whole number), `status` (optional).

```
period,metric,value,status
2025-01,headcount,32,locked
2025-01,new_customers,30,locked
2026-06,headcount,42,locked
```

The engine reads the last month's `headcount` as the starting point for personnel
cost. Make sure this file includes a headcount row for your boundary month.

### headcount_schedule.csv (the hiring plan)

Exactly six rows, one per forecast month. Columns:
- `period` (`YYYY-MM`), `new_hires` (whole number), `attrition_rate` (fraction, e.g.
  `0.015` = 1.5%), `cost_per_head_annual` (fully loaded annual cost per head).

```
period,new_hires,attrition_rate,cost_per_head_annual
2026-07,2,0.015,78000
2026-08,1,0.015,78000
```

`cost_per_head_annual` is yearly. The engine divides by twelve for the monthly cost,
so if you enter a monthly figure here, personnel cost will be twelve times too low.

### customer_targets.csv (the acquisition plan)

Exactly six rows, one per forecast month. Columns:
- `period` (`YYYY-MM`), `target_new_customers` (whole number), `cac` (cost per
  customer), `fixed_campaign` (fixed monthly campaign spend).

```
period,target_new_customers,cac,fixed_campaign
2026-07,45,1200,25000
2026-08,40,1200,25000
```

Monthly marketing spend = `target_new_customers` x `cac` + `fixed_campaign`. Note
that in this model, customer acquisition does not feed back into Revenue; the tool
flags this when it applies.

## Running it

You need an Anthropic API key. The key powers the two model calls (interpreting your
request and explaining the result); all forecasting runs locally.

From the project root:

```
pip install -r requirements.txt
streamlit run streamlit_app/Home.py
```

Paste your key into the sidebar, then open Single what-if or Three-case.

## Constraints worth knowing

- **Periods are `YYYY-MM`, zero-padded.** `2026-07`, not `2026-7`. The tool sorts
  periods as text, so a missing zero breaks the actuals/forecast boundary.
- **The six line items are fixed.** Renaming or adding lines is a code change, not a
  data change.
- **The forecast horizon is six months.** Both schedule files must have exactly six
  rows.
- **Rates are fractions, not percentages.** `0.12`, not `12`.
- **`cost_per_head_annual` is annual, not monthly.**
- **Amounts are unsigned.** Costs are positive numbers.
- **Set the display currency to match your data.** Enter amounts as plain numbers. Set
  `CURRENCY_CODE` and `CURRENCY_SYMBOL` in `config.py` to your currency (defaults to
  euros).
- **`status` must be `locked`** in `actuals_ytd.csv`.

## What stays private

Your data files stay on your machine. The only thing sent externally is what goes to
the Anthropic API under your own key: your scenario request and the computed figures
the model describes. The forecasting itself runs locally. Nothing is sent to this
project's authors or the public app.
