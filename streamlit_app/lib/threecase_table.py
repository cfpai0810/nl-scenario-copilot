# =============================================================================
# threecase_table.py — pure helpers for the three-case editable spread table
# =============================================================================
# Display ↔ machine value conversion for the three-case table. Importable and
# testable without Streamlit.
# =============================================================================

from config import LINE_ITEMS, SCHEDULE_DRIVER_TYPES
from src.step6_explainer import format_case_value


def parse_case_value(line_item, text):
    """Parse a display string back to a driver-unit float.
    Returns (value, error). Inverse of format_case_value."""
    t = (text or "").strip()
    if t == "":
        return None, "Cell is blank."
    dtype = LINE_ITEMS[line_item]
    try:
        if dtype in SCHEDULE_DRIVER_TYPES:
            return float(t.replace("x", "")), None
        if dtype == "fixed":
            return float(t.replace("EUR", "").replace(",", "").strip()), None
        return float(t.replace("%", "").strip()) / 100.0, None
    except ValueError:
        return None, "Could not read '{}' for {}.".format(text, line_item)


def rebuild_cases_from_table(edited_df, original_spreads):
    """Rebuild driver case values from an edited display table.

    edited_df has columns: Driver, Pessimistic, Realistic, Optimistic.
    original_spreads is {line_item: spread_dict}.

    Returns (cases_by_driver, errors):
      cases_by_driver = {line_item: {pessimistic: v, realistic: v, optimistic: v}}
      errors = list of messages (non-empty → block the run).

    NO-DRIFT RULE: if the display string matches the original
    format_case_value, reuse the exact original value (no round-trip).
    """
    errors = []
    cases_by_driver = {}
    case_keys = [("Pessimistic", "pessimistic"),
                 ("Realistic", "realistic"),
                 ("Optimistic", "optimistic")]

    for _, row in edited_df.iterrows():
        li = row["Driver"]
        sp = original_spreads.get(li)
        if sp is None:
            errors.append("Unknown driver: {}".format(li))
            continue
        vals = {}
        for col, key in case_keys:
            edited_text = str(row[col]).strip() if row[col] is not None else ""
            original_val = sp[key]
            original_text = format_case_value(li, original_val)
            if edited_text == original_text:
                vals[key] = original_val
            else:
                v, err = parse_case_value(li, edited_text)
                if err:
                    errors.append(err)
                else:
                    vals[key] = v
        if len(vals) == 3:
            cases_by_driver[li] = vals

    if not errors:
        for li, vals in cases_by_driver.items():
            distinct = {vals["pessimistic"], vals["realistic"], vals["optimistic"]}
            if len(distinct) < 3:
                errors.append(
                    "The three cases for {} must be different values.".format(li))

    return cases_by_driver, errors


def transpose_to_cases(cases_by_driver):
    """Transpose {line_item: {pessimistic: v, ...}} to
    {case_lower: {line_item: v, ...}} for run_three_case."""
    cases = {}
    for case in ("pessimistic", "realistic", "optimistic"):
        cases[case] = {li: vals[case] for li, vals in cases_by_driver.items()}
    return cases
