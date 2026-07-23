# =============================================================================
# tests/test_pipeline.py — NL Scenario Modelling Copilot
# =============================================================================
# Phase 5: VALIDATE
#
# Run from the project root with (venv) active:
#   pytest tests/test_pipeline.py -v
#
# Twelve test classes covering both paths:
#    1. Base model loading
#    2. validate_change, the four operations
#    3. validate_change, the rejections
#    4. classify_request aggregation
#    5. echo_back
#    6. Spread direction (regression test for the labelling bug)
#    7. Spread modes and narrow detection
#    8. Historical rate extraction
#    9. Scenario engine, the single what-if path
#   10. Three-case runner, the key invariants
#   11. Scenario JSON parsing
#   12. Explainer helpers
#
# No real API calls. The menus are deliberately untested: interactivity is
# injected through ask and confirm, so the pipeline is exercised directly.
# =============================================================================

import sys
import copy
from pathlib import Path

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    ACTUALS_FILE, DRIVERS_FILE, OPERATIONAL_FILE, HEADCOUNT_FILE, CUSTOMER_FILE,
    LINE_ITEMS, DRIVER_DIRECTION, SCENARIO_MAX_CHANGES, EBIT_LABEL,
)
from src.step1_data_loader import (
    load_actuals, load_drivers, detect_boundary,
    load_operational_actuals, load_headcount_schedule, load_customer_targets,
)
from src.step3_scenario_parser import parse_scenario_json
from src.step4_validator import validate_change, classify_request, echo_back
from src.step5_scenario_engine import (
    apply_changes, run_forecast, compute_deltas, classify_analysis, headline_deltas,
)
from src.step6_explainer import (
    format_case_value, build_takeaways, build_three_case_takeaways,
    describe_base_change, build_assumptions_rows, _esc,
)
from src.step7_scenario_spread import (
    derive_spread, preset_spread, manual_spread, history_rates_for,
    yoy_growth_rates,
)
from src.step8_three_case import (
    run_three_case, multi_case_deltas, case_ebit_summary, spread_base_for,
    CASE_ORDER,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def base():
    """The base model, assembled from the five real input files. Built once
    per session because the engine runs are the slow part."""
    actuals_df = load_actuals(ACTUALS_FILE)
    last_actual, forecast_periods = detect_boundary(actuals_df)
    return {
        "actuals_df":       actuals_df,
        "drivers_df":       load_drivers(DRIVERS_FILE),
        "operational_df":   load_operational_actuals(OPERATIONAL_FILE),
        "headcount_df":     load_headcount_schedule(HEADCOUNT_FILE),
        "customer_df":      load_customer_targets(CUSTOMER_FILE),
        "last_actual":      last_actual,
        "forecast_periods": forecast_periods,
        "seasonal_year":    int(last_actual.split("-")[0]) - 1,
    }


@pytest.fixture(scope="session")
def base_pnl(base):
    return run_forecast(base)


def _driver_value(base, line_item):
    row = base["drivers_df"].loc[
        base["drivers_df"]["line_item"] == line_item, "driver_value"]
    return float(row.iloc[0])


def _make_actuals_df(series, line_item="Revenue"):
    """Build a minimal actuals DataFrame from a plain list of monthly values,
    starting at 2025-01. Used to test yoy_growth_rates with known data."""
    periods = []
    for i in range(len(series)):
        y = 2025 + i // 12
        m = 1 + i % 12
        periods.append("{:04d}-{:02d}".format(y, m))
    return pd.DataFrame({
        "period":    periods,
        "line_item": [line_item] * len(series),
        "actual":    series,
        "locked":    [True] * len(series),
    })


# =============================================================================
# CLASS 1: Base model loading
# =============================================================================

class TestBaseModel:

    def test_six_line_items(self, base):
        assert len(base["drivers_df"]) == 6

    def test_all_line_items_known(self, base):
        for item in base["drivers_df"]["line_item"]:
            assert item in LINE_ITEMS

    def test_boundary_detected(self, base):
        assert isinstance(base["last_actual"], str)
        assert len(base["forecast_periods"]) > 0

    def test_seasonal_year_is_prior_year(self, base):
        assert base["seasonal_year"] == int(base["last_actual"].split("-")[0]) - 1

    def test_forecast_periods_after_last_actual(self, base):
        assert base["forecast_periods"][0] > base["last_actual"]


# =============================================================================
# CLASS 2: validate_change, the four operations
# =============================================================================

class TestValidOperations:

    def test_scale_driver(self):
        assert validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": 0.8})[0] == "OK"

    def test_set_driver(self):
        assert validate_change(
            {"target": "Revenue", "operation": "set_driver", "value": 0.08})[0] == "OK"

    def test_scale_schedule(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "scale_schedule", "value": 1.2})[0] == "OK"

    def test_shift_schedule(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "shift_schedule", "value": 1})[0] == "OK"

    def test_returns_three_tuple(self):
        result = validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": 0.8})
        assert isinstance(result, tuple) and len(result) == 3

    def test_normalised_carries_driver_type(self):
        _, _, norm = validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": 0.8})
        assert norm["driver_type"] == "seasonal_yoy"


# =============================================================================
# CLASS 3: validate_change, the rejections
# =============================================================================

class TestRejections:

    def test_unknown_line_item(self):
        assert validate_change(
            {"target": "Gross Margin", "operation": "scale_driver", "value": 0.8})[0] == "ILLEGAL"

    def test_unknown_operation(self):
        assert validate_change(
            {"target": "Revenue", "operation": "frobnicate", "value": 1})[0] == "ILLEGAL"

    def test_schedule_op_on_value_driver(self):
        assert validate_change(
            {"target": "Revenue", "operation": "shift_schedule", "value": 1})[0] == "ILLEGAL"

    def test_value_op_on_schedule_driver(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "set_driver", "value": 0.5})[0] == "ILLEGAL"

    def test_non_numeric_value(self):
        assert validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": "lots"})[0] == "ILLEGAL"

    def test_bool_is_not_a_number(self):
        assert validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": True})[0] == "ILLEGAL"

    def test_non_integer_shift(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "shift_schedule", "value": 1.5})[0] == "ILLEGAL"

    def test_margin_above_one(self):
        assert validate_change(
            {"target": "COGS", "operation": "set_driver", "value": 1.5})[0] == "ILLEGAL"

    def test_negative_scale(self):
        assert validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": -0.5})[0] == "ILLEGAL"

    def test_shift_beyond_range(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "shift_schedule", "value": 9})[0] == "ILLEGAL"

    def test_unit_error_asks_for_clarification(self):
        assert validate_change(
            {"target": "Revenue", "operation": "scale_driver", "value": 20})[0] == "NEEDS_CLARIFICATION"

    def test_zero_shift_asks_for_clarification(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "shift_schedule", "value": 0})[0] == "NEEDS_CLARIFICATION"

    def test_per_period_not_supported_yet(self):
        assert validate_change(
            {"target": "Revenue", "operation": "scale_driver",
             "value": 0.8, "periods": ["2026-08"]})[0] == "NEEDS_CLARIFICATION"


# =============================================================================
# CLASS 4: classify_request aggregation
# =============================================================================

class TestClassifyRequest:

    @staticmethod
    def _classify(changes):
        return classify_request([validate_change(c) for c in changes])

    def test_all_valid_is_clear(self):
        assert self._classify(
            [{"target": "Revenue", "operation": "scale_driver", "value": 0.8}]) == "CLEAR"

    def test_illegal_is_impossible(self):
        assert self._classify(
            [{"target": "Nope", "operation": "scale_driver", "value": 0.8}]) == "IMPOSSIBLE"

    def test_clarification_is_ambiguous(self):
        assert self._classify(
            [{"target": "Revenue", "operation": "scale_driver", "value": 20}]) == "AMBIGUOUS"

    def test_illegal_dominates_valid(self):
        assert self._classify([
            {"target": "Revenue", "operation": "scale_driver", "value": 0.8},
            {"target": "Nope",    "operation": "scale_driver", "value": 0.8},
        ]) == "IMPOSSIBLE"

    def test_illegal_dominates_ambiguous(self):
        assert self._classify([
            {"target": "Revenue", "operation": "scale_driver", "value": 20},
            {"target": "Nope",    "operation": "scale_driver", "value": 0.8},
        ]) == "IMPOSSIBLE"

    def test_too_many_changes(self):
        changes = [{"target": "Revenue", "operation": "scale_driver", "value": 0.9}
                   ] * (SCENARIO_MAX_CHANGES + 1)
        assert classify_request([validate_change(c) for c in changes]) == "TOO_MANY"


# =============================================================================
# CLASS 5: echo_back
# =============================================================================

class TestEchoBack:

    def test_scale_reads_as_reduce(self):
        text = echo_back([{"target": "Marketing Spend", "operation": "scale_schedule",
                           "value": 0.8, "driver_type": "cac_driven", "periods": "all"}])
        assert "reduce Marketing Spend by 20 percent" in text

    def test_scale_up_reads_as_increase(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "scale_schedule",
                           "value": 1.5, "driver_type": "headcount_driven", "periods": "all"}])
        assert "increase Personnel Cost by 50 percent" in text

    def test_set_reads_as_set_to(self):
        text = echo_back([{"target": "Revenue", "operation": "set_driver",
                           "value": 0.08, "driver_type": "seasonal_yoy", "periods": "all"}])
        assert "set Revenue to 0.08" in text

    def test_shift_later(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "shift_schedule",
                           "value": 1, "driver_type": "headcount_driven", "periods": "all"}])
        assert "later" in text

    def test_shift_earlier(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "shift_schedule",
                           "value": -2, "driver_type": "headcount_driven", "periods": "all"}])
        assert "earlier" in text


# =============================================================================
# CLASS 6: spread direction — regression test for the labelling bug
# =============================================================================

class TestSpreadDirection:
    """A pessimistic case describes the OUTCOME, not the arithmetic sign. For
    revenue that means lower growth; for a cost line it means higher cost."""

    def test_revenue_pessimistic_is_lower(self):
        s = preset_spread(0.12, 0.03, DRIVER_DIRECTION["Revenue"])
        assert s["pessimistic"] < s["realistic"] < s["optimistic"]

    def test_cost_pessimistic_is_higher(self):
        s = preset_spread(0.418, 0.03, DRIVER_DIRECTION["COGS"])
        assert s["pessimistic"] > s["realistic"] > s["optimistic"]

    def test_schedule_pessimistic_is_more_spend(self):
        s = preset_spread(1.0, 0.2, DRIVER_DIRECTION["Personnel Cost"])
        assert s["pessimistic"] > 1.0
        assert s["optimistic"] < 1.0

    def test_realistic_always_equals_base(self):
        for line_item, base_val in (("Revenue", 0.12), ("COGS", 0.418), ("R&D Expense", 0.06)):
            s = preset_spread(base_val, 0.03, DRIVER_DIRECTION[line_item])
            assert s["realistic"] == base_val

    def test_direction_applies_to_derived_spread_too(self):
        rates = [0.02, 0.22, 0.05, 0.19, 0.08, 0.16]
        rev  = derive_spread("seasonal_yoy", 0.12, rates, DRIVER_DIRECTION["Revenue"])
        cogs = derive_spread("margin_pct",   0.418, rates, DRIVER_DIRECTION["COGS"])
        assert rev["pessimistic"]  < rev["realistic"]
        assert cogs["pessimistic"] > cogs["realistic"]


# =============================================================================
# CLASS 7: spread modes and narrow detection
# =============================================================================

class TestSpreadModes:

    def test_fixed_driver_cannot_be_derived(self):
        assert derive_spread("fixed", 45000, [0.1, 0.2], "higher_worse") is None

    def test_schedule_driver_cannot_be_derived(self):
        assert derive_spread("headcount_driven", 1.0, [0.1, 0.2], "higher_worse") is None

    def test_too_little_history_returns_none(self):
        assert derive_spread("seasonal_yoy", 0.12, [0.1], "higher_better") is None

    def test_narrow_flag_fires_on_stable_history(self):
        stable = [0.122, 0.119, 0.120, 0.121, 0.119, 0.122]
        assert derive_spread("seasonal_yoy", 0.12, stable, "higher_better")["narrow"] is True

    def test_narrow_flag_clear_on_volatile_history(self):
        volatile = [0.02, 0.22, 0.05, 0.19, 0.08, 0.16]
        assert derive_spread("seasonal_yoy", 0.12, volatile, "higher_better")["narrow"] is False

    def test_preset_records_its_band(self):
        assert preset_spread(0.12, 0.03, "higher_better")["band"] == 0.03

    def test_manual_takes_values_verbatim(self):
        s = manual_spread(0.05, 0.10, 0.18)
        assert (s["pessimistic"], s["realistic"], s["optimistic"]) == (0.05, 0.10, 0.18)

    def test_every_mode_reports_a_basis(self):
        for s in (derive_spread("seasonal_yoy", 0.12, [0.1, 0.2, 0.15], "higher_better"),
                  preset_spread(0.12, 0.03, "higher_better"),
                  manual_spread(0.05, 0.10, 0.18)):
            assert s["basis"]


# =============================================================================
# CLASS 8: historical rate extraction
# =============================================================================

class TestHistoryRates:

    def test_yoy_rates_count(self):
        series = list(range(100, 118))          # 18 months
        df = _make_actuals_df(series)
        rates = yoy_growth_rates(df, "Revenue")
        assert len(rates) == len(series) - 12

    def test_yoy_rate_value(self):
        series = [100] * 12 + [110]
        df = _make_actuals_df(series)
        rates = yoy_growth_rates(df, "Revenue")
        assert abs(rates[0] - 0.10) < 1e-9

    def test_seasonal_yoy_uses_yoy(self, base):
        rates = history_rates_for("Revenue", "seasonal_yoy", base["actuals_df"])
        assert len(rates) > 0

    def test_growth_pct_uses_month_on_month(self, base):
        rates = history_rates_for("R&D Expense", "growth_pct", base["actuals_df"])
        assert len(rates) > 0

    def test_margin_pct_is_a_ratio(self, base):
        rates = history_rates_for("COGS", "margin_pct", base["actuals_df"])
        assert len(rates) > 0
        assert all(0 < r < 1 for r in rates)

    def test_schedule_driver_has_no_rates(self, base):
        assert history_rates_for("Personnel Cost", "headcount_driven", base["actuals_df"]) == []


# =============================================================================
# CLASS 9: scenario engine, the single what-if path
# =============================================================================

class TestScenarioEngine:

    def test_apply_changes_does_not_mutate_base(self, base):
        before = base["drivers_df"].copy(deep=True)
        change = {"target": "Revenue", "operation": "set_driver", "value": 0.08,
                  "driver_type": "seasonal_yoy", "periods": "all"}
        apply_changes(base, [change])
        pd.testing.assert_frame_equal(before, base["drivers_df"])

    def test_set_driver_replaces_value(self, base):
        change = {"target": "Revenue", "operation": "set_driver", "value": 0.08,
                  "driver_type": "seasonal_yoy", "periods": "all"}
        scenario, _, _ = apply_changes(base, [change])
        value = scenario["drivers_df"].loc[
            scenario["drivers_df"]["line_item"] == "Revenue", "driver_value"].iloc[0]
        assert value == 0.08

    def test_scale_driver_multiplies_value(self, base):
        original = _driver_value(base, "R&D Expense")
        change = {"target": "R&D Expense", "operation": "scale_driver", "value": 0.5,
                  "driver_type": "growth_pct", "periods": "all"}
        scenario, _, _ = apply_changes(base, [change])
        value = scenario["drivers_df"].loc[
            scenario["drivers_df"]["line_item"] == "R&D Expense", "driver_value"].iloc[0]
        assert abs(value - original * 0.5) < 1e-9

    def test_base_context_records_the_before_value(self, base):
        original = _driver_value(base, "Revenue")
        change = {"target": "Revenue", "operation": "set_driver", "value": 0.08,
                  "driver_type": "seasonal_yoy", "periods": "all"}
        _, _, base_context = apply_changes(base, [change])
        assert base_context[0]["base_value"] == original
        assert base_context[0]["new_value"] == 0.08

    def test_compute_deltas_covers_every_line(self, base, base_pnl):
        deltas = compute_deltas(base_pnl, base_pnl)
        assert len(deltas) == len(base_pnl)

    def test_identical_pnl_gives_zero_delta(self, base_pnl):
        for row in compute_deltas(base_pnl, base_pnl):
            assert row["delta"] == 0

    def test_classify_analysis_single_is_sensitivity(self):
        assert classify_analysis([{"target": "Revenue"}]) == "sensitivity"

    def test_classify_analysis_multiple_is_scenario(self):
        assert classify_analysis([{"target": "Revenue"}, {"target": "COGS"}]) == "scenario"

    def test_headline_extracts_revenue_and_ebit(self, base_pnl):
        headline = headline_deltas(compute_deltas(base_pnl, base_pnl))
        assert headline["Revenue"] is not None
        assert headline["EBIT"] is not None


# =============================================================================
# CLASS 10: three-case runner — the key invariants
# =============================================================================

class TestThreeCaseRunner:

    def test_spread_base_for_value_driver(self, base):
        assert spread_base_for("Revenue", base["drivers_df"]) == _driver_value(base, "Revenue")

    def test_spread_base_for_schedule_driver(self, base):
        assert spread_base_for("Personnel Cost", base["drivers_df"]) == 1.0

    def test_realistic_case_reproduces_the_base_exactly(self, base):
        """The strongest invariant: the realistic case IS the base plan, so it
        must reproduce the base run line for line."""
        base_val = spread_base_for("Revenue", base["drivers_df"])
        cases = {
            "pessimistic": {"Revenue": base_val - 0.03},
            "realistic":   {"Revenue": base_val},
            "optimistic":  {"Revenue": base_val + 0.03},
        }
        rows = multi_case_deltas(run_three_case(base, cases))
        for row in rows:
            assert abs(row["Realistic_delta"]) < 0.01, row["line"]

    def test_case_ordering_follows_the_driver_direction(self, base):
        base_val = spread_base_for("Revenue", base["drivers_df"])
        cases = {
            "pessimistic": {"Revenue": base_val - 0.03},
            "realistic":   {"Revenue": base_val},
            "optimistic":  {"Revenue": base_val + 0.03},
        }
        ebit = case_ebit_summary(multi_case_deltas(run_three_case(base, cases)))
        assert ebit["Pessimistic_delta"] < 0 < ebit["Optimistic_delta"]

    def test_three_case_run_does_not_mutate_the_base(self, base):
        drivers_before   = base["drivers_df"].copy(deep=True)
        headcount_before = base["headcount_df"].copy(deep=True)
        base_val = spread_base_for("Revenue", base["drivers_df"])
        cases = {
            "pessimistic": {"Revenue": base_val - 0.03, "Personnel Cost": 1.5},
            "realistic":   {"Revenue": base_val,        "Personnel Cost": 1.0},
            "optimistic":  {"Revenue": base_val + 0.03, "Personnel Cost": 0.5},
        }
        run_three_case(base, cases)
        pd.testing.assert_frame_equal(drivers_before,   base["drivers_df"])
        pd.testing.assert_frame_equal(headcount_before, base["headcount_df"])

    def test_multi_driver_moves_both_kinds_together(self, base):
        """A value driver and a schedule driver applied in the same case.
        Scale factors must be large enough that int() truncation of
        new_hires still changes the headcount (the engine floors floats)."""
        rev_base = spread_base_for("Revenue", base["drivers_df"])
        cases = {
            "pessimistic": {"Revenue": rev_base - 0.03, "Personnel Cost": 1.5},
            "realistic":   {"Revenue": rev_base,        "Personnel Cost": 1.0},
            "optimistic":  {"Revenue": rev_base + 0.03, "Personnel Cost": 0.5},
        }
        rows = {r["line"]: r for r in multi_case_deltas(run_three_case(base, cases))}
        assert rows["Revenue"]["Pessimistic_delta"] < 0
        assert rows["Personnel Cost"]["Pessimistic_delta"] > 0

    def test_multi_driver_compounds_the_downside(self, base):
        """Two adverse drivers together must hurt more than one alone."""
        rev_base = spread_base_for("Revenue", base["drivers_df"])
        single = {
            "pessimistic": {"Revenue": rev_base - 0.03},
            "realistic":   {"Revenue": rev_base},
            "optimistic":  {"Revenue": rev_base + 0.03},
        }
        both = {
            "pessimistic": {"Revenue": rev_base - 0.03, "Personnel Cost": 1.5},
            "realistic":   {"Revenue": rev_base,        "Personnel Cost": 1.0},
            "optimistic":  {"Revenue": rev_base + 0.03, "Personnel Cost": 0.5},
        }
        one = case_ebit_summary(multi_case_deltas(run_three_case(base, single)))
        two = case_ebit_summary(multi_case_deltas(run_three_case(base, both)))
        assert abs(two["Pessimistic_delta"]) > abs(one["Pessimistic_delta"])

    def test_delta_rows_carry_every_case(self, base):
        base_val = spread_base_for("Revenue", base["drivers_df"])
        cases = {"pessimistic": {"Revenue": base_val - 0.03},
                 "realistic":   {"Revenue": base_val},
                 "optimistic":  {"Revenue": base_val + 0.03}}
        row = multi_case_deltas(run_three_case(base, cases))[0]
        for name in CASE_ORDER:
            assert name in row and name + "_delta" in row


# =============================================================================
# CLASS 11: scenario JSON parsing
# =============================================================================

class TestParsing:

    def test_fenced_json_parses(self):
        text = 'here ```json\n{"changes":[{"target":"Revenue"}]}\n``` done'
        assert parse_scenario_json(text)["changes"][0]["target"] == "Revenue"

    def test_missing_json_returns_none(self):
        assert parse_scenario_json("just prose, no block") is None

    def test_malformed_json_returns_none(self):
        assert parse_scenario_json("```json\n{not valid}\n```") is None

    def test_bare_object_fallback(self):
        assert parse_scenario_json('{"changes": [], "confidence": "low"}') is not None


# =============================================================================
# CLASS 12: explainer helpers
# =============================================================================

class TestExplainerHelpers:

    def test_format_percent_driver(self):
        assert format_case_value("Revenue", 0.12) == "12.00%"

    def test_format_fixed_driver(self):
        assert "45,000" in format_case_value("IT Infrastructure", 45000)

    def test_format_schedule_driver_as_multiplier(self):
        assert format_case_value("Personnel Cost", 1.2) == "1.20x"

    def test_escape_ampersand(self):
        assert _esc("R&D Expense") == "R&amp;D Expense"

    def test_describe_base_change_for_growth(self):
        text = describe_base_change({
            "target": "Revenue", "driver_type": "seasonal_yoy",
            "base_value": 0.12, "new_value": 0.08, "operation": "set_driver"})
        assert "12%" in text and "8%" in text

    def test_describe_base_change_for_margin(self):
        text = describe_base_change({
            "target": "COGS", "driver_type": "margin_pct",
            "base_value": 0.418, "new_value": 0.45, "operation": "set_driver"})
        assert "41.8%" in text and "45.0%" in text

    def test_assumptions_rows_describe_both_sides(self):
        rows = build_assumptions_rows([{
            "target": "Revenue", "driver_type": "seasonal_yoy",
            "base_value": 0.12, "new_value": 0.08, "operation": "set_driver"}])
        assert rows[0]["line"] == "Revenue"
        assert rows[0]["base"] and rows[0]["scenario"]

    def test_takeaways_lead_with_ebit(self):
        deltas = [
            {"line": "Revenue", "base": 8490872, "scenario": 8187627,
             "delta": -303245, "pct": -0.0357},
            {"line": EBIT_LABEL, "base": 542191, "scenario": 365702,
             "delta": -176489, "pct": -0.3255},
        ]
        takeaways = build_takeaways(deltas, "sensitivity")
        assert takeaways and takeaways[0].startswith("EBIT")

    def test_three_case_takeaways_report_the_range(self):
        rows = [{"line": EBIT_LABEL, "base": 542191, "Pessimistic": 161921,
                 "Realistic": 542191, "Optimistic": 936107,
                 "Pessimistic_delta": -380270, "Realistic_delta": 0,
                 "Optimistic_delta": 393916}]
        takeaways = build_three_case_takeaways(rows, ["Revenue", "COGS"])
        assert "ranges from" in takeaways[0]
        assert "spread of" in takeaways[1]
        assert "Revenue, COGS" in takeaways[-1]

    def test_three_case_takeaways_empty_without_ebit(self):
        rows = [{"line": "Revenue", "base": 1, "Pessimistic": 1,
                 "Realistic": 1, "Optimistic": 1}]
        assert build_three_case_takeaways(rows, ["Revenue"]) == []
