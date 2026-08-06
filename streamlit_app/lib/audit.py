# =============================================================================
# lib/audit.py — per-session audit trail (foundation component)
# =============================================================================
# On the web the audit log is PER SESSION and resets — no server-side disk
# writes, no shared JSONL file that two users could corrupt (the CLI's
# _update_audit_field rewrites a shared file, which is unsafe for multi-user).
# This keeps the demo GDPR-clean and honest: it is labelled "this session's
# activity". The PERSISTENT audit log remains a feature of the downloaded
# local version.
#
# It reuses the SAME record shape the CLI's write_audit produces, so the
# governance story is identical; only the write target changes (session state,
# not a file).
# =============================================================================

import streamlit as st
from datetime import datetime, timezone


def _ensure_store():
    if "audit_runs" not in st.session_state:
        st.session_state["audit_runs"] = []


def record_run(raw_request, interpretation, status,
               revenue_delta=None, ebit_delta=None, held_constant=None,
               analysis_type=None, extra=None):
    """Append one run to this session's audit trail. status is one of
    draft / approved / locked / refused / caution. Mirrors the CLI record
    shape, minus the file hashing and disk write."""
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
