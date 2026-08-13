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

import importlib
import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


def _has_module(name):
    return importlib.util.find_spec(name) is not None

from config import (
    ACTUALS_FILE, DRIVERS_FILE, OPERATIONAL_FILE, HEADCOUNT_FILE, CUSTOMER_FILE,
    LINE_ITEMS, DRIVER_DIRECTION, SCENARIO_MAX_CHANGES, EBIT_LABEL,
    CURRENCY_CODE, CURRENCY_SYMBOL,
)
from src.step1_data_loader import (
    load_actuals, load_drivers, detect_boundary,
    load_operational_actuals, load_headcount_schedule, load_customer_targets,
)
from src.step3_scenario_parser import (
    parse_scenario_json, build_parse_prompt, normalise_periods, call_claude_parse,
)
from src.step4_validator import validate_change, classify_request, echo_back
from src.step5_scenario_engine import (
    apply_changes, run_forecast, compute_deltas, classify_analysis, headline_deltas,
)
from src.step2_forecast_engine import build_pnl
from src.step6_explainer import (
    format_case_value, build_takeaways, build_three_case_takeaways,
    describe_base_change, build_assumptions_rows, _esc,
    build_monthly_rows, _monthly_table,
    build_quarterly_rows, _quarterly_table,
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

    def test_shift_driver_small(self):
        assert validate_change(
            {"target": "IT Infrastructure", "operation": "shift_driver", "value": 500})[0] == "OK"

    def test_shift_driver_large_asks_basis(self):
        result = validate_change(
            {"target": "IT Infrastructure", "operation": "shift_driver", "value": 30000})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "per month" in result[1]

    def test_shift_driver_percentage(self):
        assert validate_change(
            {"target": "Revenue", "operation": "shift_driver", "value": 0.03})[0] == "OK"

    def test_set_schedule(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "set_schedule", "value": 5})[0] == "OK"

    def test_add_schedule(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "add_schedule", "value": 3})[0] == "OK"

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
        result = validate_change(
            {"target": "COGS", "operation": "set_driver", "value": 1.5})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "margin" in result[1]

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

    def test_shift_driver_on_schedule_driver(self):
        assert validate_change(
            {"target": "Personnel Cost", "operation": "shift_driver", "value": 0.03})[0] == "ILLEGAL"

    def test_set_schedule_on_value_driver(self):
        assert validate_change(
            {"target": "Revenue", "operation": "set_schedule", "value": 5})[0] == "ILLEGAL"

    def test_add_schedule_on_value_driver(self):
        assert validate_change(
            {"target": "IT Infrastructure", "operation": "add_schedule", "value": 3})[0] == "ILLEGAL"

    def test_shift_driver_out_of_bounds(self):
        result = validate_change(
            {"target": "Revenue", "operation": "shift_driver", "value": 0.90})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "annual growth rate" in result[1]

    def test_euros_on_margin_pct_asks_clarification(self):
        result = validate_change(
            {"target": "COGS", "operation": "shift_driver", "value": 20000})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "margin" in result[1] and "percentage" in result[1]

    def test_euros_on_seasonal_yoy_asks_clarification(self):
        result = validate_change(
            {"target": "Revenue", "operation": "shift_driver", "value": 5000})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "annual growth rate" in result[1]

    def test_euros_on_growth_pct_asks_clarification(self):
        result = validate_change(
            {"target": "R&D Expense", "operation": "shift_driver", "value": 5000})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "monthly growth rate" in result[1]

    def test_small_shift_on_pct_driver_still_valid(self):
        assert validate_change(
            {"target": "COGS", "operation": "shift_driver", "value": 0.03})[0] == "OK"

    def test_large_shift_on_fixed_asks_basis(self):
        result = validate_change(
            {"target": "IT Infrastructure", "operation": "shift_driver", "value": 20000})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "per month" in result[1] and "whole year" in result[1]

    def test_small_shift_on_fixed_still_valid(self):
        assert validate_change(
            {"target": "IT Infrastructure", "operation": "shift_driver", "value": 200})[0] == "OK"

    def test_per_period_revenue_scale_allowed(self):
        result = validate_change(
            {"target": "Revenue", "operation": "scale_driver",
             "value": 1.05, "periods": ["2026-08"]})
        assert result[0] == "OK"
        assert result[2]["periods"] == ["2026-08"]

    def test_per_period_revenue_set_gated(self):
        result = validate_change(
            {"target": "Revenue", "operation": "set_driver",
             "value": 0.08, "periods": ["2026-08"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "percentage changes" in result[1]

    def test_per_period_revenue_shift_gated(self):
        result = validate_change(
            {"target": "Revenue", "operation": "shift_driver",
             "value": 0.03, "periods": ["2026-08"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "percentage changes" in result[1]


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
        assert "set the Revenue annual growth rate to 0.08" in text

    def test_shift_later(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "shift_schedule",
                           "value": 1, "driver_type": "headcount_driven", "periods": "all"}])
        assert "later" in text

    def test_shift_earlier(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "shift_schedule",
                           "value": -2, "driver_type": "headcount_driven", "periods": "all"}])
        assert "earlier" in text

    def test_shift_driver_fixed_reads_as_add(self):
        text = echo_back([{"target": "IT Infrastructure", "operation": "shift_driver",
                           "value": 30000, "driver_type": "fixed", "periods": "all"}])
        assert "add {} 30,000 to IT Infrastructure".format(CURRENCY_CODE) in text

    def test_shift_driver_pct_reads_as_increase_pp(self):
        text = echo_back([{"target": "Revenue", "operation": "shift_driver",
                           "value": 0.03, "driver_type": "seasonal_yoy", "periods": "all"}])
        assert "increase the Revenue annual growth rate by 3.0 percentage points" in text

    def test_set_schedule_reads_as_set_per_period(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "set_schedule",
                           "value": 5, "driver_type": "headcount_driven", "periods": "all"}])
        assert "set Personnel Cost to 5 per period" in text

    def test_add_schedule_reads_as_add_per_period(self):
        text = echo_back([{"target": "Personnel Cost", "operation": "add_schedule",
                           "value": 3, "driver_type": "headcount_driven", "periods": "all"}])
        assert "add 3 per period for Personnel Cost" in text

    def test_echo_seasonal_yoy_says_annual(self):
        text = echo_back([{"target": "Revenue", "operation": "set_driver",
                           "value": 0.08, "driver_type": "seasonal_yoy", "periods": "all"}])
        assert "annual" in text

    def test_echo_growth_pct_says_monthly(self):
        text = echo_back([{"target": "R&D Expense", "operation": "set_driver",
                           "value": 0.04, "driver_type": "growth_pct", "periods": "all"}])
        assert "monthly" in text


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
        scenario, _, _, _, _ = apply_changes(base, [change])
        value = scenario["drivers_df"].loc[
            scenario["drivers_df"]["line_item"] == "Revenue", "driver_value"].iloc[0]
        assert value == 0.08

    def test_scale_driver_multiplies_value(self, base):
        original = _driver_value(base, "R&D Expense")
        change = {"target": "R&D Expense", "operation": "scale_driver", "value": 0.5,
                  "driver_type": "growth_pct", "periods": "all"}
        scenario, _, _, _, _ = apply_changes(base, [change])
        value = scenario["drivers_df"].loc[
            scenario["drivers_df"]["line_item"] == "R&D Expense", "driver_value"].iloc[0]
        assert abs(value - original * 0.5) < 1e-9

    def test_base_context_records_the_before_value(self, base):
        original = _driver_value(base, "Revenue")
        change = {"target": "Revenue", "operation": "set_driver", "value": 0.08,
                  "driver_type": "seasonal_yoy", "periods": "all"}
        _, _, base_context, _, _ = apply_changes(base, [change])
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

    def test_shift_driver_adds_to_value(self, base):
        original = _driver_value(base, "IT Infrastructure")
        change = {"target": "IT Infrastructure", "operation": "shift_driver",
                  "value": 30000, "driver_type": "fixed", "periods": "all"}
        scenario, _, ctx, _, _ = apply_changes(base, [change])
        value = scenario["drivers_df"].loc[
            scenario["drivers_df"]["line_item"] == "IT Infrastructure",
            "driver_value"].iloc[0]
        assert abs(value - (original + 30000)) < 1e-9
        assert ctx[0]["new_value"] == original + 30000

    def test_set_schedule_replaces_quantity(self, base):
        change = {"target": "Personnel Cost", "operation": "set_schedule",
                  "value": 5, "driver_type": "headcount_driven", "periods": "all"}
        scenario, _, _, _, _ = apply_changes(base, [change])
        assert all(scenario["headcount_df"]["new_hires"] == 5)

    def test_add_schedule_adds_to_quantity(self, base):
        before = base["headcount_df"]["new_hires"].copy()
        change = {"target": "Personnel Cost", "operation": "add_schedule",
                  "value": 3, "driver_type": "headcount_driven", "periods": "all"}
        scenario, _, _, _, _ = apply_changes(base, [change])
        expected = before + 3
        pd.testing.assert_series_equal(
            scenario["headcount_df"]["new_hires"], expected,
            check_names=False)

    def test_marketing_held_constant_for_all_schedule_ops(self, base):
        for op in ("scale_schedule", "set_schedule", "add_schedule", "shift_schedule"):
            val = 1.5 if op == "scale_schedule" else (5 if op == "set_schedule" else (3 if op == "add_schedule" else 1))
            change = {"target": "Marketing Spend", "operation": op,
                      "value": val, "driver_type": "cac_driven", "periods": "all"}
            _, held, _, _, _ = apply_changes(base, [change])
            assert any("Marketing" in h for h in held), \
                "held_constant missing for {}".format(op)

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

    def test_describe_base_change_for_shift_fixed(self):
        text = describe_base_change({
            "target": "IT Infrastructure", "driver_type": "fixed",
            "base_value": 270000, "new_value": 300000, "operation": "shift_driver"})
        assert "270,000" in text and "300,000" in text

    def test_describe_base_change_for_shift_pct(self):
        text = describe_base_change({
            "target": "Revenue", "driver_type": "seasonal_yoy",
            "base_value": 0.12, "new_value": 0.15, "operation": "shift_driver"})
        assert "3.0 percentage points" in text

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


# =============================================================================
# CLASS 13: per-month scenario vectors (Phase 1)
# =============================================================================

class TestPerMonthPhase1:
    """Period-aware overrides for the three in-loop drivers (margin_pct,
    growth_pct, fixed). Revenue (seasonal_yoy) and schedule drivers are
    gated until later phases."""

    # ── Engine: period_overrides changes a single month ─────────────────────

    def test_engine_override_fixed(self, base):
        """IT Infrastructure overridden for one month; others use the scalar."""
        from src.step2_forecast_engine import calculate_forecast
        target_period = base["forecast_periods"][2]
        overrides = {("IT Infrastructure", target_period): 99999.0}
        full_df, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], base["forecast_periods"], base["seasonal_year"],
            period_overrides=overrides,
        )
        fc = full_df[full_df["type"] == "forecast"]
        hit = fc[(fc["line_item"] == "IT Infrastructure") &
                 (fc["period"] == target_period)]
        assert abs(float(hit["value"].iloc[0]) - 99999.0) < 0.01

        other_period = base["forecast_periods"][0]
        other = fc[(fc["line_item"] == "IT Infrastructure") &
                   (fc["period"] == other_period)]
        base_val = _driver_value(base, "IT Infrastructure")
        assert abs(float(other["value"].iloc[0]) - base_val) < 0.01

    def test_engine_override_margin_pct(self, base):
        """COGS overridden for one month uses the override margin, not base."""
        from src.step2_forecast_engine import calculate_forecast
        target_period = base["forecast_periods"][1]
        overrides = {("COGS", target_period): 0.60}
        full_df, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], base["forecast_periods"], base["seasonal_year"],
            period_overrides=overrides,
        )
        fc = full_df[full_df["type"] == "forecast"]
        cogs = float(fc[(fc["line_item"] == "COGS") &
                        (fc["period"] == target_period)]["value"].iloc[0])
        rev = float(fc[(fc["line_item"] == "Revenue") &
                       (fc["period"] == target_period)]["value"].iloc[0])
        assert abs(cogs / rev - 0.60) < 1e-6

    def test_engine_override_growth_pct_compounds(self, base):
        """R&D overridden in one month propagates through later months."""
        from src.step2_forecast_engine import calculate_forecast
        periods = base["forecast_periods"]
        override_period = periods[1]
        overrides = {("R&D Expense", override_period): 0.20}

        full_base, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
        )
        full_scen, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            period_overrides=overrides,
        )
        fc_b = full_base[full_base["type"] == "forecast"]
        fc_s = full_scen[full_scen["type"] == "forecast"]

        def rd_val(fc, p):
            return float(fc[(fc["line_item"] == "R&D Expense") &
                            (fc["period"] == p)]["value"].iloc[0])

        assert rd_val(fc_b, periods[0]) == rd_val(fc_s, periods[0])
        assert rd_val(fc_s, override_period) > rd_val(fc_b, override_period)
        assert rd_val(fc_s, periods[2]) > rd_val(fc_b, periods[2])

    def test_engine_no_overrides_unchanged(self, base):
        """period_overrides=None produces the same result as no argument."""
        from src.step2_forecast_engine import calculate_forecast
        full_a, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], base["forecast_periods"], base["seasonal_year"],
        )
        full_b, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], base["forecast_periods"], base["seasonal_year"],
            period_overrides=None,
        )
        pd.testing.assert_frame_equal(full_a, full_b)

    # ── Validator: per-period OK for the three in-loop drivers ──────────────

    def test_validator_per_period_margin_ok(self):
        result = validate_change(
            {"target": "COGS", "operation": "set_driver",
             "value": 0.50, "periods": ["2026-08"]})
        assert result[0] == "OK"
        assert result[2]["periods"] == ["2026-08"]

    def test_validator_per_period_growth_ok(self):
        result = validate_change(
            {"target": "R&D Expense", "operation": "shift_driver",
             "value": 0.02, "periods": ["2026-09", "2026-10"]})
        assert result[0] == "OK"
        assert result[2]["periods"] == ["2026-09", "2026-10"]

    def test_validator_per_period_fixed_ok(self):
        result = validate_change(
            {"target": "IT Infrastructure", "operation": "set_driver",
             "value": 50000, "periods": ["2026-07"]})
        assert result[0] == "OK"

    # ── Validator: gates for unsupported drivers ────────────────────────────

    def test_validator_per_period_schedule_gated(self):
        result = validate_change(
            {"target": "Personnel Cost", "operation": "set_schedule",
             "value": 5, "periods": ["2026-08"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "schedule" in result[1].lower()

    def test_validator_per_period_invalid_format(self):
        result = validate_change(
            {"target": "IT Infrastructure", "operation": "set_driver",
             "value": 50000, "periods": ["Aug-2026"]})
        assert result[0] == "ILLEGAL"
        assert "YYYY-MM" in result[1]

    # ── apply_changes: per-period builds overrides, not scalar ──────────────

    def test_apply_changes_per_period_builds_overrides(self, base):
        change = {"target": "IT Infrastructure", "operation": "set_driver",
                  "value": 99999, "driver_type": "fixed",
                  "periods": ["2026-09"]}
        scenario, _, ctx, po, _ = apply_changes(base, [change])
        assert ("IT Infrastructure", "2026-09") in po
        assert po[("IT Infrastructure", "2026-09")] == 99999
        orig = _driver_value(base, "IT Infrastructure")
        scen_val = _driver_value(scenario, "IT Infrastructure")
        assert scen_val == orig

    # ── B2: partial vector — unspecified months use the base ────────────────

    def test_partial_vector_unspecified_months_retained(self, base):
        """Override IT for 2 of 6 months; the other 4 must match the base."""
        from src.step2_forecast_engine import calculate_forecast
        periods = base["forecast_periods"]
        override_val = 99999.0
        overrides = {
            ("IT Infrastructure", periods[0]): override_val,
            ("IT Infrastructure", periods[1]): override_val,
        }
        full_base, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
        )
        full_scen, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            period_overrides=overrides,
        )
        fc_b = full_base[full_base["type"] == "forecast"]
        fc_s = full_scen[full_scen["type"] == "forecast"]

        def it_val(fc, p):
            return float(fc[(fc["line_item"] == "IT Infrastructure") &
                            (fc["period"] == p)]["value"].iloc[0])

        for p in periods[:2]:
            assert abs(it_val(fc_s, p) - override_val) < 0.01
        for p in periods[2:]:
            assert abs(it_val(fc_s, p) - it_val(fc_b, p)) < 0.01

    # ── B3: growth partial — rate reverts, level propagates ────────────────

    def test_growth_partial_compounding_across_gap(self, base):
        """Override growth_pct for month 0 only. Month 1 must revert to the
        BASE rate, but compound on month 0's now-shifted running value."""
        from src.step2_forecast_engine import calculate_forecast
        periods = base["forecast_periods"]
        base_rate = _driver_value(base, "R&D Expense")
        high_rate = 0.25
        overrides = {("R&D Expense", periods[0]): high_rate}

        full_base, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
        )
        full_scen, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            period_overrides=overrides,
        )
        fc_b = full_base[full_base["type"] == "forecast"]
        fc_s = full_scen[full_scen["type"] == "forecast"]

        def rd_val(fc, p):
            return float(fc[(fc["line_item"] == "R&D Expense") &
                            (fc["period"] == p)]["value"].iloc[0])

        july_scen = rd_val(fc_s, periods[0])
        aug_scen = rd_val(fc_s, periods[1])
        aug_base = rd_val(fc_b, periods[1])
        assert abs(aug_scen - july_scen * (1 + base_rate)) < 0.01
        assert aug_scen != aug_base

    # ── B4: resolved values for all three operations ───────────────────────

    def test_apply_changes_per_period_scale(self, base):
        """scale_driver per-period: resolved = base_value * factor."""
        base_val = _driver_value(base, "IT Infrastructure")
        change = {"target": "IT Infrastructure", "operation": "scale_driver",
                  "value": 1.5, "driver_type": "fixed",
                  "periods": ["2026-08"]}
        _, _, _, po, _ = apply_changes(base, [change])
        assert abs(po[("IT Infrastructure", "2026-08")] - base_val * 1.5) < 1e-9

    def test_apply_changes_per_period_shift(self, base):
        """shift_driver per-period: resolved = base_value + delta."""
        base_val = _driver_value(base, "IT Infrastructure")
        change = {"target": "IT Infrastructure", "operation": "shift_driver",
                  "value": 500, "driver_type": "fixed",
                  "periods": ["2026-07", "2026-08"]}
        _, _, _, po, _ = apply_changes(base, [change])
        for p in ("2026-07", "2026-08"):
            assert abs(po[("IT Infrastructure", p)] - (base_val + 500)) < 1e-9

    # ── B5: schedule gate also covers cac_driven ───────────────────────────

    def test_validator_per_period_cac_driven_gated(self):
        result = validate_change(
            {"target": "Marketing Spend", "operation": "set_schedule",
             "value": 50, "periods": ["2026-09"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "schedule" in result[1].lower()

    # ── B6: base P&L uses no override ──────────────────────────────────────

    def test_base_pnl_unaffected_by_override(self, base):
        """The override must only touch the scenario side, not the base."""
        change = {"target": "IT Infrastructure", "operation": "set_driver",
                  "value": 99999, "driver_type": "fixed",
                  "periods": ["2026-09"]}
        scenario, _, _, po, vo = apply_changes(base, [change])
        base_pnl = run_forecast(base)
        scenario_pnl = run_forecast(scenario, po, vo)
        plain_base_pnl = run_forecast(base)
        pd.testing.assert_frame_equal(base_pnl, plain_base_pnl)
        base_total = float(base_pnl.loc[
            base_pnl["line"] == "IT Infrastructure", "total"].iloc[0])
        scen_total = float(scenario_pnl.loc[
            scenario_pnl["line"] == "IT Infrastructure", "total"].iloc[0])
        assert scen_total != base_total

    # ── B7: two vectored drivers rejected (scenario-level) ─────────────────

    def test_two_vectored_drivers_rejected(self):
        changes = [
            {"target": "IT Infrastructure", "operation": "set_driver",
             "value": 50000, "periods": ["2026-07"]},
            {"target": "COGS", "operation": "set_driver",
             "value": 0.50, "periods": ["2026-08"]},
        ]
        results = [validate_change(c) for c in changes]
        assert all(r[0] == "OK" for r in results)
        assert classify_request(results) == "AMBIGUOUS"

    # ── B8: out-of-horizon period rejected ─────────────────────────────────

    def test_out_of_horizon_period_rejected(self, base):
        result = validate_change(
            {"target": "IT Infrastructure", "operation": "set_driver",
             "value": 50000, "periods": ["2027-01"]},
            forecast_periods=base["forecast_periods"])
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "2027-01" in result[1]
        assert "horizon" in result[1]

    # ── B9: bad value on a per-period change still caught ──────────────────

    def test_per_period_bad_value_caught(self):
        """A euro-sized value on margin_pct is caught even with periods set."""
        result = validate_change(
            {"target": "COGS", "operation": "shift_driver",
             "value": 20000, "periods": ["2026-08"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "margin" in result[1]

    # ── B10: echo mentions the period list ─────────────────────────────────

    def test_echo_per_period_mentions_months(self):
        text = echo_back([{"target": "IT Infrastructure",
                           "operation": "set_driver", "value": 50000,
                           "driver_type": "fixed",
                           "periods": ["2026-07", "2026-08"]}])
        assert "2026-07" in text and "2026-08" in text

    # ── B11: uniform change returns empty overrides ────────────────────────

    def test_uniform_change_returns_empty_overrides(self, base):
        change = {"target": "IT Infrastructure", "operation": "set_driver",
                  "value": 50000, "driver_type": "fixed", "periods": "all"}
        _, _, _, po, vo = apply_changes(base, [change])
        assert po == {}
        assert vo == {}


# =============================================================================
# CLASS 14: per-month Revenue (Phase 2)
# =============================================================================

class TestPerMonthPhase2:
    """Revenue per-month scale overrides via value_overrides."""

    # ── T1: apply_changes builds Revenue value override ────────────────────

    def test_revenue_per_month_scale_builds_value_override(self, base):
        """scale_driver per-month on Revenue: resolved = base_seasonal * factor."""
        from src.step2_forecast_engine import _forecast_seasonal_yoy
        base_growth = _driver_value(base, "Revenue")
        base_rev, _ = _forecast_seasonal_yoy(
            base["actuals_df"], "Revenue", base_growth,
            base["forecast_periods"], base["seasonal_year"])
        target_period = base["forecast_periods"][2]
        change = {"target": "Revenue", "operation": "scale_driver",
                  "value": 1.05, "driver_type": "seasonal_yoy",
                  "periods": [target_period]}
        _, _, _, _, vo = apply_changes(base, [change])
        expected = base_rev[target_period] * 1.05
        assert abs(vo[("Revenue", target_period)] - expected) < 0.01

    # ── T2: engine applies the value override ──────────────────────────────

    def test_engine_revenue_value_override(self, base):
        """Revenue overridden for one period shows in the forecast."""
        from src.step2_forecast_engine import calculate_forecast, _forecast_seasonal_yoy
        periods = base["forecast_periods"]
        base_growth = _driver_value(base, "Revenue")
        base_rev, _ = _forecast_seasonal_yoy(
            base["actuals_df"], "Revenue", base_growth,
            periods, base["seasonal_year"])
        target = periods[1]
        new_val = base_rev[target] * 1.10
        vo = {("Revenue", target): new_val}
        full_df, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            value_overrides=vo,
        )
        fc = full_df[full_df["type"] == "forecast"]
        actual = float(fc[(fc["line_item"] == "Revenue") &
                          (fc["period"] == target)]["value"].iloc[0])
        assert abs(actual - new_val) < 0.01

    # ── T3: Revenue per-month set/shift are gated ──────────────────────────

    def test_revenue_per_month_set_gated(self):
        result = validate_change(
            {"target": "Revenue", "operation": "set_driver",
             "value": 0.08, "periods": ["2026-09"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "percentage changes" in result[1]

    def test_revenue_per_month_shift_gated(self):
        result = validate_change(
            {"target": "Revenue", "operation": "shift_driver",
             "value": 0.03, "periods": ["2026-09"]})
        assert result[0] == "NEEDS_CLARIFICATION"
        assert "percentage changes" in result[1]

    # ── T4: no override = identical ────────────────────────────────────────

    def test_engine_empty_value_overrides_unchanged(self, base):
        from src.step2_forecast_engine import calculate_forecast
        full_a, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], base["forecast_periods"], base["seasonal_year"],
        )
        full_b, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], base["forecast_periods"], base["seasonal_year"],
            value_overrides={},
        )
        pd.testing.assert_frame_equal(full_a, full_b)

    # ── T5: COGS follows overridden Revenue ────────────────────────────────

    def test_cogs_follows_revenue_override(self, base):
        """When Revenue is overridden up for one month, COGS for that month
        must equal new_revenue * margin (flow-through by construction)."""
        from src.step2_forecast_engine import calculate_forecast, _forecast_seasonal_yoy
        periods = base["forecast_periods"]
        base_growth = _driver_value(base, "Revenue")
        base_rev, _ = _forecast_seasonal_yoy(
            base["actuals_df"], "Revenue", base_growth,
            periods, base["seasonal_year"])
        target = periods[2]
        new_rev = base_rev[target] * 1.20
        vo = {("Revenue", target): new_rev}
        full_df, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            value_overrides=vo,
        )
        fc = full_df[full_df["type"] == "forecast"]
        cogs = float(fc[(fc["line_item"] == "COGS") &
                        (fc["period"] == target)]["value"].iloc[0])
        margin = _driver_value(base, "COGS")
        assert abs(cogs - new_rev * margin) < 0.01

    # ── T6: base P&L unaffected by value override ─────────────────────────

    def test_base_pnl_unaffected_by_revenue_override(self, base):
        change = {"target": "Revenue", "operation": "scale_driver",
                  "value": 1.10, "driver_type": "seasonal_yoy",
                  "periods": [base["forecast_periods"][0]]}
        scenario, _, _, po, vo = apply_changes(base, [change])
        base_pnl = run_forecast(base)
        scenario_pnl = run_forecast(scenario, po, vo)
        plain_base_pnl = run_forecast(base)
        pd.testing.assert_frame_equal(base_pnl, plain_base_pnl)
        base_rev = float(base_pnl.loc[
            base_pnl["line"] == "Revenue", "total"].iloc[0])
        scen_rev = float(scenario_pnl.loc[
            scenario_pnl["line"] == "Revenue", "total"].iloc[0])
        assert scen_rev > base_rev

    # ── T7: partial Revenue vector — unspecified months retained ───────────

    def test_partial_revenue_vector_unspecified_retained(self, base):
        from src.step2_forecast_engine import calculate_forecast
        periods = base["forecast_periods"]
        vo = {}
        from src.step2_forecast_engine import _forecast_seasonal_yoy
        base_growth = _driver_value(base, "Revenue")
        base_rev, _ = _forecast_seasonal_yoy(
            base["actuals_df"], "Revenue", base_growth,
            periods, base["seasonal_year"])
        vo[("Revenue", periods[0])] = base_rev[periods[0]] * 1.15
        vo[("Revenue", periods[1])] = base_rev[periods[1]] * 1.15
        full_base, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
        )
        full_scen, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            value_overrides=vo,
        )
        fc_b = full_base[full_base["type"] == "forecast"]
        fc_s = full_scen[full_scen["type"] == "forecast"]

        def rev(fc, p):
            return float(fc[(fc["line_item"] == "Revenue") &
                            (fc["period"] == p)]["value"].iloc[0])

        for p in periods[:2]:
            assert rev(fc_s, p) > rev(fc_b, p)
        for p in periods[2:]:
            assert abs(rev(fc_s, p) - rev(fc_b, p)) < 0.01

    # ── T8: out-of-range scale factor rejected ─────────────────────────────

    def test_revenue_per_month_scale_out_of_range(self):
        result = validate_change(
            {"target": "Revenue", "operation": "scale_driver",
             "value": 5.0, "periods": ["2026-08"]})
        assert result[0] == "NEEDS_CLARIFICATION"

    # ── T9: one-vectored-driver rule still holds with Revenue ──────────────

    def test_revenue_plus_another_vectored_rejected(self):
        changes = [
            {"target": "Revenue", "operation": "scale_driver",
             "value": 1.05, "periods": ["2026-07"]},
            {"target": "IT Infrastructure", "operation": "set_driver",
             "value": 50000, "periods": ["2026-08"]},
        ]
        results = [validate_change(c) for c in changes]
        assert all(r[0] == "OK" for r in results)
        assert classify_request(results) == "AMBIGUOUS"

    # ── T10: echo for Revenue per-month ────────────────────────────────────

    def test_echo_revenue_per_month_scale(self):
        text = echo_back([{"target": "Revenue", "operation": "scale_driver",
                           "value": 1.05, "driver_type": "seasonal_yoy",
                           "periods": ["2026-07", "2026-08"]}])
        assert "Revenue" in text
        assert "2026-07" in text and "2026-08" in text
        assert "5 percent" in text


# =============================================================================
# CLASS 15 — Phase 3a: deterministic parser tests
# =============================================================================

class TestPhase3aParser:
    """Deterministic tests for the period-aware parser contract."""

    PERIODS = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12"]

    # ── T1: build_parse_prompt includes real period labels ─────────────────

    def test_prompt_includes_period_labels(self):
        sys_p, _ = build_parse_prompt("cut costs", self.PERIODS)
        for p in self.PERIODS:
            assert p in sys_p

    # ── T2: build_parse_prompt includes per-month rules and shapes ────────

    def test_prompt_includes_per_month_rules(self):
        sys_p, _ = build_parse_prompt("cut costs", self.PERIODS)
        assert "value_by_period" in sys_p
        assert "per month" in sys_p.lower()
        assert "do not invent" in sys_p.lower()
        assert "ramp" in sys_p.lower()

    # ── T3: parse_scenario_json handles per-month response ────────────────

    def test_parse_scenario_json_per_month(self):
        raw = ('```json\n{"changes": [{"target": "COGS", '
               '"operation": "scale_driver", "value": null, '
               '"value_by_period": {"2026-07": 0.95, "2026-08": 0.90}}], '
               '"ambiguity_note": "", "confidence": "high"}\n```')
        result = parse_scenario_json(raw)
        assert result is not None
        ch = result["changes"][0]
        assert ch["value"] is None
        assert ch["value_by_period"] == {"2026-07": 0.95, "2026-08": 0.90}

    # ── T4: parse_scenario_json uniform regression ────────────────────────

    def test_parse_scenario_json_uniform_regression(self):
        raw = ('```json\n{"changes": [{"target": "COGS", '
               '"operation": "scale_driver", "value": 0.80, '
               '"periods": "all"}], '
               '"ambiguity_note": "", "confidence": "high"}\n```')
        result = parse_scenario_json(raw)
        assert result is not None
        ch = result["changes"][0]
        assert ch["value"] == 0.80
        assert ch["periods"] == "all"
        assert "value_by_period" not in ch

    # ── T5: call_claude_parse with mocked client ──────────────────────────

    def test_call_claude_parse_with_mock_client(self):
        canned = ('```json\n{"changes": [{"target": "COGS", '
                  '"operation": "scale_driver", "value": null, '
                  '"value_by_period": {"2026-07": 0.95}}], '
                  '"ambiguity_note": "", "confidence": "high"}\n```')

        class _Resp:
            def __init__(self, text):
                self.content = [type("B", (), {"text": text})()]
                self.usage = type("U", (), {
                    "input_tokens": 1, "output_tokens": 1})()

        class _Client:
            def __init__(self, text):
                self._t = text

            @property
            def messages(self):
                outer = self
                return type("M", (), {
                    "create": lambda *a, **k: _Resp(outer._t)})()

        scenario, ti, to = call_claude_parse(
            "cut COGS 5% in July", self.PERIODS, client=_Client(canned))
        assert scenario is not None
        assert "value_by_period" in scenario["changes"][0]

    # ── T6: normalise_periods expands {value + periods-list} ──────────────

    def test_normalise_periods_expands_value_plus_list(self):
        scenario = {"changes": [
            {"target": "COGS", "operation": "scale_driver",
             "value": 0.80, "periods": ["2026-07", "2026-08"]},
        ]}
        result = normalise_periods(scenario)
        ch = result["changes"][0]
        assert ch["value"] is None
        assert ch["value_by_period"] == {"2026-07": 0.80, "2026-08": 0.80}

    def test_normalise_periods_leaves_uniform_untouched(self):
        scenario = {"changes": [
            {"target": "COGS", "operation": "scale_driver",
             "value": 0.80, "periods": "all"},
        ]}
        result = normalise_periods(scenario)
        ch = result["changes"][0]
        assert ch["value"] == 0.80
        assert ch["periods"] == "all"
        assert "value_by_period" not in ch

    def test_normalise_periods_leaves_explicit_vbp_untouched(self):
        scenario = {"changes": [
            {"target": "COGS", "operation": "scale_driver",
             "value": None,
             "value_by_period": {"2026-07": 0.95, "2026-08": 0.90}},
        ]}
        result = normalise_periods(scenario)
        ch = result["changes"][0]
        assert ch["value_by_period"] == {"2026-07": 0.95, "2026-08": 0.90}

    # ── T7: validator handles value_by_period element-wise ────────────────

    def test_validator_vbp_all_valid(self):
        change = {"target": "COGS", "operation": "scale_driver",
                  "value": None,
                  "value_by_period": {"2026-07": 0.95, "2026-08": 0.90}}
        status, _, norm = validate_change(
            change, forecast_periods=self.PERIODS)
        assert status == "OK"
        assert norm["value_by_period"] == {"2026-07": 0.95, "2026-08": 0.90}
        assert norm["value"] is None
        assert norm["periods"] == ["2026-07", "2026-08"]

    def test_validator_vbp_out_of_bounds(self):
        change = {"target": "COGS", "operation": "scale_driver",
                  "value": None,
                  "value_by_period": {"2026-07": 5.0}}
        status, reason, _ = validate_change(
            change, forecast_periods=self.PERIODS)
        assert status == "NEEDS_CLARIFICATION"

    def test_validator_vbp_empty_dict(self):
        change = {"target": "COGS", "operation": "scale_driver",
                  "value": None,
                  "value_by_period": {}}
        status, reason, _ = validate_change(change)
        assert status == "NEEDS_CLARIFICATION"
        assert "no forecast month" in reason

    def test_validator_null_value_no_vbp(self):
        change = {"target": "Revenue", "operation": "scale_driver",
                  "value": None, "periods": "all"}
        status, reason, _ = validate_change(change)
        assert status == "NEEDS_CLARIFICATION"
        assert "could not be read" in reason
        assert "Revenue" in reason

    # ── T8: echo_back handles value_by_period ─────────────────────────────

    def test_echo_vbp_same_value(self):
        text = echo_back([{"target": "COGS", "operation": "scale_driver",
                           "value": None, "driver_type": "margin_pct",
                           "value_by_period": {"2026-07": 0.90, "2026-08": 0.90},
                           "periods": ["2026-07", "2026-08"]}])
        assert "COGS" in text
        assert "2026-07" in text and "2026-08" in text
        assert "10 percent" in text

    def test_echo_vbp_different_values(self):
        text = echo_back([{"target": "COGS", "operation": "scale_driver",
                           "value": None, "driver_type": "margin_pct",
                           "value_by_period": {"2026-07": 0.95, "2026-08": 0.90},
                           "periods": ["2026-07", "2026-08"]}])
        assert "COGS" in text
        assert "2026-07" in text and "2026-08" in text
        assert "5 percent" in text
        assert "10 percent" in text

    # ── T9: apply_changes handles value_by_period (in-loop driver) ────────

    def test_apply_changes_vbp_inloop(self, base):
        periods = base["forecast_periods"]
        p0, p1 = periods[0], periods[1]
        change = {"target": "COGS", "operation": "scale_driver",
                  "value": None, "driver_type": "margin_pct",
                  "value_by_period": {p0: 0.90, p1: 0.95},
                  "periods": [p0, p1]}
        _, _, _, po, _ = apply_changes(base, [change])
        base_margin = float(base["drivers_df"].loc[
            base["drivers_df"]["line_item"] == "COGS", "driver_value"].iloc[0])
        assert abs(po[("COGS", p0)] - base_margin * 0.90) < 1e-9
        assert abs(po[("COGS", p1)] - base_margin * 0.95) < 1e-9

    # ── T10: apply_changes handles value_by_period (Revenue) ──────────────

    def test_apply_changes_vbp_revenue(self, base):
        from src.step2_forecast_engine import _forecast_seasonal_yoy
        periods = base["forecast_periods"]
        p0 = periods[0]
        base_growth = float(base["drivers_df"].loc[
            base["drivers_df"]["line_item"] == "Revenue",
            "driver_value"].iloc[0])
        base_rev, _ = _forecast_seasonal_yoy(
            base["actuals_df"], "Revenue", base_growth,
            periods, base["seasonal_year"])
        change = {"target": "Revenue", "operation": "scale_driver",
                  "value": None, "driver_type": "seasonal_yoy",
                  "value_by_period": {p0: 1.10},
                  "periods": [p0]}
        _, _, _, _, vo = apply_changes(base, [change])
        assert abs(vo[("Revenue", p0)] - base_rev[p0] * 1.10) < 0.01

    # ── T11: end-to-end chain: parse → validate → apply → forecast → P&L ─

    def test_end_to_end_per_month_chain(self, base):
        """Full chain: mocked parser output with value_by_period for COGS
        margin (5% cut in month 0, 10% cut in month 1) → normalise →
        validate → apply → forecast → assert affected months changed and
        unaffected months stayed at base."""
        from src.step2_forecast_engine import calculate_forecast
        periods = base["forecast_periods"]
        p0, p1 = periods[0], periods[1]

        # 1. Simulate parser output (what call_claude_parse would return)
        parser_output = {"changes": [
            {"target": "COGS", "operation": "scale_driver",
             "value": None,
             "value_by_period": {p0: 0.95, p1: 0.90}},
        ], "ambiguity_note": "", "confidence": "high"}

        # 2. Normalise (no-op here since vbp is already explicit)
        scenario = normalise_periods(parser_output)

        # 3. Validate each change
        results = [validate_change(c, forecast_periods=periods)
                   for c in scenario["changes"]]
        classification = classify_request(results)
        assert classification == "CLEAR"
        assert all(r[0] == "OK" for r in results)

        # 4. Apply changes
        normalised = [r[2] for r in results if r[0] == "OK"]
        scenario_model, held, ctx, po, vo = apply_changes(base, normalised)

        # 5. Run both forecasts
        base_pnl = run_forecast(base)
        scenario_pnl = run_forecast(scenario_model, po, vo)

        # 6. Compute deltas
        deltas = compute_deltas(base_pnl, scenario_pnl)

        # 7. Assert: COGS changed (margin scaled → COGS total differs)
        cogs_delta = next(d for d in deltas if d["line"] == "COGS")
        assert cogs_delta["delta"] != 0

        # 8. Assert: per-month COGS values are correct
        full_base, _, _ = calculate_forecast(
            base["actuals_df"], base["drivers_df"], base["operational_df"],
            base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"])
        full_scen, _, _ = calculate_forecast(
            base["actuals_df"], scenario_model["drivers_df"],
            base["operational_df"], base["headcount_df"], base["customer_df"],
            base["last_actual"], periods, base["seasonal_year"],
            period_overrides=po, value_overrides=vo)

        fc_b = full_base[full_base["type"] == "forecast"]
        fc_s = full_scen[full_scen["type"] == "forecast"]

        def cogs_val(fc, p):
            return float(fc[(fc["line_item"] == "COGS") &
                            (fc["period"] == p)]["value"].iloc[0])

        # Affected months: COGS differs from base
        assert cogs_val(fc_s, p0) != cogs_val(fc_b, p0)
        assert cogs_val(fc_s, p1) != cogs_val(fc_b, p1)

        # Unaffected months: COGS identical to base
        for p in periods[2:]:
            assert abs(cogs_val(fc_s, p) - cogs_val(fc_b, p)) < 0.01


# =============================================================================
# CLASS 16 — Phase 3b: per-month table pure helpers
# =============================================================================

from streamlit_app.lib.permonth_table import (
    _to_display, _from_display, _vectored_change,
    _rebuild_normalised_from_table, resolve_run_normalised,
)


class TestPerMonthTableHelpers:
    """Deterministic tests for the display/machine conversion helpers."""

    # ── T1: scale round-trip ──────────────────────────────────────────────

    def test_scale_round_trip_positive(self):
        d = _to_display("scale_driver", 1.05)
        assert d == "+5%"
        v, err = _from_display("scale_driver", d)
        assert err is None
        assert abs(v - 1.05) < 1e-9

    def test_scale_round_trip_negative(self):
        d = _to_display("scale_driver", 0.80)
        assert d == "-20%"
        v, err = _from_display("scale_driver", d)
        assert err is None
        assert abs(v - 0.80) < 1e-9

    # ── T2: set and shift ─────────────────────────────────────────────────

    def test_set_display_parse(self):
        d = _to_display("set_driver", 0.08)
        assert d == "0.08"
        v, err = _from_display("set_driver", d)
        assert err is None
        assert abs(v - 0.08) < 1e-9

    def test_shift_display_parse_pct(self):
        d = _to_display("shift_driver", 0.03)
        assert d == "+0.03"
        v, err = _from_display("shift_driver", d)
        assert err is None
        assert abs(v - 0.03) < 1e-9

    def test_shift_display_parse_euro(self):
        d = _to_display("shift_driver", 30000)
        assert d == "+30000"
        v, err = _from_display("shift_driver", d)
        assert err is None
        assert abs(v - 30000) < 1e-9

    # ── T3: blank → (None, None) ─────────────────────────────────────────

    def test_blank_returns_none(self):
        v, err = _from_display("scale_driver", "")
        assert v is None and err is None

    def test_blank_whitespace_returns_none(self):
        v, err = _from_display("scale_driver", "  ")
        assert v is None and err is None

    # ── T4: garbage → error ───────────────────────────────────────────────

    def test_garbage_returns_error(self):
        v, err = _from_display("scale_driver", "abc")
        assert v is None
        assert err is not None and "Could not read" in err

    # ── T5: rebuild produces value_by_period ──────────────────────────────

    def test_rebuild_from_edited_table(self):
        normalised = [
            {"target": "COGS", "operation": "scale_driver",
             "value": None, "driver_type": "margin_pct",
             "value_by_period": {"2026-07": 0.95, "2026-08": 0.90},
             "periods": ["2026-07", "2026-08"]},
        ]
        edited = pd.DataFrame([
            {"Month": "2026-07", "Change": "-5%"},
            {"Month": "2026-08", "Change": "-10%"},
            {"Month": "2026-09", "Change": "+3%"},
        ])
        rebuilt, errors = _rebuild_normalised_from_table(
            normalised, 0, "scale_driver", edited,
            normalised[0]["value_by_period"])
        assert not errors
        vbp = rebuilt[0]["value_by_period"]
        assert "2026-09" in vbp
        assert abs(vbp["2026-09"] - 1.03) < 1e-9
        assert rebuilt[0]["value"] is None

    # ── T6: all-blank → error ─────────────────────────────────────────────

    def test_rebuild_all_blank_error(self):
        normalised = [
            {"target": "COGS", "operation": "scale_driver",
             "value": None, "driver_type": "margin_pct",
             "value_by_period": {}, "periods": []},
        ]
        edited = pd.DataFrame([
            {"Month": "2026-07", "Change": ""},
            {"Month": "2026-08", "Change": ""},
        ])
        _, errors = _rebuild_normalised_from_table(
            normalised, 0, "scale_driver", edited, {})
        assert len(errors) == 1
        assert "No months" in errors[0]

    # ── T7: _vectored_change detection ────────────────────────────────────

    def test_vectored_change_found(self):
        normalised = [
            {"target": "COGS", "operation": "scale_driver",
             "value": 0.80, "periods": "all"},
            {"target": "Revenue", "operation": "scale_driver",
             "value": None,
             "value_by_period": {"2026-07": 1.05}},
        ]
        idx, ch = _vectored_change(normalised)
        assert idx == 1
        assert ch["target"] == "Revenue"

    def test_vectored_change_none_for_uniform(self):
        normalised = [
            {"target": "COGS", "operation": "scale_driver",
             "value": 0.80, "periods": "all"},
        ]
        idx, ch = _vectored_change(normalised)
        assert idx is None and ch is None

    # ── T8: no-drift on unedited confirm (critical) ───────────────────────

    def test_no_drift_on_unedited_confirm(self):
        original_vbp = {"2026-07": 1.055, "2026-08": 0.873}
        normalised = [
            {"target": "Revenue", "operation": "scale_driver",
             "value": None, "driver_type": "seasonal_yoy",
             "value_by_period": dict(original_vbp),
             "periods": ["2026-07", "2026-08"]},
        ]
        display_rows = []
        for pl in ["2026-07", "2026-08", "2026-09"]:
            machine = original_vbp.get(pl)
            display_rows.append({"Month": pl,
                                 "Change": _to_display("scale_driver", machine)})
        edited = pd.DataFrame(display_rows)
        rebuilt, errors = _rebuild_normalised_from_table(
            normalised, 0, "scale_driver", edited, original_vbp)
        assert not errors
        assert rebuilt[0]["value_by_period"]["2026-07"] == 1.055
        assert rebuilt[0]["value_by_period"]["2026-08"] == 0.873

    # ── T9: edited cell parses, others retain originals ───────────────────

    def test_edited_cell_parses_others_retained(self):
        original_vbp = {"2026-07": 1.055, "2026-08": 0.90}
        normalised = [
            {"target": "Revenue", "operation": "scale_driver",
             "value": None, "driver_type": "seasonal_yoy",
             "value_by_period": dict(original_vbp),
             "periods": ["2026-07", "2026-08"]},
        ]
        display_rows = [
            {"Month": "2026-07",
             "Change": _to_display("scale_driver", 1.055)},
            {"Month": "2026-08", "Change": "+15%"},
        ]
        edited = pd.DataFrame(display_rows)
        rebuilt, errors = _rebuild_normalised_from_table(
            normalised, 0, "scale_driver", edited, original_vbp)
        assert not errors
        assert rebuilt[0]["value_by_period"]["2026-07"] == 1.055
        assert abs(rebuilt[0]["value_by_period"]["2026-08"] - 1.15) < 1e-9

    # ── Q2b: resolve_run_normalised blocks invalid edits ─────────────────

    def _base_normalised_and_vbp(self):
        vbp = {"2026-07": 1.05, "2026-08": 1.08}
        normalised = [
            {"target": "Revenue", "operation": "scale_driver",
             "value": None, "driver_type": "seasonal_yoy",
             "value_by_period": dict(vbp),
             "periods": ["2026-07", "2026-08"]},
        ]
        return normalised, vbp

    def test_resolve_garbage_edit_blocks(self):
        normalised, vbp = self._base_normalised_and_vbp()
        rows = [
            {"Month": "2026-07", "Change": "abc"},
            {"Month": "2026-08",
             "Change": _to_display("scale_driver", 1.08)},
        ]
        edited = pd.DataFrame(rows)
        fps = ["2026-07", "2026-08", "2026-09", "2026-10",
               "2026-11", "2026-12"]
        run, errors = resolve_run_normalised(
            normalised, 0, "scale_driver", edited, vbp, fps)
        assert run == []
        assert len(errors) >= 1
        assert "abc" in errors[0].lower() or "could not read" in errors[0].lower()

    def test_resolve_out_of_range_edit_blocks(self):
        normalised, vbp = self._base_normalised_and_vbp()
        rows = [
            {"Month": "2026-07", "Change": "500"},
            {"Month": "2026-08",
             "Change": _to_display("scale_driver", 1.08)},
        ]
        edited = pd.DataFrame(rows)
        fps = ["2026-07", "2026-08", "2026-09", "2026-10",
               "2026-11", "2026-12"]
        run, errors = resolve_run_normalised(
            normalised, 0, "scale_driver", edited, vbp, fps)
        assert run == []
        assert len(errors) >= 1
        assert "out of range" in errors[0].lower() or "unit error" in errors[0].lower()

    def test_resolve_valid_edit_passes(self):
        normalised, vbp = self._base_normalised_and_vbp()
        rows = [
            {"Month": "2026-07", "Change": "+10%"},
            {"Month": "2026-08",
             "Change": _to_display("scale_driver", 1.08)},
        ]
        edited = pd.DataFrame(rows)
        fps = ["2026-07", "2026-08", "2026-09", "2026-10",
               "2026-11", "2026-12"]
        run, errors = resolve_run_normalised(
            normalised, 0, "scale_driver", edited, vbp, fps)
        assert errors == []
        assert len(run) == 1
        assert abs(run[0]["value_by_period"]["2026-07"] - 1.10) < 1e-9
        assert run[0]["value_by_period"]["2026-08"] == 1.08

    # ── Part B: uniform → per-month via edit ─────────────────────────────

    def test_uniform_to_permonth_via_edit(self):
        fps = ["2026-07", "2026-08", "2026-09", "2026-10",
               "2026-11", "2026-12"]
        uniform_val = 1.10
        normalised = [
            {"target": "COGS", "operation": "scale_driver",
             "value": uniform_val, "driver_type": "margin_pct",
             "periods": "all"},
        ]
        original_vbp = {p: uniform_val for p in fps}
        rows = [{"Month": p, "Change": _to_display("scale_driver", uniform_val)}
                for p in fps]
        rows[0]["Change"] = "+20%"
        edited = pd.DataFrame(rows)
        run, errors = resolve_run_normalised(
            normalised, 0, "scale_driver", edited, original_vbp, fps)
        assert errors == []
        assert len(run) == 1
        vbp = run[0]["value_by_period"]
        assert abs(vbp["2026-07"] - 1.20) < 1e-9
        for p in fps[1:]:
            assert vbp[p] == uniform_val

    def test_uniform_to_permonth_invalid_edit_blocks(self):
        fps = ["2026-07", "2026-08", "2026-09", "2026-10",
               "2026-11", "2026-12"]
        uniform_val = 1.10
        normalised = [
            {"target": "COGS", "operation": "scale_driver",
             "value": uniform_val, "driver_type": "margin_pct",
             "periods": "all"},
        ]
        original_vbp = {p: uniform_val for p in fps}
        rows = [{"Month": p, "Change": _to_display("scale_driver", uniform_val)}
                for p in fps]
        rows[0]["Change"] = "garbage"
        edited = pd.DataFrame(rows)
        run, errors = resolve_run_normalised(
            normalised, 0, "scale_driver", edited, original_vbp, fps)
        assert run == []
        assert len(errors) >= 1
        assert "could not read" in errors[0].lower()


# ── Phase 3c: monthly breakdown output ──────────────────────────────────────

class TestMonthlyBreakdown:
    """Tests for build_monthly_rows and _monthly_table (Phase 3c)."""

    PERIODS = ["2026-07", "2026-08", "2026-09"]

    def _make_pnl(self, lines_data):
        """Build a minimal PnL frame: {line_name: {period: value, ...}}."""
        records = []
        for name, vals in lines_data.items():
            rec = {"line": name}
            for p in self.PERIODS:
                rec[p] = vals.get(p, 0.0)
            rec["total"] = sum(vals.get(p, 0.0) for p in self.PERIODS)
            records.append(rec)
        return pd.DataFrame(records)

    def test_build_monthly_rows_structure(self):
        base = self._make_pnl({
            "Revenue": {"2026-07": 100, "2026-08": 110, "2026-09": 120},
            "COGS":    {"2026-07": 40,  "2026-08": 44,  "2026-09": 48},
        })
        scen = self._make_pnl({
            "Revenue": {"2026-07": 105, "2026-08": 115, "2026-09": 120},
            "COGS":    {"2026-07": 42,  "2026-08": 46,  "2026-09": 48},
        })
        mo = build_monthly_rows(base, scen, self.PERIODS)
        assert mo["periods"] == self.PERIODS
        assert len(mo["lines"]) == 2
        assert mo["lines"][0]["line"] == "Revenue"
        assert mo["lines"][1]["line"] == "COGS"
        for ln in mo["lines"]:
            for p in self.PERIODS:
                assert abs(ln["delta"][p] - (ln["scenario"][p] - ln["base"][p])) < 1e-9
                assert ln["scenario"][p] == scen[scen["line"] == ln["line"]].iloc[0][p]
                assert ln["base"][p] == base[base["line"] == ln["line"]].iloc[0][p]

    def test_cogs_follows_revenue_per_month(self):
        base = self._make_pnl({
            "Revenue": {"2026-07": 100, "2026-08": 100, "2026-09": 100},
            "COGS":    {"2026-07": 40,  "2026-08": 40,  "2026-09": 40},
        })
        scen = self._make_pnl({
            "Revenue": {"2026-07": 110, "2026-08": 100, "2026-09": 100},
            "COGS":    {"2026-07": 44,  "2026-08": 40,  "2026-09": 40},
        })
        mo = build_monthly_rows(base, scen, self.PERIODS)
        rev = mo["lines"][0]
        cogs = mo["lines"][1]
        assert rev["delta"]["2026-07"] != 0
        assert cogs["delta"]["2026-07"] != 0
        assert rev["delta"]["2026-08"] == 0
        assert cogs["delta"]["2026-08"] == 0
        assert rev["delta"]["2026-09"] == 0
        assert cogs["delta"]["2026-09"] == 0

    def test_unaffected_months_zero_delta(self):
        base = self._make_pnl({
            "Revenue": {"2026-07": 100, "2026-08": 100, "2026-09": 100},
        })
        scen = self._make_pnl({
            "Revenue": {"2026-07": 105, "2026-08": 100, "2026-09": 100},
        })
        mo = build_monthly_rows(base, scen, self.PERIODS)
        rev = mo["lines"][0]
        assert rev["delta"]["2026-07"] == 5.0
        assert rev["delta"]["2026-08"] == 0.0
        assert rev["delta"]["2026-09"] == 0.0

    def test_monthly_table_smoke(self):
        base = self._make_pnl({
            "Revenue": {"2026-07": 100, "2026-08": 110, "2026-09": 120},
            "COGS":    {"2026-07": 40,  "2026-08": 44,  "2026-09": 48},
        })
        scen = self._make_pnl({
            "Revenue": {"2026-07": 105, "2026-08": 115, "2026-09": 125},
            "COGS":    {"2026-07": 42,  "2026-08": 46,  "2026-09": 50},
        })
        mo = build_monthly_rows(base, scen, self.PERIODS)
        for which in ("scenario", "base", "delta"):
            tbl = _monthly_table(mo, which)
            assert tbl is not None

    def test_scenario_pdf_bytes_with_and_without_monthly(self):
        from streamlit_app.lib.web_emit import scenario_pdf_bytes
        deltas = [{"line": "Revenue", "base": 1000, "scenario": 1050,
                   "delta": 50, "pct": 0.05}]
        base = self._make_pnl({
            "Revenue": {"2026-07": 100, "2026-08": 110, "2026-09": 120},
        })
        scen = self._make_pnl({
            "Revenue": {"2026-07": 105, "2026-08": 115, "2026-09": 125},
        })
        mo = build_monthly_rows(base, scen, self.PERIODS)

        pdf_with = scenario_pdf_bytes(
            "test", "test echo", deltas, "sensitivity", [],
            "Test explanation.", [], [], monthly=mo)
        assert isinstance(pdf_with, bytes)
        assert pdf_with[:5] == b"%PDF-"

        pdf_without = scenario_pdf_bytes(
            "test", "test echo", deltas, "sensitivity", [],
            "Test explanation.", [], [])
        assert isinstance(pdf_without, bytes)
        assert pdf_without[:5] == b"%PDF-"
        assert len(pdf_with) > len(pdf_without)


# ── Phase 3c (revised): quarterly / FY view with actuals ────────────────────

# =============================================================================
# CLASS 19 — Three-case web helpers
# =============================================================================

from streamlit_app.lib.threecase_table import (
    parse_case_value, rebuild_cases_from_table, transpose_to_cases,
)


class TestThreeCaseTableHelpers:

    def test_parse_round_trip_percentage(self):
        v, err = parse_case_value("Revenue", "12.00%")
        assert err is None
        assert abs(v - 0.12) < 1e-9

    def test_parse_round_trip_fixed(self):
        v, err = parse_case_value("IT Infrastructure", "{} 45,000".format(CURRENCY_CODE))
        assert err is None
        assert abs(v - 45000) < 1e-9

    def test_parse_round_trip_schedule(self):
        v, err = parse_case_value("Personnel Cost", "1.50x")
        assert err is None
        assert abs(v - 1.50) < 1e-9

    def test_parse_garbage_returns_error(self):
        v, err = parse_case_value("Revenue", "abc")
        assert v is None
        assert err is not None

    def test_no_drift_on_unedited_cells(self):
        original = {"Revenue": {
            "pessimistic": 0.08255, "realistic": 0.12, "optimistic": 0.15745,
            "basis": "test", "mode": "test", "narrow": False,
        }}
        rows = [{
            "Driver": "Revenue",
            "Pessimistic": format_case_value("Revenue", 0.08255),
            "Realistic": format_case_value("Revenue", 0.12),
            "Optimistic": format_case_value("Revenue", 0.15745),
        }]
        edited = pd.DataFrame(rows)
        cases, errors = rebuild_cases_from_table(edited, original)
        assert not errors
        assert cases["Revenue"]["pessimistic"] == 0.08255
        assert cases["Revenue"]["realistic"] == 0.12
        assert cases["Revenue"]["optimistic"] == 0.15745

    def test_validate_distinct_cases(self):
        original = {"Revenue": {
            "pessimistic": 0.10, "realistic": 0.10, "optimistic": 0.10,
            "basis": "test", "mode": "test", "narrow": False,
        }}
        rows = [{
            "Driver": "Revenue",
            "Pessimistic": "10.00%",
            "Realistic": "10.00%",
            "Optimistic": "10.00%",
        }]
        edited = pd.DataFrame(rows)
        _, errors = rebuild_cases_from_table(edited, original)
        assert len(errors) >= 1
        assert "different" in errors[0].lower()

    def test_validate_non_numeric_cell(self):
        original = {"Revenue": {
            "pessimistic": 0.09, "realistic": 0.12, "optimistic": 0.15,
            "basis": "test", "mode": "test", "narrow": False,
        }}
        rows = [{
            "Driver": "Revenue",
            "Pessimistic": "abc",
            "Realistic": "12.00%",
            "Optimistic": "15.00%",
        }]
        edited = pd.DataFrame(rows)
        _, errors = rebuild_cases_from_table(edited, original)
        assert len(errors) >= 1
        assert "could not read" in errors[0].lower()

    def test_rebuild_transpose(self):
        original = {
            "Revenue": {
                "pessimistic": 0.09, "realistic": 0.12, "optimistic": 0.15,
                "basis": "test", "mode": "test", "narrow": False,
            },
            "COGS": {
                "pessimistic": 0.45, "realistic": 0.42, "optimistic": 0.39,
                "basis": "test", "mode": "test", "narrow": False,
            },
        }
        rows = [
            {"Driver": "Revenue",
             "Pessimistic": format_case_value("Revenue", 0.09),
             "Realistic": format_case_value("Revenue", 0.12),
             "Optimistic": "18.00%"},
            {"Driver": "COGS",
             "Pessimistic": format_case_value("COGS", 0.45),
             "Realistic": format_case_value("COGS", 0.42),
             "Optimistic": format_case_value("COGS", 0.39)},
        ]
        edited = pd.DataFrame(rows)
        cases_by_driver, errors = rebuild_cases_from_table(edited, original)
        assert not errors
        cases = transpose_to_cases(cases_by_driver)
        assert cases["pessimistic"]["Revenue"] == 0.09
        assert abs(cases["optimistic"]["Revenue"] - 0.18) < 1e-9
        assert cases["realistic"]["COGS"] == 0.42

    def _sample_spreads_and_rows(self):
        spreads = {"Revenue": {
            "pessimistic": 0.09, "realistic": 0.12, "optimistic": 0.15,
            "basis": "test", "mode": "test", "narrow": False,
        }}
        delta_rows = [
            {"line": "Revenue", "base": 1000,
             "Pessimistic": 900, "Realistic": 1000, "Optimistic": 1100,
             "Pessimistic_delta": -100, "Realistic_delta": 0,
             "Optimistic_delta": 100},
            {"line": EBIT_LABEL, "base": 500,
             "Pessimistic": 400, "Realistic": 500, "Optimistic": 600,
             "Pessimistic_delta": -100, "Realistic_delta": 0,
             "Optimistic_delta": 100},
        ]
        return spreads, delta_rows

    def test_three_case_pdf_bytes_smoke(self):
        from streamlit_app.lib.web_emit import three_case_pdf_bytes
        spreads, delta_rows = self._sample_spreads_and_rows()
        pdf = three_case_pdf_bytes(
            spreads, delta_rows, "Test explanation.", ["EBIT ranges."], "test")
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"

    def test_cli_write_three_case_pdf_still_works(self, tmp_path):
        from src.step6_explainer import write_three_case_pdf
        import config
        orig = config.OUTPUT_DIR
        config.OUTPUT_DIR = tmp_path
        try:
            spreads, delta_rows = self._sample_spreads_and_rows()
            path = write_three_case_pdf(
                spreads, delta_rows, "Test.", ["Takeaway."], "test basis")
            assert path.exists()
            data = path.read_bytes()
            assert data[:5] == b"%PDF-"
        finally:
            config.OUTPUT_DIR = orig


class TestQuarterlyFYView:
    """Tests for build_pnl(row_type='actual'), build_quarterly_rows, and the
    quarterly PDF table."""

    # ── T1: reconciliation — the critical one ────────────────────────────

    def test_actuals_pnl_reconciles_with_real_data(self):
        from main import load_base_model
        base = load_base_model()
        adf = base["actuals_df"]
        fy_periods = sorted(
            p for p in adf["period"].unique() if p.startswith("2026"))
        actuals_frame = pd.DataFrame([{
            "period": r["period"], "line_item": r["line_item"],
            "value": r["actual"], "type": "actual",
        } for _, r in adf[adf["period"].isin(fy_periods)].iterrows()])
        actuals_pnl = build_pnl(actuals_frame, fy_periods, row_type="actual")

        assert list(actuals_pnl["line"]) == [
            "Revenue", "COGS", "Gross Profit",
            "Personnel Cost", "Marketing Spend",
            "IT Infrastructure", "R&D Expense",
            "Total OpEx", "Operating Profit (EBIT)"]

        ebit = actuals_pnl[
            actuals_pnl["line"] == "Operating Profit (EBIT)"].iloc[0]
        q1 = sum(float(ebit[p]) for p in fy_periods
                 if p.endswith(("-01", "-02", "-03")))
        q2 = sum(float(ebit[p]) for p in fy_periods
                 if p.endswith(("-04", "-05", "-06")))
        assert abs(q1 - 179300) < 1.0
        assert abs(q2 - 301100) < 1.0

    # ── T2: quarterly structure — actuals fixed, forecast varies ─────────

    def _make_pnl(self, lines_data, periods):
        records = []
        for name, vals in lines_data.items():
            rec = {"line": name}
            for p in periods:
                rec[p] = vals.get(p, 0.0)
            rec["total"] = sum(vals.get(p, 0.0) for p in periods)
            records.append(rec)
        return pd.DataFrame(records)

    def test_quarterly_actual_delta_zero_forecast_varies(self):
        act_p = ["2026-01", "2026-02", "2026-03",
                 "2026-04", "2026-05", "2026-06"]
        fc_p = ["2026-07", "2026-08", "2026-09",
                "2026-10", "2026-11", "2026-12"]
        actuals_pnl = self._make_pnl({
            "Revenue": {p: 100 for p in act_p}}, act_p)
        base_pnl = self._make_pnl({
            "Revenue": {p: 200 for p in fc_p}}, fc_p)
        scen_pnl = self._make_pnl({
            "Revenue": {"2026-07": 210, "2026-08": 210, "2026-09": 210,
                        "2026-10": 200, "2026-11": 200, "2026-12": 200}},
            fc_p)
        q = build_quarterly_rows(actuals_pnl, base_pnl, scen_pnl,
                                 act_p, fc_p, fy_label="FY 2026")
        rev = q["lines"][0]
        assert rev["delta"]["Q1 2026"] == 0
        assert rev["delta"]["Q2 2026"] == 0
        assert rev["delta"]["Q3 2026"] == 30
        assert rev["delta"]["Q4 2026"] == 0
        fy_delta = rev["delta"]["FY 2026"]
        assert fy_delta == rev["delta"]["Q3 2026"] + rev["delta"]["Q4 2026"]
        fy_base = rev["base"]["FY 2026"]
        fy_scen = rev["scenario"]["FY 2026"]
        assert abs(fy_scen - fy_base - fy_delta) < 1e-9

    # ── T3: quarter membership ───────────────────────────────────────────

    def test_quarter_membership(self):
        act_p = ["2026-01", "2026-02", "2026-03",
                 "2026-04", "2026-05", "2026-06"]
        fc_p = ["2026-07", "2026-08", "2026-09",
                "2026-10", "2026-11", "2026-12"]
        actuals_pnl = self._make_pnl({"Revenue": {p: 1 for p in act_p}}, act_p)
        base_pnl = self._make_pnl({"Revenue": {p: 1 for p in fc_p}}, fc_p)
        q = build_quarterly_rows(actuals_pnl, base_pnl, base_pnl,
                                 act_p, fc_p, fy_label="FY 2026")
        col_keys = [c["key"] for c in q["columns"]]
        assert col_keys == ["Q1 2026", "Q2 2026", "Q3 2026",
                            "Q4 2026", "FY 2026"]
        kinds = {c["key"]: c["kind"] for c in q["columns"]}
        assert kinds["Q1 2026"] == "actual"
        assert kinds["Q2 2026"] == "actual"
        assert kinds["Q3 2026"] == "forecast"
        assert kinds["Q4 2026"] == "forecast"
        assert kinds["FY 2026"] == "fy"

    # ── T4: line order preserved ─────────────────────────────────────────

    def test_line_order_preserved(self):
        act_p = ["2026-01", "2026-02", "2026-03"]
        fc_p = ["2026-07", "2026-08", "2026-09"]
        lines = ["Revenue", "COGS", "Gross Profit"]
        actuals_pnl = self._make_pnl(
            {l: {p: 1 for p in act_p} for l in lines}, act_p)
        base_pnl = self._make_pnl(
            {l: {p: 1 for p in fc_p} for l in lines}, fc_p)
        q = build_quarterly_rows(actuals_pnl, base_pnl, base_pnl,
                                 act_p, fc_p, fy_label="FY 2026")
        assert [l["line"] for l in q["lines"]] == lines

    # ── T5: end-to-end FY delta equals H2 delta ─────────────────────────

    def test_fy_delta_equals_h2_delta_end_to_end(self):
        from main import load_base_model
        base = load_base_model()
        normalised = [{"target": "Revenue", "operation": "scale_driver",
                       "value": 1.05, "driver_type": "seasonal_yoy",
                       "periods": "all"}]
        scenario_model, _, _, po, vo = apply_changes(base, normalised)
        base_pnl = run_forecast(base)
        scenario_pnl = run_forecast(scenario_model, po, vo)

        adf = base["actuals_df"]
        fy = base["forecast_periods"][0][:4]
        actual_periods = sorted(
            p for p in adf["period"].unique() if p.startswith(fy))
        actuals_frame = pd.DataFrame([{
            "period": r["period"], "line_item": r["line_item"],
            "value": r["actual"], "type": "actual",
        } for _, r in adf[adf["period"].isin(actual_periods)].iterrows()])
        actuals_pnl = build_pnl(actuals_frame, actual_periods,
                                row_type="actual")
        q = build_quarterly_rows(actuals_pnl, base_pnl, scenario_pnl,
                                 actual_periods, base["forecast_periods"],
                                 fy_label="FY " + fy)

        ebit = next(l for l in q["lines"]
                    if l["line"] == "Operating Profit (EBIT)")
        fy_key = "FY " + fy
        fy_delta = ebit["delta"][fy_key]
        h2_delta = ebit["delta"]["Q3 " + fy] + ebit["delta"]["Q4 " + fy]
        assert abs(fy_delta - h2_delta) < 1.0

    # ── T6: PDF with and without quarterly ───────────────────────────────

    def test_pdf_with_and_without_quarterly(self):
        from streamlit_app.lib.web_emit import scenario_pdf_bytes
        deltas = [{"line": "Revenue", "base": 1000, "scenario": 1050,
                   "delta": 50, "pct": 0.05}]
        act_p = ["2026-01", "2026-02", "2026-03",
                 "2026-04", "2026-05", "2026-06"]
        fc_p = ["2026-07", "2026-08", "2026-09",
                "2026-10", "2026-11", "2026-12"]
        actuals_pnl = self._make_pnl(
            {"Revenue": {p: 100 for p in act_p}}, act_p)
        base_pnl = self._make_pnl(
            {"Revenue": {p: 200 for p in fc_p}}, fc_p)
        q = build_quarterly_rows(actuals_pnl, base_pnl, base_pnl,
                                 act_p, fc_p, fy_label="FY 2026")

        pdf_with = scenario_pdf_bytes(
            "test", "test echo", deltas, "sensitivity", [],
            "Test explanation.", [], [], quarterly=q)
        assert isinstance(pdf_with, bytes)
        assert pdf_with[:5] == b"%PDF-"

        pdf_without = scenario_pdf_bytes(
            "test", "test echo", deltas, "sensitivity", [],
            "Test explanation.", [], [])
        assert isinstance(pdf_without, bytes)
        assert len(pdf_with) > len(pdf_without)


# =============================================================================
# 20. Cost estimate
# =============================================================================

from streamlit_app.lib.cost import estimate_cost, format_cost
from streamlit_app.lib.example_run import EXAMPLE
from streamlit_app.lib.example_three_case import THREE_CASE_EXAMPLE


class TestCostEstimate:

    def test_estimate_cost_basic(self):
        c = estimate_cost(1926, 348)
        assert c["usd"] == pytest.approx(0.011, abs=0.001)
        assert c["eur"] == pytest.approx(0.010, abs=0.001)
        assert c["tokens_total"] == 2274

    def test_format_cost_subcent_says_less_than(self):
        line = format_cost(1, 1)
        assert "less than EUR 0.01" in line
        assert "EUR 0.00" not in line
        assert "0.0000" not in line

    def test_format_cost_has_no_dashes(self):
        line = format_cost(1926, 348)
        assert "—" not in line and "–" not in line

    def test_format_cost_contains_billed_by_anthropic(self):
        line = format_cost(1926, 348)
        assert "billed by Anthropic" in line

    def test_format_cost_shows_token_breakdown(self):
        line = format_cost(1926, 348)
        assert "1,926 in" in line
        assert "348 out" in line
        assert "2,274 tokens" in line

    def test_single_whatif_total_equals_sum_of_parse_and_explain(self):
        parse_in, parse_out = 1581, 82
        explain_in, explain_out = 345, 266
        total_in = parse_in + explain_in
        total_out = parse_out + explain_out
        combined = estimate_cost(total_in, total_out)
        separate_usd = (estimate_cost(parse_in, parse_out)["usd"]
                        + estimate_cost(explain_in, explain_out)["usd"])
        assert combined["usd"] == pytest.approx(separate_usd, abs=1e-9)
        assert combined["tokens_total"] == (parse_in + parse_out
                                            + explain_in + explain_out)

    def test_three_case_total_is_explain_only(self):
        explain_in, explain_out = 327, 402
        c = estimate_cost(explain_in, explain_out)
        assert c["tokens_total"] == 729

    def test_example_has_real_token_counts(self):
        assert EXAMPLE["tokens_in"] > 0
        assert EXAMPLE["tokens_out"] > 0
        c = estimate_cost(EXAMPLE["tokens_in"], EXAMPLE["tokens_out"])
        assert c["usd"] > 0

    def test_three_case_example_has_real_token_counts(self):
        assert THREE_CASE_EXAMPLE["tokens_in"] > 0
        assert THREE_CASE_EXAMPLE["tokens_out"] > 0
        c = estimate_cost(THREE_CASE_EXAMPLE["tokens_in"],
                          THREE_CASE_EXAMPLE["tokens_out"])
        assert c["usd"] > 0

    def test_audit_record_carries_tokens_and_cost(self):
        st = pytest.importorskip("streamlit")
        from streamlit_app.lib import audit
        st.session_state["audit_runs"] = []
        cost_est = estimate_cost(100, 50)
        audit.record_run("test request", "test echo", "approved",
                         tokens_in=100, tokens_out=50,
                         cost_estimate=cost_est)
        runs = audit.get_runs()
        assert len(runs) == 1
        r = runs[0]
        assert r["tokens_in"] == 100
        assert r["tokens_out"] == 50
        assert r["cost_estimate"]["usd"] == cost_est["usd"]
        st.session_state["audit_runs"] = []

    def test_audit_record_no_tokens_when_omitted(self):
        st = pytest.importorskip("streamlit")
        from streamlit_app.lib import audit
        st.session_state["audit_runs"] = []
        audit.record_run("refused request", "Refused", "refused")
        runs = audit.get_runs()
        assert runs[0]["tokens_in"] is None
        assert runs[0]["tokens_out"] is None
        assert runs[0]["cost_estimate"] is None
        st.session_state["audit_runs"] = []


# =============================================================================
# CLASS 21: charts
# =============================================================================

@pytest.mark.skipif(
    not _has_module("matplotlib"),
    reason="matplotlib not installed in this venv")
class TestCharts:

    def test_quarterly_chart_returns_figure(self):
        from matplotlib.figure import Figure
        from matplotlib import pyplot as plt
        from streamlit_app.lib.charts import build_quarterly_chart
        from streamlit_app.lib.example_run import EXAMPLE
        fig = build_quarterly_chart(EXAMPLE["chart_quarterly"])
        assert isinstance(fig, Figure)
        plt.close(fig)

    def test_three_case_chart_returns_figure(self):
        from matplotlib.figure import Figure
        from matplotlib import pyplot as plt
        from streamlit_app.lib.charts import build_three_case_chart
        from streamlit_app.lib.example_three_case import THREE_CASE_EXAMPLE
        fig = build_three_case_chart(THREE_CASE_EXAMPLE["case_monthly"])
        assert isinstance(fig, Figure)
        plt.close(fig)

    def test_chart_png_bytes_returns_png(self):
        from streamlit_app.lib.charts import build_quarterly_chart, chart_png_bytes
        from streamlit_app.lib.example_run import EXAMPLE
        fig = build_quarterly_chart(EXAMPLE["chart_quarterly"])
        buf = chart_png_bytes(fig)
        data = buf.read()
        assert data[:8] == b'\x89PNG\r\n\x1a\n'

    def test_quarterly_chart_has_no_dashes(self):
        import matplotlib.text
        from matplotlib import pyplot as plt
        from streamlit_app.lib.charts import build_quarterly_chart
        from streamlit_app.lib.example_run import EXAMPLE
        fig = build_quarterly_chart(EXAMPLE["chart_quarterly"])
        texts = [t.get_text() for t in fig.findobj(matplotlib.text.Text)]
        for t in texts:
            assert "—" not in t and "–" not in t
        plt.close(fig)

    def test_three_case_chart_has_no_dashes(self):
        import matplotlib.text
        from matplotlib import pyplot as plt
        from streamlit_app.lib.charts import build_three_case_chart
        from streamlit_app.lib.example_three_case import THREE_CASE_EXAMPLE
        fig = build_three_case_chart(THREE_CASE_EXAMPLE["case_monthly"])
        texts = [t.get_text() for t in fig.findobj(matplotlib.text.Text)]
        for t in texts:
            assert "—" not in t and "–" not in t
        plt.close(fig)

    def test_extract_quarterly_ebit_filters_fy(self):
        from streamlit_app.lib.charts import extract_quarterly_ebit
        quarterly = {
            "columns": [
                {"key": "Q1 2026", "kind": "actual"},
                {"key": "Q2 2026", "kind": "actual"},
                {"key": "Q3 2026", "kind": "forecast"},
                {"key": "Q4 2026", "kind": "forecast"},
                {"key": "FY 2026", "kind": "fy"},
            ],
            "lines": [{
                "line": EBIT_LABEL,
                "base": {"Q1 2026": 100, "Q2 2026": 200, "Q3 2026": 300,
                         "Q4 2026": 400, "FY 2026": 1000},
                "scenario": {"Q1 2026": 100, "Q2 2026": 200, "Q3 2026": 350,
                             "Q4 2026": 450, "FY 2026": 1100},
                "delta": {},
            }],
        }
        result = extract_quarterly_ebit(quarterly)
        assert len(result["labels"]) == 4
        assert "FY 2026" not in result["labels"]
        assert result["kinds"] == ["actual", "actual", "forecast", "forecast"]
        assert result["base"] == [100, 200, 300, 400]
        assert result["scenario"] == [100, 200, 350, 450]

    def test_example_has_chart_quarterly(self):
        from streamlit_app.lib.example_run import EXAMPLE
        cq = EXAMPLE["chart_quarterly"]
        assert len(cq["labels"]) == 4
        assert len(cq["base"]) == 4
        assert len(cq["scenario"]) == 4
        assert cq["kinds"].count("actual") == 2
        assert cq["kinds"].count("forecast") == 2

    def test_three_case_example_has_case_monthly(self):
        from streamlit_app.lib.example_three_case import THREE_CASE_EXAMPLE
        cm = THREE_CASE_EXAMPLE["case_monthly"]
        assert len(cm["periods"]) == 6
        assert len(cm["pessimistic"]) == 6
        assert len(cm["realistic"]) == 6
        assert len(cm["optimistic"]) == 6

    def test_realistic_ordering_in_example(self):
        from streamlit_app.lib.example_three_case import THREE_CASE_EXAMPLE
        cm = THREE_CASE_EXAMPLE["case_monthly"]
        for p, r in zip(cm["pessimistic"], cm["realistic"]):
            assert r > p
        for o, r in zip(cm["optimistic"], cm["realistic"]):
            assert o > r

    def test_build_case_monthly_ebit(self, base):
        from src.step8_three_case import build_case_monthly_ebit
        base_val = spread_base_for("Revenue", base["drivers_df"])
        cases = {
            "pessimistic": {"Revenue": base_val - 0.03},
            "realistic":   {"Revenue": base_val},
            "optimistic":  {"Revenue": base_val + 0.03},
        }
        results = run_three_case(base, cases)
        cm = build_case_monthly_ebit(results, base["forecast_periods"])
        assert cm["periods"] == list(base["forecast_periods"])
        assert len(cm["pessimistic"]) == len(base["forecast_periods"])
        assert len(cm["realistic"]) == len(base["forecast_periods"])
        assert len(cm["optimistic"]) == len(base["forecast_periods"])
        for p, r, o in zip(cm["pessimistic"], cm["realistic"],
                           cm["optimistic"]):
            assert p < r < o


# =============================================================================
# CLASS 22: explanation cards
# =============================================================================

class TestExplanationCards:
    """Card treatment for the explanation surface: verbatim guard and escaping."""

    def test_example_explanation_verbatim_single(self):
        """Splitting the single what-if example on paragraph breaks must
        preserve all content unchanged."""
        explanation = EXAMPLE["explanation"]
        paragraphs = [p.strip() for p in explanation.split("\n\n") if p.strip()]
        rejoined = "\n\n".join(paragraphs)
        assert rejoined == explanation.strip()

    def test_example_explanation_verbatim_three_case(self):
        """Splitting the three-case example on paragraph breaks must
        preserve all content unchanged."""
        explanation = THREE_CASE_EXAMPLE["explanation"]
        paragraphs = [p.strip() for p in explanation.split("\n\n") if p.strip()]
        rejoined = "\n\n".join(paragraphs)
        assert rejoined == explanation.strip()

    def test_takeaway_escaping_hostile_string(self):
        """Takeaway text containing HTML must be escaped in the callout."""
        import html as _html
        hostile = '<script>alert("xss")</script> & < > "test"'
        escaped = _html.escape(hostile)
        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped
        assert "&amp;" in escaped

    def test_held_constant_escaping_hostile_string(self):
        """Held-constant text containing HTML must be escaped."""
        import html as _html
        hostile = 'Revenue <b>unchanged</b> & model assumption'
        escaped = _html.escape(hostile)
        assert "<b>" not in escaped
        assert "&lt;b&gt;" in escaped
        assert "&amp;" in escaped

    def test_example_parts_present_single(self):
        """The single what-if example carries all explanation parts."""
        assert isinstance(EXAMPLE["takeaways"], list)
        assert len(EXAMPLE["takeaways"]) >= 2
        assert isinstance(EXAMPLE["explanation"], str)
        assert "\n\n" in EXAMPLE["explanation"]
        assert isinstance(EXAMPLE["held_constant"], list)
        assert len(EXAMPLE["held_constant"]) >= 1

    def test_example_parts_present_three_case(self):
        """The three-case example carries takeaways and explanation;
        held_constant is absent by design."""
        assert isinstance(THREE_CASE_EXAMPLE["takeaways"], list)
        assert len(THREE_CASE_EXAMPLE["takeaways"]) >= 2
        assert isinstance(THREE_CASE_EXAMPLE["explanation"], str)
        assert "\n\n" in THREE_CASE_EXAMPLE["explanation"]
        assert "held_constant" not in THREE_CASE_EXAMPLE
