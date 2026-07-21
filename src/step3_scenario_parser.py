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

from config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, LINE_ITEMS

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def build_parse_prompt(request):
    """Build the prompt that asks Claude to extract a structured scenario."""
    line_item_list = "\n".join(
        "  - {} (driver type: {})".format(name, dtype)
        for name, dtype in LINE_ITEMS.items())

    system_prompt = (
        "You translate a finance what-if request into a structured scenario. "
        "You extract structure and numbers only. You never do arithmetic and "
        "you never compute a result. A separate system validates and runs it.\n\n"
        "<the_model>\n"
        "The forecast has exactly these line items and driver types:\n"
        "{line_items}\n"
        "</the_model>\n\n"
        "<operations>\n"
        "  scale_driver: multiply a driver value by a factor. A cut of 20 "
        "percent is a factor of 0.80. Legal for Revenue, COGS, IT "
        "Infrastructure, R&D Expense.\n"
        "  set_driver: replace a driver value outright. Set growth to 8 "
        "percent is value 0.08. Legal for Revenue, COGS, IT Infrastructure, "
        "R&D Expense.\n"
        "  scale_schedule: scale a schedule quantity by a factor. Legal for "
        "Personnel Cost (hires) and Marketing Spend (target customers).\n"
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
        "- If the request is vague or you are unsure, say so in "
        "ambiguity_note and set confidence to low. Do not guess a number you "
        "were not given.\n"
        "- Use only standard ASCII. No arrows, em dashes, or en dashes.\n"
        "</rules>\n\n"
        "<output_format>\n"
        "Return ONLY a JSON object inside a fenced json block, nothing else:\n"
        "```json\n"
        "{{\n"
        '  "changes": [\n'
        '    {{"target": "LINE ITEM", "operation": "OPERATION", '
        '"value": NUMBER, "periods": "all"}}\n'
        "  ],\n"
        '  "ambiguity_note": "empty string, or what is unclear",\n'
        '  "confidence": "high, medium, or low"\n'
        "}}\n"
        "```\n"
        "</output_format>"
    ).format(line_items=line_item_list)

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


def call_claude_parse(request):
    """Call Claude to parse the request. Returns (scenario, tok_in, tok_out)."""
    system_prompt, user_prompt = build_parse_prompt(request)
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
    if scenario is None:
        print("[WARN] Could not parse a scenario from the response.")
    else:
        print("[OK] Parsed {} change(s), confidence {}".format(
            len(scenario.get("changes", [])), scenario.get("confidence", "?")))
    return scenario, response.usage.input_tokens, response.usage.output_tokens
