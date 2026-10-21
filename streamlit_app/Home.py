# =============================================================================
# Home.py: the app shell and landing page (Streamlit entry point)
# =============================================================================
# Run locally: streamlit run streamlit_app/Home.py
# The landing page, in the series pattern: the banner, the one button to the
# worked example, the key gate, then what it does, the governance promise,
# the pages and the key explainer. The governance flow diagram is at the top
# of How it's built.
#
# Streamlit >= 1.36 uses st.navigation + st.Page for multipage apps.
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css, header_html
from streamlit_app.lib.key_gate import render_key_gate


def _home_page():
    """Render the landing page content."""
    st.markdown(inject_css(), unsafe_allow_html=True)

    st.markdown(header_html(
        "Scenario Modelling Copilot",
        "Describe a financial what-if in plain language, and watch it move "
        "through every safeguard before you rely on the result."),
        unsafe_allow_html=True)

    # The worked example, one click away and needing no key: the Single
    # what-if page opens on it, its first tab.
    if st.button("See the worked example"):
        st.switch_page(SINGLE_WHAT_IF)

    # The key gate (sidebar): returns a per-session client or None
    client = render_key_gate()
    st.session_state["live_client"] = client

    st.markdown("#### What it does")
    st.markdown(
        "- Takes one change to the plan in plain language, or one to three "
        "drivers to flex across pessimistic, realistic and optimistic cases.\n"
        "- Turns it into a structured scenario that you confirm before the "
        "forecast runs.\n"
        "- Runs it through the driver-based forecast and compares the result "
        "with the base plan, line by line.\n"
        "- The model explains the result.\n"
        "- Produces a PDF report. On the web a finished run stays a draft for "
        "review.")

    st.markdown("#### The governance promise")
    st.markdown(
        "- **The model reads.** It turns your sentence into a scenario, and you "
        "confirm it.\n"
        "- **Python calculates.** Every reported figure is computed in Python, "
        "never by the model.\n"
        "- **The model explains.** It writes the wording around figures it was "
        "given. This version does not check the numbers in that wording.\n"
        "- **A person reviews.** This version has no sign-off step on the web, so "
        "nothing here is signed or final.")

    st.markdown("#### Pages")
    st.page_link(HOW_MODEL_WORKS, label="How the model works: the drivers behind "
                 "the forecast, and how to phrase a request the tool can run")
    st.page_link(SINGLE_WHAT_IF, label="Single what-if: a worked example, and "
                 "one change of your own in plain language")
    st.page_link(THREE_CASE, label="Three-case: a worked example, and "
                 "pessimistic, realistic and optimistic cases for up to three "
                 "drivers")
    st.page_link(OWN_DATA, label="Your own data: the five data files, and how "
                 "to run the project locally on your own figures")
    st.page_link(HOW_ITS_BUILT, label="How it's built: the governance flow, "
                 "the pipeline, the AI boundary and the tests")

    # ── Using your own API key ──────────────────────────────────────────────
    with st.expander("Using your own API key"):
        st.write(
            "The worked examples on each tool are free and need no key. To run "
            "your own what-if, you supply your own Anthropic API key, and each "
            "run bills your Anthropic account directly. This is not a free AI "
            "service; it is your key and your usage.")
        st.write(
            "To get a key, sign up at the Anthropic Console "
            "(console.anthropic.com, which now opens the same developer console "
            "as platform.claude.com), add a payment method under Settings, then "
            "open Settings and API keys and create a key. The key is shown "
            "once, so copy it when it is created. Paste it into the sidebar "
            "here to run a live analysis.")
        st.write(
            "What it costs. A single what-if makes two calls to Claude Sonnet, "
            "one to read your request and one to explain the result; a "
            "three-case run makes one call, to explain the result. At the "
            "rates set in this app, three US dollars per million input tokens "
            "and fifteen per million output tokens, the worked single what-if "
            "(1,926 input and 348 output tokens) costs about 1.1 US cents, and "
            "the worked three-case run (327 input and 402 output tokens) about "
            "0.7 US cents. It is a small amount, but it is not zero, so it is "
            "worth setting a spending limit in the console if you plan to run "
            "many.")
        st.write(
            "For a complete guide to running the tool on your own figures, "
            "including the data file layout and common pitfalls, see "
            "Your own data in the sidebar.")
        st.caption(
            "Your key is used only for your session and is not stored by this "
            "site. On the web the data stays sample-only; to work on your own "
            "numbers, run the project locally from GitHub.")

    st.divider()
    st.caption("Sample data only: Valencia Operations is a fictional company. "
               "All figures are illustrative.")

    # The warning shows only when a sample data file is missing.
    DATA_DIR = ROOT / "data"
    expected = [
        "actuals_ytd.csv", "driver_table.csv", "operational_actuals.csv",
        "headcount_schedule.csv", "customer_targets.csv",
    ]
    missing = [f for f in expected if not (DATA_DIR / f).exists()]
    if missing:
        st.warning(
            "The sample data is incomplete. Missing: " + ", ".join(missing) +
            ". The tool pages need these files in the project's data folder.")


# ── Multipage navigation (Streamlit >= 1.36) ─────────────────────────────────
st.set_page_config(
    page_title="Scenario Modelling Copilot",
    page_icon=":material/tune:",
    layout="centered",
    initial_sidebar_state="auto",
)

PAGES_DIR = Path(__file__).resolve().parent / "pages"

# Each page's address comes from its file name, and links to them are
# published: rename a title, never a file.
HOW_MODEL_WORKS = st.Page(str(PAGES_DIR / "how_the_model_works.py"),
                          title="How the model works", icon=":material/menu_book:")
SINGLE_WHAT_IF = st.Page(str(PAGES_DIR / "single_what_if.py"),
                         title="Single what-if", icon=":material/tune:")
THREE_CASE = st.Page(str(PAGES_DIR / "three_case.py"),
                     title="Three-case", icon=":material/analytics:")
OWN_DATA = st.Page(str(PAGES_DIR / "your_own_data.py"),
                   title="Your own data", icon=":material/upload_file:")
HOW_ITS_BUILT = st.Page(str(PAGES_DIR / "how_its_built.py"),
                        title="How it's built", icon=":material/build:")

pg = st.navigation([
    st.Page(_home_page, title="Home", icon=":material/home:"),
    HOW_MODEL_WORKS,
    SINGLE_WHAT_IF,
    THREE_CASE,
    OWN_DATA,
    HOW_ITS_BUILT,
])

pg.run()
