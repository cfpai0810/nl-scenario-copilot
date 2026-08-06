# =============================================================================
# lib/key_gate.py — the bring-your-own-key gate (foundation component)
# =============================================================================
# Renders the API-key input, holds the key ONLY in session state for the live
# session, and builds a per-session Anthropic client. The key is never written
# to disk, never logged, never stored server-side. This is reused by every
# tool page.
#
# Honest disclosure (per the master plan): "used only for your session, never
# stored" — NOT "never leaves your device", because the key does reach the
# server to make the Claude call. Users who want their key to never leave
# their machine are pointed to the GitHub download.
# =============================================================================

import streamlit as st

from lib.claude_client import build_client, looks_like_anthropic_key
from lib.errors import friendly_message


def render_key_gate():
    """Render the key input in the sidebar. Returns the per-session client, or
    None if no valid key is present yet. Safe to call on every page."""
    st.sidebar.markdown("### Run it yourself")
    st.sidebar.caption(
        "Paste your Anthropic API key to run a live analysis. "
        "Don't have one? Use the example output on the main page, no key needed.")

    # The key lives ONLY in session state. Password field masks it on entry.
    key = st.sidebar.text_input(
        "Anthropic API key",
        type="password",
        placeholder="sk-ant-...",
        key="byo_api_key",
        help="Used only for this session. Never stored, never logged.",
    )

    st.sidebar.caption(
        "Running a live analysis uses your own Anthropic API key and bills "
        "your Anthropic account a small amount per run. The worked examples "
        "are free and need no key.")

    # Honest disclosure, always visible.
    st.sidebar.markdown(
        '<div class="sc-keynote">Your key is held only for this browser '
        'session to make the AI calls, then discarded. It is never stored or '
        'logged. If you would rather your key never leave your own machine, '
        'download the project from GitHub and run it locally.</div>',
        unsafe_allow_html=True,
    )

    if not key:
        return None

    # Cheap client-side sanity check before we build anything.
    if not looks_like_anthropic_key(key):
        st.sidebar.warning(
            "That does not look like a complete Anthropic key. It should start "
            "with sk-ant- and be the full string.")
        return None

    # Build a per-session client. Never global, never shared across users.
    try:
        client = build_client(key)
    except Exception as exc:
        st.sidebar.error(friendly_message(exc))
        return None

    st.sidebar.success("Key accepted for this session. You can run a live analysis.")
    return client


def has_live_client(client):
    """Small helper for pages to branch on live vs example mode."""
    return client is not None
