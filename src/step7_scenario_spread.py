# =============================================================================
# step7_scenario_spread.py — Three-case spread calculator
# =============================================================================
# Derives pessimistic / realistic / optimistic values for a driver, using
# either historical data (for derivable driver types) or a preset band.
# Direction-aware: "pessimistic" describes the OUTCOME for EBIT, not the
# arithmetic direction. For Revenue, pessimistic = lower growth. For a cost
# driver like COGS, pessimistic = higher cost.
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


def _assign(base_value, delta, direction):
    """Assign the three cases by OUTCOME, not by arithmetic sign. For a
    higher-is-better driver the optimistic case is above the base; for a cost
    driver it is below."""
    if direction == "higher_better":
        return (base_value - delta, base_value, base_value + delta)
    return (base_value + delta, base_value, base_value - delta)


def derive_spread(driver_type, base_value, history_rates, direction):
    """Data-derived spread: base plus and minus one historical standard
    deviation, labelled by outcome. Returns a dict, or None if the driver has
    no usable history. Flags a spread too narrow to be useful."""
    if driver_type not in DERIVABLE_TYPES or len(history_rates) < 2:
        return None
    sd = statistics.stdev(history_rates)
    pess, real, opt = _assign(base_value, sd, direction)
    return {
        "mode":        "data-derived",
        "std_dev":     sd,
        "pessimistic": pess,
        "realistic":   real,
        "optimistic":  opt,
        "narrow":      (2 * sd) < NARROW_THRESHOLD,
        "basis": ("one historical standard deviation ({:.2%}) either side of "
                  "the base plan, from the variation observed in the actuals"
                  ).format(sd),
    }


def preset_spread(base_value, band, direction):
    """Preset band: base plus and minus a fixed band, labelled by outcome."""
    pess, real, opt = _assign(base_value, band, direction)
    return {
        "mode":        "preset-band",
        "band":        band,
        "pessimistic": pess,
        "realistic":   real,
        "optimistic":  opt,
        "narrow":      False,
        "basis":       "a chosen band of {} either side of the base plan".format(band),
    }


def manual_spread(pessimistic, realistic, optimistic):
    """User enters all three case values directly, labelled as they wish."""
    return {
        "mode":        "manual",
        "pessimistic": pessimistic,
        "realistic":   realistic,
        "optimistic":  optimistic,
        "narrow":      False,
        "basis":       "values entered manually",
    }
