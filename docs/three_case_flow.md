# Three-Case Analysis — User Flow Blueprint

Build target for the next session. Path 2 of the copilot (single what-if is Path 1, already done).

## Decision tree
pick driver (Revenue / COGS / R&D / IT / Personnel / Marketing)
|
+-- Auto-populate  ("calculate the cases for me")
|     +-- derivable driver (Revenue/COGS/R&D)? -> data-derived spread
|     |     +-- if narrow: offer to widen to a standard band (+/-3pp or +/-5pp)
|     +-- fixed/schedule driver (IT/Personnel/Marketing)? -> standard band (+/-3 or +/-5)
|
+-- Manual  ("I'll provide the numbers")
+-- Enter each value  (pessimistic / realistic / optimistic)
+-- Enter a range     (base +/- a band I choose)
|
confirm (echo-back the three cases) -> run base + 3 cases -> multi-column report

## Design principles
1. Progressive disclosure: top menu has 3 items (single / three-case / quit). Complexity revealed one step at a time.
2. First question is the clean binary: auto-populate vs manual.
3. "Range vs each value" lives INSIDE manual, not as a peer of auto.
4. Auto-populate hides its own complexity (derived vs band); only asks a follow-up if the derived spread is narrow.
5. Context-aware: derivable drivers can derive; fixed ones go straight to a band.
6. Every path ends at the same echo-back + confirm. One checkpoint.
7. Always a [back]. Same "run another" exit after every run.

## Spread logic (already built and committed)
`src/step7_scenario_spread.py`:
- `derive_spread(driver_type, base_value, history_rates)` -> data-derived, flags narrow (<2pp width). None for non-derivable types.
- `preset_spread(base_value, band)` -> base +/- band.
- `manual_spread(pess, real, opt)` -> user values verbatim.
- Derivable types: seasonal_yoy, margin_pct, growth_pct. IT (fixed), Personnel/Marketing (schedule) are band/manual only.
- Real data note: revenue YoY std dev is ~0.15%, so derived spread flags NARROW on the real actuals.

## Build order for next session
1. Menu state machine in main.py (small functions returning choice or 'back'; ask/confirm injected).
2. run_three_case pipeline (three engine runs on deep copies, multi-column deltas). Logic already sandbox-proven.
3. Multi-column PDF (assumptions + delta table: base + 3 cases; AI explains across cases).

## Validated so far
- Spread calculator works against real actuals (narrow correctly flagged).
- Three-scenario run validated end to end in sandbox: derive -> 3 cases -> run each -> multi-col deltas -> ordering (pess<base<opt) -> realistic delta = 0 -> base never mutated.
- Multi-column assumptions table design confirmed (extends the single-scenario table by adding columns).
