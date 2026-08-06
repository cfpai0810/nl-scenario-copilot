# =============================================================================
# lib/example_run.py: a saved single what-if result for the keyless example tab
# =============================================================================
# This is a real run of the tool ("Cut marketing spend by 20 percent") captured
# verbatim, so a visitor can see a full worked result with no API key. The values
# are the tool's own output and are illustrative sample-company figures.
#
# The displayed line figures are rounded to whole euros, so a rounded Revenue
# minus a rounded COGS may differ from the rounded Gross Profit by a euro. That
# is display rounding, not an error; the figures are stored exactly as the run
# produced them.
# =============================================================================

EXAMPLE = {
    "request": "Cut marketing spend by 20 percent",
    "echo": "You want to reduce Marketing Spend by 20 percent.",
    "analysis": "sensitivity",

    # EBIT headline (forecast horizon), for the metric band at the top of the
    # example. Same figures as the EBIT row in "deltas" below.
    "ebit": {
        "base": 542191,
        "scenario": 618991,
        "delta": 76800,
        "pct": 0.142,
    },

    # The full base-versus-scenario P&L over the forecast horizon.
    "deltas": [
        {"line": "Revenue",                 "base": 8490872, "scenario": 8490872, "delta": 0,      "pct": 0.0},
        {"line": "COGS",                    "base": 3549185, "scenario": 3549185, "delta": 0,      "pct": 0.0},
        {"line": "Gross Profit",            "base": 4941688, "scenario": 4941688, "delta": 0,      "pct": 0.0},
        {"line": "Personnel Cost",          "base": 1732250, "scenario": 1732250, "delta": 0,      "pct": 0.0},
        {"line": "Marketing Spend",         "base": 534000,  "scenario": 457200,  "delta": -76800, "pct": -0.144},
        {"line": "IT Infrastructure",       "base": 270000,  "scenario": 270000,  "delta": 0,      "pct": 0.0},
        {"line": "R&D Expense",             "base": 1863247, "scenario": 1863247, "delta": 0,      "pct": 0.0},
        {"line": "Total OpEx",              "base": 4399497, "scenario": 4322697, "delta": -76800, "pct": -0.017},
        {"line": "Operating Profit (EBIT)", "base": 542191,  "scenario": 618991,  "delta": 76800,  "pct": 0.142},
    ],

    "takeaways": [
        "EBIT changes by +76,800 (+14.2%), from 542,191 to 618,991.",
        "Lines that moved: Marketing Spend. Lines held flat: Revenue, COGS, "
        "Personnel Cost, IT Infrastructure, R&D Expense.",
    ],

    "explanation": (
        "This is a sensitivity analysis in which a single driver, Marketing "
        "Spend, was reduced by 20 percent from its base level, with all other "
        "inputs held unchanged.\n\n"
        "The most important result is the EBIT improvement. Cutting marketing "
        "spend by 20 percent adds 76,800 euros to EBIT over the forecast "
        "horizon, lifting it from 542,191 to 618,991 euros, a gain of roughly "
        "14 percent on the base EBIT figure. Revenue remains flat at 8,490,872 "
        "euros in both cases, so the entire benefit flows through as a direct "
        "cost reduction rather than as any change in the top line.\n\n"
        "There is a material limitation to note. The model treats revenue as "
        "driven by its own seasonal trend, meaning the reduction in marketing "
        "spend has no effect on customer acquisition or retention and therefore "
        "no effect on Revenue. In practice, lower marketing investment would "
        "very likely weigh on future revenue to some degree, so the 76,800 euro "
        "EBIT benefit shown here should be treated as an upper-bound estimate. "
        "A fuller scenario that also modelled the revenue drag from reduced "
        "marketing activity would give a more conservative and arguably more "
        "realistic picture of the net impact."
    ),

    "held_constant": [
        "Marketing and customer changes do not flow through to Revenue, which "
        "is modelled on its own seasonal trend, so the revenue line is "
        "unchanged in this scenario.",
    ],
}
