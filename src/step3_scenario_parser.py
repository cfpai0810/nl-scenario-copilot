# =============================================================================
# step3_scenario_parser.py — PARSE: natural language to structured scenario
# =============================================================================
# Claude extracts structure and numbers only. It does no arithmetic and never
# computes a result. Returns a scenario dict, or None on a parse failure so
# the pipeline degrades gracefully.
# =============================================================================

import re
import json
import anthropic

from config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, LINE_ITEMS, CURRENCY_CODE

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


def build_parse_prompt(request, forecast_periods):
    """Build the prompt that asks Claude to extract a structured scenario."""
    line_item_list = "\n".join(
        "  - {} (driver type: {})".format(name, dtype)
        for name, dtype in LINE_ITEMS.items())

    periods_line = ", ".join(forecast_periods)

    system_prompt = (
        "You translate a finance what-if request into a structured scenario. "
        "You extract structure and numbers only. You never do arithmetic and "
        "you never compute a result. A separate system validates and runs it.\n\n"
        "<the_model>\n"
        "The forecast has exactly these line items and driver types:\n"
        "{line_items}\n"
        "</the_model>\n\n"
        "<forecast_periods>\n"
        "The forecast covers exactly these periods, in order:\n"
        "  {forecast_periods}\n"
        "Use these exact labels for any per-month change. Do not invent "
        "periods. If the user names a period outside this list (for example "
        "a month not shown, or a different year), do not map it to a real "
        "period: set confidence to low and say so in the ambiguity note.\n"
        "</forecast_periods>\n\n"
        "<operations>\n"
        "  scale_driver: multiply a driver value by a factor. 'Cut by 20 "
        "percent' is factor 0.80; 'increase by 50 percent' is 1.50. Legal "
        "for Revenue, COGS, IT Infrastructure, R&D Expense.\n"
        "  set_driver: replace a driver value outright. 'Set growth to 8 "
        "percent' is value 0.08. Legal for Revenue, COGS, IT Infrastructure, "
        "R&D Expense.\n"
        "  shift_driver: add or subtract a fixed amount to the current driver "
        "value. 'Add 30000 to IT Infrastructure' is value 30000. 'Increase "
        "revenue growth by 3 percentage points' is value 0.03. Legal for "
        "Revenue, COGS, IT Infrastructure, R&D Expense.\n"
        "  scale_schedule: scale a schedule quantity by a factor. 'Double the "
        "hiring' is 2.0. Legal for Personnel Cost (hires) and Marketing "
        "Spend (target customers).\n"
        "  set_schedule: replace all schedule entries with a uniform value. "
        "'Set new hires to 5 per month' is value 5. Legal for Personnel "
        "Cost and Marketing Spend.\n"
        "  add_schedule: add a fixed quantity to each schedule period. 'Hire "
        "3 more people per month' is value 3. Legal for Personnel Cost and "
        "Marketing Spend.\n"
        "  shift_schedule: move a schedule later or earlier by whole months. "
        "A one month delay is value 1. Legal for Personnel Cost and Marketing "
        "Spend.\n"
        "</operations>\n\n"
        "<rules>\n"
        "- Use only the exact line item names given above, character for "
        "character.\n"
        "- Percentages become fractions: 20 percent is 0.20, not 20.\n"
        "- A cut, reduction, or delay is a smaller factor or a positive shift "
        "of months. Match the sign to the verb.\n"
        "- 'Increase by 20 percent' is scale_driver (multiply by 1.20). "
        "'Increase by 3 percentage points' is shift_driver (add 0.03). "
        "'Add 30000' on a fixed-{ccy} driver is shift_driver (value 30000). "
        "Choose the operation that matches the user's intent.\n"
        "- COGS, Revenue growth, and R&D growth are percentages, not {ccy} "
        "amounts. If the user gives a {ccy} figure for one of these, still "
        "parse your best guess but set confidence to low and explain in the "
        "ambiguity note that the driver is a percentage, not {ccy}.\n"
        "- IT Infrastructure is a per-period (monthly) {ccy} cost. If the user "
        "adds a {ccy} amount without saying per month or per year, set "
        "confidence to low and note in the ambiguity note that the amount "
        "will apply per month unless they say otherwise.\n"
        "- If the request is vague or you are unsure, say so in "
        "ambiguity_note and set confidence to low. Do not guess a number you "
        "were not given.\n"
        "- Use only standard ASCII. No arrows, em dashes, or en dashes.\n"
        "- A change can apply to all periods (uniform) or to specific months "
        "(per month). For a uniform change over the whole horizon, give a "
        'single "value" and set "periods" to "all". For a change that differs '
        "by month, or applies only to some months, OMIT value (use null) and "
        'give "value_by_period": an object mapping each affected period label '
        "to its value.\n"
        "- Same change over several months (for example 'cut marketing 20 "
        "percent in Q3'): you may give value_by_period with the same value "
        "for each month, or give a value with a periods list. Either is "
        "accepted.\n"
        "- Different change per month (for example 'increase revenue 5 "
        "percent in July and 8 percent in August'): value_by_period maps "
        "each month to its own value.\n"
        "- Named periods: only expand a named period to the months that "
        "actually appear in the forecast periods above. 'Q3' means July, "
        "August, September ONLY if those appear. If a named or vague period "
        "('summer', 'mid year', 'the second half') does not map cleanly to "
        "the listed periods, set confidence to low and explain in the "
        "ambiguity note; do not guess the months.\n"
        "- Do not invent a gradual ramp. If the user says 'ramp from 5 to "
        "10 percent', do not fabricate the intermediate months: set "
        "confidence to low and note that the per-month values should be "
        "confirmed.\n"
        "- Per-month values use the same convention as a single value: a "
        "scale factor (1.05 for plus 5 percent), a set value, or a shift. "
        "For per-month Revenue, prefer scale_driver (a percentage change), "
        "not an absolute figure.\n"
        "</rules>\n\n"
        "<output_format>\n"
        "Return ONLY a JSON object inside a fenced json block, nothing else.\n"
        "Each change is EITHER uniform:\n"
        '  {{"target": "LINE ITEM", "operation": "OPERATION", '
        '"value": NUMBER, "periods": "all"}}\n'
        "OR applies to specific months (same value):\n"
        '  {{"target": "LINE ITEM", "operation": "OPERATION", '
        '"value": NUMBER, "periods": ["YYYY-MM", "YYYY-MM"]}}\n'
        "OR differs by month:\n"
        '  {{"target": "LINE ITEM", "operation": "OPERATION", '
        '"value": null,\n'
        '   "value_by_period": {{"YYYY-MM": NUMBER, "YYYY-MM": NUMBER}}}}\n'
        "\n"
        "```json\n"
        "{{\n"
        '  "changes": [\n'
        "    ... one or more change objects ...\n"
        "  ],\n"
        '  "ambiguity_note": "empty string, or what is unclear",\n'
        '  "confidence": "high, medium, or low"\n'
        "}}\n"
        "```\n"
        "</output_format>"
    ).format(line_items=line_item_list, forecast_periods=periods_line,
             ccy=CURRENCY_CODE)

    user_prompt = (
        "What-if request:\n\"{}\"\n\n"
        "Extract the structured scenario as JSON."
    ).format(request)

    return system_prompt, user_prompt


def parse_scenario_json(text):
    """Extract the scenario JSON from a fenced block. Returns the dict, or
    None if not found or malformed."""
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if not match:
        match = re.search(r'(\{.*"changes".*\})', text, re.DOTALL)
        if not match:
            return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def normalise_periods(scenario):
    """Expand any {value + periods-list} change into value_by_period, so
    downstream only ever sees value_by_period for period-specific changes.
    Deterministic; no judgement. Leaves 'all'-period uniform changes untouched."""
    for ch in scenario.get("changes", []):
        periods = ch.get("periods")
        if isinstance(periods, list) and ch.get("value") is not None \
                and "value_by_period" not in ch:
            ch["value_by_period"] = {p: ch["value"] for p in periods}
            ch["value"] = None
    return scenario


def call_claude_parse(request, forecast_periods, client=None):
    """Call Claude to parse the request. Returns (scenario, tok_in, tok_out)."""
    client = client or globals().get("client")
    if client is None:
        raise RuntimeError("No Anthropic client available.")
    system_prompt, user_prompt = build_parse_prompt(request, forecast_periods)
    print("\n[..] Parsing request with Claude...")
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("Authentication failed. Check ANTHROPIC_API_KEY in .env.")
    except anthropic.APIStatusError as e:
        raise RuntimeError("API error {}: {}".format(e.status_code, e.message))

    text     = response.content[0].text
    scenario = parse_scenario_json(text)
    if scenario is not None:
        scenario = normalise_periods(scenario)
    if scenario is None:
        print("[WARN] Could not parse a scenario from the response.")
    else:
        print("[OK] Parsed {} change(s), confidence {}".format(
            len(scenario.get("changes", [])), scenario.get("confidence", "?")))
    return scenario, response.usage.input_tokens, response.usage.output_tokens
