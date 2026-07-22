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
    LINE_ITEMS, VALIDATION_RULES, SET_BOUNDS, SCENARIO_MAX_CHANGES,
)


def validate_change(change):
    """Validate one change. Returns (status, reason, normalised).
    status is OK, NEEDS_CLARIFICATION, or ILLEGAL."""
    target  = change.get("target")
    op      = change.get("operation")
    val     = change.get("value")
    periods = change.get("periods", "all")

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

    if op == "set_driver":
        lo, hi = SET_BOUNDS[dtype]
    else:
        lo, hi = rule["bounds"]

    if not (lo <= val <= hi):
        if op in ("scale_driver", "scale_schedule") and abs(val) > hi:
            return ("NEEDS_CLARIFICATION",
                    "{} {} value {} looks out of range, possible unit error "
                    "(did you mean {}?)".format(target, op, val, val / 100.0),
                    None)
        return ("ILLEGAL",
                "{} {} value {} is out of bounds ({}, {})".format(target, op, val, lo, hi),
                None)

    if op == "shift_schedule" and int(val) == 0:
        return ("NEEDS_CLARIFICATION", "{} shift of 0 does nothing".format(target), None)

    if periods != "all":
        return ("NEEDS_CLARIFICATION",
                "per-period changes are not supported yet; this run applies to "
                "the whole horizon", None)

    normalised = {"target": target, "operation": op, "value": float(val),
                  "driver_type": dtype, "periods": "all"}
    return ("OK", "valid", normalised)


def classify_request(results):
    """Aggregate per-change results into one classification.
    An illegal change dominates; then clarification; then too-many; else clear."""
    statuses = [r[0] for r in results]
    if "ILLEGAL" in statuses:
        return "IMPOSSIBLE"
    if "NEEDS_CLARIFICATION" in statuses:
        return "AMBIGUOUS"
    if len(results) > SCENARIO_MAX_CHANGES:
        return "TOO_MANY"
    return "CLEAR"


def echo_back(normalised_changes):
    """Restate the parsed scenario in plain English so a misparse is visible."""
    parts = []
    for c in normalised_changes:
        t, op, v = c["target"], c["operation"], c["value"]
        if op in ("scale_driver", "scale_schedule"):
            pct = (1 - v) * 100 if v < 1 else (v - 1) * 100
            direction = "reduce" if v < 1 else "increase"
            parts.append("{} {} by {:.0f} percent".format(direction, t, abs(pct)))
        elif op == "set_driver":
            parts.append("set {} to {:.2f}".format(t, v))
        elif op == "shift_schedule":
            when = "later" if v > 0 else "earlier"
            parts.append("shift {} by {:.0f} month(s) {}".format(t, abs(v), when))
    return "You want to " + "; and ".join(parts) + "."
