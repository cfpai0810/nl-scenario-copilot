# =============================================================================
# step1_data_loader.py — Layer 2: Data Loading and Validation
# =============================================================================
# Responsibilities:
#   - load_actuals():     load and validate the actuals CSV
#   - load_drivers():     load and validate the driver table CSV
#   - detect_boundary():  find last locked period, generate forecast periods
#
# This layer knows about: pandas, file paths, data validation
# This layer does NOT know about: forecast calculation, Claude, output files
# =============================================================================

import pandas as pd
from pathlib import Path

from config import VALID_DRIVER_TYPES, FORECAST_HORIZON

REQUIRED_ACTUALS_COLS = {"period", "line_item", "actual", "status"}
REQUIRED_DRIVER_COLS  = {"line_item", "driver_type", "driver_value"}


def load_actuals(filepath):
    """
    Load the year-to-date actuals CSV and return a validated DataFrame.

    Finance context: Actuals are historical facts — locked and immutable.
    Every row must have status = 'locked' confirming the period is closed.
    Forecast logic must never touch these rows.

    Validates: file exists, required columns present, no nulls in key
    fields, all status values are 'locked'.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(
            "Actuals file not found: {}\n"
            "Expected: {}".format(filepath, filepath.resolve())
        )

    df = pd.read_csv(filepath, dtype={
        "period":    "str",
        "line_item": "str",
        "actual":    "float64",
        "status":    "str",
    })

    for col in ["period", "line_item", "status"]:
        if col in df.columns:
            df[col] = df[col].str.strip()

    missing = REQUIRED_ACTUALS_COLS - set(df.columns)
    if missing:
        raise ValueError(
            "Actuals CSV missing columns: {}\n"
            "Found: {}".format(sorted(missing), sorted(df.columns))
        )

    for col in ["period", "line_item", "actual"]:
        if df[col].isna().any():
            raise ValueError(
                "Null values found in column '{}' — "
                "every row must have a value.".format(col)
            )

    unlocked = df[df["status"] != "locked"]
    if len(unlocked) > 0:
        raise ValueError(
            "{} rows have status != 'locked'.\n"
            "All actuals must be locked before running the forecast.\n"
            "Unlocked periods: {}".format(
                len(unlocked),
                unlocked["period"].unique().tolist()
            )
        )

    periods    = sorted(df["period"].unique())
    line_items = sorted(df["line_item"].unique())

    print("[OK] Actuals loaded")
    print("     Rows:       {}".format(len(df)))
    print("     Periods:    {} ({} to {})".format(
        len(periods), periods[0], periods[-1]))
    print("     Line items: {}".format(len(line_items)))
    print("     All rows locked: True")

    return df


def load_drivers(filepath):
    """
    Load the driver table and return a validated DataFrame.

    Finance context: The driver table is the control panel for the
    forecast. Each row defines how one line item evolves in future periods.
    driver_type tells Python which formula to apply.
    driver_value is the assumption — the number a CFO would challenge.

    Validates: file exists, required columns present, all driver_types
    recognised, no duplicate line items.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(
            "Driver file not found: {}".format(filepath)
        )

    df = pd.read_csv(filepath, dtype={
        "line_item":    "str",
        "driver_type":  "str",
        "driver_value": "float64",
    })

    for col in ["line_item", "driver_type"]:
        if col in df.columns:
            df[col] = df[col].str.strip()

    missing = REQUIRED_DRIVER_COLS - set(df.columns)
    if missing:
        raise ValueError(
            "Driver CSV missing columns: {}".format(sorted(missing))
        )

    invalid_types = set(df["driver_type"]) - set(VALID_DRIVER_TYPES)
    if invalid_types:
        raise ValueError(
            "Unrecognised driver types: {}\n"
            "Valid types: {}".format(
                sorted(invalid_types), VALID_DRIVER_TYPES
            )
        )

    dupes = df[df.duplicated("line_item", keep=False)]
    if len(dupes) > 0:
        raise ValueError(
            "Duplicate line items in driver table: {}".format(
                dupes["line_item"].unique().tolist()
            )
        )

    print("[OK] Driver table loaded")
    print("     Rows:         {}".format(len(df)))
    print("     Driver types: {}".format(
        sorted(df["driver_type"].unique().tolist())))
    for _, row in df.iterrows():
        print("     {:20s} {:12s}  {}".format(
            row["line_item"], row["driver_type"],
            "{:,.3f}".format(row["driver_value"])
        ))

    return df


def detect_boundary(actuals_df):
    """
    Find the last locked period and generate forecast periods.

    Finance context: The boundary divides fact from forecast. Everything
    on or before the boundary is actual. Everything after is calculated
    from drivers. This boundary moves forward each month as new actuals
    are locked — that is what makes it a rolling forecast.

    Returns:
        last_actual:      string e.g. '2026-06'
        forecast_periods: list of strings e.g. ['2026-07', ..., '2026-12']
    """
    periods     = sorted(actuals_df["period"].unique())
    last_actual = periods[-1]

    year, month = map(int, last_actual.split("-"))
    forecast_periods = []
    for _ in range(FORECAST_HORIZON):
        month += 1
        if month > 12:
            month = 1
            year += 1
        forecast_periods.append("{:04d}-{:02d}".format(year, month))

    print("\n[OK] Boundary detected")
    print("     Last actual:      {}".format(last_actual))
    print("     Forecast periods: {} to {}".format(
        forecast_periods[0], forecast_periods[-1]))

    return last_actual, forecast_periods


def load_operational_actuals(filepath):
    """
    Load operational actuals (headcount, new_customers) from the HR/CRM export.

    Finance context: Companies track operational metrics separately from
    financials. Headcount lives in the HR system, customer counts in the
    CRM. This file holds those hard facts. The forecast reads the last
    actual headcount directly from here — it is not derived from cost.

    Expected columns: period, metric, value, status

    Returns a DataFrame. Also returns the most recent headcount as an int
    so the forecast engine has a clean starting point.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(
            "Operational actuals file not found: {}".format(filepath)
        )

    df = pd.read_csv(filepath, dtype={
        "period": "str",
        "metric": "str",
        "value":  "float64",
        "status": "str",
    })

    for col in ["period", "metric", "status"]:
        if col in df.columns:
            df[col] = df[col].str.strip()

    required = {"period", "metric", "value"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(
            "Operational actuals missing columns: {}".format(sorted(missing))
        )

    metrics = sorted(df["metric"].unique())

    print("[OK] Operational actuals loaded")
    print("     Rows:    {}".format(len(df)))
    print("     Metrics: {}".format(metrics))

    return df


def load_headcount_schedule(filepath):
    """
    Load the forward hiring plan for the Personnel Cost forecast.

    Finance context: The headcount schedule is the hiring plan agreed
    with department heads. Each forecast month lists planned new hires,
    an assumed attrition rate, and the fully-loaded cost per head
    (salary + payroll taxes + benefits).

    Expected columns: period, new_hires, attrition_rate, cost_per_head_annual
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(
            "Headcount schedule not found: {}".format(filepath)
        )

    df = pd.read_csv(filepath, dtype={
        "period":               "str",
        "new_hires":            "int64",
        "attrition_rate":       "float64",
        "cost_per_head_annual": "float64",
    })

    df["period"] = df["period"].str.strip()

    required = {"period", "new_hires", "attrition_rate", "cost_per_head_annual"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(
            "Headcount schedule missing columns: {}".format(sorted(missing))
        )

    print("[OK] Headcount schedule loaded")
    print("     Periods: {}".format(len(df)))
    print("     Total planned hires: {}".format(int(df["new_hires"].sum())))

    return df


def load_customer_targets(filepath):
    """
    Load the customer acquisition targets for the Marketing Spend forecast.

    Finance context: The customer targets come from the sales and
    marketing plan. Each forecast month lists the target number of new
    customers, the assumed cost to acquire each one (CAC), and a fixed
    campaign budget on top of the variable acquisition spend.

    Expected columns: period, target_new_customers, cac, fixed_campaign
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(
            "Customer targets not found: {}".format(filepath)
        )

    df = pd.read_csv(filepath, dtype={
        "period":               "str",
        "target_new_customers": "int64",
        "cac":                  "float64",
        "fixed_campaign":       "float64",
    })

    df["period"] = df["period"].str.strip()

    required = {"period", "target_new_customers", "cac", "fixed_campaign"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(
            "Customer targets missing columns: {}".format(sorted(missing))
        )

    print("[OK] Customer targets loaded")
    print("     Periods: {}".format(len(df)))
    print("     Total target new customers: {}".format(
        int(df["target_new_customers"].sum())))

    return df
