# =============================================================================
# lib/cost.py - per-run cost ESTIMATE from token counts and config rates
# =============================================================================
# This is an ESTIMATE, never a bill. Anthropic's own metering is authoritative;
# every display frames the figure as an estimate and points the user to their
# Anthropic console for the real charge. Rates and the assumed FX live in config
# (keyed to the model the tool uses), so there is one place to update.
#
# Pure functions only: no Streamlit, no IO, so they import and test cleanly.
# =============================================================================

from config import (
    COST_INPUT_USD_PER_MTOK,
    COST_OUTPUT_USD_PER_MTOK,
    ASSUMED_USD_PER_EUR,
)


def estimate_cost(tokens_in, tokens_out):
    """Estimate the cost of a run from token counts and the config rates.

    Returns a dict: usd, eur, tokens_total, and the assumed rate used. This is an
    ESTIMATE, not a bill.
    """
    usd = (tokens_in / 1_000_000.0) * COST_INPUT_USD_PER_MTOK \
        + (tokens_out / 1_000_000.0) * COST_OUTPUT_USD_PER_MTOK
    eur = usd / ASSUMED_USD_PER_EUR
    return {
        "usd": usd,
        "eur": eur,
        "tokens_total": tokens_in + tokens_out,
        "assumed_rate": ASSUMED_USD_PER_EUR,
    }


def format_cost(tokens_in, tokens_out):
    """Human-readable cost line. EUR primary, USD in parentheses, framed as an
    estimate. Sub-cent runs read 'less than EUR 0.01', never 'EUR 0.00' and never
    false precision."""
    c = estimate_cost(tokens_in, tokens_out)
    total = c["tokens_total"]
    if c["eur"] < 0.01:
        eur_str = "less than EUR 0.01"
        usd_str = "less than USD 0.01"
    else:
        eur_str = "about EUR {:.2f}".format(c["eur"])
        usd_str = "about USD {:.2f}".format(c["usd"])
    return (
        "{:,} tokens ({:,} in + {:,} out). Estimated cost {} "
        "({} at an assumed {:.2f} USD/EUR). This is an estimate; your actual "
        "cost is billed by Anthropic.".format(
            total, tokens_in, tokens_out, eur_str, usd_str, c["assumed_rate"])
    )
