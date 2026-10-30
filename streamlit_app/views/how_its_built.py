# =============================================================================
# views/how_its_built.py: "How it's built" (trust story, then technical depth)
# =============================================================================
# Two audiences on one page. Part 1 is for a finance leader: why an AI-assisted
# forecasting tool can be trusted (the three-layer model, the confirm gate, the
# deterministic core, the audit trail). Part 2 is for an engineer or technical
# evaluator: the pipeline, the module boundaries, the AI-versus-deterministic
# split as a design choice, and the test coverage. Prose, not a README dump.
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css, header_html
from streamlit_app.lib.governance_flow import governance_flow_html
from streamlit_app.lib.governance_lifecycle import governance_lifecycle_html
from streamlit_app.lib.pipeline_flow import pipeline_flow_html

st.markdown(inject_css(), unsafe_allow_html=True)

st.markdown(header_html(
    "How it's built",
    "Why the figures can be trusted, then the pipeline, the AI boundary and "
    "the tests."), unsafe_allow_html=True)

# The governance flow, the thesis of the tool, first (it was on Home).
st.markdown(governance_flow_html(), unsafe_allow_html=True)

st.write(
    "This tool was built on one principle: an AI system that touches financial "
    "numbers has to earn trust before it earns time. The sections below explain "
    "how that principle shapes the design, first for a finance reader and then "
    "in technical detail.")

# ── Part 1: the trust story (finance reader) ─────────────────────────────────
st.markdown("---")
st.markdown("#### Why you can trust the numbers")

st.write(
    "Python produces every reported figure, and the language model writes the "
    "wording around it. That is the whole idea. A large language model is "
    "good at reading a sentence and turning it into structure, and good at "
    "describing a result in plain words. It is not a calculator, and it is not "
    "asked to be one. Every figure in the tables and charts is computed by "
    "deterministic Python code, the same way a spreadsheet would, and the same "
    "inputs always produce the same output. This version does not check the "
    "numbers in the model's wording, so read the table for figures.")

st.markdown("**The work happens in separated layers, and you open and close "
            "every one.**")
st.write(
    "You describe a change and confirm the tool's interpretation before anything "
    "runs; Python then computes on a copy of the plan, so the base is never "
    "altered; and the model describes the result it was handed. If a request is "
    "ambiguous in a way that would change the answer, the tool asks you to "
    "rephrase rather than guessing.")

st.markdown("**What the audit keeps.**")
st.write(
    "In the command-line version, a single what-if that was run, refused, "
    "flagged as ambiguous, or cancelled is written to the audit log, along "
    "with the model used, the parsed scenario and a fingerprint of the exact "
    "input data; a finished three-case run is written with its cases, the "
    "model and the same fingerprint. That makes a scenario reproducible and "
    "reviewable after the fact, which is what a finance function needs before "
    "it will rely on any tool, assisted by AI or not. This web demo keeps a "
    "shorter list, for your session only: each finished run and each refused "
    "or ambiguous what-if, with its status and token count. It has no input "
    "fingerprint, no model id and no parsed values, and a what-if you cancel "
    "with Rephrase before it runs is not recorded. A run you sign off is "
    "marked FINAL in the list, with the reviewer's name, the time and the "
    "warnings shown at sign-off.")

st.info(
    "In one line: the model reads your request and writes the explanation; "
    "Python calculates every figure; you review and sign off the result. "
    "The intelligence is at the edges, and the numbers in the middle are "
    "deterministic and checkable.")

st.markdown("#### The governance lifecycle")
st.markdown(governance_lifecycle_html(), unsafe_allow_html=True)
st.write(
    "In a production deployment, review is the start of the record, not "
    "the end. Once a reviewer approves the explanation, the approved version "
    "becomes the immutable record: it is what everyone reads afterwards, "
    "and it cannot be edited. A later change is issued as a new, "
    "separately approved version, and the original approved explanation still "
    "stands unchanged. This live demo stops at a typed sign-off, kept for "
    "your session; it does not store or lock anything, so no data is "
    "retained. The lifecycle diagram above shows the full model this design "
    "is built towards.")

# ── Part 2: the technical detail (engineer / evaluator) ──────────────────────
st.markdown("---")
st.markdown("#### The technical detail")

st.write(
    "The system is a small, layered pipeline. Each stage has one responsibility, "
    "and the boundary between the AI stages and the deterministic stages is "
    "deliberate and strict.")

st.markdown("**The pipeline.**")
st.markdown(pipeline_flow_html(), unsafe_allow_html=True)
st.write(
    "Each stage has one job. The data layer finds the boundary between actuals "
    "and the forecast horizon; the engine applies six driver types period by "
    "period and rolls them into a profit and loss; the validator classifies a "
    "request as clear, ambiguous, impossible, or too large for one scenario; the "
    "scenario engine computes on copies. Two further deterministic modules derive "
    "the pessimistic, realistic, and optimistic spreads from history and run the "
    "three forecasts for the three-case tool.")

st.markdown("**The AI boundary is the important design choice.**")
st.write(
    "The language model appears in exactly two stages: the parser, which "
    "extracts structure and numbers from a sentence and is explicitly forbidden "
    "from computing anything, and the explainer, which is given already-computed "
    "figures and asked only to describe them. Everything numeric, the forecast, "
    "the validation, the deltas, the spreads, the three cases, is deterministic "
    "Python. This is what makes the output reproducible and testable. The "
    "model is not asked to calculate: Python produces every figure in the "
    "table.")

st.markdown("**Reconciliation by construction.**")
st.write(
    "The full-year view stitches actual quarters to forecast quarters. Rather "
    "than assembling the actuals with a separate function that could drift from "
    "the forecast logic, the same profit-and-loss builder produces both, "
    "filtered by row type. Actual and forecast figures therefore reconcile "
    "automatically, because they are built by identical code. There is no second "
    "implementation to keep in sync.")

st.markdown("**Safety properties that are enforced, not hoped for.**")
st.write(
    "The base plan is deep-copied before any change is applied, so it cannot be "
    "mutated. Per-month edits reuse the exact original value for any month left "
    "untouched, so an unedited confirmation runs precisely the figures that were "
    "derived, with no rounding drift. Invalid edits are re-validated at the "
    "moment of confirmation and block the run rather than reaching the engine. "
    "Each of these is covered by a test that would fail if the property were "
    "broken.")

st.markdown("**Tested to a level a finance tool should be.**")
st.write(
    "The suite covers the forecast engine, the parser contract, the validator, "
    "the per-month vector logic end to end, the quarterly reconciliation against "
    "independently computed figures, the three-case mechanics, and the pure "
    "helpers behind the editable tables. The web layer reuses the same tested "
    "pipeline unchanged; the pages orchestrate and render, they never recompute. "
    "Reports are generated to memory for download rather than written to a "
    "shared file, so nothing one visitor does can affect another.")

st.markdown("**Built for a public, keyless demo.**")
st.write(
    "Everything here runs on a synthetic sample entity, so there is no "
    "confidential data anywhere in the app or its history. Each tool includes a "
    "worked example that needs no API key, so the reasoning and the output are "
    "visible even without running a live analysis.")

st.markdown("---")
st.caption(
    "The sample data is synthetic and the figures are illustrative. The "
    "architecture, the governance model, and the test coverage are real.")
