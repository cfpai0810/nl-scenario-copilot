# =============================================================================
# lib/claude_client.py — the client-provider seam
# =============================================================================
# Solves the bring-your-own-key problem. The CLI created the Anthropic client
# once at import time from the .env key. The web cannot do that: the user
# pastes their key AFTER the app loads, and two users have two keys. So the
# client is provided on demand, per session, never as a frozen module global.
#
# The CLI path can still call set_default_client() once at startup and keep
# behaving exactly as before. The web path builds a per-session client and
# passes it explicitly into the call_claude_* functions.
# =============================================================================

import anthropic

_default_client = None


class NoKeyError(RuntimeError):
    """Raised when a Claude call is attempted with no client available."""
    pass


def build_client(api_key):
    """Build an Anthropic client from a pasted or env key. Does not store it."""
    if not api_key or not api_key.strip():
        raise NoKeyError("No API key provided.")
    return anthropic.Anthropic(api_key=api_key.strip())


def set_default_client(api_key):
    """CLI convenience: set a process-wide default client from the env key.
    The web layer does NOT use this; it passes a per-session client instead."""
    global _default_client
    _default_client = build_client(api_key)
    return _default_client


def get_default_client():
    """Return the process-wide default client, or raise if none is set."""
    if _default_client is None:
        raise NoKeyError(
            "No Anthropic client is configured. On the web, paste your API "
            "key first. On the CLI, set ANTHROPIC_API_KEY in your .env.")
    return _default_client


def looks_like_anthropic_key(api_key):
    """Cheap client-side sanity check before we even try a call. Not
    validation (only a real request proves a key works), just a fast filter
    for obvious mistakes so we give a friendly message instead of an API error."""
    if not api_key:
        return False
    k = api_key.strip()
    return k.startswith("sk-ant-") and len(k) > 20
