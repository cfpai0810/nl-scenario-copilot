# =============================================================================
# step4_validator.py — VALIDATE: the deterministic gate
# =============================================================================
# Every change from the parser is validated here against the model's real
# line items and sane bounds. This layer NEVER calls Claude. It is fully
# deterministic and fully testable: same change in, same verdict out.
#
#   validate_change():    one change -> (status, reason, normalised)
#   classify_request():   aggregate per-change results into one verdict
#   echo_back():          restate the parsed scenario in plain English
# =============================================================================

from config import (
    LINE_ITEMS, VALIDATION_RULES, SET_BOUNDS, SHIFT_BOUNDS,
    SET_SCHEDULE_BOUNDS, ADD_SCHEDULE_BOUNDS, SCENARIO_MAX_CHANGES,
)

_PCT_KIND = {
    "margin_pct":   "a margin (a percentage of revenue)",
    "seasonal_yoy": "an annual growth rate",
    "growth_pct":   "a monthly growth rate",
}

_DRIVER_NOUN = {
    "headcount_driven": ("new hires per month", "people"),
    "cac_driven":       ("target new customers per month", "customers"),
}

_GROWTH_BASIS = {
    "seasonal_yoy": "annual growth rate",
    "growth_pct":   "monthly growth rate",
}


def _driver_desc(target, dtype):
    """One-line plain-language description of what a driver is."""
    if dtype in _PCT_KIND:
        return "{} is modelled as {} in this plan".format(target, _PCT_KIND[dtype])
    if dtype == "fixed":
        return "{} is a fixed euro amount applied per month".format(target)
    noun, _ = _DRIVER_NOUN.get(dtype, ("a schedule quantity", "units"))
    return "{} is driven by {} in this plan".format(target, noun)


def _schedule_bounds_msg(target, dtype, op, val, lo, hi):
    """Helpful message when a schedule operation is out of bounds."""
    noun, unit = _DRIVER_NOUN.get(dtype, ("a schedule quantity", "units"))
    if op == "add_schedule":
        return (
            "{target} is driven by {noun}. Adding {val:g} {unit} per month "
            "is outside the allowed range ({lo:g} to {hi:g}). If you meant a "
            "euro amount, try phrasing it as a percentage change instead — "
            "for example, 'increase marketing spend by 20 percent'."
        ).format(target=target, noun=noun, val=val, unit=unit, lo=lo, hi=hi)
    if op == "set_schedule":
        return (
            "{target} is driven by {noun}. Setting it to {val:g} {unit} per "
            "month is outside the allowed range ({lo:g} to {hi:g}). If you "
            "meant a euro amount, try a percentage change instead."
        ).format(target=target, noun=noun, val=val, unit=unit, lo=lo, hi=hi)
    return (
        "{target} is driven by {noun}. A scale factor of {val:g} is outside "
        "the allowed range. Did you mean {fix:.2f}? (Enter the factor, not "
        "the percentage.)"
    ).format(target=target, noun=noun, val=val, fix=val / 100.0)


def _set_driver_bounds_msg(target, dtype, val, lo, hi):
    """Helpful message when set_driver is out of bounds."""
    if dtype in _PCT_KIND:
        return (
            "{target} is modelled as {kind}. Setting it to {val:g} is outside "
            "the allowed range ({lo:g} to {hi:g}). Remember that percentages "
            "are entered as fractions — for example, 8 percent is 0.08, not 8."
        ).format(target=target, kind=_PCT_KIND[dtype], val=val, lo=lo, hi=hi)
    return (
        "Setting {target} to {val:g} is outside the allowed range "
        "({lo:g} to {hi:g})."
    ).format(target=target, val=val, lo=lo, hi=hi)


def _shift_driver_bounds_msg(target, dtype, val, lo, hi):
    """Helpful message when shift_driver is out of bounds (small pct shifts
    that exceed the narrow range but are below the Change 1 threshold)."""
    if dtype in _PCT_KIND:
        return (
            "{target} is modelled as {kind}. A shift of {val:g} ({pp:.1f} "
            "percentage points) is outside the allowed range ({lo:g} to "
            "{hi:g}). Try a smaller adjustment."
        ).format(target=target, kind=_PCT_KIND[dtype], val=val,
                 pp=abs(val) * 100, lo=lo, hi=hi)
    return (
        "Shifting {target} by {val:g} is outside the allowed range "
        "({lo:g} to {hi:g})."
    ).format(target=target, val=val, lo=lo, hi=hi)


def validate_change(change, forecast_periods=None):
    """Validate one change. Returns (status, reason, normalised).
    status is OK, NEEDS_CLARIFICATION, or ILLEGAL.
    If forecast_periods is provided, per-month periods are checked
    against the horizon."""
    target  = change.get("target")
    op      = change.get("operation")
    val     = change.get("value")
    periods = change.get("periods", "all")
    vbp     = change.get("value_by_period")

    if vbp is not None:
        if not isinstance(vbp, dict) or len(vbp) == 0:
            return ("NEEDS_CLARIFICATION",
                    "no forecast month matched the request — please name "
                    "specific months from the forecast horizon", None)
        periods_list = sorted(vbp.keys())
        for p, v in vbp.items():
            result = validate_change(
                {"target": target, "operation": op, "value": v,
                 "periods": [p]},
                forecast_periods=forecast_periods)
            if result[0] != "OK":
                return result
        normalised = {"target": target, "operation": op, "value": None,
                      "value_by_period": dict(vbp),
                      "driver_type": LINE_ITEMS[target],
                      "periods": periods_list}
        return ("OK", "valid", normalised)

    if val is None and vbp is None:
        return ("NEEDS_CLARIFICATION",
                "the request for {} could not be read as a clear numeric "
                "change. Try rephrasing — for example, 'increase {} by "
                "5 percent in July'.".format(target or "this line item",
                                             target or "it"),
                None)

    if target not in LINE_ITEMS:
        return ("ILLEGAL", "unknown line item '{}'".format(target), None)
    dtype = LINE_ITEMS[target]

    if op not in VALIDATION_RULES:
        return ("ILLEGAL", "unknown operation '{}'".format(op), None)
    rule = VALIDATION_RULES[op]

    if dtype not in rule["legal"]:
        return ("ILLEGAL",
                "operation '{}' is not valid for {} (driver {})".format(op, target, dtype),
                None)

    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return ("ILLEGAL", "value for {} is not a number: {!r}".format(target, val), None)

    if rule["value_type"] == "int" and float(val) != int(val):
        return ("ILLEGAL",
                "{} shift must be whole months, got {}".format(target, val), None)

    # ── Euros on a percentage driver (helpful clarification) ─────────────────
    if op == "shift_driver" and dtype in _PCT_KIND and abs(val) >= 1.0:
        return ("NEEDS_CLARIFICATION",
                "{} is modelled as {} in this plan, not a euro amount, so it "
                "cannot be changed by a fixed figure like {:g}. You can change "
                "it in percentage terms instead — for example, shift it by a "
                "few percentage points, or set it to a specific "
                "percentage.".format(target, _PCT_KIND[dtype], val),
                None)

    # ── Euro shift on a per-period driver (ask the time basis) ───────────────
    if op == "shift_driver" and dtype == "fixed" and abs(val) >= 1000:
        return ("NEEDS_CLARIFICATION",
                "Adding {:g} to {} applies per month in this plan, so over a "
                "year it is about {:g}. If you meant {:g} for the whole year, "
                "that is about {:,.0f} per month. Please restate it as a "
                "per-month amount, or confirm you mean {:g} every "
                "month.".format(val, target, val * 12, val, val / 12, val),
                None)

    # ── Schedule operations with euro-sized values ───────────────────────────
    if op in ("add_schedule", "set_schedule") and dtype in _DRIVER_NOUN:
        _, unit = _DRIVER_NOUN[dtype]
        if op == "add_schedule":
            lo, hi = ADD_SCHEDULE_BOUNDS[dtype]
        else:
            lo, hi = SET_SCHEDULE_BOUNDS[dtype]
        if not (lo <= val <= hi):
            return ("NEEDS_CLARIFICATION",
                    _schedule_bounds_msg(target, dtype, op, val, lo, hi),
                    None)

    if op == "set_driver":
        lo, hi = SET_BOUNDS[dtype]
    elif op == "shift_driver":
        lo, hi = SHIFT_BOUNDS[dtype]
    elif op == "set_schedule":
        lo, hi = SET_SCHEDULE_BOUNDS[dtype]
    elif op == "add_schedule":
        lo, hi = ADD_SCHEDULE_BOUNDS[dtype]
    else:
        lo, hi = rule["bounds"]

    if not (lo <= val <= hi):
        if op in ("scale_driver", "scale_schedule") and abs(val) > hi:
            return ("NEEDS_CLARIFICATION",
                    _schedule_bounds_msg(target, dtype, op, val, lo, hi)
                    if op == "scale_schedule"
                    else "{} {} value {} looks out of range, possible unit "
                         "error (did you mean {}?)".format(
                             target, op, val, val / 100.0),
                    None)
        if op == "set_driver":
            return ("NEEDS_CLARIFICATION",
                    _set_driver_bounds_msg(target, dtype, val, lo, hi), None)
        if op == "shift_driver":
            return ("NEEDS_CLARIFICATION",
                    _shift_driver_bounds_msg(target, dtype, val, lo, hi), None)
        return ("ILLEGAL",
                "{} {} value {} is out of bounds ({}, {})".format(
                    target, op, val, lo, hi), None)

    if op == "shift_schedule" and int(val) == 0:
        return ("NEEDS_CLARIFICATION", "{} shift of 0 does nothing".format(target), None)

    if periods != "all":
        # Phase 2: Revenue per-month supports scale only. set/shift are
        # absolute euro figures whose bounds differ from the growth-fraction
        # bounds — deferred until per-month absolute-euro bounds are added.
        if dtype == "seasonal_yoy" and op != "scale_driver":
            return ("NEEDS_CLARIFICATION",
                    "Per-month changes to Revenue are supported as percentage "
                    "changes (e.g. 'increase Revenue by 5 percent in July'). "
                    "Absolute set or shift on individual months is not "
                    "available yet.",
                    None)
        # Schedule per-month deferred: requires row-level DataFrame edits,
        # a different mechanism from the period_overrides dict used for
        # value drivers. The DataFrames have period columns so it is
        # mechanically possible — not built yet.
        if dtype in ("headcount_driven", "cac_driven"):
            return ("NEEDS_CLARIFICATION",
                    "Per-month changes to schedule drivers are not supported "
                    "yet. Use set_schedule, scale_schedule, or add_schedule to "
                    "change the whole horizon.",
                    None)
        if not isinstance(periods, list) or len(periods) == 0:
            return ("ILLEGAL",
                    "periods must be 'all' or a non-empty list of YYYY-MM "
                    "strings", None)
        import re
        _PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")
        for p in periods:
            if not isinstance(p, str) or not _PERIOD_RE.match(p):
                return ("ILLEGAL",
                        "invalid period '{}' — expected YYYY-MM format".format(p),
                        None)
        if forecast_periods is not None:
            for p in periods:
                if p not in forecast_periods:
                    return ("NEEDS_CLARIFICATION",
                            "period '{}' is outside the forecast horizon "
                            "({} to {})".format(
                                p, forecast_periods[0], forecast_periods[-1]),
                            None)

    normalised = {"target": target, "operation": op, "value": float(val),
                  "driver_type": dtype, "periods": periods}
    return ("OK", "valid", normalised)


def classify_request(results):
    """Aggregate per-change results into one classification.
    An illegal change dominates; then clarification; then multi-vector;
    then too-many; else clear."""
    statuses = [r[0] for r in results]
    if "ILLEGAL" in statuses:
        return "IMPOSSIBLE"
    if "NEEDS_CLARIFICATION" in statuses:
        return "AMBIGUOUS"
    vectored = {r[2]["target"] for r in results
                if r[0] == "OK" and r[2] and r[2].get("periods") != "all"}
    if len(vectored) > 1:
        return "AMBIGUOUS"
    if len(results) > SCENARIO_MAX_CHANGES:
        return "TOO_MANY"
    return "CLEAR"


def _echo_one(t, op, v, driver_type):
    """Format one operation+value pair for echo_back."""
    if op in ("scale_driver", "scale_schedule"):
        pct = (1 - v) * 100 if v < 1 else (v - 1) * 100
        direction = "reduce" if v < 1 else "increase"
        return "{} {} by {:.0f} percent".format(direction, t, abs(pct))
    if op == "set_driver":
        basis = _GROWTH_BASIS.get(driver_type)
        if basis:
            return "set the {} {} to {:.2f}".format(t, basis, v)
        return "set {} to {:.2f}".format(t, v)
    if op == "shift_driver":
        if driver_type == "fixed":
            if v >= 0:
                return "add EUR {:,.0f} to {}".format(v, t)
            return "subtract EUR {:,.0f} from {}".format(abs(v), t)
        direction = "increase" if v > 0 else "decrease"
        basis = _GROWTH_BASIS.get(driver_type)
        if basis:
            return "{} the {} {} by {:.1f} percentage points".format(
                direction, t, basis, abs(v) * 100)
        return "{} {} by {:.1f} percentage points".format(
            direction, t, abs(v) * 100)
    if op == "set_schedule":
        return "set {} to {:.0f} per period".format(t, v)
    if op == "add_schedule":
        direction = "add" if v > 0 else "remove"
        return "{} {:.0f} per period for {}".format(direction, abs(v), t)
    if op == "shift_schedule":
        when = "later" if v > 0 else "earlier"
        return "shift {} by {:.0f} month(s) {}".format(t, abs(v), when)
    return ""


def echo_back(normalised_changes):
    """Restate the parsed scenario in plain English so a misparse is visible."""
    parts = []
    for c in normalised_changes:
        t, op = c["target"], c["operation"]
        vbp = c.get("value_by_period")
        periods = c.get("periods", "all")

        if vbp:
            unique_vals = set(vbp.values())
            if len(unique_vals) == 1:
                v = next(iter(unique_vals))
                part = _echo_one(t, op, v, c.get("driver_type"))
                part += " for {}".format(", ".join(sorted(vbp.keys())))
            else:
                sub = []
                for p in sorted(vbp.keys()):
                    sub.append(
                        _echo_one(t, op, vbp[p], c.get("driver_type"))
                        + " in " + p)
                part = "; ".join(sub)
        else:
            v = c["value"]
            part = _echo_one(t, op, v, c.get("driver_type"))
            if periods != "all":
                part += " for {}".format(", ".join(periods))

        parts.append(part)
    return "You want to " + "; and ".join(parts) + "."


def driver_guide():
    """Return a list of (line_item, description) tuples for the reference
    section on the web page."""
    guide = []
    for name, dtype in LINE_ITEMS.items():
        guide.append((name, _driver_desc(name, dtype)))
    return guide
