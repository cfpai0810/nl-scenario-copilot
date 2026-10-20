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
# tokens. It has no input fingerprint, no model id and no parsed values, and
# a finished run stays a draft for review: the web has no sign-off step (the
# CLI's is review.py).
# =============================================================================

import streamlit as st
from datetime import datetime, timezone

# The status of a finished web run. Nobody approves anything on the web, so
# the web never records "approved" or "locked".
DRAFT = "draft"


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


def get_runs():
    """Return this session's runs, most recent first."""
    _ensure_store()
    return list(reversed(st.session_state["audit_runs"]))


def clear_runs():
    st.session_state["audit_runs"] = []
