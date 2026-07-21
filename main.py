# =============================================================================
# main.py — NL Scenario Modelling Copilot
# Pass 1: flat script, interactive entry, understand every line
# =============================================================================
#
# The user asks a "what if" in plain English. Claude PARSES it into a
# structured scenario. Python VALIDATES every change against the model's real
# drivers and sane bounds. If clear, it applies the changes to COPIES of the
# base inputs, reruns the reused Project 2 engine for base and scenario,
# computes the deltas, and Claude EXPLAINS the impact, naming any assumption
# held constant. Python calculates; Claude parses and narrates.
# =============================================================================

import copy
import json
import hashlib
import re
import pandas as pd
import anthropic
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from config import (
    ANTHROPIC_API_KEY, MODEL, MAX_TOKENS,
    ACTUALS_FILE, DRIVERS_FILE, OPERATIONAL_FILE, HEADCOUNT_FILE, CUSTOMER_FILE,
    OUTPUT_DIR, AUDIT_LOG, DEFAULT_ENTITY,
    LINE_ITEMS, VALIDATION_RULES, SET_BOUNDS,
    NEG_VERBS, POS_VERBS, SCENARIO_MAX_CHANGES,
)

# The reused Project 2 engine and loaders, called UNCHANGED
from src.step1_data_loader import (
    load_actuals, load_drivers, detect_boundary,
    load_operational_actuals, load_headcount_schedule, load_customer_targets,
)
from src.step2_forecast_engine import calculate_forecast, build_pnl
from src.step3_scenario_parser import call_claude_parse

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# =============================================================================
# STEP 0: Load the base model (the five Project 2 inputs)
# =============================================================================
def load_base_model():
    """Load the five input files into DataFrames plus the forecast boundary."""
    actuals_df     = load_actuals(ACTUALS_FILE)
    drivers_df     = load_drivers(DRIVERS_FILE)
    operational_df = load_operational_actuals(OPERATIONAL_FILE)
    headcount_df   = load_headcount_schedule(HEADCOUNT_FILE)
    customer_df    = load_customer_targets(CUSTOMER_FILE)

    last_actual, forecast_periods = detect_boundary(actuals_df)
    seasonal_year = int(last_actual.split("-")[0]) - 1

    base = {
        "actuals_df":       actuals_df,
        "drivers_df":       drivers_df,
        "operational_df":   operational_df,
        "headcount_df":     headcount_df,
        "customer_df":      customer_df,
        "last_actual":      last_actual,
        "forecast_periods": forecast_periods,
        "seasonal_year":    seasonal_year,
    }
    print("[OK] Base model loaded")
    print("     Line items:       {}".format(len(LINE_ITEMS)))
    print("     Last actual:      {}".format(last_actual))
    print("     Forecast periods: {} ({} to {})".format(
        len(forecast_periods), forecast_periods[0], forecast_periods[-1]))
    return base


# =============================================================================
# STEP 2: VALIDATE — deterministic gate. No Claude here.
# =============================================================================
def validate_change(change):
    """Validate one change. Returns (status, reason, normalised).
    status is OK, NEEDS_CLARIFICATION, or ILLEGAL."""
    target = change.get("target")
    op     = change.get("operation")
    val    = change.get("value")
    periods = change.get("periods", "all")

    if target not in LINE_ITEMS:
        return ("ILLEGAL", "unknown line item '{}'".format(target), None)
    dtype = LINE_ITEMS[target]

    if op not in VALIDATION_RULES:
        return ("ILLEGAL", "unknown operation '{}'".format(op), None)
    rule = VALIDATION_RULES[op]

    if dtype not in rule["legal"]:
        return ("ILLEGAL",
                "operation '{}' is not valid for {} (driver {})".format(op, target, dtype),
                None)

    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return ("ILLEGAL", "value for {} is not a number: {!r}".format(target, val), None)

    if rule["value_type"] == "int" and float(val) != int(val):
        return ("ILLEGAL",
                "{} shift must be whole months, got {}".format(target, val), None)

    # bounds
    if op == "set_driver":
        lo, hi = SET_BOUNDS[dtype]
    else:
        lo, hi = rule["bounds"]

    if not (lo <= val <= hi):
        # a scale far out of range often means a unit error (20 not 0.20)
        if op in ("scale_driver", "scale_schedule") and abs(val) > hi:
            return ("NEEDS_CLARIFICATION",
                    "{} {} value {} looks out of range, possible unit error "
                    "(did you mean {}?)".format(target, op, val, val / 100.0),
                    None)
        return ("ILLEGAL",
                "{} {} value {} is out of bounds ({}, {})".format(target, op, val, lo, hi),
                None)

    if op == "shift_schedule" and int(val) == 0:
        return ("NEEDS_CLARIFICATION", "{} shift of 0 does nothing".format(target), None)

    if periods != "all":
        return ("NEEDS_CLARIFICATION",
                "per-period changes are not supported yet; this run applies to "
                "the whole horizon", None)

    normalised = {"target": target, "operation": op, "value": float(val),
                  "driver_type": dtype, "periods": "all"}
    return ("OK", "valid", normalised)


def classify_request(results):
    """Aggregate per-change results into one classification."""
    statuses = [r[0] for r in results]
    if "ILLEGAL" in statuses:
        return "IMPOSSIBLE"
    if "NEEDS_CLARIFICATION" in statuses:
        return "AMBIGUOUS"
    if len(results) > SCENARIO_MAX_CHANGES:
        return "TOO_MANY"
    return "CLEAR"


def echo_back(normalised_changes):
    """Restate the parsed scenario in plain English so a misparse is visible."""
    parts = []
    for c in normalised_changes:
        t, op, v = c["target"], c["operation"], c["value"]
        if op == "scale_driver" or op == "scale_schedule":
            pct = (1 - v) * 100 if v < 1 else (v - 1) * 100
            direction = "reduce" if v < 1 else "increase"
            parts.append("{} {} by {:.0f} percent".format(direction, t, abs(pct)))
        elif op == "set_driver":
            parts.append("set {} to {:.2f}".format(t, v))
        elif op == "shift_schedule":
            when = "later" if v > 0 else "earlier"
            parts.append("shift {} by {:.0f} month(s) {}".format(t, abs(v), when))
    return "You want to " + "; and ".join(parts) + "."


# =============================================================================
# STEP 3: RUN — apply to COPIES, rerun base + scenario, compute deltas
# =============================================================================
def apply_changes(base, normalised_changes):
    """Return deep copies of the three mutable DataFrames with changes applied.
    The base DataFrames are never touched."""
    drivers   = base["drivers_df"].copy(deep=True)
    headcount = base["headcount_df"].copy(deep=True)
    customer  = base["customer_df"].copy(deep=True)

    held_constant = []

    for c in normalised_changes:
        target, op, val = c["target"], c["operation"], c["value"]

        if op == "scale_driver":
            mask = drivers["line_item"] == target
            drivers.loc[mask, "driver_value"] *= val

        elif op == "set_driver":
            mask = drivers["line_item"] == target
            drivers.loc[mask, "driver_value"] = val

        elif op == "scale_schedule":
            if target == "Personnel Cost":
                headcount["new_hires"] = headcount["new_hires"] * val
            elif target == "Marketing Spend":
                customer["target_new_customers"] = customer["target_new_customers"] * val
                held_constant.append(
                    "Marketing and customer changes do not flow through to "
                    "Revenue, which is modelled on its own seasonal trend, so "
                    "the revenue line is unchanged.")

        elif op == "shift_schedule":
            n = int(val)
            if target == "Personnel Cost":
                headcount["new_hires"] = _shift_column(headcount["new_hires"], n)
            elif target == "Marketing Spend":
                customer["target_new_customers"] = _shift_column(
                    customer["target_new_customers"], n)

    scenario = dict(base)
    scenario["drivers_df"]   = drivers
    scenario["headcount_df"] = headcount
    scenario["customer_df"]  = customer
    return scenario, held_constant


def _shift_column(series, n):
    """Shift a quantity column n periods later (n>0) or earlier (n<0),
    filling vacated periods with 0. A positive shift delays the values."""
    shifted = series.shift(n)
    return shifted.fillna(0.0)


def run_forecast(model):
    """Run the reused Project 2 engine on one model dict."""
    full_df, _driver_detail, _flags = calculate_forecast(
        model["actuals_df"], model["drivers_df"], model["operational_df"],
        model["headcount_df"], model["customer_df"],
        model["last_actual"], model["forecast_periods"], model["seasonal_year"],
    )
    pnl = build_pnl(full_df, model["forecast_periods"])
    return pnl


def compute_deltas(base_pnl, scenario_pnl, forecast_periods):
    """Compute base, scenario, and delta for Revenue and EBIT over the horizon.
    build_pnl returns a DataFrame with a 'line' column and period columns."""
    PNL_LABELS = {"Revenue": "Revenue", "EBIT": "Operating Profit (EBIT)"}

    def horizon_total(pnl, row_label):
        row = pnl[pnl["line"] == row_label]
        return float(row[forecast_periods].sum(axis=1).iloc[0]) if not row.empty else 0.0

    result = {}
    for display, pnl_label in PNL_LABELS.items():
        b = horizon_total(base_pnl, pnl_label)
        s = horizon_total(scenario_pnl, pnl_label)
        result[display] = {"base": b, "scenario": s, "delta": s - b}
    return result


def classify_analysis(normalised_changes):
    """One driver changed is a sensitivity; several is a scenario."""
    return "sensitivity" if len(normalised_changes) == 1 else "scenario"


# =============================================================================
# STEP 4: EXPLAIN — Claude narrates driver to delta to why
# =============================================================================
def build_explain_prompt(request, echo, deltas, analysis_type, held_constant):
    hc = " ".join(held_constant) if held_constant else "None stated."
    delta_lines = []
    for label, d in deltas.items():
        delta_lines.append("  {}: base {:,.0f}, scenario {:,.0f}, change {:+,.0f}".format(
            label, d["base"], d["scenario"], d["delta"]))
    delta_block = "\n".join(delta_lines)

    system_prompt = (
        "You are an FP&A analyst explaining a what-if result to a finance "
        "team. The numbers are already computed. You explain them clearly and "
        "you never invent or recompute a figure.\n\n"
        "<rules>\n"
        "- State plainly what changed and what happened to Revenue and EBIT "
        "over the forecast horizon.\n"
        "- Say whether this was a sensitivity (one driver moved) or a scenario "
        "(several moved together).\n"
        "- If any assumption was held constant, state it clearly as a "
        "limitation. This matters: a reader must know what the model did not "
        "capture.\n"
        "- Keep it to a short, professional paragraph. Standard ASCII only, "
        "no arrows or dashes.\n"
        "</rules>"
    )

    user_prompt = (
        "Original request: \"{request}\"\n"
        "Parsed as: {echo}\n"
        "Analysis type: {atype}\n\n"
        "Computed impact over the forecast horizon:\n"
        "{deltas}\n\n"
        "Assumptions held constant: {hc}\n\n"
        "Explain the result."
    ).format(request=request, echo=echo, atype=analysis_type,
             deltas=delta_block, hc=hc)

    return system_prompt, user_prompt


def call_claude_explain(request, echo, deltas, analysis_type, held_constant):
    system_prompt, user_prompt = build_explain_prompt(
        request, echo, deltas, analysis_type, held_constant)
    print("\n[..] Explaining result with Claude...")
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text
    return text, response.usage.input_tokens, response.usage.output_tokens


# =============================================================================
# THE PIPELINE — testable, interactivity injected via ask/confirm
# =============================================================================
def run_scenario(request, base, ask=input, confirm=input):
    """Run one what-if end to end. ask and confirm default to real input()
    for interactive use; tests pass lambdas so the flow is deterministic."""
    print("\n" + "=" * 64)
    print("REQUEST: {}".format(request))
    print("=" * 64)

    # STEP 1: parse
    scenario, ptok_in, ptok_out = call_claude_parse(request)
    if scenario is None or not scenario.get("changes"):
        print("[STOP] Nothing to run. Could not parse a valid scenario.")
        return None

    # STEP 2: validate each change, classify the request
    results = [validate_change(c) for c in scenario["changes"]]
    classification = classify_request(results)
    print("\n[OK] Validation: {}".format(classification))
    for (status, reason, _), change in zip(results, scenario["changes"]):
        print("     {} {}: {}".format(
            change.get("target", "?"), status, reason))

    if classification == "IMPOSSIBLE":
        print("\n[REFUSED] One or more changes cannot be run:")
        for status, reason, _ in results:
            if status == "ILLEGAL":
                print("     - {}".format(reason))
        print("     Try one of the supported operations on a valid line item.")
        return None

    if classification == "AMBIGUOUS":
        for status, reason, _ in results:
            if status == "NEEDS_CLARIFICATION":
                answer = ask("\nClarify: {}\nYour answer: ".format(reason))
                print("     (You said: {})".format(answer))
                print("     For this Pass 1 run, ambiguous changes are not "
                      "auto-applied. Please rephrase the request.")
                return None

    normalised = [r[2] for r in results if r[0] == "OK"]
    if classification == "TOO_MANY":
        print("\n[NOTE] {} changes is a lot for one scenario. Running anyway."
              .format(len(normalised)))

    # STEP 2.5: echo-back and confirm
    echo = echo_back(normalised)
    print("\n{}".format(echo))
    decision = confirm("Run this scenario? [y/n]: ").strip().lower()
    if decision != "y":
        print("[CANCELLED] Scenario not run.")
        return None

    # STEP 3: apply to copies, run base + scenario, deltas
    scenario_model, held_constant = apply_changes(base, normalised)
    base_pnl     = run_forecast(base)
    scenario_pnl = run_forecast(scenario_model)
    deltas        = compute_deltas(base_pnl, scenario_pnl, base["forecast_periods"])
    analysis_type = classify_analysis(normalised)

    print("\n[OK] Forecast rerun. Impact over horizon:")
    for label, d in deltas.items():
        print("     {}: {:+,.0f}  (base {:,.0f} -> scenario {:,.0f})".format(
            label, d["delta"], d["base"], d["scenario"]))

    # STEP 4: explain
    explanation, etok_in, etok_out = call_claude_explain(
        request, echo, deltas, analysis_type, held_constant)
    print("\n" + "=" * 64)
    print("EXPLANATION ({})".format(analysis_type))
    print("=" * 64)
    print(explanation)
    print("=" * 64)

    # audit
    _write_audit(request, scenario, normalised, classification,
                 analysis_type, deltas, held_constant, base)

    return {"deltas": deltas, "explanation": explanation,
            "analysis_type": analysis_type, "held_constant": held_constant}


def _write_audit(request, parsed, normalised, classification,
                 analysis_type, deltas, held_constant, base):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    def file_hash(path):
        with open(path, "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()

    audit = {
        "run_id":          now.isoformat(),
        "project":         "nl-scenario-copilot",
        "entity":          DEFAULT_ENTITY,
        "raw_request":     request,
        "parsed_scenario": parsed,
        "classification":  classification,
        "analysis_type":   analysis_type,
        "changes_applied": normalised,
        "revenue_delta":   deltas["Revenue"]["delta"],
        "ebit_delta":      deltas["EBIT"]["delta"],
        "held_constant":   held_constant,
        "input_hashes": {
            "actuals":     file_hash(ACTUALS_FILE),
            "drivers":     file_hash(DRIVERS_FILE),
            "operational": file_hash(OPERATIONAL_FILE),
            "headcount":   file_hash(HEADCOUNT_FILE),
            "customer":    file_hash(CUSTOMER_FILE),
        },
        "human_reviewed":  False,
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(audit) + "\n")
    print("\n[OK] Audit record written to {}".format(AUDIT_LOG.name))


# =============================================================================
# MAIN — the interactive menu (never imported by tests)
# =============================================================================
PRESETS = {
    "1": "Cut marketing spend by 20 percent",
    "2": "Cut marketing spend by 20 percent and delay hiring by one month",
    "3": "Set revenue growth to 8 percent",
    "4": "Increase headcount hires by 50 percent",
}

if __name__ == "__main__":
    base = load_base_model()

    while True:
        print("\n" + "-" * 64)
        print("NL SCENARIO MODELLING COPILOT")
        print("-" * 64)
        print("  [1-4] pick a preset example")
        print("  [t]   type your own what-if")
        print("  [q]   quit")
        for k, v in PRESETS.items():
            print("    {}. {}".format(k, v))

        choice = input("> ").strip().lower()
        if choice == "q":
            print("Goodbye.")
            break
        elif choice == "t":
            request = input("Describe your what-if: ").strip()
            if request:
                run_scenario(request, base)
        elif choice in PRESETS:
            run_scenario(PRESETS[choice], base)
        else:
            print("Not a valid choice.")
