# =============================================================================
# lib/governance_flow.py: the signature diagram
# =============================================================================
# The three-layer governance model as a flow that carries the logic, not three
# static boxes. A person opens and closes every run; the model works only in
# between and never touches a figure. Colour encodes WHO OWNS each step: the
# person, the language model (language), the deterministic engine (numbers).
# This same diagram is the "what good looks like" content piece later. Drawn
# in HTML by lib/flow_diagram.py, so it reads on a phone.
# =============================================================================

from streamlit_app.lib.flow_diagram import Role, Stage, flow_html
from streamlit_app.lib.theme import DARK_BLUE, GREEN, LIGHT_BLUE

PERSON = Role(DARK_BLUE, LIGHT_BLUE)
MODEL  = Role("#6B4FA8", "#F0ECF8")     # purple: the model handles language, not numbers
ENGINE = Role(GREEN, "#E8F3E9")

LABEL = ("How a request is governed: you describe a change in plain language, "
         "the model shows how it understood you and you confirm it, a "
         "deterministic engine computes every figure with the model absent from "
         "the numbers, the model writes up the figures it was given, and you "
         "review and sign off. The command-line version keeps an audit log on "
         "disk; this web demo keeps a shorter list for your session.")

STAGES = (
    Stage("YOU DESCRIBE", "A change, stated in plain language", "the person",
          PERSON),
    Stage("INTENT CONFIRMED", "The model shows what it understood; you agree",
          "you confirm", MODEL),
    Stage("CALCULATION", "Deterministic code runs every figure", "no model here",
          ENGINE),
    Stage("EXPLAINED", "The model writes up figures it was given",
          "words, not numbers", MODEL),
    Stage("YOU SIGN OFF", "You review the result", "the person", PERSON),
)


def governance_flow_html():
    """Return the governance flow diagram as HTML for st.markdown. The title
    CALCULATION needs a 639 px diagram to sit on one line in a row, more than
    the 600 px of the global rule, so this flow asks for a 660 px row and stays
    stacked, as on a phone, below that."""
    return flow_html(
        LABEL, STAGES, row_min=660,
        heading="A person opens and closes every run. The model works only in "
                "between.",
        note="The command-line version keeps an audit log on disk; this web demo "
             "keeps a shorter list for your session.",
        key=((PERSON, "You"), (MODEL, "The model, for language"),
             (ENGINE, "Deterministic code, for numbers")))
