# =============================================================================
# step8_three_case.py — run a pessimistic / realistic / optimistic analysis
# =============================================================================
# Flexes ONE driver across three cases, runs the base plus each case through
# the reused Project 2 engine on deep copies, and builds a multi-column delta
# table. The base is never mutated.
#
# A case value means different things by driver kind:
#   value drivers    (seasonal_yoy, margin_pct, growth_pct, fixed)
#                    -> the case value IS the new driver_value
#   schedule drivers (headcount_driven, cac_driven)
#                    -> the case value is a SCALE FACTOR on the quantity
# =============================================================================

import copy

from config import (
    LINE_ITEMS, VALUE_DRIVER_TYPES, SCHEDULE_DRIVER_TYPES, EBIT_LABEL,
)
from src.step5_scenario_engine import run_forecast

CASE_ORDER = ["Pessimistic", "Realistic", "Optimistic"]


def spread_base_for(line_item, drivers_df):
    """The value the three cases are built around. Value drivers use their
    driver_value; schedule drivers use 1.0, a scale factor meaning unchanged."""
    dtype = LINE_ITEMS[line_item]
    if dtype in SCHEDULE_DRIVER_TYPES:
        return 1.0
    row = drivers_df.loc[drivers_df["line_item"] == line_item, "driver_value"]
    return float(row.iloc[0])


def _apply_case(base, case_values):
    """Apply ALL drivers for one case to DEEP COPIES. case_values is a dict
    of {line_item: value}. The base is never touched."""
    drivers   = base["drivers_df"].copy(deep=True)
    headcount = base["headcount_df"].copy(deep=True)
    customer  = base["customer_df"].copy(deep=True)

    for line_item, value in case_values.items():
        dtype = LINE_ITEMS[line_item]
        if dtype in VALUE_DRIVER_TYPES:
            drivers.loc[drivers["line_item"] == line_item, "driver_value"] = value
        elif dtype == "headcount_driven":
            headcount["new_hires"] = headcount["new_hires"] * value
        elif dtype == "cac_driven":
            customer["target_new_customers"] = customer["target_new_customers"] * value

    model = dict(base)
    model["drivers_df"]   = drivers
    model["headcount_df"] = headcount
    model["customer_df"]  = customer
    return model


def run_three_case(base, cases):
    """cases is {'pessimistic': {line_item: value, ...}, 'realistic': {...},
    'optimistic': {...}}. Runs the base plus each case."""
    results = {"Base": run_forecast(base)}
    for name in CASE_ORDER:
        results[name] = run_forecast(_apply_case(base, cases[name.lower()]))
    return results


def multi_case_deltas(results):
    """Build one row per P&L line: the base total, then each case total and
    its delta against the base."""
    base_pnl = results["Base"]
    rows = []
    for line in base_pnl["line"]:
        b = float(base_pnl.loc[base_pnl["line"] == line, "total"].iloc[0])
        row = {"line": line, "base": b}
        for name in CASE_ORDER:
            pnl = results[name]
            v = float(pnl.loc[pnl["line"] == line, "total"].iloc[0])
            row[name] = v
            row[name + "_delta"] = v - b
        rows.append(row)
    return rows


def case_ebit_summary(delta_rows):
    """Pull the EBIT row for the headline comparison across cases."""
    for r in delta_rows:
        if r["line"] == EBIT_LABEL:
            return r
    return None
