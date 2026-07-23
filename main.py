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
        write_audit(request, scenario, [], classification, None, None, [],
                    outcome="refused")
        return None

    if classification == "AMBIGUOUS":
        for status, reason, _ in results:
            if status == "NEEDS_CLARIFICATION":
                answer = ask("\nClarify: {}\nYour answer: ".format(reason))
                print("     (You said: {})".format(answer))
                print("     For this Pass 1 run, ambiguous changes are not "
                      "auto-applied. Please rephrase the request.")
                write_audit(request, scenario, [], classification, None, None, [],
                            outcome="ambiguous")
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
        write_audit(request, scenario, normalised, classification, None, None, [],
                    outcome="cancelled")
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
                analysis_type, headline, held_constant,
                parse_tokens=ptok_in + ptok_out,
                explain_tokens=etok_in + etok_out,
                outcome="completed")
    write_pdf(request, echo, deltas, analysis_type, held_constant, explanation, assumption_rows, takeaways)
    export_deltas_csv(deltas, request, echo, held_constant)

    return {"deltas": deltas, "explanation": explanation,
            "analysis_type": analysis_type, "held_constant": held_constant}


# =============================================================================
# MENU STATE MACHINE — never imported by tests
# =============================================================================
from config import (
    DRIVER_DIRECTION, VALUE_DRIVER_TYPES, SCHEDULE_DRIVER_TYPES, PRESET_BANDS,
)
from src.step7_scenario_spread import (
    derive_spread, preset_spread, manual_spread, history_rates_for,
)
from src.step8_three_case import (
    run_three_case, multi_case_deltas, case_ebit_summary, spread_base_for,
    CASE_ORDER,
)

PRESETS = {
    "a": "Cut marketing spend by 20 percent",
    "b": "Set revenue growth to 8 percent",
    "c": "Cut marketing spend by 20 percent and delay hiring by one month",
}

DRIVER_MENU = [
    ("Revenue",           "Revenue growth"),
    ("COGS",              "COGS margin"),
    ("R&D Expense",       "R&D growth"),
    ("IT Infrastructure", "IT Infrastructure (fixed cost)"),
    ("Personnel Cost",    "Personnel hires"),
    ("Marketing Spend",   "Marketing customers"),
]


def _fmt(line_item, value):
    """Show a case value in the driver's own units."""
    dtype = LINE_ITEMS[line_item]
    if dtype in SCHEDULE_DRIVER_TYPES:
        return "{:.2f}x".format(value)
    if dtype == "fixed":
        return "EUR {:,.0f}".format(value)
    return "{:.2%}".format(value)


def pick_drivers(ask):
    """Multi-select: one or more drivers to flex together."""
    print("\nWhich drivers do you want to flex? You can pick several.")
    for i, (_, label) in enumerate(DRIVER_MENU, start=1):
        print("  [{}] {}".format(i, label))
    print("  [b] back")
    raw = ask("Enter numbers separated by commas (e.g. 1,2): ").strip().lower()
    if raw == "b":
        return "back"
    picked = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(DRIVER_MENU):
            item = DRIVER_MENU[int(part) - 1][0]
            if item not in picked:
                picked.append(item)
    if not picked:
        print("No valid drivers selected.")
        return None
    if len(picked) > 3:
        print("Keep it to three drivers or fewer for a readable scenario.")
        return None
    return picked


def _band_for(line_item, level):
    """The preset band for a driver, at a given level (0 moderate, 1 wide)."""
    kind = "schedule" if LINE_ITEMS[line_item] in SCHEDULE_DRIVER_TYPES else "value"
    return PRESET_BANDS[kind][level]


def _show_spreads(spreads):
    print("\n{:<20}{:>16}{:>16}{:>16}".format(
        "Driver", "Pessimistic", "Realistic", "Optimistic"))
    for line_item, sp in spreads.items():
        print("{:<20}{:>16}{:>16}{:>16}".format(
            line_item,
            _fmt(line_item, sp["pessimistic"]),
            _fmt(line_item, sp["realistic"]),
            _fmt(line_item, sp["optimistic"])))


def auto_populate_multi(line_items, base, ask):
    """Derive each driver's cases from history where possible, else a band.
    Shows them all, then one decision: accept, widen, or back."""
    spreads = {}
    any_narrow = False
    for line_item in line_items:
        dtype     = LINE_ITEMS[line_item]
        direction = DRIVER_DIRECTION[line_item]
        base_val  = spread_base_for(line_item, base["drivers_df"])
        rates     = history_rates_for(line_item, dtype, base["actuals_df"])
        sp        = derive_spread(dtype, base_val, rates, direction)
        if sp is None:
            sp = preset_spread(base_val, _band_for(line_item, 0), direction)
        elif sp["narrow"]:
            any_narrow = True
        spreads[line_item] = sp

    _show_spreads(spreads)
    if any_narrow:
        print("\nSome of these come from very stable history, so the cases sit "
              "close together.")
    print("\n  [y] use these cases")
    print("  [w] widen to a standard band instead")
    print("  [b] back")
    choice = ask("> ").strip().lower()
    if choice == "b":
        return "back"
    if choice == "w":
        print("\n  [1] moderate band   [2] wide band   [b] back")
        lvl = ask("> ").strip().lower()
        if lvl == "b":
            return "back"
        if lvl not in ("1", "2"):
            print("Not a valid choice.")
            return None
        level = int(lvl) - 1
        widened = {}
        for line_item in line_items:
            base_val  = spread_base_for(line_item, base["drivers_df"])
            direction = DRIVER_DIRECTION[line_item]
            widened[line_item] = preset_spread(
                base_val, _band_for(line_item, level), direction)
        _show_spreads(widened)
        return widened
    if choice == "y":
        return spreads
    print("Not a valid choice.")
    return None


def manual_entry_multi(line_items, base, ask):
    """Ask, per driver, for its three case values or a band."""
    print("\nHow do you want to enter the cases?")
    print("  [1] Enter each case value, per driver")
    print("  [2] Enter a band per driver")
    print("  [b] back")
    mode = ask("> ").strip().lower()
    if mode == "b":
        return "back"
    if mode not in ("1", "2"):
        print("Not a valid choice.")
        return None

    spreads = {}
    for line_item in line_items:
        dtype     = LINE_ITEMS[line_item]
        direction = DRIVER_DIRECTION[line_item]
        base_val  = spread_base_for(line_item, base["drivers_df"])
        is_pct    = dtype not in SCHEDULE_DRIVER_TYPES and dtype != "fixed"
        unit      = "percent" if is_pct else (
                    "multiplier" if dtype in SCHEDULE_DRIVER_TYPES else "euros")

        def read(prompt, default=None):
            raw = ask(prompt).strip()
            if raw == "" and default is not None:
                return default
            try:
                v = float(raw)
            except ValueError:
                return None
            return v / 100.0 if is_pct else v

        print("\n{}  (base is {})".format(line_item, _fmt(line_item, base_val)))
        if mode == "1":
            p = read("  Pessimistic ({}): ".format(unit))
            r = read("  Realistic ({}, enter for base): ".format(unit), default=base_val)
            o = read("  Optimistic ({}): ".format(unit))
            if p is None or r is None or o is None:
                print("Those need to be numbers.")
                return None
            if len({p, r, o}) < 3:
                print("The three cases must be different values.")
                return None
            spreads[line_item] = manual_spread(p, r, o)
        else:
            band = read("  Band either side of the base ({}): ".format(unit))
            if band is None or band <= 0:
                print("The band must be a positive number.")
                return None
            spreads[line_item] = preset_spread(base_val, band, direction)

    _show_spreads(spreads)
    return spreads


def confirm_cases_multi(spreads, confirm):
    print("\nBasis: {}".format(
        "; ".join(sorted({sp["basis"] for sp in spreads.values()}))))
    return confirm("Run all three cases? [y/n]: ").strip().lower() == "y"


def path_three_case(base, ask=input, confirm=input):
    """The guided three-case flow: drivers, method, cases, confirm, run."""
    while True:
        line_items = pick_drivers(ask)
        if line_items == "back":
            return
        if line_items is None:
            continue

        print("\nHow do you want to set the three cases?")
        print("  [1] Auto-populate (I calculate them for you)")
        print("  [2] Manual (you provide the numbers)")
        print("  [b] back")
        method = ask("> ").strip().lower()
        if method == "b":
            continue

        if method == "1":
            spreads = auto_populate_multi(line_items, base, ask)
        elif method == "2":
            spreads = manual_entry_multi(line_items, base, ask)
        else:
            print("Not a valid choice.")
            continue

        if spreads == "back" or spreads is None:
            continue

        if not confirm_cases_multi(spreads, confirm):
            print("[CANCELLED] Not run.")
            return

        cases = {}
        for case in ("pessimistic", "realistic", "optimistic"):
            cases[case] = {li: sp[case] for li, sp in spreads.items()}

        results = run_three_case(base, cases)
        rows    = multi_case_deltas(results)
        ebit    = case_ebit_summary(rows)

        print("\n{:<26}{:>13}{:>13}{:>13}{:>13}".format(
            "Line", "Base", "Pessimistic", "Realistic", "Optimistic"))
        for r in rows:
            print("{:<26}{:>13,.0f}{:>13,.0f}{:>13,.0f}{:>13,.0f}".format(
                r["line"], r["base"], r["Pessimistic"], r["Realistic"], r["Optimistic"]))
        if ebit:
            print("\nEBIT versus base:")
            for name in CASE_ORDER:
                print("  {:<12} {:+,.0f}".format(name, ebit[name + "_delta"]))

        from src.step6_explainer import (
            build_three_case_takeaways, call_claude_explain_three_case,
            write_three_case_pdf, export_three_case_csv, write_three_case_audit,
        )
        basis_text = "; ".join(sorted({sp["basis"] for sp in spreads.values()}))
        takeaways  = build_three_case_takeaways(rows, list(spreads.keys()))
        print("\nKEY TAKEAWAYS:")
        for t in takeaways:
            print("  - {}".format(t))

        explanation, _, _ = call_claude_explain_three_case(rows, spreads, basis_text)
        print("\n" + "=" * 64)
        print("ANALYSIS")
        print("=" * 64)
        print(explanation)
        print("=" * 64)

        write_three_case_audit(spreads, rows, basis_text)
        write_three_case_pdf(spreads, rows, explanation, takeaways, basis_text)
        export_three_case_csv(rows)
        return


def path_single(base, ask=input, confirm=input):
    print("\nDescribe your what-if, or pick an example:")
    for k, v in PRESETS.items():
        print("  [{}] {}".format(k, v))
    print("  [t] type your own")
    print("  [b] back")
    choice = ask("> ").strip().lower()
    if choice == "b":
        return
    if choice == "t":
        request = ask("Describe your what-if: ").strip()
        if request:
            run_scenario(request, base, ask=ask, confirm=confirm)
    elif choice in PRESETS:
        run_scenario(PRESETS[choice], base, ask=ask, confirm=confirm)
    else:
        print("Not a valid choice.")


if __name__ == "__main__":
    base = load_base_model()

    while True:
        print("\n" + "-" * 64)
        print("NL SCENARIO MODELLING COPILOT")
        print("-" * 64)
        print("  [1] Run a single what-if (plain English)")
        print("  [2] Run a three-case analysis (pessimistic, realistic, optimistic)")
        print("  [q] Quit")

        choice = input("> ").strip().lower()
        if choice == "q":
            print("Goodbye.")
            break
        elif choice == "1":
            path_single(base)
        elif choice == "2":
            path_three_case(base)
        else:
            print("Not a valid choice.")
