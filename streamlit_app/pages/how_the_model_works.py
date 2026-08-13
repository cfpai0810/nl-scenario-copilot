# =============================================================================
# pages/how_the_model_works.py: driver reference for the forecast model
# =============================================================================
# Explains how each P&L line is calculated, what driver controls it, and what
# kinds of changes work. No API key needed. This page answers the question
# every user hits eventually: "why did the tool refuse my request?"
# =============================================================================

import sys
from pathlib import Path

import streamlit as st
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css, DARK_BLUE, MUTED
from config import CURRENCY_CODE

st.markdown(inject_css(), unsafe_allow_html=True)

st.markdown("### How the model works")
st.write(
    "The forecast is a driver-based model, which means every line of the "
    "profit and loss is produced by a specific lever rather than typed in by "
    "hand. Revenue comes from a growth rate, COGS from a margin, IT from a "
    "fixed monthly cost, and so on. When you describe a what-if, the tool's job "
    "is to map your sentence onto one of these levers and change it. Knowing "
    "what each lever is, and what units it takes, is the whole trick to phrasing "
    "a request the tool can run, which is what the rest of this page is for.")

st.markdown("---")

# ── The sample company and horizon ───────────────────────────────────────────
st.markdown("#### The numbers behind it")
st.write(
    "Everything runs on Valencia Operations, a synthetic company built for the "
    "demo. It has a full history of monthly actuals running through June 2026, "
    "and the tool forecasts the six months from July to December on top of that "
    "history. That split matters when you read a result: the first half of the "
    "year is settled actuals that no scenario can change, and the second half "
    "is the forecast your what-if actually moves. The full-year view you see in "
    "a result stitches the two together, so you are always looking at actuals "
    "plus your scenario, not a forecast floating on its own.")

st.markdown("---")

# ── P&L structure ────────────────────────────────────────────────────────────
st.markdown("#### The P&L lines and their drivers")
st.write(
    "Each line below is controlled by one driver. The units column is the part "
    "worth reading closely, because most requests the tool cannot run fail on "
    "units rather than on intent.")

lines = [
    {
        "P&L line": "Revenue",
        "Driver": "Annual growth rate (YoY)",
        "Units": "Fraction (e.g. 0.12 = 12%)",
        "What you can say": "\"Set revenue growth to 8 percent\", "
        "\"Increase revenue growth by 3 percentage points\"",
    },
    {
        "P&L line": "COGS",
        "Driver": "Margin (% of Revenue)",
        "Units": "Fraction (e.g. 0.42 = 42%)",
        "What you can say": "\"Set COGS margin to 45 percent\", "
        "\"Increase COGS by 3 percentage points\"",
    },
    {
        "P&L line": "Personnel Cost",
        "Driver": "Headcount schedule",
        "Units": "New hires per month (count)",
        "What you can say": "\"Cut hiring by 50 percent\", "
        "\"Delay hiring by 2 months\", "
        "\"Set new hires to 3 per month\"",
    },
    {
        "P&L line": "Marketing Spend",
        "Driver": "Customer acquisition targets",
        "Units": "Target new customers per month",
        "What you can say": "\"Cut marketing spend by 20 percent\", "
        "\"Reduce target customers by 30 percent\"",
    },
    {
        "P&L line": "IT Infrastructure",
        "Driver": "Fixed cost per month",
        "Units": "{} per month".format(CURRENCY_CODE),
        "What you can say": "\"Add 5000 per month to IT\", "
        "\"Cut IT infrastructure by 10 percent\"",
    },
    {
        "P&L line": "R&D Expense",
        "Driver": "Monthly growth rate",
        "Units": "Fraction (e.g. 0.06 = 6%)",
        "What you can say": "\"Set R&D growth to 4 percent\", "
        "\"Reduce R&D growth by 2 percentage points\"",
    },
]

st.dataframe(pd.DataFrame(lines), use_container_width=True, hide_index=True)

# ── Common mistakes ──────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("#### Common mistakes and what to do instead")
st.write(
    "When the tool asks you to rephrase, it is almost always because the number "
    "you gave does not match the unit the driver expects. These are the four "
    "that come up most, and each has a simple fix.")

mistakes = [
    ("\"Increase COGS by 20,000\"",
     "COGS is a margin percentage, not a euro amount. "
     "Say \"increase COGS by 3 percentage points\" or \"set COGS margin to 45 percent\"."),
    ("\"Add 30,000 to marketing\"",
     "Marketing is driven by customer targets, not a euro budget. "
     "Say \"increase marketing spend by 20 percent\" to scale the target customers."),
    ("\"Increase IT infrastructure by 50,000\"",
     "IT is a per-month cost. Adding 50,000 per month means 600,000 per year. "
     "Specify the time basis: \"add 4,000 per month to IT\" or "
     "\"increase IT infrastructure by 10 percent\"."),
    ("\"Set revenue growth to 12\"",
     "Growth rates are fractions, not whole numbers. 12% growth is 0.12. "
     "Say \"set revenue growth to 12 percent\" and the tool converts it for you."),
]

for request, fix in mistakes:
    st.markdown(f"**{request}**")
    st.caption(fix)

# ── How derived lines work ───────────────────────────────────────────────────
st.markdown("---")
st.markdown("#### The lines you cannot change directly")
st.write(
    "Three lines are not drivers at all. They are computed from the ones above, "
    "so you change them by changing their inputs rather than by naming them.")
st.write(
    "**Gross Profit** is Revenue minus COGS. It moves when you change Revenue "
    "or COGS, never on its own.")
st.write(
    "**Total OpEx** is the sum of Personnel Cost, Marketing Spend, IT "
    "Infrastructure, and R&D Expense.")
st.write(
    "**Operating Profit (EBIT)** is Gross Profit minus Total OpEx. It is the "
    "bottom line of the model and the figure the tool leads with in every "
    "analysis, because it is where a scenario's whole effect lands.")

# ── The important assumption ─────────────────────────────────────────────────
st.markdown("---")
st.markdown("#### One assumption worth knowing")
st.info(
    "Marketing Spend is driven by customer acquisition targets, but Revenue is "
    "modelled on its own seasonal trend, and the two are not linked in this "
    "model. Changing marketing spend does not change revenue. That is a real "
    "simplification, so the tool flags it whenever it applies, and a cut to "
    "marketing should be read as an upper bound on the profit benefit rather "
    "than the full picture.")

st.divider()
st.caption(
    "This page describes the sample model. Your own model may have different "
    "drivers and line items.")
