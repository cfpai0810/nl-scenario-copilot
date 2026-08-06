# =============================================================================
# permonth_table.py — pure helpers for the per-month editable intent table
# =============================================================================
# Display ↔ machine value conversion, table rebuild, and change detection.
# Importable and testable without Streamlit.
# =============================================================================


def _to_display(op, machine):
    """Machine value -> friendly display string. '' for None (no change)."""
    if machine is None:
        return ""
    if op == "scale_driver":
        pct = (machine - 1.0) * 100.0
        return "{:+.0f}%".format(pct)
    if op == "set_driver":
        return "{:g}".format(machine)
    if op == "shift_driver":
        return "{:+g}".format(machine)
    return "{:g}".format(machine)


def _format_hint(op):
    if op == "scale_driver":
        return "Enter a percent like +5 or -20."
    if op == "set_driver":
        return "Enter a value like 0.08 or 30000."
    return "Enter a shift like +0.03 or +30000."


def _from_display(op, text):
    """Friendly display string -> machine value. Returns (value, error)."""
    t = (text or "").strip()
    if t == "":
        return None, None
    try:
        if op == "scale_driver":
            num = float(t.replace("%", "").replace("+", ""))
            return 1.0 + num / 100.0, None
        return float(t.replace("+", "")), None
    except ValueError:
        return None, "Could not read '{}'. {}".format(text, _format_hint(op))


def _column_caption(op):
    if op == "scale_driver":
        return ("Percent change: enter +5 for a 5 percent increase, "
                "-20 for a 20 percent cut.")
    if op == "set_driver":
        return "Set to: enter the value in the driver's own unit."
    if op == "shift_driver":
        return "Change by: enter the shift in the driver's own unit."
    return ""


def _vectored_change(normalised):
    """Return (index, change) of the single per-month change, or (None, None).
    Phase 1 allows at most one vectored driver (enforced by the validator)."""
    for i, c in enumerate(normalised):
        if c.get("value_by_period"):
            return i, c
    return None, None


def resolve_run_normalised(normalised, vec_idx, vec_op, edited, original_vbp,
                           forecast_periods):
    """Decide what to run from an edited per-month table.

    Returns (run_normalised, errors):
      - errors non-empty  -> the caller shows each error and does NOT run.
      - errors empty      -> run_normalised is the validated list to run.
    """
    from src.step4_validator import validate_change

    rebuilt, errors = _rebuild_normalised_from_table(
        normalised, vec_idx, vec_op, edited, original_vbp)
    if errors:
        return [], errors

    revalid = [validate_change(c, forecast_periods=forecast_periods)
               for c in rebuilt]
    bad = [r for r in revalid if r[0] != "OK"]
    if bad:
        return [], [reason for (_status, reason, _n) in bad]

    return [r[2] for r in revalid], []


def _rebuild_normalised_from_table(normalised, idx, op, edited, original_vbp):
    """Return (rebuilt_normalised, errors). For each month: if the display
    text is unchanged from the original, reuse the original machine value (no
    round-trip, no drift); if changed, parse it. Blank means no change that
    month. Leaves other changes untouched."""
    errors = []
    new_vbp = {}
    for _, row in edited.iterrows():
        pl = row["Month"]
        shown = str(row["Change"]) if row["Change"] is not None else ""
        original_machine = original_vbp.get(pl)
        original_shown = _to_display(op, original_machine)
        if shown.strip() == original_shown.strip():
            if original_machine is not None:
                new_vbp[pl] = original_machine
            continue
        machine, err = _from_display(op, shown)
        if err:
            errors.append(err)
            continue
        if machine is not None:
            new_vbp[pl] = machine
    if not errors and not new_vbp:
        errors.append(
            "No months have a change. Enter at least one, or rephrase.")
    rebuilt = [dict(c) for c in normalised]
    if not errors:
        rebuilt[idx] = dict(normalised[idx])
        rebuilt[idx]["value_by_period"] = new_vbp
        rebuilt[idx]["value"] = None
        rebuilt[idx]["periods"] = sorted(new_vbp.keys())
    return rebuilt, errors
