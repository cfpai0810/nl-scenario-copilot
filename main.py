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

from dotenv import load_dotenv

load_dotenv()

from config import (
    ACTUALS_FILE, DRIVERS_FILE, OPERATIONAL_FILE, HEADCOUNT_FILE, CUSTOMER_FILE,
    LINE_ITEMS,
)

# The reused Project 2 engine and loaders, called UNCHANGED
from src.step1_data_loader import (
    load_actuals, load_drivers, detect_boundary,
    load_operational_actuals, load_headcount_schedule, load_customer_targets,
)
from src.step3_scenario_parser import call_claude_parse
from src.step4_validator       import validate_change, classify_request, echo_back
from src.step5_scenario_engine import (
    apply_changes, run_forecast, compute_deltas, classify_analysis, headline_deltas,
)
from src.step6_explainer import (
    call_claude_explain, write_audit, write_pdf, export_deltas_csv,
)


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

    # STEP 3: apply to copies, run base + scenario, full-P&L deltas
    scenario_model, held_constant, base_context = apply_changes(base, normalised)
    base_pnl     = run_forecast(base)
    scenario_pnl = run_forecast(scenario_model)
    deltas        = compute_deltas(base_pnl, scenario_pnl)
    analysis_type = classify_analysis(normalised)
    headline      = headline_deltas(deltas)

    print("\n[OK] Forecast rerun. Full P&L impact over horizon:")
    for d in deltas:
        if d["delta"] != 0:
            print("     {:<26} {:+,.0f}  (base {:,.0f} -> scenario {:,.0f})".format(
                d["line"], d["delta"], d["base"], d["scenario"]))

    # STEP 4: explain (base context first, so the direction of the numbers is clear)
    from src.step6_explainer import (
        build_base_context_text, build_assumptions_rows, build_takeaways,
    )
    base_context_text = build_base_context_text(base_context)
    assumption_rows   = build_assumptions_rows(base_context)
    takeaways         = build_takeaways(deltas, analysis_type)
    explanation, etok_in, etok_out = call_claude_explain(
        request, echo, headline, analysis_type, held_constant, base_context_text)
    print("\n" + "=" * 64)
    print("EXPLANATION ({})".format(analysis_type))
    print("=" * 64)
    print(explanation)
    print("=" * 64)

    if takeaways:
        print("\nKEY TAKEAWAYS:")
        for t in takeaways:
            print("  - {}".format(t))

    # audit, then the report and the working artefact
    write_audit(request, scenario, normalised, classification,
                analysis_type, headline, held_constant)
    write_pdf(request, echo, deltas, analysis_type, held_constant, explanation, assumption_rows, takeaways)
    export_deltas_csv(deltas, request, echo, held_constant)

    return {"deltas": deltas, "explanation": explanation,
            "analysis_type": analysis_type, "held_constant": held_constant}


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
