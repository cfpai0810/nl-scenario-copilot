# =============================================================================
# step7_scenario_spread.py — Three-case spread calculator
# =============================================================================
# Derives pessimistic / realistic / optimistic values for a driver, using
# either historical data (for derivable driver types) or a preset band.
# All three functions return the same shape: a dict with pessimistic,
# realistic, optimistic, narrow flag, width, and method.
#
# The spread is always in driver-value units (fractions, not percentages).
# Python computes; nothing here calls Claude.
#
#   derive_spread():   data-derived from historical YoY rates
#   preset_spread():   base +/- a fixed band
#   manual_spread():   user-provided values verbatim
#   yoy_growth_rates(): extract YoY rates from actuals for overlapping months
# =============================================================================

import statistics

DERIVABLE_TYPES = {"seasonal_yoy", "margin_pct", "growth_pct"}
NARROW_THRESHOLD = 0.02  # 2 percentage points — below this, flag for widening


def yoy_growth_rates(actuals_df, line_item):
    """Extract year-on-year growth rates from actuals for months present in
    both the prior and current year. Returns a list of floats (fractions)."""
    df = actuals_df[actuals_df["line_item"] == line_item].copy()
    df["year"]  = df["period"].str[:4].astype(int)
    df["month"] = df["period"].str[5:7].astype(int)

    years = sorted(df["year"].unique())
    if len(years) < 2:
        return []

    prior_year   = years[-2]
    current_year = years[-1]

    prior   = df[df["year"] == prior_year].set_index("month")["actual"]
    current = df[df["year"] == current_year].set_index("month")["actual"]

    rates = []
    for m in sorted(set(prior.index) & set(current.index)):
        if prior[m] > 0:
            rates.append((current[m] - prior[m]) / prior[m])
    return rates


def derive_spread(driver_type, base_value, history_rates):
    """Data-derived three-case spread from historical rates.
    Returns a dict with the three values, a narrow flag, and the method.
    Returns None for non-derivable driver types or insufficient history."""
    if driver_type not in DERIVABLE_TYPES:
        return None
    if not history_rates or len(history_rates) < 2:
        return None

    mean = statistics.mean(history_rates)
    std  = statistics.stdev(history_rates)

    pessimistic = mean - 2 * std
    optimistic  = mean + 2 * std
    realistic   = base_value

    width  = optimistic - pessimistic
    narrow = width < NARROW_THRESHOLD

    return {
        "pessimistic": round(pessimistic, 4),
        "realistic":   round(realistic, 4),
        "optimistic":  round(optimistic, 4),
        "narrow":      narrow,
        "width":       round(width, 4),
        "method":      "data-derived",
    }


def preset_spread(base_value, band):
    """Standard band spread: base +/- band. Used when the driver is not
    derivable (fixed, schedule-driven) or when the derived spread is too
    narrow and the user opts for a wider band."""
    return {
        "pessimistic": round(base_value - band, 4),
        "realistic":   round(base_value, 4),
        "optimistic":  round(base_value + band, 4),
        "narrow":      False,
        "width":       round(2 * band, 4),
        "method":      "preset-band",
    }


def manual_spread(pessimistic, realistic, optimistic):
    """User-provided values verbatim. No derivation, no adjustment."""
    return {
        "pessimistic": pessimistic,
        "realistic":   realistic,
        "optimistic":  optimistic,
        "narrow":      False,
        "width":       round(optimistic - pessimistic, 4),
        "method":      "manual",
    }
