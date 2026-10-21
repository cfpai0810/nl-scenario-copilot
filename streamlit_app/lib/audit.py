# =============================================================================
# lib/audit.py - per-session audit trail (foundation component)
# =============================================================================
# On the web the audit log is PER SESSION and resets - no server-side disk
# writes, no shared JSONL file that two users could corrupt (the CLI's
# _update_audit_field rewrites a shared file, which is unsafe for multi-user).
# This keeps the demo GDPR-clean and honest: it is labelled "this session's
# audit". The PERSISTENT audit log remains a feature of the downloaded
# local version.
#
# A web record is smaller than the CLI's (write_audit): the request, the
# interpretation, a status, the deltas, what was held constant and the
# tokens. It has no input fingerprint, no model id and no parsed values. A
# finished run is a DRAFT until a named reviewer signs it off in the page's
# Sign-off section (the rule is src/signoff.py); its row then becomes FINAL
# and carries the sign-off: who, when, and the warnings shown at sign-off.
# The CLI's own sign-off is review.py.
# =============================================================================

import streamlit as st
from datetime import datetime, timezone

# The status of a finished web run: DRAFT, then FINAL once a named reviewer
# signs it off (both from the shared rule). A request that did not run is
# "refused" or "caution". The web never records "approved" or "locked": those
# belong to the production lifecycle (approve, lock, re-issue), which this
# demo does not run.
from src.signoff import DRAFT, FINAL, SignOff


def _ensure_store():
    if "audit_runs" not in st.session_state:
        st.session_state["audit_runs"] = []


def record_run(raw_request, interpretation, status,
               revenue_delta=None, ebit_delta=None, held_constant=None,
               analysis_type=None, extra=None,
               tokens_in=None, tokens_out=None, cost_estimate=None):
    """Append one run to this session's audit trail. status is DRAFT for a
    finished run, or refused or caution for a request that did not run."""
    _ensure_store()
    run = {
        "run_id":        datetime.now(timezone.utc).isoformat(),
        "raw_request":   raw_request,
        "interpretation": interpretation,
        "status":        status,
        "analysis_type": analysis_type,
        "revenue_delta": revenue_delta,
        "ebit_delta":    ebit_delta,
        "held_constant": held_constant or [],
        "tokens_in":     tokens_in,
        "tokens_out":    tokens_out,
        "cost_estimate": cost_estimate,
    }
    if extra:
        run.update(extra)
    st.session_state["audit_runs"].append(run)
    return run


def sign_run(run_id, signed, acknowledged):
    """Record a sign-off on the finished run it was made for: the row's status
    becomes FINAL and it gains signed.record(acknowledged), the name, the time
    and the warnings shown at sign-off. Returns the row, or None when the run
    is not in this session's list."""
    _ensure_store()
    for run in reversed(st.session_state["audit_runs"]):
        if run["run_id"] == run_id:
            run.update(signed.record(acknowledged))
            return run
    return None


def signed_line(run):
    """The sign-off line of a FINAL row, as the Sign-off section shows it, or
    None for a row that is not signed off."""
    if run.get("status") != FINAL:
        return None
    return SignOff(run["signed_off_by"],
                   datetime.fromisoformat(run["signed_off_at"])).line


def get_runs():
    """Return this session's runs, most recent first."""
    _ensure_store()
    return list(reversed(st.session_state["audit_runs"]))


def clear_runs():
    st.session_state["audit_runs"] = []
