# =============================================================================
# lib/example_three_case.py: a saved three-case result for the keyless example
# =============================================================================
# A real run of the three-case tool captured verbatim (Revenue and COGS flexed
# one historical standard deviation either side of the base), so a visitor can
# see a full worked pessimistic/realistic/optimistic result with no API key. The
# values are the tool's own output and are illustrative sample-company figures.
#
# Structure mirrors example_run.py (the single what-if example): an "ebit" block
# for the headline metric band, "rows" for the multi-case table, plus the spread
# table, basis, takeaways, and explanation. The example tab reads these keys; the
# data file and the tab rendering are designed together so they match.
#
# Displayed figures are rounded to whole euros, so a case delta computed from the
# rounded levels may differ from the stored delta by a euro. That is display
# rounding; values are stored as the run produced them.
# =============================================================================

THREE_CASE_EXAMPLE = {
    # The drivers that were flexed, in the order shown.
    "drivers": ["Revenue", "COGS"],

    # The headline EBIT across the three cases, for the metric band at the top.
    # Realistic equals the base plan by design (no delta).
    "ebit": {
        "pessimistic": {"value": 512037, "delta": -30153},
        "realistic":   {"value": 542191, "delta": 0},
        "optimistic":  {"value": 572408, "delta": 30217},
    },

    # The spread table: each driver's three case values, in the driver's units.
    # (Revenue growth and COGS margin are both percentages here.)
    "spreads": [
        {"driver": "Revenue", "pessimistic": "11.85%", "realistic": "12.00%",
         "optimistic": "12.15%"},
        {"driver": "COGS",    "pessimistic": "42.08%", "realistic": "41.80%",
         "optimistic": "41.52%"},
    ],

    # The EBIT row across cases (the headline of a three-case analysis). Only the
    # EBIT line is stored here, because it is the figure this captured run
    # reports; the per-case breakdown of every P&L line is not part of the
    # captured example, and is not fabricated.
    "rows": [
        {"line": "Operating Profit (EBIT)", "base": 542191, "Pessimistic": 512037, "Realistic": 542191, "Optimistic": 572408},
    ],

    # The basis line (tightened form, matching the preview page).
    "basis": (
        "Each case is one historical standard deviation either side of the base, "
        "from the variation in the actuals: Revenue +/-0.15%, COGS +/-0.28%."
    ),

    "takeaways": [
        "EBIT ranges from 512,037 in the pessimistic case to 572,408 in the "
        "optimistic case, against a base of 542,191.",
        "Downside -30,153, upside +30,217, a total spread of 60,371.",
        "The downside and upside are broadly symmetric.",
        "Drivers flexed together: Revenue, COGS.",
    ],

    "explanation": (
        "The three-case scenario analysis produces an EBIT range of 512,037 in "
        "the downside to 572,408 in the upside, with the base plan sitting at "
        "542,191. The realistic case is the base plan, so its EBIT figure is "
        "identical to the base at 542,191. The upside relative to base is "
        "30,217, while the downside is 30,154, meaning the two tails are nearly "
        "symmetric around the base, with the upside marginally larger. For "
        "planning purposes, this symmetry suggests the business is not "
        "materially skewed toward either outperformance or underperformance "
        "under the historical variation observed, though even the downside "
        "represents a meaningful swing of roughly 30,000 in EBIT that teams "
        "should be prepared to manage.\n\n"
        "Two drivers were flexed to construct the cases. Revenue growth was "
        "moved one historical standard deviation of 0.15 percentage points "
        "either side of the base, running from 11.85 percent in the pessimistic "
        "case to 12.15 percent in the optimistic case. COGS as a percent of "
        "revenue was moved one historical standard deviation of 0.28 percentage "
        "points either side of the base, from 42.08 percent in the pessimistic "
        "case to 41.52 percent in the optimistic case. In the downside, lower "
        "revenue growth and a higher COGS rate compress EBIT together, while in "
        "the upside both drivers move favourably at once. All other drivers are "
        "held constant across the three cases and do not move. This model also "
        "does not link marketing spend or customer volume to revenue, so those "
        "items have no effect on the scenario outcomes shown here."
    ),

    # Per-month EBIT for the three-case fan chart. Values from the same live
    # run as the totals above. Realistic equals the base by design.
    "case_monthly": {
        "periods": ["2026-07", "2026-08", "2026-09",
                     "2026-10", "2026-11", "2026-12"],
        "pessimistic": [51892.32, 18064.77, 72477.59,
                        79820.13, 113982.64, 175799.60],
        "realistic":   [56308.10, 22354.38, 77355.97,
                        84950.84, 119533.91, 181687.31],
        "optimistic":  [60733.23, 26653.08, 82244.68,
                        90092.42, 125096.93, 187587.48],
    },

    # Real token counts captured from a live run of this example (explain
    # call only; three-case does not parse from natural language).
    "tokens_in": 327,
    "tokens_out": 402,
}
