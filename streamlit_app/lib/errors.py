# =============================================================================
# lib/errors.py — friendly error handling for a non-technical audience
# =============================================================================
# A business user must never see a Python traceback. Every failure becomes a
# plain-language message that says what went wrong and what to do. This wraps
# the Claude calls (bad key, rate limit, network) and any unexpected error.
# =============================================================================

import anthropic


def friendly_message(exc):
    """Turn an exception into a plain-language message for the user.
    Returns a short string. Never exposes a stack trace."""
    # Authentication: the pasted key is wrong, expired, or revoked.
    if isinstance(exc, anthropic.AuthenticationError):
        return ("That API key was not accepted. Check you pasted the whole "
                "key (it starts with sk-ant-) and that it is active in your "
                "Anthropic console, then try again.")

    # Rate limit or overloaded.
    if isinstance(exc, anthropic.RateLimitError):
        return ("Your account hit a rate limit. Wait a moment and try again. "
                "If it keeps happening, check your usage limits in the "
                "Anthropic console.")

    # Any other API-status error (network, server, bad request).
    if isinstance(exc, anthropic.APIStatusError):
        return ("The AI service returned an error and could not complete the "
                "request. This is usually temporary. Please try again in a "
                "moment.")
    if isinstance(exc, anthropic.APIConnectionError):
        return ("Could not reach the AI service. Check your connection and "
                "try again.")

    # Our own no-key signal.
    if exc.__class__.__name__ == "NoKeyError":
        return ("Paste your Anthropic API key above to run a live analysis, "
                "or view the example output which needs no key.")

    # Anything unexpected: a calm generic message, no traceback.
    return ("Something went wrong running that request. Please try again, or "
            "try one of the example prompts.")
