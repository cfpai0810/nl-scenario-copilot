# =============================================================================
# review.py — Analyst sign-off for a scenario run
# =============================================================================
# Usage:
#   python review.py
#   python review.py "Chun-Feng Pai"
#
# A scenario result is only as good as its assumptions. This script surfaces
# the latest run, shows what was changed and what the model held constant,
# and requires the reviewer to confirm they have read those assumptions
# before recording the sign-off.
# =============================================================================

import sys
import json
from datetime import datetime, timezone

from config import AUDIT_LOG


def _load_last_record():
    if not AUDIT_LOG.exists():
        print("No audit log found at {}".format(AUDIT_LOG.name))
        return None, None
    lines = AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n")
    if not lines or not lines[-1].strip():
        print("Audit log is empty.")
        return None, None
    return json.loads(lines[-1]), lines


def _show_single(rec):
    print("  Type:     single what-if ({})".format(rec.get("analysis_type", "?")))
    print("  Asked:    \"{}\"".format(rec.get("raw_request", "")))
    print("  Verdict:  {}".format(rec.get("classification", "?")))
    for change in rec.get("changes_applied", []):
        print("    {} {} {}".format(
            change.get("target"), change.get("operation"), change.get("value")))
    rev  = rec.get("revenue_delta")
    ebit = rec.get("ebit_delta")
    if rev is not None:
        print("  Revenue:  {:+,.0f}".format(rev))
    if ebit is not None:
        print("  EBIT:     {:+,.0f}".format(ebit))


def _show_three_case(rec):
    print("  Type:     three-case analysis")
    print("  Drivers:  {}".format(", ".join(rec.get("drivers_flexed", []))))
    print("  Basis:    {}".format(rec.get("basis", "")))
    for line_item, cases in rec.get("cases", {}).items():
        print("    {}: pessimistic {}, realistic {}, optimistic {}".format(
            line_item, cases.get("pessimistic"), cases.get("realistic"),
            cases.get("optimistic")))
    b = rec.get("ebit_base")
    p = rec.get("ebit_pessimistic")
    o = rec.get("ebit_optimistic")
    if None not in (b, p, o):
        print("  EBIT:     base {:,.0f}, pessimistic {:,.0f}, optimistic {:,.0f}".format(b, p, o))


def _show_assumptions(rec):
    """The checkpoint that matters: what the model did NOT capture."""
    held = rec.get("held_constant") or []
    print("\n  ASSUMPTIONS HELD CONSTANT")
    if held:
        for note in held:
            print("    - {}".format(note))
    else:
        print("    - Drivers that were not selected do not move in this run.")
    if rec.get("analysis_type") == "three-case" or "drivers_flexed" in rec:
        print("    - The realistic case is the base plan, so it matches the base "
              "figures by design.")
    print("    - This model does not link marketing or customer changes to "
          "revenue, which is forecast on its own trend.")


def mark_reviewed(reviewer):
    rec, lines = _load_last_record()
    if rec is None:
        return

    if rec.get("human_reviewed"):
        print("Latest run ({}) was already signed off by {} at {}.".format(
            rec.get("run_id", "?"), rec.get("reviewed_by", "?"),
            rec.get("reviewed_at", "?")))
        return

    print("\nLATEST RUN")
    print("  Run id:   {}".format(rec.get("run_id", "?")))
    print("  Entity:   {}".format(rec.get("entity", "?")))
    if "drivers_flexed" in rec:
        _show_three_case(rec)
    else:
        _show_single(rec)

    for label, key in (("Report", "pdf_file"), ("Data", "csv_file")):
        path = rec.get(key)
        if path:
            print("  {}:   {}".format(label, path.split("\\")[-1].split("/")[-1]))

    _show_assumptions(rec)

    print("\nA scenario result is only valid within its assumptions.")
    answer = input("Have you read the assumptions above? [y/N]: ").strip().lower()
    if answer != "y":
        print("Sign-off cancelled. No changes made.")
        return

    rec["human_reviewed"]        = True
    rec["reviewed_by"]           = reviewer
    rec["reviewed_at"]           = datetime.now(timezone.utc).isoformat()
    rec["assumptions_confirmed"] = True
    lines[-1] = json.dumps(rec)
    AUDIT_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\nRun {} signed off by {}.".format(rec["run_id"], reviewer))


if __name__ == "__main__":
    reviewer = sys.argv[1] if len(sys.argv) > 1 else "Analyst"
    mark_reviewed(reviewer)
