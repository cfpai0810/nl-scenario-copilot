# Three-Case Analysis — User Flow Blueprint

Build target for the next session. Path 2 of the copilot (single what-if is Path 1, already done).

## Decision tree
pick drivers — multi-select, up to three (Revenue / COGS / R&D / IT / Personnel / Marketing)
|
+-- Auto-populate  ("calculate the cases for me")
|     +-- each driver: derivable? -> data-derived spread; else -> moderate preset band
|     +-- show ALL drivers' cases at once in a single table
|     +-- one decision: accept [y], widen to a standard band [w], or back [b]
|     |     +-- if any driver is narrow: note shown, widen offered (moderate or wide)
|
+-- Manual  ("I'll provide the numbers")
+-- Enter each value per driver  (pessimistic / realistic / optimistic)
+-- Enter a band per driver      (base +/- a band I choose)
|
confirm (echo-back all drivers' cases) -> run base + 3 cases -> multi-column report

## Design principles
1. Progressive disclosure: top menu has 3 items (single / three-case / quit). Complexity revealed one step at a time.
2. Multi-select drivers first (up to three), then one method choice: auto-populate vs manual.
3. "Range vs each value" lives INSIDE manual, not as a peer of auto.
4. Auto-populate derives each driver independently, then shows all cases in one table with a single accept-or-widen decision.
5. Context-aware: derivable drivers can derive; fixed/schedule ones fall back to a preset band automatically.
6. Every path ends at the same echo-back + confirm. One checkpoint.
7. Always a [back]. Same "run another" exit after every run.

## Spread logic (already built and committed)
`src/step7_scenario_spread.py`:
- `derive_spread(driver_type, base_value, history_rates)` -> data-derived, flags narrow (<2pp width). None for non-derivable types.
- `preset_spread(base_value, band)` -> base +/- band.
- `manual_spread(pess, real, opt)` -> user values verbatim.
- Derivable types: seasonal_yoy, margin_pct, growth_pct. IT (fixed), Personnel/Marketing (schedule) are band/manual only.
- Real data note: revenue YoY std dev is ~0.15%, so derived spread flags NARROW on the real actuals.

## Build status — COMPLETE
All three steps built and validated:
1. Menu state machine in main.py — multi-select drivers, auto/manual paths, ask/confirm injected.
2. Multi-driver run_three_case pipeline — cases dict keyed by {line_item: value}, deep copies, base never mutated.
3. Multi-column PDF — cases table, full P&L across four columns, amber EBIT strip, Python takeaways, Claude analysis.

## Validated
- Spread calculator works against real actuals (narrow correctly flagged on Revenue at ~0.15%).
- Multi-driver run validated: realistic column matches base exactly on every line (delta = 0).
- Two-driver run (Revenue + COGS) produces correct compounding: asymmetric EBIT swing from COGS being a % of changing Revenue.
- PDF, CSV, and audit trail all written correctly.
