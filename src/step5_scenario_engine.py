# =============================================================================
# step5_scenario_engine.py — RUN: apply changes, rerun base and scenario
# =============================================================================
# Applies validated changes to DEEP COPIES of the base DataFrames, reruns the
# reused Project 2 engine for both base and scenario, and computes the full
# P&L delta. The base DataFrames are never mutated, so base and scenario are
# genuinely independent states.
#
#   apply_changes():     validated changes -> scenario model (copies)
#   run_forecast():      one model -> P&L (calls the reused engine)
#   compute_deltas():    base P&L + scenario P&L -> full-line delta list
#   classify_analysis(): one driver is a sensitivity; several a scenario
# =============================================================================

from config import EBIT_LABEL


def apply_changes(base, normalised_changes):
    """Return a scenario model with changes applied to DEEP COPIES of the
    three mutable DataFrames. The base is never touched. Also returns the
    list of assumptions held constant, the base context (what each changed
    driver was before), and two override dicts for the engine:
      period_overrides  — {(line_item, period): driver_value}  (Phase 1)
      value_overrides   — {(line_item, period): resolved_value} (Phase 2)"""
    drivers   = base["drivers_df"].copy(deep=True)
    headcount = base["headcount_df"].copy(deep=True)
    customer  = base["customer_df"].copy(deep=True)

    held_constant    = []
    base_context     = []
    period_overrides = {}
    value_overrides  = {}

    for c in normalised_changes:
        target, op, val = c["target"], c["operation"], c["value"]
        periods = c.get("periods", "all")
        dtype = c.get("driver_type")
        vbp = c.get("value_by_period")

        # ── Per-period Revenue override (Phase 2: value-level) ─────────
        if periods != "all" and dtype == "seasonal_yoy":
            from src.step2_forecast_engine import _forecast_seasonal_yoy
            base_growth = float(base["drivers_df"].loc[
                base["drivers_df"]["line_item"] == target,
                "driver_value"].iloc[0])
            base_rev, _ = _forecast_seasonal_yoy(
                base["actuals_df"], target, base_growth,
                base["forecast_periods"], base["seasonal_year"])
            if vbp:
                for p, pval in vbp.items():
                    value_overrides[(target, p)] = base_rev[p] * pval
            else:
                for p in periods:
                    value_overrides[(target, p)] = base_rev[p] * val
            ctx_val = next(iter(vbp.values())) if vbp else val
            base_context.append({
                "target": target, "driver_type": dtype,
                "base_value": base_growth, "operation": op,
                "new_value": ctx_val, "periods": periods})
            continue

        # ── Per-period override on an in-loop value driver (Phase 1) ───
        if periods != "all" and op in ("set_driver", "shift_driver", "scale_driver"):
            base_val = float(base["drivers_df"].loc[
                base["drivers_df"]["line_item"] == target, "driver_value"].iloc[0])
            if vbp:
                for p, pval in vbp.items():
                    if op == "set_driver":
                        period_overrides[(target, p)] = pval
                    elif op == "shift_driver":
                        period_overrides[(target, p)] = base_val + pval
                    else:
                        period_overrides[(target, p)] = base_val * pval
                ctx_val = next(iter(vbp.values())) if vbp else val
            else:
                if op == "set_driver":
                    resolved = val
                elif op == "shift_driver":
                    resolved = base_val + val
                else:
                    resolved = base_val * val
                for p in periods:
                    period_overrides[(target, p)] = resolved
                ctx_val = resolved
            base_context.append({
                "target": target, "driver_type": dtype,
                "base_value": base_val, "operation": op,
                "new_value": ctx_val, "periods": periods})
            continue

        # ── Whole-horizon changes (periods == "all") ────────────────────
        if op == "scale_driver":
            mask = drivers["line_item"] == target
            base_val = float(base["drivers_df"].loc[
                base["drivers_df"]["line_item"] == target, "driver_value"].iloc[0])
            base_context.append({"target": target, "driver_type": dtype,
                                 "base_value": base_val, "operation": op, "new_value": val})
            drivers.loc[mask, "driver_value"] *= val

        elif op == "set_driver":
            mask = drivers["line_item"] == target
            base_val = float(base["drivers_df"].loc[
                base["drivers_df"]["line_item"] == target, "driver_value"].iloc[0])
            base_context.append({"target": target, "driver_type": dtype,
                                 "base_value": base_val, "operation": op, "new_value": val})
            drivers.loc[mask, "driver_value"] = val

        elif op == "shift_driver":
            mask = drivers["line_item"] == target
            base_val = float(base["drivers_df"].loc[
                base["drivers_df"]["line_item"] == target, "driver_value"].iloc[0])
            base_context.append({"target": target, "driver_type": dtype,
                                 "base_value": base_val, "operation": op,
                                 "new_value": base_val + val})
            drivers.loc[mask, "driver_value"] += val

        elif op == "scale_schedule":
            if target == "Personnel Cost":
                headcount["new_hires"] = headcount["new_hires"] * val
            elif target == "Marketing Spend":
                customer["target_new_customers"] = customer["target_new_customers"] * val
            _marketing_held_constant(target, held_constant)

        elif op == "set_schedule":
            if target == "Personnel Cost":
                headcount["new_hires"] = val
            elif target == "Marketing Spend":
                customer["target_new_customers"] = val
            _marketing_held_constant(target, held_constant)

        elif op == "add_schedule":
            if target == "Personnel Cost":
                headcount["new_hires"] = headcount["new_hires"] + val
            elif target == "Marketing Spend":
                customer["target_new_customers"] = (
                    customer["target_new_customers"] + val)
            _marketing_held_constant(target, held_constant)

        elif op == "shift_schedule":
            n = int(val)
            if target == "Personnel Cost":
                headcount["new_hires"] = _shift_column(headcount["new_hires"], n)
            elif target == "Marketing Spend":
                customer["target_new_customers"] = _shift_column(
                    customer["target_new_customers"], n)
            _marketing_held_constant(target, held_constant)

    scenario = dict(base)
    scenario["drivers_df"]   = drivers
    scenario["headcount_df"] = headcount
    scenario["customer_df"]  = customer
    return scenario, held_constant, base_context, period_overrides, value_overrides


_MARKETING_HELD = (
    "Marketing and customer changes do not flow through to "
    "Revenue, which is modelled on its own seasonal trend, so "
    "the revenue line is unchanged in this scenario.")


def _marketing_held_constant(target, held_constant):
    if target == "Marketing Spend" and _MARKETING_HELD not in held_constant:
        held_constant.append(_MARKETING_HELD)


def _shift_column(series, n):
    """Shift a quantity column n periods later (n>0) or earlier (n<0),
    filling vacated periods with 0. A positive shift delays the values."""
    return series.shift(n).fillna(0.0)


def run_forecast(model, period_overrides=None, value_overrides=None):
    """Run the reused Project 2 engine on one model dict, return its P&L."""
    from src.step2_forecast_engine import calculate_forecast, build_pnl
    full_df, _driver_detail, _flags = calculate_forecast(
        model["actuals_df"], model["drivers_df"], model["operational_df"],
        model["headcount_df"], model["customer_df"],
        model["last_actual"], model["forecast_periods"], model["seasonal_year"],
        period_overrides=period_overrides,
        value_overrides=value_overrides,
    )
    return build_pnl(full_df, model["forecast_periods"])


def compute_deltas(base_pnl, scenario_pnl):
    """Walk every P&L line. Use the 'total' column (already the horizon sum).
    Returns an ordered list of {line, base, scenario, delta, pct}. The
    percentage is blank when the base is zero, since a percent of zero has
    no meaning."""
    out = []
    for line in base_pnl["line"]:
        b_row = base_pnl[base_pnl["line"] == line]
        s_row = scenario_pnl[scenario_pnl["line"] == line]
        b = float(b_row["total"].iloc[0])
        s = float(s_row["total"].iloc[0]) if not s_row.empty else b
        delta = s - b
        pct = (delta / b) if b != 0 else None
        out.append({"line": line, "base": b, "scenario": s,
                    "delta": delta, "pct": pct})
    return out


def headline_deltas(deltas):
    """Pull Revenue and EBIT from the full delta list, for the explanation."""
    by_line = {d["line"]: d for d in deltas}
    return {
        "Revenue": by_line.get("Revenue"),
        "EBIT":    by_line.get(EBIT_LABEL),
    }


def classify_analysis(normalised_changes):
    """One driver changed is a sensitivity; several is a scenario."""
    return "sensitivity" if len(normalised_changes) == 1 else "scenario"
