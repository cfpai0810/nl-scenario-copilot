# =============================================================================
# lib/governance_lifecycle.py: the approve-lock-read-only lifecycle
# =============================================================================
# A visual walkthrough of the full governance lifecycle as intended for
# production deployment. This is a documentation diagram only: it does not
# change any app behaviour, store anything, or add persistence. The diagram
# shows where the live demo stops (at a typed sign-off) and how the production
# model continues (approve, lock, read only, re-issue). It is drawn in HTML by
# lib/flow_diagram.py, so it reads on a phone as well as on a desktop.
# =============================================================================

from streamlit_app.lib.flow_diagram import Boundary, Role, Stage, flow_html
from streamlit_app.lib.theme import DARK_BLUE, LIGHT_BLUE, MID_BLUE

PERSON = Role(DARK_BLUE, LIGHT_BLUE)
RECORD = Role(MID_BLUE, "#E6F1FB")

LABEL = ("The governance lifecycle: you review the flagged output, approve the "
         "report, the approved version is locked and becomes immutable, everyone "
         "reads the locked record, and a change requires a new separately "
         "approved version.")

STAGES = (
    Stage("YOU REVIEW", "Check the flagged output and data", "the person", PERSON),
    Stage("YOU APPROVE", "Sign off the report as final", "the person", PERSON),
    Stage("LOCKED", "The approved report becomes immutable", "the record", RECORD),
    Stage("READ ONLY", "Everyone reads it, no one edits it", "the record", RECORD),
    Stage("RE-ISSUE", "A new version; the original still stands", "the person",
          PERSON),
)

# The live demo stops at a typed sign-off; the stages from approval on are
# production
BOUNDARY = Boundary(1, "Live demo", "Production workflow")


def governance_lifecycle_html():
    """Return the governance lifecycle diagram as HTML for st.markdown."""
    return flow_html(
        LABEL, STAGES, boundary=BOUNDARY,
        heading="Review is the start of the record, not the end.",
        note="The live demo stops at a typed sign-off. The stages beyond show the "
             "production model this design is built towards.",
        key=((PERSON, "You"), (RECORD, "The immutable record")))
