# =============================================================================
# step2_forecast_engine.py — Layer 3: Forecast Calculation Engine
# =============================================================================
# Responsibilities:
#   - derive_seasonal_indices(): derive monthly seasonality from a full year
#   - calculate_forecast():      apply all six driver types period by period
#   - build_pnl():               roll the forecast up into a simplified P&L
#
# Six driver types:
#   seasonal_yoy      Revenue: annual YoY growth spread by derived seasonality
#   margin_pct        COGS: percentage of same-period revenue
#   headcount_driven  Personnel: (existing + hires - attrition) x loaded cost
#   cac_driven        Marketing: (new customers x CAC) + fixed campaign
#   growth_pct        R&D: month-on-month compounding growth
#   fixed             IT: constant value
#
# Core rule: Python calculates every number. Nothing is estimated by the
# language model. Revenue is calculated first each period because COGS
# (margin_pct) depends on the same-period revenue figure.
# =============================================================================

import pandas as pd

from config import (
    FORECAST_HORIZON,
    MAX_COGS_MARGIN,
    MAX_REVENUE,
    REVENUE_ITEMS,
    COGS_ITEMS,
    OPEX_ITEMS,
)


def derive_seasonal_indices(actuals_df, line_item, calendar_year):
    """
    Derive monthly seasonal indices from a full calendar year of actuals.

    A seasonal index expresses each month relative to the average month:
        index[month] = month_value / average_monthly_value
    An index of 1.20 means that month runs 20% above the average month;
    0.90 means 10% below. The twelve indices always sum to 12.0.

    Finance context: This is how FP&A teams capture seasonality. Rather
    than assuming flat growth, the forecast spreads an annual target across
    months using the shape the business actually exhibited last year.

    Args:
        actuals_df:    DataFrame of financial actuals
        line_item:     the line to derive indices for (e.g. 'Revenue')
        calendar_year: the year to use (must have all 12 months)

    Returns:
        dict mapping month string ('01'..'12') to seasonal index (float)
    """
    year_data = actuals_df[
        (actuals_df["line_item"] == line_item) &
        (actuals_df["period"].str.startswith(str(calendar_year)))
    ].copy()

    if len(year_data) != 12:
        raise ValueError(
            "Cannot derive seasonality for {}: need 12 months of {} actuals, "
            "found {}.".format(line_item, calendar_year, len(year_data))
        )

    avg = year_data["actual"].mean()
    if avg == 0:
        raise ValueError(
            "Cannot derive seasonality for {}: average is zero.".format(line_item)
        )

    indices = {}
    for _, row in year_data.iterrows():
        month = row["period"].split("-")[1]
        indices[month] = row["actual"] / avg

    return indices


def _forecast_seasonal_yoy(actuals_df, line_item, yoy_growth,
                           forecast_periods, seasonal_year):
    """
    Forecast a line item using annual YoY growth spread by seasonality.

    Method:
        trailing_12m   = sum of the most recent 12 months of actuals
        annual_target  = trailing_12m x (1 + yoy_growth)
        monthly_base   = annual_target / 12
        month_forecast = monthly_base x seasonal_index[month]

    Returns:
        (forecast dict {period: value}, detail dict for narration)
    """
    indices = derive_seasonal_indices(actuals_df, line_item, seasonal_year)

    all_periods  = sorted(actuals_df[actuals_df["line_item"] == line_item]["period"].unique())
    trailing     = all_periods[-12:]
    trailing_sum = actuals_df[
        (actuals_df["line_item"] == line_item) &
        (actuals_df["period"].isin(trailing))
    ]["actual"].sum()

    annual_target = trailing_sum * (1 + yoy_growth)
    monthly_base  = annual_target / 12

    forecast = {}
    for period in forecast_periods:
        month = period.split("-")[1]
        forecast[period] = monthly_base * indices[month]

    detail = {
        "trailing_12m":  trailing_sum,
        "yoy_growth":    yoy_growth,
        "annual_target": annual_target,
        "monthly_base":  monthly_base,
        "indices":       indices,
    }
    return forecast, detail


def _forecast_headcount_driven(operational_df, headcount_df,
                               forecast_periods, last_actual):
    """
    Forecast Personnel Cost from the headcount plan.

    Method, per forecast month:
        attrition   = round(start_headcount x attrition_rate)
        end_headcount = start_headcount + new_hires - attrition
        avg_headcount = (start + end) / 2
        cost        = avg_headcount x (cost_per_head_annual / 12)

    The starting headcount is read directly from operational actuals for
    the last actual period — it is a tracked fact, not a derived figure.

    Returns:
        (forecast dict {period: value}, detail dict for narration)
    """
    start_hc = int(operational_df[
        (operational_df["period"] == last_actual) &
        (operational_df["metric"] == "headcount")
    ]["value"].iloc[0])

    forecast = {}
    detail   = {"start_headcount": start_hc, "schedule": {}}
    current_hc = start_hc

    for period in forecast_periods:
        sched = headcount_df[headcount_df["period"] == period]
        if len(sched) == 0:
            raise ValueError(
                "No headcount schedule row for forecast period {}".format(period)
            )
        sched = sched.iloc[0]

        hires       = int(sched["new_hires"])
        attr_rate   = sched["attrition_rate"]
        cph_monthly = sched["cost_per_head_annual"] / 12

        attrition = round(current_hc * attr_rate)
        end_hc    = current_hc + hires - attrition
        avg_hc    = (current_hc + end_hc) / 2

        forecast[period] = avg_hc * cph_monthly
        detail["schedule"][period] = {
            "start":     current_hc,
            "hires":     hires,
            "attrition": attrition,
            "end":       end_hc,
            "avg":       avg_hc,
            "cph_month": cph_monthly,
        }
        current_hc = end_hc

    return forecast, detail


def _forecast_cac_driven(customer_df, forecast_periods):
    """
    Forecast Marketing Spend from the customer acquisition plan.

    Method, per forecast month:
        variable = target_new_customers x cac
        spend    = variable + fixed_campaign

    Returns:
        (forecast dict {period: value}, detail dict for narration)
    """
    forecast = {}
    detail   = {}

    for period in forecast_periods:
        row = customer_df[customer_df["period"] == period]
        if len(row) == 0:
            raise ValueError(
                "No customer target row for forecast period {}".format(period)
            )
        row = row.iloc[0]

        new_cust = int(row["target_new_customers"])
        cac      = row["cac"]
        fixed    = row["fixed_campaign"]
        variable = new_cust * cac

        forecast[period]   = variable + fixed
        detail[period] = {
            "new_customers": new_cust,
            "cac":           cac,
            "variable":      variable,
            "fixed":         fixed,
        }

    return forecast, detail


def calculate_forecast(actuals_df, drivers_df, operational_df,
                       headcount_df, customer_df,
                       last_actual, forecast_periods, seasonal_year):
    """
    Calculate the full forecast by applying each line item's driver.

    Each line item uses its own driver type. Revenue is calculated first
    because COGS (margin_pct) depends on the same-period revenue value.

    Args:
        actuals_df:       financial actuals
        drivers_df:       driver configuration (one row per line item)
        operational_df:   operational actuals (headcount, new_customers)
        headcount_df:     forward hiring plan
        customer_df:      forward acquisition targets
        last_actual:      last locked period string
        forecast_periods: list of forecast period strings
        seasonal_year:    the calendar year to derive seasonality from

    Returns:
        full_df:      DataFrame (period, line_item, value, type)
        driver_detail: dict of per-driver narration detail
        flags:        list of validation flag strings
    """
    driver_lookup = {}
    for _, row in drivers_df.iterrows():
        driver_lookup[row["line_item"]] = {
            "driver_type":  row["driver_type"],
            "driver_value": row["driver_value"],
        }

    # Revenue first — COGS depends on it
    line_items = actuals_df["line_item"].unique().tolist()
    if "Revenue" in line_items:
        line_items = ["Revenue"] + [li for li in line_items if li != "Revenue"]

    flags         = []
    driver_detail = {}

    # Value lookup seeded with all actuals
    value_lookup = {}
    for _, row in actuals_df.iterrows():
        value_lookup[(row["period"], row["line_item"])] = row["actual"]

    # ── Pre-calculate the driver-based line items that do not depend on others ─
    # Revenue (seasonal_yoy)
    if "Revenue" in driver_lookup and driver_lookup["Revenue"]["driver_type"] == "seasonal_yoy":
        rev_fcst, rev_detail = _forecast_seasonal_yoy(
            actuals_df, "Revenue",
            driver_lookup["Revenue"]["driver_value"],
            forecast_periods, seasonal_year
        )
        driver_detail["Revenue"] = rev_detail
        for period, val in rev_fcst.items():
            value_lookup[(period, "Revenue")] = val

    # Personnel (headcount_driven)
    for li, cfg in driver_lookup.items():
        if cfg["driver_type"] == "headcount_driven":
            pers_fcst, pers_detail = _forecast_headcount_driven(
                operational_df, headcount_df, forecast_periods, last_actual
            )
            driver_detail[li] = pers_detail
            for period, val in pers_fcst.items():
                value_lookup[(period, li)] = val

    # Marketing (cac_driven)
    for li, cfg in driver_lookup.items():
        if cfg["driver_type"] == "cac_driven":
            mkt_fcst, mkt_detail = _forecast_cac_driven(customer_df, forecast_periods)
            driver_detail[li] = mkt_detail
            for period, val in mkt_fcst.items():
                value_lookup[(period, li)] = val

    # ── Period-by-period for the remaining driver types ───────────────────────
    forecast_rows = []
    for period in forecast_periods:
        idx = forecast_periods.index(period)
        prior_period = last_actual if idx == 0 else forecast_periods[idx - 1]
        revenue_this_period = value_lookup.get((period, "Revenue"))

        for line_item in line_items:
            if line_item not in driver_lookup:
                flags.append("MISSING_DRIVER: {}".format(line_item))
                continue

            dtype  = driver_lookup[line_item]["driver_type"]
            dvalue = driver_lookup[line_item]["driver_value"]

            # These were pre-calculated above — just read the stored value
            if dtype in ("seasonal_yoy", "headcount_driven", "cac_driven"):
                forecast_value = value_lookup.get((period, line_item))
                if forecast_value is None:
                    flags.append(
                        "CALCULATION_FAILED: {} in {}".format(line_item, period)
                    )
                    continue
            else:
                prior_value = value_lookup.get((prior_period, line_item))

                if dtype == "margin_pct":
                    if revenue_this_period is None:
                        flags.append("CALCULATION_ORDER: {} in {}".format(line_item, period))
                        continue
                    forecast_value = revenue_this_period * dvalue

                elif dtype == "growth_pct":
                    if prior_value is None:
                        flags.append("MISSING_PRIOR: {} in {}".format(line_item, prior_period))
                        continue
                    forecast_value = prior_value * (1 + dvalue)

                elif dtype == "fixed_growth":
                    if prior_value is None:
                        flags.append("MISSING_PRIOR: {} in {}".format(line_item, prior_period))
                        continue
                    forecast_value = prior_value * (1 + dvalue)

                elif dtype == "fixed":
                    forecast_value = dvalue

                else:
                    flags.append("UNKNOWN_DRIVER_TYPE: {} for {}".format(dtype, line_item))
                    continue

                value_lookup[(period, line_item)] = forecast_value

            # ── Sanity checks ──────────────────────────────────────────────
            if forecast_value < 0:
                flags.append(
                    "NEGATIVE_VALUE: {} in {} = {:,.0f}".format(
                        line_item, period, forecast_value)
                )
            if (line_item in COGS_ITEMS and revenue_this_period
                    and (forecast_value / revenue_this_period) > MAX_COGS_MARGIN):
                flags.append(
                    "MARGIN_EXCEEDS_100PCT: {} {:.1%} of Revenue in {}".format(
                        line_item, forecast_value / revenue_this_period, period)
                )
            if line_item in REVENUE_ITEMS and forecast_value > MAX_REVENUE:
                flags.append(
                    "UNUSUALLY_HIGH_REVENUE: {} in {}".format(period, line_item)
                )

            forecast_rows.append({
                "period":    period,
                "line_item": line_item,
                "value":     round(forecast_value, 2),
                "type":      "forecast",
            })

    # ── Assemble the full DataFrame ───────────────────────────────────────────
    actual_rows = []
    for _, row in actuals_df.iterrows():
        actual_rows.append({
            "period":    row["period"],
            "line_item": row["line_item"],
            "value":     row["actual"],
            "type":      "actual",
        })

    full_df = pd.DataFrame(actual_rows + forecast_rows)
    full_df = full_df.sort_values(["period", "line_item"]).reset_index(drop=True)

    print("\n[OK] Forecast calculated")
    print("     Actual rows:   {}".format(len(actual_rows)))
    print("     Forecast rows: {}".format(len(forecast_rows)))
    print("     Flags raised:  {}".format(len(flags)))
    for flag in flags:
        print("     --> {}".format(flag))

    return full_df, driver_detail, flags


def build_pnl(full_df, forecast_periods):
    """
    Roll the forecast up into a simplified P&L per forecast period.

    Structure:
        Revenue
        less COGS
        = Gross Profit
        less Operating Expenses (Personnel + Marketing + IT + R&D)
        = Operating Profit (EBIT)

    Args:
        full_df:          DataFrame with actual + forecast rows
        forecast_periods: list of forecast period strings

    Returns:
        pnl_df: DataFrame with one row per P&L line and one column per
                forecast period, plus a total column. Rows: Revenue, COGS,
                Gross Profit, each OpEx line, Total OpEx, EBIT.
    """
    fc = full_df[full_df["type"] == "forecast"]

    def value_for(line_item, period):
        match = fc[(fc["line_item"] == line_item) & (fc["period"] == period)]
        return float(match["value"].iloc[0]) if len(match) else 0.0

    def sum_items(items, period):
        return sum(value_for(li, period) for li in items)

    pnl_rows = []

    # Revenue
    rev = {p: sum_items(REVENUE_ITEMS, p) for p in forecast_periods}
    pnl_rows.append(("Revenue", rev))

    # COGS
    cogs = {p: sum_items(COGS_ITEMS, p) for p in forecast_periods}
    pnl_rows.append(("COGS", cogs))

    # Gross profit
    gross = {p: rev[p] - cogs[p] for p in forecast_periods}
    pnl_rows.append(("Gross Profit", gross))

    # Operating expense lines
    for li in OPEX_ITEMS:
        line_vals = {p: value_for(li, p) for p in forecast_periods}
        pnl_rows.append((li, line_vals))

    # Total OpEx
    opex = {p: sum_items(OPEX_ITEMS, p) for p in forecast_periods}
    pnl_rows.append(("Total OpEx", opex))

    # EBIT
    ebit = {p: gross[p] - opex[p] for p in forecast_periods}
    pnl_rows.append(("Operating Profit (EBIT)", ebit))

    # Build DataFrame
    records = []
    for label, vals in pnl_rows:
        record = {"line": label}
        for p in forecast_periods:
            record[p] = round(vals[p], 2)
        record["total"] = round(sum(vals.values()), 2)
        records.append(record)

    pnl_df = pd.DataFrame(records)

    # Print the P&L to console
    total_rev  = rev_total = sum(rev.values())
    total_ebit = sum(ebit.values())
    gp_margin  = (sum(gross.values()) / total_rev) if total_rev else 0
    ebit_margin= (total_ebit / total_rev) if total_rev else 0

    print("\n[OK] Simplified P&L built")
    print("     H2 Revenue:      EUR {:>14,.0f}".format(total_rev))
    print("     H2 Gross Profit: EUR {:>14,.0f}  ({:.1%})".format(
        sum(gross.values()), gp_margin))
    print("     H2 EBIT:         EUR {:>14,.0f}  ({:.1%})".format(
        total_ebit, ebit_margin))

    return pnl_df
