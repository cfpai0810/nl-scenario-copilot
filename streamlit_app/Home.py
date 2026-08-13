# =============================================================================
# Home.py: the app shell and landing page (Streamlit entry point)
# =============================================================================
# Run locally: streamlit run streamlit_app/Home.py
# The landing page: header, the governance flow diagram (the tool's thesis,
# shown once as a diagram rather than repeated in prose), the key gate, and a
# sample-data check. The tool pages come next.
#
# Streamlit >= 1.36 uses st.navigation + st.Page for multipage apps.
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css, DARK_BLUE
from streamlit_app.lib.key_gate import render_key_gate
from streamlit_app.lib.governance_flow import governance_flow_svg


def _home_page():
    """Render the landing page content."""
    st.markdown(inject_css(), unsafe_allow_html=True)

    # ── Header ───────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="sc-header">'
        '<h1>Scenario Modelling Copilot</h1>'
        '<p>Describe a financial what-if in plain language, and watch it move '
        'through every safeguard before you rely on the result.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── The governance flow (the thesis, shown once as a diagram) ────────────
    import streamlit.components.v1 as components
    components.html(governance_flow_svg(), height=220, scrolling=False)

    # ── The key gate (sidebar): returns a per-session client or None ─────────
    client = render_key_gate()
    st.session_state["live_client"] = client

    # ── What this is ─────────────────────────────────────────────────────────
    st.markdown("#### What this does")
    st.write(
        "Finance teams ask what-if questions all the time. What happens to "
        "operating profit if we cut marketing by twenty percent, or if revenue "
        "growth comes in two points below plan? Answering well usually means "
        "opening the model, editing a driver, and reading the result back "
        "carefully. This tool lets you ask the question in a sentence instead.")
    st.write(
        "You describe a change in plain language. The tool turns it into a "
        "structured scenario, runs it through a genuine driver-based forecast, "
        "and explains what happens to revenue and operating profit. One "
        "principle holds the whole thing together, and it is the reason the "
        "output can be trusted: the language model reads your request and "
        "writes the explanation, a deterministic engine performs every "
        "calculation, and a person approves the outcome. The diagram above is "
        "that principle in full. The intelligence sits at the edges; the "
        "numbers in the middle are computed the way a spreadsheet would compute "
        "them, and never by the model.")

    # ── Sample-data status (the acceptance check) ────────────────────────────
    st.markdown("#### The sample company")
    st.write(
        "Everything here runs on Valencia Operations, a synthetic company "
        "invented for the demo. Its five data files describe a full history of "
        "monthly actuals through mid-2026 and the drivers behind them, and the "
        "tool forecasts the second half of the year on top of that history. "
        "Because the company and its numbers are entirely fictional, nothing "
        "you do on this site touches real or personal information.")

    DATA_DIR = ROOT / "data"
    expected = [
        "actuals_ytd.csv", "driver_table.csv", "operational_actuals.csv",
        "headcount_schedule.csv", "customer_targets.csv",
    ]
    present = [f for f in expected if (DATA_DIR / f).exists()]
    if len(present) == len(expected):
        st.success(
            f"Sample dataset loaded, {len(present)} of {len(expected)} files "
            "present. All figures on this site are illustrative.")
    else:
        missing = [f for f in expected if f not in present]
        st.warning(
            "The sample data is incomplete. Missing: " + ", ".join(missing) +
            ". The tool pages need these files in the project's data folder.")

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
            "What it costs. Each run makes two short calls to Claude Sonnet, "
            "one to read your request and one to explain the result. At "
            "Sonnet's current rate of about three US dollars per million input "
            "tokens and fifteen per million output tokens, a single scenario is "
            "well under one US cent in practice. It is a small amount, but it "
            "is not zero, so it is worth setting a spending limit in the "
            "console if you plan to run many.")
        st.write(
            "For a complete guide to running the tool on your own figures, "
            "including the data file layout and common pitfalls, see "
            "Your own data in the sidebar.")
        st.caption(
            "Your key is used only for your session and is not stored by this "
            "site. On the web the data stays sample-only; to work on your own "
            "numbers, run the project locally from GitHub.")

    # ── Where to start ───────────────────────────────────────────────────────
    st.markdown("#### Where to start")
    st.write(
        "If you want the reasoning before the tools, read How the model works "
        "next; it explains what each driver is and how to phrase a request the "
        "tool can run. If you would rather try it, open Single what-if or "
        "Three-case from the sidebar. Either tool shows a worked example "
        "without a key, or you can paste your own Anthropic key to run your own "
        "analysis. On the web the data stays sample-only; to work on your own "
        "numbers, download the project from GitHub and run it on your machine.")

    st.divider()
    st.caption("Sample data only. All figures are illustrative.")


# ── Multipage navigation (Streamlit >= 1.36) ─────────────────────────────────
st.set_page_config(
    page_title="Scenario Modelling Copilot",
    page_icon="•",
    layout="centered",
    initial_sidebar_state="expanded",
)

PAGES_DIR = Path(__file__).resolve().parent / "pages"

pg = st.navigation([
    st.Page(_home_page, title="Home", icon=":material/home:"),
    st.Page(str(PAGES_DIR / "how_the_model_works.py"),
            title="How the model works", icon=":material/menu_book:"),
    st.Page(str(PAGES_DIR / "single_what_if.py"),
            title="Single what-if", icon=":material/tune:"),
    st.Page(str(PAGES_DIR / "three_case.py"),
            title="Three-case", icon=":material/analytics:"),
    st.Page(str(PAGES_DIR / "your_own_data.py"),
            title="Your own data", icon=":material/upload_file:"),
    st.Page(str(PAGES_DIR / "how_its_built.py"),
            title="How it's built", icon=":material/build:"),
])

pg.run()
