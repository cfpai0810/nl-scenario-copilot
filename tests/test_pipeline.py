# =============================================================================
# tests/test_pipeline.py: NL Scenario Modelling Copilot
# =============================================================================
# Phase 5: VALIDATE
#
# Run from the project root with (venv) active:
#   pytest tests/test_pipeline.py -v
#
# Test classes covering both paths, the web pages and their helpers. The
# first twelve are the core:
#    1. Base model loading
#    2. validate_change, the seven operations
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
# CLASS 2: validate_change, the seven operations
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
        audit.record_run("test request", "test echo", audit.DRAFT,
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


class TestDeploymentPins:
    """The web server stack the live app installs."""

    def test_item1_starlette_pinned_for_streamlit_1_60(self):
        """Streamlit 1.60 cannot serve images with starlette 1.8 or later (item 1)."""
        from importlib.metadata import version
        requirements = Path(__file__).resolve().parent.parent / "requirements.txt"
        assert "starlette==1.7.0" in requirements.read_text(encoding="utf-8-sig").split()
        if version("streamlit").startswith("1.60."):
            major, minor = (int(part) for part in version("starlette").split(".")[:2])
            assert (major, minor) < (1, 8)


# =============================================================================
# Step 2, bundle 1: plain failure messages and the key gate
# =============================================================================
import anthropic
import httpx

from streamlit_app.lib.errors import (
    failure_code, friendly_message, model_failure_message, KEY_LOOKS_COMPLETE,
)

APP_DIR = Path(__file__).resolve().parent.parent / "streamlit_app"
STAND_IN_KEY = "sk-ant-api03-" + "x" * 90


def _sdk_error(cls, status):
    """A stand-in SDK error built from httpx objects; nothing is sent."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("stand-in", response=httpx.Response(status, request=request),
               body=None)


def _stand_in_anthropic(error=None):
    """A stand-in for anthropic.Anthropic that records each key it is built
    with and, when error is given, raises it on every model call."""
    class StandIn:
        built = []

        def __init__(self, api_key=None, **kwargs):
            StandIn.built.append(api_key)
            self.messages = self

        def create(self, **kwargs):
            if error is not None:
                raise error
            raise AssertionError("no model call is expected in this test")
    return StandIn


class TestItem17ModelFailures:
    """One plain message per kind of failed model call (items 17 and 55)."""

    @pytest.mark.parametrize("cls,status,code", [
        (anthropic.AuthenticationError, 401, "key_rejected"),
        (anthropic.PermissionDeniedError, 403, "key_forbidden"),
        (anthropic.NotFoundError, 404, "model_not_found"),
        (anthropic.RateLimitError, 429, "rate_limited"),
        (anthropic.BadRequestError, 400, "bad_request"),
        (anthropic.InternalServerError, 500, "service_unavailable"),
    ])
    def test_item17_each_status_maps_to_one_code(self, cls, status, code):
        exc = _sdk_error(cls, status)
        assert failure_code(exc) == code
        message = friendly_message(exc)
        assert message == model_failure_message(code)
        assert message.endswith("Code: `{}`".format(code))
        assert cls.__name__ not in message
        assert "Error" not in message

    def test_item17_overloaded_and_connection_are_service_unavailable(self):
        assert failure_code(_sdk_error(anthropic.APIStatusError, 529)) == \
            "service_unavailable"
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        assert failure_code(anthropic.APIConnectionError(request=request)) == \
            "service_unavailable"

    def test_item17_no_usually_temporary_for_400_or_404(self):
        for code in ("bad_request", "model_not_found"):
            assert "usually temporary" not in model_failure_message(code)
        assert "claude-" in model_failure_message("model_not_found")

    def test_item17_parser_wrap_still_names_the_key(self):
        """The parser wraps the SDK error in RuntimeError; the cause chain
        still maps it, so the single what-if names the key too."""
        from src.step3_scenario_parser import call_claude_parse
        client = _stand_in_anthropic(
            _sdk_error(anthropic.AuthenticationError, 401))(api_key="x")
        with pytest.raises(RuntimeError) as info:
            call_claude_parse("Cut marketing by 20 percent", ["2026-07"],
                              client=client)
        assert friendly_message(info.value) == \
            model_failure_message("key_rejected")

    def test_item17_other_failures_stay_plain(self):
        from streamlit_app.lib.claude_client import NoKeyError
        assert "sidebar" in friendly_message(NoKeyError("no key"))
        generic = friendly_message(ValueError("boom"))
        assert "example prompts" in generic
        assert "ValueError" not in generic and "boom" not in generic
        assert failure_code(ValueError("boom")) is None

    @staticmethod
    def _enter_key(at):
        at.text_input(key="byo_api_key").input(STAND_IN_KEY).run()

    def test_item55_single_what_if_shows_the_key_message(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic(
            _sdk_error(anthropic.AuthenticationError, 401)))
        at = AppTest.from_file(str(APP_DIR / "views" / "single_what_if.py"),
                               default_timeout=60).run()
        self._enter_key(at)
        next(t for t in at.text_input if t.label == "Your what-if").input(
            "Cut marketing spend by 20 percent")
        next(b for b in at.button if b.label == "Interpret this").click().run()
        assert [e.value for e in at.error] == \
            [model_failure_message("key_rejected")]

    def test_item55_three_case_shows_the_same_key_message(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic(
            _sdk_error(anthropic.AuthenticationError, 401)))
        at = AppTest.from_file(str(APP_DIR / "views" / "three_case.py"),
                               default_timeout=60).run()
        self._enter_key(at)
        at.multiselect[0].set_value(["COGS margin"]).run()
        next(b for b in at.button if b.label == "Derive cases").click().run()
        next(b for b in at.button if b.label == "Confirm and run").click().run()
        assert [e.value for e in at.error] == \
            [model_failure_message("key_rejected")]


class TestItem2And4KeyGate:
    """The key gate's privacy wording (item 2) and its persistence across
    pages (item 4)."""

    def test_item2_privacy_wording(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
        at = AppTest.from_file(str(APP_DIR / "Home.py"),
                               default_timeout=60).run()
        box = at.text_input(key="byo_api_key")
        assert box.help == ("Held in the server's memory for this session "
                            "only. Never stored, never logged.")
        notes = " ".join(m.value for m in at.sidebar.markdown)
        assert ("Your key is held in the server's memory for your session "
                "only, used to make the call, and never stored or logged. If "
                "you would rather your key never leave your own machine, "
                "download the project from GitHub and run it locally.") in notes
        assert "browser" not in notes
        box.input(STAND_IN_KEY).run()
        assert [s.value for s in at.sidebar.success] == [KEY_LOOKS_COMPLETE]

    def test_item2_module_header_does_not_overclaim(self):
        source = (APP_DIR / "lib" / "key_gate.py").read_text(encoding="utf-8")
        assert "stored server-side" not in source
        assert "browser" not in source.lower()
        assert "Key accepted" not in source

    def test_item4_key_entered_on_home_works_on_single_what_if(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        stand_in = _stand_in_anthropic()
        monkeypatch.setattr(anthropic, "Anthropic", stand_in)
        at = AppTest.from_file(str(APP_DIR / "Home.py"),
                               default_timeout=60).run()
        at.text_input(key="byo_api_key").input(STAND_IN_KEY).run()
        assert at.session_state["live_client"] is not None
        stand_in.built.clear()

        at.switch_page("views/single_what_if.py").run()
        assert not at.exception
        assert at.text_input(key="byo_api_key").value == STAND_IN_KEY
        assert stand_in.built == [STAND_IN_KEY]
        assert [s.value for s in at.sidebar.success] == [KEY_LOOKS_COMPLETE]
        assert not any("Paste your Anthropic API key" in i.value
                       for i in at.info)


# =============================================================================
# Step 2, bundle 2: signed figures, PDF text and the sidebar on arrival
# =============================================================================
from src.step6_explainer import pdf_safe, signed_money, delta_color_for, clean_markdown


def _metric_arrow_and_colour(metric):
    """The arrow and colour st.metric draws for one rendered metric."""
    from streamlit.proto.Metric_pb2 import Metric as MetricProto
    return (MetricProto.MetricDirection.Name(metric.proto.direction),
            MetricProto.MetricColor.Name(metric.proto.color))


def _signed_metric_app(line, change):
    import streamlit as st
    from src.step6_explainer import signed_money, delta_color_for
    st.metric(line, "x", delta=signed_money(change),
              delta_color=delta_color_for(line))


class TestItem7SignedFigures:
    """A loss reads as a loss: the sign before the symbol, the arrow and the
    colour from the signed value and the line's polarity (items 7 and 58)."""

    def test_item7_sign_goes_before_the_symbol(self):
        assert signed_money(-30153) == "-€30,153"
        assert signed_money(76800) == "+€76,800"
        assert signed_money(0) == "€0"
        assert signed_money(-0.4) == "€0"

    def test_item7_polarity_of_each_line(self):
        assert delta_color_for("Revenue") == "normal"
        assert delta_color_for(EBIT_LABEL) == "normal"
        assert delta_color_for("Gross Profit") == "normal"
        for cost in ("COGS", "Personnel Cost", "Marketing Spend",
                     "IT Infrastructure", "R&D Expense", "Total OpEx"):
            assert delta_color_for(cost) == "inverse"

    @pytest.mark.parametrize("line,change,expected", [
        ("Revenue", 1000, ("UP", "GREEN")),
        ("Revenue", -1000, ("DOWN", "RED")),
        ("COGS", 1000, ("UP", "RED")),
        ("COGS", -1000, ("DOWN", "GREEN")),
        (EBIT_LABEL, -30153, ("DOWN", "RED")),
    ])
    def test_item58_metric_arrow_and_colour(self, line, change, expected):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_signed_metric_app, args=(line, change)).run()
        assert _metric_arrow_and_colour(at.metric[0]) == expected

    def test_item58_three_case_example_loss_is_a_red_down_arrow(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(APP_DIR / "views" / "three_case.py"),
                               default_timeout=60).run()
        metrics = {m.label: m for m in at.metric}
        pessimistic, optimistic = metrics["Pessimistic"], metrics["Optimistic"]
        assert pessimistic.delta == "-€30,153"
        assert _metric_arrow_and_colour(pessimistic) == ("DOWN", "RED")
        assert optimistic.delta == "+€30,217"
        assert _metric_arrow_and_colour(optimistic) == ("UP", "GREEN")

    def test_item7_single_what_if_example_change_reads_signed(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(APP_DIR / "views" / "single_what_if.py"),
                               default_timeout=60).run()
        change = next(m for m in at.metric if m.label == "Change")
        assert change.value == "+€76,800"
        assert _metric_arrow_and_colour(change) == ("UP", "GREEN")


HOSTILE_MODEL_TEXT = ("**Stand-in.** Profit & loss moves when COGS <target is "
                      "missed and IT <b falls.\n\nA second & < paragraph.")


class TestItem35PdfText:
    """Model, data and reviewer text never breaks the PDF (items 35 and 28)."""

    def test_item35_helper_escapes_ampersand_first(self):
        assert pdf_safe("R&D <b> & x < y > z") == \
            "R&amp;D &lt;b&gt; &amp; x &lt; y &gt; z"
        assert pdf_safe("&lt;") == "&amp;lt;"
        assert clean_markdown("**Bold** & <b") == "<b>Bold</b> &amp; &lt;b"

    def test_item35_single_what_if_pdf_builds_with_angle_brackets(self):
        from streamlit_app.lib.web_emit import scenario_pdf_bytes
        deltas = [{"line": "IT Infrastructure", "base": 270000,
                   "scenario": 243000, "delta": -27000, "pct": -0.1},
                  {"line": EBIT_LABEL, "base": 542191, "scenario": 569191,
                   "delta": 27000, "pct": 0.05}]
        pdf = scenario_pdf_bytes(
            "Keep COGS <target and cut IT", "You want <b IT & less", deltas,
            "sensitivity", ["R&D <b is held constant."], HOSTILE_MODEL_TEXT,
            [{"line": "IT Infrastructure", "base": "EUR <b", "scenario": "x < y"}],
            ["EBIT <b rises & more."])
        assert pdf[:5] == b"%PDF-"

    def test_item28_three_case_pdf_builds_with_angle_brackets(self):
        from streamlit_app.lib.web_emit import three_case_pdf_bytes
        spreads = {"COGS": {"pessimistic": 0.45, "realistic": 0.42,
                            "optimistic": 0.39, "basis": "history <b & more",
                            "mode": "data-derived", "narrow": False}}
        delta_rows = [{"line": EBIT_LABEL, "base": 500,
                       "Pessimistic": 400, "Realistic": 500, "Optimistic": 600,
                       "Pessimistic_delta": -100, "Realistic_delta": 0,
                       "Optimistic_delta": 100}]
        pdf = three_case_pdf_bytes(spreads, delta_rows, HOSTILE_MODEL_TEXT,
                                   ["Downside <b & upside."], "basis <b & <")
        assert pdf[:5] == b"%PDF-"


class TestItem53SidebarOnArrival:
    """The sidebar opens on a wide screen and stays closed on a phone (item 53)."""

    def test_item53_initial_sidebar_state_is_auto(self):
        source = (APP_DIR / "Home.py").read_text(encoding="utf-8")
        assert 'initial_sidebar_state="auto"' in source
        assert 'initial_sidebar_state="expanded"' not in source


# =============================================================================
# Step 2, bundle 3: the key gate expander, the Home button, tabular figures
# =============================================================================
GATE_LABEL = "Run it live with your own key (optional)"


def _blocks_of_type(node, block_type):
    """Every block of one type under an AppTest node, in page order."""
    found = []
    for child in getattr(node, "children", {}).values():
        if getattr(child, "type", None) == block_type:
            found.append(child)
        found.extend(_blocks_of_type(child, block_type))
    return found


class TestUx1KeyGateExpander:
    """The whole key gate sits in one closed expander, with a live-mode
    status line below it (UX1)."""

    def test_ux1_gate_is_inside_a_closed_expander(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
        at = AppTest.from_file(str(APP_DIR / "Home.py"),
                               default_timeout=60).run()
        gates = at.sidebar.expander
        assert [g.label for g in gates] == [GATE_LABEL]
        assert gates[0].proto.expanded is False
        assert [t.key for t in gates[0].text_input] == ["byo_api_key"]
        inside = " ".join(c.value for c in gates[0].caption)
        assert "bills your Anthropic account" in inside
        assert "never stored or logged" in " ".join(
            m.value for m in gates[0].markdown)
        assert not any("Run it yourself" in m.value for m in at.sidebar.markdown)
        assert at.sidebar.caption[-1].value == \
            "Live mode is off. The worked examples need no key."

        gates[0].text_input(key="byo_api_key").input(STAND_IN_KEY).run()
        gates = at.sidebar.expander
        assert [g.label for g in gates] == [GATE_LABEL]
        assert [s.value for s in gates[0].success] == [KEY_LOOKS_COMPLETE]
        assert at.sidebar.caption[-1].value == "Live mode is on for this session."

    def test_ux1_warning_stays_inside_the_expander(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
        at = AppTest.from_file(str(APP_DIR / "Home.py"),
                               default_timeout=60).run()
        at.text_input(key="byo_api_key").input("sk-ant-short").run()
        gate = at.sidebar.expander[0]
        assert len(gate.warning) == 1
        assert at.sidebar.caption[-1].value == \
            "Live mode is off. The worked examples need no key."


class TestUx2WorkedExampleButton:
    """Home's button under the banner opens the worked example (UX2). Since
    Step 5a the example is the first tab, so the button only switches page."""

    def test_ux2_button_switches_to_single_what_if_example_tab(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
        at = AppTest.from_file(str(APP_DIR / "Home.py"),
                               default_timeout=60).run()
        # The button comes straight after the banner, before anything else.
        kids = list(at.main.children.values())
        banner = next(i for i, k in enumerate(kids) if getattr(k, "type", "") ==
                      "markdown" and k.value.startswith('<div class="sc-header">'))
        assert getattr(kids[banner + 1], "label", None) == "See the worked example"

        at.main.button[0].click().run()
        assert not at.exception
        assert any(m.value.startswith('<div class="sc-header"><h1>Single what-if</h1>')
                   for m in at.markdown)
        tab_sets = _blocks_of_type(at.main, "tab_container")
        assert len(tab_sets) == 1
        assert [t.label for t in at.tabs] == \
            ["View a worked example", "Run it yourself"]
        assert tab_sets[0].proto.tab_container.default_tab_index == 0
        assert "swi_open_example" not in at.session_state
        assert any(m.label == "Change" for m in at.metric)
        assert at.session_state["live_client"] is None

    def test_ux2_sidebar_link_still_opens_the_first_tab(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(APP_DIR / "views" / "single_what_if.py"),
                               default_timeout=60).run()
        tab_sets = _blocks_of_type(at.main, "tab_container")
        assert tab_sets[0].proto.tab_container.default_tab_index == 0
        assert at.tabs[0].label == "View a worked example"


class TestUx4TabularFigures:
    """Tabular figures only on numeric elements, so a hyphen in text keeps
    its own width (UX4)."""

    @staticmethod
    def _rules():
        import re
        from streamlit_app.lib.theme import inject_css
        css = re.sub(r"/\*.*?\*/", "", inject_css(), flags=re.S)
        return [(sel.split(";")[-1].strip(), body) for sel, body in
                re.findall(r"([^{}]+)\{([^{}]*)\}", css)]

    def test_ux4_global_rule_has_no_tabular_figures(self):
        global_rule = next(body for sel, body in self._rules()
                           if sel.startswith("html, body"))
        assert '"cv05" 1' in global_rule
        assert "tnum" not in global_rule
        # Lists, bold, italics and captions in Inter too, as Project 6's
        # theme sets them (5b fix round)
        global_sel = next(sel for sel, body in self._rules()
                          if sel.startswith("html, body"))
        assert global_sel.endswith("th, ul, ol, li, strong, em, caption")

    def test_ux4_numeric_rule_lists_tables_and_metrics(self):
        numeric = [(sel, body) for sel, body in self._rules() if "tnum" in body]
        assert len(numeric) == 1
        selectors = [s.strip() for s in numeric[0][0].split(",")]
        for wanted in ("table", "table *", '[data-testid="stMetricValue"]',
                       '[data-testid="stMetricDelta"]'):
            assert wanted in selectors


# =============================================================================
# Step 3, fixes: a finished web run is a draft for review
# =============================================================================

def _replying_anthropic():
    """A stand-in for anthropic.Anthropic that answers every model call with a
    canned reply: one scenario for the parse call, a short text otherwise.
    Nothing is sent."""
    from types import SimpleNamespace

    def reply(text):
        return SimpleNamespace(
            content=[SimpleNamespace(text=text)], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50))

    class StandIn:
        def __init__(self, api_key=None, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            if "structured scenario" in kwargs.get("system", ""):
                return reply(
                    '```json\n{"changes": [{"target": "Marketing Spend", '
                    '"operation": "scale_schedule", "value": 0.8, '
                    '"periods": "all"}], "ambiguity_note": "", '
                    '"confidence": "high"}\n```')
            return reply("A stand-in explanation.")
    return StandIn


class TestItem31DraftForReview:
    """The web never approves: the worked example and every finished web run
    carry the DRAFT chip, never "Approved" (item 31). Since Step 5a the chip
    is the series' amber DRAFT, not the blue badge; since Step 5b a run is
    FINAL only once a named reviewer signs it off (TestStep5bSignOff)."""

    CHIP = '<span class="sc-status draft">DRAFT</span>'

    @classmethod
    def _badges(cls, at):
        return [m.value for m in at.markdown
                if '<span class="sc-badge"' in m.value or cls.CHIP in m.value]

    def test_item31_web_runs_are_drafts_for_review(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _replying_anthropic())

        # Single what-if: the worked example, then a finished live run.
        at = AppTest.from_file(str(APP_DIR / "views" / "single_what_if.py"),
                               default_timeout=60).run()
        assert [self.CHIP in b for b in self._badges(at)] == [True]
        at.text_input(key="byo_api_key").input(STAND_IN_KEY).run()
        next(t for t in at.text_input if t.label == "Your what-if").input(
            "Cut marketing spend by 20 percent")
        next(b for b in at.button if b.label == "Interpret this").click().run()
        next(b for b in at.button if b.label == "Confirm and run").click().run()
        assert not at.exception and not at.error
        assert at.session_state["phase"] == "result"
        assert [r["status"] for r in at.session_state["audit_runs"]] == ["draft"]
        badges = self._badges(at)
        # the example, the result, its Sign-off section, the audit list
        assert len(badges) == 4
        assert all(self.CHIP in b for b in badges)

        # Three-case: a finished live run.
        at = AppTest.from_file(str(APP_DIR / "views" / "three_case.py"),
                               default_timeout=60).run()
        at.text_input(key="byo_api_key").input(STAND_IN_KEY).run()
        at.multiselect[0].set_value(["COGS margin"]).run()
        next(b for b in at.button if b.label == "Derive cases").click().run()
        next(b for b in at.button if b.label == "Confirm and run").click().run()
        assert not at.exception and not at.error
        assert at.session_state["tc_phase"] == "result"
        assert [r["status"] for r in at.session_state["audit_runs"]] == ["draft"]
        badges = self._badges(at)
        assert len(badges) == 3   # the result, its Sign-off section, the audit list
        assert all(self.CHIP in b for b in badges)

        # No web page sets the approved status any more.
        for page in ("single_what_if.py", "three_case.py"):
            source = (APP_DIR / "views" / page).read_text(encoding="utf-8")
            assert '"approved"' not in source
        from streamlit_app.lib import audit
        from streamlit_app.lib.theme import status_chip_html
        assert audit.DRAFT == "draft"
        assert status_chip_html(audit.DRAFT) == self.CHIP


class TestDependencies:
    """The pinned packages (items 21 and 22): test tools only in the dev file,
    no byte order mark, one anthropic version, patched versions."""

    @staticmethod
    def _pins(name):
        text = (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8-sig")
        pins = {}
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-r")) and "==" in line:
                package, version = line.split("==", 1)
                pins[package.strip().lower()] = version.strip()
        return text, pins

    @staticmethod
    def _version(text):
        return tuple(int(part) for part in text.split(".")[:3])

    def test_item21_test_tools_only_in_dev_requirements(self):
        _, runtime = self._pins("requirements.txt")
        dev_text, dev = self._pins("requirements-dev.txt")
        assert not {"pytest", "pluggy", "iniconfig", "pygments", "colorama"} & set(runtime)
        assert "-r requirements.txt" in dev_text and "pytest" in dev

    def test_item21_no_byte_order_mark(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("requirements.txt", "requirements-dev.txt"):
            assert not (root / name).read_bytes().startswith(b"\xef\xbb\xbf"), name

    def test_item21_one_anthropic_version(self):
        assert self._pins("requirements.txt")[1]["anthropic"] == "0.116.0"

    def test_item22_patched_versions(self):
        pins = self._pins("requirements.txt")[1]
        assert self._version(pins["anyio"]) >= (4, 14, 2)
        assert self._version(pins["pillow"]) >= (12, 3, 0)

    def test_item22_certifi_has_the_fixed_root_bundle(self):
        # certifi before 2026.10.18 trusts emSign ECC Root CA G3, whose
        # intermediates had compromised keys (GHSA-6q48-6c7w-9gw2).
        text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(
            encoding="utf-8-sig")
        assert "certifi==2026.10.19" in text.split()


# =============================================================================
# Step 5a: Project 6's look and layout, in the series navy (V1 to V5)
# =============================================================================

def _home_with_spies(monkeypatch):
    """Run Home and record the pages it hands to st.navigation and the
    arguments it gives st.set_page_config."""
    import streamlit
    from streamlit.testing.v1 import AppTest
    seen = {}
    navigation, set_page_config = streamlit.navigation, streamlit.set_page_config

    def navigation_spy(pages, *args, **kwargs):
        seen["pages"] = list(pages)
        return navigation(pages, *args, **kwargs)

    def page_config_spy(*args, **kwargs):
        seen["page_config"] = kwargs
        return set_page_config(*args, **kwargs)

    monkeypatch.setattr(streamlit, "navigation", navigation_spy)
    monkeypatch.setattr(streamlit, "set_page_config", page_config_spy)
    monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
    at = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=60).run()
    assert not at.exception
    return at, seen


def _main_blocks(at):
    """The blocks of the main area in page order, and the banner's place."""
    kids = list(at.main.children.values())
    banner = next(i for i, k in enumerate(kids) if getattr(k, "type", "") ==
                  "markdown" and k.value.startswith('<div class="sc-header">'))
    return kids, banner


class TestStep5aLook:
    """Every page in the series pattern: navy controls, one banner, Home in
    three blocks, the addresses kept, the worked example first (V1 to V5)."""

    def test_v1_primary_colour_is_the_navy(self):
        import tomllib
        from streamlit_app.lib.theme import DARK_BLUE
        config = tomllib.loads(
            (APP_DIR.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        assert config["theme"]["primaryColor"] == DARK_BLUE == "#1A3A5C"
        assert config["client"]["toolbarMode"] == "minimal"

    @pytest.mark.parametrize("page,title", [
        ("Home.py", "Scenario Modelling Copilot"),
        ("views/how_the_model_works.py", "How the model works"),
        ("views/single_what_if.py", "Single what-if"),
        ("views/three_case.py", "Three-case analysis"),
        ("views/your_own_data.py", "Your own data"),
        ("views/how_its_built.py", "How it's built"),
    ])
    def test_v2_every_page_has_a_banner(self, page, title, monkeypatch):
        import html
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
        at = AppTest.from_file(str(APP_DIR / page), default_timeout=60).run()
        assert not at.exception
        banners = [m.value for m in at.main.markdown if 'class="sc-header"' in m.value]
        assert len(banners) == 1
        assert banners[0].startswith(
            '<div class="sc-header"><h1>{}</h1><p>'.format(html.escape(title)))
        # The banner replaces the old plain page heading
        assert not any(m.value.startswith("### ") for m in at.main.markdown)

    def test_v2_draft_and_final_chips(self):
        from streamlit_app.lib.theme import (
            status_chip_html, inject_css, DRAFT_CHIP, FINAL_CHIP,
        )
        assert status_chip_html("draft") == '<span class="sc-status draft">DRAFT</span>'
        assert status_chip_html("final") == '<span class="sc-status final">FINAL</span>'
        assert status_chip_html("refused") == status_chip_html("draft")
        assert (DRAFT_CHIP, FINAL_CHIP) == ("#B45F06", "#2E6B3F")
        css = inject_css()
        assert ".sc-status.draft { background: #B45F06; }" in css
        assert ".sc-status.final { background: #2E6B3F; }" in css
        # The blue "draft for review" badge is gone: a run's DRAFT is the chip
        for path in APP_DIR.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "badge_html(audit.DRAFT)" not in source, path
            assert "BADGE_LABEL" not in source, path

    def test_v3_home_blocks_and_page_links(self, monkeypatch):
        at, seen = _home_with_spies(monkeypatch)
        kids, banner = _main_blocks(at)
        assert getattr(kids[banner + 1], "label", None) == "See the worked example"
        values = [getattr(k, "value", None) for k in kids]
        headings = [v for v in values if isinstance(v, str) and v.startswith("#### ")]
        assert headings == ["#### What it does", "#### The governance promise",
                            "#### Pages"]
        promise = values[values.index("#### The governance promise") + 1]
        assert [line.split("**")[1] for line in promise.splitlines()] == [
            "The model reads.", "Python calculates.", "The model explains.",
            "The reviewer signs."]
        # Since Step 5b, the series' bullet (TestStep5bSignOff.test_5b_wording)
        assert promise.endswith("- **The reviewer signs.** Nothing is final until a "
                                "named reviewer reads it and signs.")
        # One link per page except Home, in the navigation order, each with
        # the page's own icon and "Title: what it is"
        links = at.get("page_link")
        pages = seen["pages"][1:]
        assert [link.proto.page for link in links] == [p.url_path for p in pages]
        for link, page in zip(links, pages):
            assert link.proto.label.startswith(page.title + ": ")
            assert link.proto.icon == page.icon
        # The links wrap rather than clip on a phone, as in Project 6
        from streamlit_app.lib.theme import inject_css
        assert ('[data-testid="stPageLink-NavLink"], [data-testid="stPageLink-NavLink"] * {\n'
                '        white-space: normal !important;') in inject_css()
        # Under the links: the key explainer, closed, then the one caption
        after = kids[max(i for i, k in enumerate(kids)
                         if getattr(k, "type", "") == "page_link") + 1:]
        assert after[0].type == "expander"
        assert after[0].label == "Using your own API key"
        assert after[0].proto.expanded is False
        assert "1.1 US cents" in " ".join(m.value for m in after[0].markdown)
        assert [k.type for k in after[1:]] == ["divider", "caption"]
        assert after[2].value == ("Sample data only: Valencia Operations is a "
                                  "fictional company. All figures are illustrative.")
        # Gone from Home: the green data box and the diagram, which is now
        # the first thing on How it's built
        assert not at.success and not at.warning
        assert not at.get("iframe")
        from streamlit.testing.v1 import AppTest
        from streamlit_app.lib.governance_flow import governance_flow_html
        built = AppTest.from_file(str(APP_DIR / "views" / "how_its_built.py"),
                                  default_timeout=60).run()
        kids, banner = _main_blocks(built)
        # Since Step 6c the diagrams are HTML lists, not SVG in frames
        assert kids[banner + 1].type == "markdown"
        assert kids[banner + 1].value == governance_flow_html()
        assert kids[banner + 1].value.startswith('<figure class="fd">')
        assert not built.get("iframe")
        figures = [m.value for m in built.main.markdown
                   if m.value.startswith('<figure class="fd">')]
        assert len(figures) == 3   # the flow, the lifecycle, the pipeline

    def test_v4_titles_are_sentence_case_and_urls_unchanged(self, monkeypatch):
        _, seen = _home_with_spies(monkeypatch)
        # Every page in the navigation order, with the address it had before
        # Step 5a: posts and READMEs link to these.
        assert [(p.title, p.url_path) for p in seen["pages"]] == [
            ("Home", ""),
            ("How the model works", "how_the_model_works"),
            ("Single what-if", "single_what_if"),
            ("Three-case", "three_case"),
            ("Your own data", "your_own_data"),
            ("How it's built", "how_its_built"),
        ]
        for page in seen["pages"]:
            assert all(word == word.lower() for word in page.title.split()[1:]), \
                page.title

    @pytest.mark.parametrize("page", ["single_what_if.py", "three_case.py"])
    def test_v5_example_tab_first_and_no_open_example_flag(self, page):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(APP_DIR / "views" / page),
                               default_timeout=60).run()
        assert not at.exception
        assert [t.label for t in at.tabs] == ["View a worked example", "Run it yourself"]
        tab_sets = _blocks_of_type(at.main, "tab_container")
        assert len(tab_sets) == 1
        assert tab_sets[0].proto.tab_container.default_tab_index == 0
        # The tab bar sits directly under the banner
        kids, banner = _main_blocks(at)
        assert kids[banner + 1].type == "tab_container"
        # The Home button only switches page: no session flag, no default tab
        for path in APP_DIR.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "_open_example" not in source, path
            assert "OPEN_EXAMPLE" not in source, path
        assert "default=" not in (APP_DIR / "views" / page).read_text(encoding="utf-8")

    def test_v5_page_icon_is_the_tool_page_icon(self, monkeypatch):
        _, seen = _home_with_spies(monkeypatch)
        tool = next(p for p in seen["pages"] if p.title == "Single what-if")
        assert seen["page_config"]["page_icon"] == tool.icon == ":material/tune:"


# =============================================================================
# Step 5b: the sign-off section, after Project 6 (both tool pages)
# =============================================================================

SWI_PAGE = APP_DIR / "views" / "single_what_if.py"
TC_PAGE = APP_DIR / "views" / "three_case.py"
_DRAFT_CHIP = '<span class="sc-status draft">DRAFT</span>'
_FINAL_CHIP = '<span class="sc-status final">FINAL</span>'
# The scenario engine's own warning for a marketing change (the stand-in's parse)
_HELD = ("Marketing and customer changes do not flow through to Revenue, which "
         "is modelled on its own seasonal trend, so the revenue line is "
         "unchanged in this scenario.")
_NARROW = ("Some spreads come from very stable history and sit close together. "
           "Consider widening them.")


def _pdf_unescape(match):
    """One PDF string escape: an octal code (a character such as é) or a
    character taken as it is."""
    code = match.group(1)
    return bytes([int(code, 8)]) if code[0] in b"01234567" else code


def _pdf_text(pdf):
    """The text of a ReportLab PDF, read with the standard library only: the
    strings each page stream draws, in order. Image streams are skipped."""
    import base64
    import re
    import zlib
    out = []
    for head, raw in re.findall(rb"<<((?:(?!>>).)*)>>\s*stream\r?\n(.*?)endstream",
                                pdf, re.S):
        if b"/Image" in head:
            continue
        data = zlib.decompress(base64.a85decode(raw.strip().removesuffix(b"~>")))
        for s in re.findall(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", data):
            out.append(re.sub(rb"\\([0-7]{1,3}|.)", _pdf_unescape, s, flags=re.S)
                       .decode("latin-1"))
    return " ".join(" ".join(out).split())


@pytest.fixture
def downloads(monkeypatch):
    """Reads the file behind a download button: while the test runs, every
    file the page hands to one is kept by its media id."""
    from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
    files = {}
    load = MemoryMediaFileStorage.load_and_get_id

    def _load(self, data, mimetype, kind, filename=None):
        file_id = load(self, data, mimetype, kind, filename)
        files[file_id] = self.get_file(file_id).content
        return file_id

    monkeypatch.setattr(MemoryMediaFileStorage, "load_and_get_id", _load)

    def read(at, label):
        (button,) = [d for d in at.get("download_button") if d.proto.label == label]
        return files[button.proto.url.rsplit("/", 1)[-1].split(".")[0]]
    return read


def _ask(at, request, confirm=True):
    """Interpret this, then Confirm and run (Single what-if)."""
    next(t for t in at.text_input if t.label == "Your what-if").input(request)
    next(b for b in at.button if b.label == "Interpret this").click().run()
    if confirm:
        next(b for b in at.button if b.label == "Confirm and run").click().run()
        assert not at.exception and at.session_state["phase"] == "result"


def _derive(at, drivers, confirm=True):
    """Derive cases, then Confirm and run (Three-case)."""
    at.multiselect[0].set_value(drivers).run()
    next(b for b in at.button if b.label == "Derive cases").click().run()
    if confirm:
        next(b for b in at.button if b.label == "Confirm and run").click().run()
        assert not at.exception and at.session_state["tc_phase"] == "result"


def _live_page(page, monkeypatch):
    """A tool page with the stand-in client and a key entered."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(anthropic, "Anthropic", _replying_anthropic())
    at = AppTest.from_file(str(page), default_timeout=90).run()
    at.text_input(key="byo_api_key").input(STAND_IN_KEY).run()
    return at


@pytest.fixture
def swi_result(monkeypatch):
    """Single what-if after a finished run, with the stand-in client."""
    at = _live_page(SWI_PAGE, monkeypatch)
    _ask(at, "Cut marketing spend by 20 percent")
    return at


@pytest.fixture
def tc_result(monkeypatch):
    """Three-case after a finished run (COGS margin, cases as derived)."""
    at = _live_page(TC_PAGE, monkeypatch)
    _derive(at, ["COGS margin"])
    return at


def _section(at):
    """The Sign-off section's markdown, from its heading on ("Run it yourself")."""
    values = [m.value for m in at.tabs[1].markdown]
    return values[values.index("#### Sign-off"):]


def _result_chip(at):
    """The chip at the top of the result, under its rule."""
    values = [m.value for m in at.tabs[1].markdown]
    return values[values.index("---") + 1]


def _sign(at, prefix, name="José Núñez", word="SIGN"):
    at.text_input(key=prefix + "reviewer").input(name).run()
    at.text_input(key=prefix + "sign").input(word).run()
    at.button(key=prefix + "btn_signoff").click().run()
    assert not at.exception


def _boxes(at, prefix):
    return [(t.key, t.value) for t in at.text_input
            if t.key in (prefix + "reviewer", prefix + "sign")]


def _signoff_buttons(at, prefix):
    return [b for b in at.button if b.key == prefix + "btn_signoff"]


def _statuses(at):
    return [r["status"] for r in at.session_state["audit_runs"]]


def _flat(text):
    """PDF text without its spaces: the reader splits a line at each escape."""
    return "".join(text.split())


class TestStep5bSignOff:
    """The Sign-off section on both tool pages: the rule in src/signoff.py,
    drawn by lib/signoff_section.py, as in Project 6 (Step 5b)."""

    def test_5b_the_sentence_is_the_briefs(self):
        from streamlit_app.lib.signoff_section import SENTENCE
        assert SENTENCE == ("Signing says you have read the result against the "
                            "figures above. The figures come from Python. This "
                            "version does not check the numbers in the model's "
                            "wording.")

    @pytest.mark.parametrize("page,prefix", [(SWI_PAGE, "swi_"), (TC_PAGE, "tc_")])
    def test_5b_no_section_without_a_result(self, page, prefix, monkeypatch):
        at = _live_page(page, monkeypatch)
        assert not at.exception
        # The worked example has no section and no download, as before
        assert "#### Sign-off" not in [m.value for m in at.markdown]
        assert not at.get("download_button")
        assert not _boxes(at, prefix) and not _signoff_buttons(at, prefix)
        # Up to the confirm step there is still no result to sign
        if prefix == "swi_":
            _ask(at, "Cut marketing spend by 20 percent", confirm=False)
        else:
            _derive(at, ["COGS margin"], confirm=False)
        assert not at.exception
        assert "#### Sign-off" not in [m.value for m in at.markdown]
        assert not _boxes(at, prefix) and not _signoff_buttons(at, prefix)

    def test_5b_single_what_if_from_run_to_final(self, downloads, swi_result):
        import re
        from src.signoff import (
            NAME_REFUSAL, SCOPE_NOTE, WORD_HELP, WORD_LABEL, WORD_REFUSAL, when_text,
        )
        from streamlit_app.lib.signoff_section import SENTENCE
        at = swi_result
        live = at.tabs[1]
        # After the result, at the level of its sibling sections, before the audit
        assert [m.value for m in live.markdown if m.value.startswith("#")] == [
            "#### Impact on the forecast (Jul-Dec 2026)",
            "#### Full year with your scenario", "#### Sign-off"]
        main = [m.value for m in at.main.markdown]
        assert main.index("#### Sign-off") < main.index("#### This session's audit")
        assert _section(at) == ["#### Sign-off", SENTENCE, _DRAFT_CHIP,
                                "Outstanding before you sign:", "1. " + _HELD]
        assert _result_chip(at) == _DRAFT_CHIP
        assert [(t.label, t.help) for t in live.text_input
                if t.key in ("swi_reviewer", "swi_sign")] == [
            ("Reviewer name", ""), (WORD_LABEL, WORD_HELP)]
        (button,) = _signoff_buttons(at, "swi_")
        assert button.label == "Sign off" and button.proto.type == "primary"
        assert not button.disabled
        assert [c.value for c in live.caption].count(SCOPE_NOTE) == 1
        assert [d.proto.label for d in at.get("download_button")] == [
            "Download the DRAFT PDF"]
        draft = _pdf_text(downloads(at, "Download the DRAFT PDF"))
        assert re.search(r"^WHAT-IF SCENARIO ANALYSIS Valencia Operations · "
                         r"Sensitivity · \d{4}-\d\d-\d\d DRAFT: not signed off "
                         r"THE REQUEST Asked: ", draft)

        # Refused: no name, a wrong word, the word in lower case. Each stays DRAFT.
        for name, word, refusal in [("   ", "SIGN", NAME_REFUSAL),
                                    ("José Núñez", "SIGNED", WORD_REFUSAL),
                                    ("José Núñez", "sign", WORD_REFUSAL)]:
            _sign(at, "swi_", name, word)
            assert [w.value for w in at.tabs[1].warning] == [refusal]
            assert _section(at)[2] == _DRAFT_CHIP
            assert _result_chip(at) == _DRAFT_CHIP
            assert "swi_signed_off" not in at.session_state
            assert _statuses(at) == ["draft"]

        # Signed: FINAL, the signed line, read only, the FINAL PDF
        _sign(at, "swi_", "José Núñez", " SIGN ")
        signed = at.session_state["swi_signed_off"]
        when = when_text(signed.signed_at)
        line = "Signed off by José Núñez on {}.".format(when)
        assert _section(at) == ["#### Sign-off", SENTENCE,
                                "{}&nbsp; {}".format(_FINAL_CHIP, line)]
        assert _result_chip(at) == _FINAL_CHIP
        assert not at.tabs[1].warning
        assert not _boxes(at, "swi_") and not _signoff_buttons(at, "swi_")
        assert [c.value for c in at.tabs[1].caption].count(SCOPE_NOTE) == 1
        assert [d.proto.label for d in at.get("download_button")] == [
            "Download the FINAL PDF"]
        final = _pdf_text(downloads(at, "Download the FINAL PDF"))
        assert re.search(r"^WHAT-IF SCENARIO ANALYSIS Valencia Operations · "
                         r"Sensitivity · \d{4}-\d\d-\d\d FINAL: signed off by "
                         r"José Núñez on " + re.escape(when) + " THE REQUEST", final)
        assert "DRAFT: not signed off" not in final

        # The run's audit row carries the sign-off and what it acknowledged,
        # and the session audit list shows it FINAL with the signed line
        (row,) = at.session_state["audit_runs"]
        assert row["run_id"] == at.session_state["result"]["run_id"]
        assert {k: row[k] for k in ("status", "signed_off_by", "signed_off_at",
                                    "acknowledged_at_sign_off")} == {
            "status": "final", "signed_off_by": "José Núñez",
            "signed_off_at": signed.signed_at.isoformat(),
            "acknowledged_at_sign_off": [_HELD]}
        assert row["raw_request"] == "Cut marketing spend by 20 percent"
        main = [m.value for m in at.main.markdown]
        audit_list = main[main.index("#### This session's audit"):]
        assert _FINAL_CHIP in audit_list and _DRAFT_CHIP not in audit_list
        assert line in [c.value for c in at.main.caption]

    def test_5b_single_what_if_a_second_signoff_is_refused(self, swi_result):
        from src.signoff import ALREADY_SIGNED
        at = swi_result
        _sign(at, "swi_")
        signed = at.session_state["swi_signed_off"]
        result = at.session_state["swi_signed_for"]
        row = dict(at.session_state["audit_runs"][0])
        # The page offers no second Sign off.
        assert not _signoff_buttons(at, "swi_") and not _boxes(at, "swi_")
        # And the rule refuses one: a second click that lands after the first,
        # from the section as it was drawn before the first sign-off.
        del at.session_state["swi_signed_off"]
        at.run()
        at.text_input(key="swi_sign").input("SIGN").run()
        at.session_state["swi_signed_off"] = signed
        at.session_state["swi_signed_for"] = result
        at.button(key="swi_btn_signoff").click().run()
        assert not at.exception
        assert [w.value for w in at.tabs[1].warning] == [ALREADY_SIGNED]
        assert at.session_state["swi_signed_off"] == signed
        assert at.session_state["audit_runs"] == [row]
        assert _section(at)[2].startswith(_FINAL_CHIP)

    def test_5b_single_what_if_a_new_run_is_a_new_draft(self, downloads, swi_result):
        at = swi_result
        _sign(at, "swi_")
        first = at.session_state["result"]["run_id"]
        next(b for b in at.button if b.label == "Run another what-if").click().run()
        assert "#### Sign-off" not in [m.value for m in at.markdown]
        # The same request, run again: a new result, so a new DRAFT. The name
        # stays filled in; SIGN must be typed again.
        _ask(at, "Cut marketing spend by 20 percent")
        assert at.session_state["result"]["run_id"] != first
        assert _section(at)[2] == _DRAFT_CHIP and _result_chip(at) == _DRAFT_CHIP
        assert _boxes(at, "swi_") == [("swi_reviewer", "José Núñez"), ("swi_sign", "")]
        assert "swi_signed_off" not in at.session_state
        assert "DRAFT: not signed off" in _pdf_text(
            downloads(at, "Download the DRAFT PDF"))
        assert _statuses(at) == ["final", "draft"]
        _sign(at, "swi_")
        assert _statuses(at) == ["final", "final"]
        assert at.session_state["audit_runs"][1]["run_id"] == \
            at.session_state["result"]["run_id"]

    def test_5b_the_name_stays_without_a_signoff(self, swi_result):
        at = swi_result
        at.text_input(key="swi_reviewer").input("A. Reviewer").run()
        next(b for b in at.button if b.label == "Run another what-if").click().run()
        _ask(at, "Cut marketing spend by 20 percent")
        assert _boxes(at, "swi_") == [("swi_reviewer", "A. Reviewer"), ("swi_sign", "")]

    def test_5b_single_what_if_a_changed_request_is_stale(self, swi_result):
        from src.signoff import STALE_CAPTION

        def state():
            (button,) = _signoff_buttons(at, "swi_")
            return button.disabled, STALE_CAPTION in [c.value for c in at.caption]

        at = swi_result
        assert state() == (False, False)
        # The key on its own is not an input of the result: removed, the
        # result and its section stay as they are.
        at.text_input(key="byo_api_key").input("").run()
        assert state() == (False, False)
        # Another request sent with no key cannot run, so the result on screen
        # was made from a request that no longer stands: Sign off waits.
        _ask(at, "Set revenue growth to 8 percent", confirm=False)
        assert [w.value for w in at.tabs[1].warning] == [
            "Paste your API key in the sidebar to run a live analysis."]
        assert at.session_state["phase"] == "result"
        assert _section(at)[2] == _DRAFT_CHIP
        assert state() == (True, True)
        # The request it was made from, sent again: no longer stale
        _ask(at, "Cut marketing spend by 20 percent", confirm=False)
        assert state() == (False, False)
        # An empty box sent is not the request either
        _ask(at, "", confirm=False)
        assert state() == (True, True)

    def test_5b_a_key_change_alone_keeps_the_signoff(self, swi_result):
        at = swi_result
        _sign(at, "swi_")
        section = _section(at)
        at.text_input(key="byo_api_key").input("sk-ant-api03-" + "y" * 90).run()
        assert _section(at) == section and _result_chip(at) == _FINAL_CHIP
        at.text_input(key="byo_api_key").input("").run()
        assert _section(at) == section
        assert _statuses(at) == ["final"]

    def test_5b_three_case_from_run_to_final(self, downloads, tc_result):
        import re
        from src.signoff import SCOPE_NOTE, STALE_CAPTION, WORD_REFUSAL, when_text
        from streamlit_app.lib.signoff_section import SENTENCE
        from streamlit_app.lib.web_emit import three_case_csv_bytes
        at = tc_result
        live = at.tabs[1]
        assert [m.value for m in live.markdown if m.value.startswith("#")] == [
            "#### Profit and loss across cases", "#### Sign-off"]
        main = [m.value for m in at.main.markdown]
        assert main.index("#### Sign-off") < main.index("#### This session's audit")
        # COGS margin as derived is a narrow spread: the engine's own warning
        assert _section(at) == ["#### Sign-off", SENTENCE, _DRAFT_CHIP,
                                "Outstanding before you sign:", "1. " + _NARROW]
        assert _result_chip(at) == _DRAFT_CHIP
        # The CSV download stays as it was; the PDF is the DRAFT PDF
        assert [d.proto.label for d in at.get("download_button")] == [
            "Download data (CSV)", "Download the DRAFT PDF"]
        assert downloads(at, "Download data (CSV)") == three_case_csv_bytes(
            at.session_state["tc_result"]["delta_rows"])
        draft = _pdf_text(downloads(at, "Download the DRAFT PDF"))
        assert re.search(r"^WHAT-IF SCENARIO ANALYSIS Valencia Operations · "
                         r"Three-case · \d{4}-\d\d-\d\d DRAFT: not signed off "
                         r"CASES ", draft)
        # No input is on screen with a result, so it is never stale
        assert not at.multiselect and not at.radio
        (button,) = _signoff_buttons(at, "tc_")
        assert not button.disabled
        assert STALE_CAPTION not in [c.value for c in at.caption]
        assert [c.value for c in live.caption].count(SCOPE_NOTE) == 1

        _sign(at, "tc_", "José Núñez", "sign")
        assert [w.value for w in at.tabs[1].warning] == [WORD_REFUSAL]
        assert _statuses(at) == ["draft"]
        _sign(at, "tc_")
        signed = at.session_state["tc_signed_off"]
        when = when_text(signed.signed_at)
        assert _section(at) == ["#### Sign-off", SENTENCE, "{}&nbsp; {}".format(
            _FINAL_CHIP, "Signed off by José Núñez on {}.".format(when))]
        assert _result_chip(at) == _FINAL_CHIP
        assert not _boxes(at, "tc_") and not _signoff_buttons(at, "tc_")
        assert [d.proto.label for d in at.get("download_button")] == [
            "Download data (CSV)", "Download the FINAL PDF"]
        final = _pdf_text(downloads(at, "Download the FINAL PDF"))
        assert re.search(r"· \d{4}-\d\d-\d\d FINAL: signed off by José Núñez on "
                         + re.escape(when) + " CASES ", final)
        (row,) = at.session_state["audit_runs"]
        assert row["run_id"] == at.session_state["tc_result"]["run_id"]
        assert row["status"] == "final" and row["analysis_type"] == "three-case"
        assert row["signed_off_by"] == "José Núñez"
        assert row["acknowledged_at_sign_off"] == [_NARROW]

        # A key change alone keeps it; a new derive and run is a new DRAFT
        at.text_input(key="byo_api_key").input("sk-ant-api03-" + "y" * 90).run()
        assert _result_chip(at) == _FINAL_CHIP
        next(b for b in at.button if b.label == "Run another analysis").click().run()
        assert "#### Sign-off" not in [m.value for m in at.markdown]
        _derive(at, ["IT Infrastructure (fixed cost)"])
        assert _section(at) == ["#### Sign-off", SENTENCE, _DRAFT_CHIP,
                                "Nothing is outstanding."]
        assert _boxes(at, "tc_") == [("tc_reviewer", "José Núñez"), ("tc_sign", "")]
        assert _statuses(at) == ["final", "draft"]
        _sign(at, "tc_")
        assert _statuses(at) == ["final", "final"]
        assert at.session_state["audit_runs"][1]["acknowledged_at_sign_off"] == []

    def test_5b_each_page_keeps_its_own_signoff(self, monkeypatch):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(anthropic, "Anthropic", _replying_anthropic())
        at = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=90).run()
        at.text_input(key="byo_api_key").input(STAND_IN_KEY).run()
        at.switch_page("views/single_what_if.py").run()
        _ask(at, "Cut marketing spend by 20 percent")
        _sign(at, "swi_")
        signed = _section(at)
        # The pages share one session; the sign-off keys carry each page's prefix
        at.switch_page("views/three_case.py").run()
        assert not at.exception
        assert "#### Sign-off" not in [m.value for m in at.markdown]
        assert "tc_signed_off" not in at.session_state
        # Back on Single what-if its form starts empty, but the request last
        # sent is kept: the signed result is neither stale nor a new DRAFT
        at.switch_page("views/single_what_if.py").run()
        assert not at.exception
        assert _section(at) == signed and _result_chip(at) == _FINAL_CHIP

    def test_5b_names_print_safely(self, downloads, swi_result):
        import html
        from src.signoff import NAME_LIMIT, clean_name, pdf_name, when_text
        at = swi_result
        typed = "Zoë 李 <b>R&D</b>\x07" + "x" * 100
        _sign(at, "swi_", typed)
        signed = at.session_state["swi_signed_off"]
        assert signed.name == clean_name(typed) and len(signed.name) == NAME_LIMIT
        assert signed.name.startswith("Zoë 李 <b>R&D</b>xxx")
        # On screen as typed, escaped; in the PDF as its fonts print it, never as markup
        assert _section(at)[2] == "{}&nbsp; {}".format(
            _FINAL_CHIP, html.escape(signed.line))
        final = _flat(_pdf_text(downloads(at, "Download the FINAL PDF")))
        status = "FINAL: signed off by {} on {}".format(
            pdf_name(signed.name), when_text(signed.signed_at))
        assert status.startswith("FINAL: signed off by Zoë ? <b>R&D</b>xxx")
        assert _flat(status) + "THEREQUEST" in final
        assert at.session_state["audit_runs"][0]["signed_off_by"] == signed.name

    def test_5b_example_and_cli_pdfs_print_no_status_line(self):
        import inspect
        from src.signoff import DRAFT_PDF_LINE
        from src.step6_explainer import (
            _three_case_story, write_pdf, write_three_case_pdf,
        )
        from streamlit_app.lib.example_run import EXAMPLE
        from streamlit_app.lib.web_emit import scenario_pdf_bytes, three_case_pdf_bytes
        # Left at its default, the keyword prints nothing: the line is the
        # only difference between the two
        args = (EXAMPLE["request"], EXAMPLE["echo"], EXAMPLE["deltas"],
                EXAMPLE["analysis"], EXAMPLE["held_constant"], EXAMPLE["explanation"],
                [], EXAMPLE["takeaways"])
        plain = _pdf_text(scenario_pdf_bytes(*args))
        draft = _pdf_text(scenario_pdf_bytes(*args, status_line=DRAFT_PDF_LINE))
        assert "signed off" not in plain
        assert draft.replace(" " + DRAFT_PDF_LINE, "", 1) == plain != draft
        spreads = {"COGS": {"pessimistic": 0.45, "realistic": 0.42,
                            "optimistic": 0.39, "basis": "history",
                            "mode": "data-derived", "narrow": True}}
        rows = [{"line": EBIT_LABEL, "base": 500, "Pessimistic": 400,
                 "Realistic": 500, "Optimistic": 600, "Pessimistic_delta": -100,
                 "Realistic_delta": 0, "Optimistic_delta": 100}]
        targs = (spreads, rows, "Text.", ["One."], "history")
        plain = _pdf_text(three_case_pdf_bytes(*targs))
        draft = _pdf_text(three_case_pdf_bytes(*targs, status_line=DRAFT_PDF_LINE))
        assert "signed off" not in plain
        assert draft.replace(" " + DRAFT_PDF_LINE, "", 1) == plain != draft
        # The command-line reports take no status line at all
        for builder in (write_pdf, write_three_case_pdf, _three_case_story):
            assert "status_line" not in inspect.signature(builder).parameters

    def test_5b_audit_rows(self):
        from datetime import datetime, timezone
        import streamlit as st
        from src.signoff import sign_off
        from streamlit_app.lib import audit
        st.session_state["audit_runs"] = []
        refused = audit.record_run("Refused request", "Refused before running", "refused")
        run = audit.record_run("A request", "An echo", audit.DRAFT)
        assert audit.signed_line(run) is None
        signed, _ = sign_off("A. Reviewer", "SIGN",
                             now=datetime(2026, 10, 21, 9, 5, tzinfo=timezone.utc))
        row = audit.sign_run(run["run_id"], signed, ["One warning."])
        assert row is run and row["status"] == audit.FINAL == "final"
        assert row["signed_off_at"] == "2026-10-21T09:05:00+00:00"
        assert row["acknowledged_at_sign_off"] == ["One warning."]
        assert audit.signed_line(row) == ("Signed off by A. Reviewer on 21 October "
                                          "2026 at 09:05 UTC.")
        assert audit.sign_run("no such run", signed, []) is None
        assert refused["status"] == "refused" and audit.signed_line(refused) is None
        st.session_state["audit_runs"] = []

    def test_5b_wording(self, monkeypatch):
        from streamlit_app.lib.theme import BADGE
        at, _ = _home_with_spies(monkeypatch)
        values = [getattr(k, "value", None) for k in at.main.children.values()]
        what = values[values.index("#### What it does") + 1]
        promise = values[values.index("#### The governance promise") + 1]
        assert what.splitlines()[-1] == (
            "- Produces a PDF report, a DRAFT until a named reviewer signs it.")
        assert promise.splitlines()[-1] == (
            "- **The reviewer signs.** Nothing is final until a named reviewer reads "
            "it and signs.")
        built = (APP_DIR / "views" / "how_its_built.py").read_text(encoding="utf-8")
        assert '"stands unchanged. This live demo stops at a typed sign-off, kept for "' \
            in built
        assert "you review and sign off the result" in built
        stale = ["stops at flagging for review", "no sign-off step",
                 "nobody approves", "Nobody approves", "stays a draft for review",
                 "A person reviews", "Download the report (PDF)",
                 "Download report (PDF)"]
        root = APP_DIR.parent
        for path in [root / "README.md", *APP_DIR.rglob("*.py"),
                     *(root / "src").glob("*.py")]:
            text = path.read_text(encoding="utf-8")
            assert not [s for s in stale if s in text], path.name
        # The web has no approved or locked state to draw
        assert set(BADGE) == {"draft", "refused", "caution"}


# =============================================================================
# Step 6a: the page files in views/, every address pinned
# =============================================================================

# Every page's address except Home's ("/"), as published in the README and in
# posts, written out so that no rename can move one.
PINNED_ADDRESSES = ["how_the_model_works", "single_what_if", "three_case",
                    "your_own_data", "how_its_built"]


class TestStep6aViews:
    """A page opened first runs inside Home (the shell, the key gate, the
    sidebar titles), and every address stays where it was published."""

    def test_6a_no_folder_named_pages(self):
        # Streamlit switches on its older multipage mode whenever a folder
        # named pages sits beside the entry script, even an empty one, and in
        # that mode a page opened first can run without Home.
        assert [path for path in APP_DIR.rglob("*")
                if path.is_dir() and path.name == "pages"] == []
        assert (APP_DIR / "views").is_dir()

    def test_6a_every_address_is_pinned(self, monkeypatch):
        import streamlit
        given = {}
        page = streamlit.Page

        def page_spy(*args, **kwargs):
            made = page(*args, **kwargs)
            given[made.title] = kwargs.get("url_path")
            return made

        monkeypatch.setattr(streamlit, "Page", page_spy)
        _, seen = _home_with_spies(monkeypatch)
        assert [p.url_path for p in seen["pages"]] == ["", *PINNED_ADDRESSES]
        # Each address is given with url_path, not taken from the file name
        assert [given[p.title] for p in seen["pages"][1:]] == PINNED_ADDRESSES


# =============================================================================
# Step 6c: the How it's built diagrams, readable on a phone
# =============================================================================
# The three diagrams were SVG in fixed-height frames, so on a phone their text
# shrank to about 4 px and a blank gap followed. They are now HTML lists drawn
# by lib/flow_diagram.py: a row of boxes from 640 px, stacked below that. These
# tests read the page as AppTest renders it and check each diagram's words, its
# accessible name (the old SVG's aria-label), its colours and the order of its
# stages.

import re
from html.parser import HTMLParser

# Each box keeps the stroke and fill it had in the SVG: the person navy, the
# model purple, deterministic code green, the immutable record mid blue
_PERSON = ("#1A3A5C", "#EAF2FB")
_MODEL = ("#6B4FA8", "#F0ECF8")
_ENGINE = ("#1D6B0F", "#E8F3E9")
_RECORD = ("#2D6A9F", "#E6F1FB")

GOVERNANCE_FLOW = {
    "label": "How a request is governed: you describe a change in plain language, "
             "the model shows how it understood you and you confirm it, a "
             "deterministic engine computes every figure with the model absent "
             "from the numbers, the model writes up the figures it was given, and "
             "you review and sign off. The command-line version keeps an audit "
             "log on disk; this web demo keeps a shorter list for your session.",
    "head": "A person opens and closes every run. The model works only in between.",
    "stages": [
        ("YOU DESCRIBE", "A change, stated in plain language", "the person"),
        ("INTENT CONFIRMED", "The model shows what it understood; you agree",
         "you confirm"),
        ("CALCULATION", "Deterministic code runs every figure", "no model here"),
        ("EXPLAINED", "The model writes up figures it was given", "words, not numbers"),
        ("YOU SIGN OFF", "You review the result", "the person"),
    ],
    "colours": [_PERSON, _MODEL, _ENGINE, _MODEL, _PERSON],
    "note": "The command-line version keeps an audit log on disk; this web demo "
            "keeps a shorter list for your session.",
    "key": ["You", "The model, for language", "Deterministic code, for numbers"],
}
LIFECYCLE = {
    "label": "The governance lifecycle: you review the flagged output, approve the "
             "report, the approved version is locked and becomes immutable, everyone "
             "reads the locked record, and a change requires a new separately "
             "approved version.",
    "head": "Review is the start of the record, not the end.",
    "stages": [
        ("YOU REVIEW", "Check the flagged output and data", "the person"),
        ("YOU APPROVE", "Sign off the report as final", "the person"),
        ("LOCKED", "The approved report becomes immutable", "the record"),
        ("READ ONLY", "Everyone reads it, no one edits it", "the record"),
        ("RE-ISSUE", "A new version; the original still stands", "the person"),
    ],
    "colours": [_PERSON, _PERSON, _RECORD, _RECORD, _PERSON],
    "note": "The live demo stops at a typed sign-off. The stages beyond show the "
            "production model this design is built towards.",
    "key": ["You", "The immutable record"],
}
PIPELINE = {
    "label": "The pipeline: a deterministic data stage loads inputs and finds the "
             "forecast horizon; the model parses words into structure; a "
             "deterministic validator checks, classifies and confirms; a "
             "deterministic engine computes the forecast on a copy and the deltas; "
             "and the model explains the figures in words. Only the parse and "
             "explain stages use the model; everything numeric is deterministic.",
    "head": "The model appears in two stages only. Everything numeric is "
            "deterministic.",
    "stages": [
        ("Data", "load inputs, find horizon", "deterministic"),
        ("Parse", "words to structure", "model, language"),
        ("Validate", "check, classify, confirm", "deterministic"),
        ("Compute", "forecast on a copy, deltas", "deterministic"),
        ("Explain", "figures to words", "model, language"),
    ],
    "colours": [_PERSON, _MODEL, _ENGINE, _ENGINE, _MODEL],
    "note": "A separate spread calculator and three-case runner extend the engine "
            "for the three-case tool, and are deterministic too.",
    "key": ["Model, for language", "Deterministic code, for numbers"],
}


class _Diagram(HTMLParser):
    """One flow diagram's HTML read back: the list's accessible name and
    classes, the heading, the note, the colour key, and each stage's parts by
    class, with the box's border and fill colours."""

    def __init__(self, markup):
        super().__init__()
        self.label, self.head, self.note, self.key, self.stages = None, None, None, [], []
        self.classes = None
        self._open = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "ol" and "fd-flow" in classes:
            self.label = attrs.get("aria-label")
            self.classes = classes
        if tag == "li" and "fd-stage" in classes:
            style = dict(part.split(":", 1) for part in
                         (attrs.get("style") or "").split(";") if ":" in part)
            self.stages.append({"cross": "fd-cross" in classes,
                                "colours": (style.get("border-color"),
                                            style.get("background"))})
        self._open.append(classes)

    def handle_endtag(self, tag):
        self._open.pop()

    def handle_data(self, data):
        words, classes = data.strip(), self._open[-1] if self._open else []
        if not words:
            return
        if ["fd-key"] in self._open:
            self.key.append(words)
        elif "fd-head" in classes:
            self.head = words
        elif "fd-note" in classes:
            self.note = words
        elif classes and classes[0] in ("fd-zone", "fd-title", "fd-text", "fd-foot"):
            self.stages[-1][classes[0]] = words


@pytest.fixture(scope="class")
def how_its_built():
    """The How it's built page as AppTest renders it, opened from Home, and its
    flow diagrams read back in page order, each with its place among the
    page's markdown."""
    from streamlit.testing.v1 import AppTest
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(anthropic, "Anthropic", _stand_in_anthropic())
        at = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=120).run()
        at.switch_page("views/how_its_built.py").run()
    assert not at.exception
    flows = [(i, _Diagram(m.value)) for i, m in enumerate(at.main.markdown)
             if m.value.startswith('<figure class="fd">')]
    return at, flows


def _check_flow(flow, expected):
    """A diagram has the expected words and colours, and its stages in the
    expected order."""
    assert flow.label == expected["label"]
    assert flow.head == expected["head"]
    assert [(s["fd-title"], s["fd-text"], s["fd-foot"]) for s in flow.stages] == \
        expected["stages"]
    assert [s["colours"] for s in flow.stages] == expected["colours"]
    assert flow.note == expected["note"]
    assert flow.key == expected["key"]


class TestStep6cDiagrams:

    def test_6c_diagrams_are_html_lists_not_frames(self, how_its_built):
        from streamlit_app.lib import theme
        at, flows = how_its_built
        assert not at.get("iframe"), "no fixed-height frame left on the page"
        source = (APP_DIR / "views" / "how_its_built.py").read_text(encoding="utf-8")
        assert "components" not in source
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        # Each sits where its frame was: the flow just under the banner, the
        # other two just under their lead-ins, in page order
        values = [m.value for m in at.main.markdown]
        banner = next(v for v in values if v.startswith('<div class="sc-header">'))
        leads = [banner, "#### The governance lifecycle", "**The pipeline.**"]
        assert [i for i, _ in flows] == [values.index(lead) + 1 for lead in leads]
        # The CSS is in inject_css: stacked on a phone, a row from 640 px, and
        # no text in a diagram under 13 px
        from streamlit_app.lib import flow_diagram
        flow_css = flow_diagram.flow_css(theme.DARK_BLUE, theme.MUTED, theme.RULE)
        assert flow_css in theme.inject_css()
        assert "@media (min-width: 640px)" in flow_css and "ol.fd-flow" in flow_css
        sizes = [int(s) for s in re.findall(r"font-size: (\d+)px", flow_css)]
        assert sizes and min(sizes) >= 13
        # The renderer imports nothing from an app, so it copies byte for byte
        source = Path(flow_diagram.__file__).read_text(encoding="utf-8")
        assert "import streamlit_app" not in source and "from streamlit_app" not in source

    def test_6c_governance_flow_stages_in_order(self, how_its_built):
        _, flows = how_its_built
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        flow = flows[0][1]
        _check_flow(flow, GOVERNANCE_FLOW)
        # A person opens and closes the run: the flow ends at YOU SIGN OFF
        assert flow.stages[-1]["fd-title"] == "YOU SIGN OFF"
        assert not any(s["cross"] for s in flow.stages)
        # CALCULATION needs a 639 px diagram to stay on one line in a row, so
        # this flow asks for a 660 px row and stays stacked below that
        assert flow.classes == ["fd-flow", "fd-row660"]

    def test_6c_lifecycle_stages_in_order_with_the_demo_boundary(self, how_its_built):
        _, flows = how_its_built
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        flow = flows[1][1]
        _check_flow(flow, LIFECYCLE)
        titles = [s["fd-title"] for s in flow.stages]
        assert "YOU APPROVE" in titles and "LOCKED" in titles
        # The live demo ends after YOU REVIEW: the boundary sits just before YOU
        # APPROVE, with a label for the zone on each side
        assert [s["cross"] for s in flow.stages] == [False, True, False, False, False]
        assert [(s.get("fd-zone"), s["fd-title"]) for s in flow.stages[:2]] == [
            ("Live demo", "YOU REVIEW"), ("Production workflow", "YOU APPROVE")]
        assert all("fd-zone" not in s for s in flow.stages[2:])

    def test_6c_pipeline_stages_in_order(self, how_its_built):
        _, flows = how_its_built
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        flow = flows[2][1]
        _check_flow(flow, PIPELINE)
        # The model appears in exactly two stages: the parser and the explainer
        assert [s["fd-title"] for s in flow.stages
                if s["fd-foot"] == "model, language"] == ["Parse", "Explain"]
        assert not any(s["cross"] for s in flow.stages)


# =============================================================================
# Step 7b: width="stretch" in place of use_container_width
# =============================================================================
# Streamlit 1.60 deprecates use_container_width and logs a line each time a
# table passes it. use_container_width=True set exactly width="stretch", so
# every table now passes that, and looks and behaves as before.

import ast

PROJECT_DIR = APP_DIR.parent


class TestStep7bWidth:

    def test_step7b_no_use_container_width(self):
        files = (sorted(APP_DIR.rglob("*.py"))
                 + sorted((PROJECT_DIR / "src").rglob("*.py"))
                 + [PROJECT_DIR / "main.py"])
        # The scan covers the pages, not an empty folder
        assert APP_DIR / "views" / "single_what_if.py" in files
        found = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    found += [(path.relative_to(PROJECT_DIR).as_posix(), kw.lineno)
                              for kw in node.keywords
                              if kw.arg == "use_container_width"]
        assert found == [], "use_container_width is still passed at " + ", ".join(
            "{}:{}".format(*site) for site in sorted(found))
