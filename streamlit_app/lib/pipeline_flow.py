# =============================================================================
# lib/pipeline_flow.py: the pipeline diagram (matches governance_flow's language)
# =============================================================================
# Same visual grammar as governance_flow.py: purple = the model (used only for
# language, never numbers), green = deterministic engine, dark blue = data.
# Drawn in HTML by lib/flow_diagram.py, so it reads on a phone. Shows the AI
# boundary: the model appears in exactly two of five stages; everything numeric
# is deterministic. Kept as a pure function so any page can render it and it
# stays consistent.
# =============================================================================

from streamlit_app.lib.flow_diagram import Role, Stage, flow_html
from streamlit_app.lib.governance_flow import ENGINE, MODEL
from streamlit_app.lib.theme import DARK_BLUE, LIGHT_BLUE

DATA = Role(DARK_BLUE, LIGHT_BLUE)

LABEL = ("The pipeline: a deterministic data stage loads inputs and finds the "
         "forecast horizon; the model parses words into structure; a "
         "deterministic validator checks, classifies and confirms; a "
         "deterministic engine computes the forecast on a copy and the deltas; "
         "and the model explains the figures in words. Only the parse and "
         "explain stages use the model; everything numeric is deterministic.")

STAGES = (
    Stage("Data", "load inputs, find horizon", "deterministic", DATA),
    Stage("Parse", "words to structure", "model, language", MODEL),
    Stage("Validate", "check, classify, confirm", "deterministic", ENGINE),
    Stage("Compute", "forecast on a copy, deltas", "deterministic", ENGINE),
    Stage("Explain", "figures to words", "model, language", MODEL),
)


def pipeline_flow_html():
    """Return the pipeline diagram as HTML for st.markdown."""
    return flow_html(
        LABEL, STAGES,
        heading="The model appears in two stages only. Everything numeric is "
                "deterministic.",
        note="A separate spread calculator and three-case runner extend the "
             "engine for the three-case tool, and are deterministic too.",
        key=((MODEL, "Model, for language"),
             (ENGINE, "Deterministic code, for numbers")))
