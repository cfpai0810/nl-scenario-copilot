# NL Scenario Modelling Copilot

Ask a finance model a "what if" in plain English and get an answer you can
defend. The request is parsed by AI into a structured change, validated
deterministically in Python, run through a driver-based forecast, and
explained with its assumptions stated.

This project reuses the forecast engine from
[driver-based-rolling-forecast](https://github.com/cfpai0810/driver-based-rolling-forecast)
unchanged, and wraps a natural-language front end and back end around it.

---

## What it does

Two ways to interrogate the same base forecast.

**Single what-if.** Type something like "cut marketing spend by 20 percent and
delay hiring by one month". The copilot parses that into structured changes,
validates each one, echoes back what it understood, reruns the model, and
explains the impact against the base.

**Three-case analysis.** Pick up to three drivers and flex them together
across a pessimistic, realistic and optimistic case. The cases can be derived
from your own historical volatility, set from a standard band, or entered by
hand. The result is a full profit and loss across all three cases beside the
base.

In both paths the AI reads the request and writes the commentary. Every
number is computed in Python.

The data in this repository is illustrative sample data for a fictional entity.

---

## The design rule

The language model never does arithmetic.

It extracts structure and numbers from the request, and it narrates the result
once the numbers exist. Everything in between is deterministic Python: the
validation, the model changes, the forecast, the deltas. This matters because
language models are reliable at language and unreliable at multi-step
calculation, so the trust boundary is drawn exactly where the reliability
boundary is.

The model's output is treated as untrusted throughout. A parsed change has to
clear a validation gate before anything runs, which catches the failure modes
that matter in practice: a percentage arriving as 20 instead of 0.20, a sign
that contradicts the verb, a value outside a sane range, an operation that
makes no sense for that driver.

---

## How a request becomes a result

```
plain English request
      |
      v
step3_scenario_parser   Claude extracts a structured scenario as JSON.
                        Structure and numbers only, no arithmetic.
      |
      v
step4_validator         Deterministic gate. Every change checked against the
                        real line items, the legal operations for that driver
                        type, and sane bounds. Never calls Claude.
                        Verdict: CLEAR, AMBIGUOUS, IMPOSSIBLE, or TOO_MANY.
      |
      v
echo back and confirm   The parsed scenario is restated in plain English so a
                        misparse is visible before anything runs.
      |
      v
step5_scenario_engine   Changes applied to DEEP COPIES of the base data. The
                        reused engine runs twice, base and scenario. Full
                        profit and loss deltas computed.
      |
      v
step6_explainer         Python derives the key takeaways from its own numbers.
                        Claude explains the result and states what was held
                        constant. Report, data file and audit record written.
```

The three-case path follows the same spine, with `step7_scenario_spread`
setting the cases and `step8_three_case` running the base plus three cases.

A verdict of IMPOSSIBLE refuses the request and explains why. A verdict of
AMBIGUOUS asks one clarifying question and then asks the user to rephrase;
it does not yet loop and re-validate the answer. Refused, ambiguous and
cancelled requests are all recorded in the audit log, since an attempt that
did not run is still worth a trace.

---

## The four operations

A scenario can only do things the model actually supports. The schema defines
that space, and the validator enforces it.

| Operation | What it does | Valid for |
|-----------|--------------|-----------|
| `scale_driver` | Multiply a driver value by a factor | Revenue, COGS, IT Infrastructure, R&D Expense |
| `set_driver` | Replace a driver value outright | Revenue, COGS, IT Infrastructure, R&D Expense |
| `scale_schedule` | Scale a schedule quantity | Personnel Cost, Marketing Spend |
| `shift_schedule` | Move a schedule later or earlier by whole months | Personnel Cost, Marketing Spend |

A request that asks for something outside this space is refused with an
explanation rather than approximated.

---

## Setting the three cases

Three honest ways, and the copilot says which one it used.

**From your history.** Python measures the historical volatility of the driver
from the actuals and sets the cases one standard deviation either side of the
base plan. If that spread turns out too narrow to be useful, the copilot says
so and offers a wider band rather than presenting three near-identical cases
as if they were meaningful.

**From a standard band.** A fixed band either side of the base, chosen by the
user and recorded as such.

**By hand.** All three values entered directly.

Which end is pessimistic depends on the driver. Lower revenue growth is bad
news; a lower cost margin is good news. The cases are labelled by outcome, not
by arithmetic sign.

---

## Stating what was held constant

A scenario number without its assumptions is misleading, so the copilot names
them.

If marketing spend is cut, this model does not reduce revenue, because revenue
is forecast on its own seasonal trend rather than from customer acquisition.
That is a real limitation, and the report says so plainly instead of
presenting a cost saving as free profit. In a three-case run the report also
notes that the realistic case is the base plan, so its figures match the base
by design.

Naming a model's boundaries is the judgement a good analyst adds. It is built
into the output rather than left to the reader.

---

## Human sign-off

A scenario result is only valid within its assumptions, so the sign-off
checks that they were read:

```bash
python review.py "Your Name"
```

The script surfaces the latest run, what was changed, the resulting impact,
and the assumptions held constant, then requires an explicit confirmation
before recording who signed off and when.

---

## How to run

```bash
git clone https://github.com/cfpai0810/nl-scenario-copilot.git
cd nl-scenario-copilot
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt
```

Add your Anthropic API key to a `.env` file:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Run:

```bash
python main.py
```

The menu offers a single what-if or a three-case analysis. Reports and data
files are written to `output/`.

---

## Project structure

```
main.py                          Pipeline orchestration and the guided menu
config.py                        Line items, operations, validation rules, bands
review.py                        Sign-off that requires reading the assumptions
requirements.txt

src/
  step1_data_loader.py           Reused unchanged from Project 2
  step2_forecast_engine.py       Reused unchanged from Project 2
  step3_scenario_parser.py       Plain English to structured scenario
  step4_validator.py             The deterministic gate. No AI.
  step5_scenario_engine.py       Apply changes, rerun, compute deltas
  step6_explainer.py             Takeaways, commentary, reports, audit
  step7_scenario_spread.py       Three-case spread: derived, band, or manual
  step8_three_case.py            Run base plus three cases

data/                            Five input files, reused from Project 2
docs/                            Flow blueprint and sample outputs
output/                          Generated files (gitignored)
tests/
  test_pipeline.py               85 assertions across 12 test classes
```

---

## Reusing the forecast engine

The engine is called through its DataFrame interface and never modified:

```python
calculate_forecast(actuals_df, drivers_df, operational_df,
                   headcount_df, customer_df,
                   last_actual, forecast_periods, seasonal_year)
```

Because it takes DataFrames rather than reading files itself, a scenario is
just a copy of those DataFrames with changes applied. The base is never
mutated, so the base and scenario runs are genuinely independent states and
the delta between them means something.

---

## Audit trail

Every run appends one record to `output/audit_log.jsonl`, capturing the
verbatim request, the parsed scenario, the validation verdict, the changes
applied, the resulting impact, the assumptions held constant, the report and
data file paths, and a SHA256 hash of all five input files. After sign-off the
record also carries the reviewer, the timestamp, and the assumptions
confirmation.

---

## Test suite

85 assertions across 12 test classes, no real API calls:

```bash
pytest tests/test_pipeline.py -v
```

The classes cover base model loading, all four operations and their
rejections, the classification logic, the echo back, the direction of the
case labelling, the spread modes and narrow detection, historical rate
extraction, the scenario engine including the deep copy isolation, the
three-case runner, JSON parsing, and the explainer helpers.

The strongest test asserts that the realistic case reproduces the base run
line for line, which proves the case machinery is clean. The menus are
deliberately untested: interactivity is injected through `ask` and `confirm`,
so the pipeline is exercised directly.

---

## A note on hiring changes

Hires are whole people. A percentage change to a small hiring schedule can
round back to the same integers, so the standard bands for schedule drivers
are wide enough to produce a visible change.

---

## Tech stack

Python 3.11 · pandas · Anthropic Claude API · python-dotenv · reportlab ·
hashlib · pytest

---

## Related projects

| # | Project | Status |
|---|---------|--------|
| 1 | AI Variance Commentary Engine | Complete |
| 2 | Driver-Based Rolling Forecast Pipeline | Complete |
| 3 | Anomaly Detection and Alert Agent | Complete |
| 4 | NL Scenario Modelling Copilot | Complete |
| 5 | Budget Challenge Assistant | Planned |
| 6 | Agentic Board Pack Generator | Planned |
| 7 | Anaplan to Snowflake to LLM Pipeline | Planned |
| 8 | Cuenta y Cocina Live AI Finance | Planned |
| 9 | AI Governance Playbook | Planned |
