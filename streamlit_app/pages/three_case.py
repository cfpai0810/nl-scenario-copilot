# =============================================================================
# pages/three_case.py - pessimistic / realistic / optimistic analysis
# =============================================================================
# Structured controls: pick drivers, derive a spread, optionally edit it,
# confirm, and run. Reuses the tested step7/step8/step6 mechanics - the page
# only orchestrates and renders.
#
# Session-state keys are prefixed tc_ to avoid collision with the single
# what-if page which shares the same session_state namespace.
# =============================================================================

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
from matplotlib import pyplot as plt

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css, badge_html
from streamlit_app.lib.key_gate import render_key_gate
from streamlit_app.lib.errors import friendly_message
from streamlit_app.lib import audit
from streamlit_app.lib.web_emit import three_case_pdf_bytes, three_case_csv_bytes
from streamlit_app.lib.example_three_case import THREE_CASE_EXAMPLE
from streamlit_app.lib.cost import estimate_cost, format_cost
from streamlit_app.lib.charts import build_three_case_chart, chart_png_bytes
from streamlit_app.lib.explanation_cards import render_explanation_cards

from config import (
    LINE_ITEMS, DRIVER_DIRECTION, SCHEDULE_DRIVER_TYPES,
    PRESET_BANDS, EBIT_LABEL, CURRENCY_SYMBOL,
)
from src.step6_explainer import format_case_value
from src.step7_scenario_spread import derive_spread, preset_spread, history_rates_for
from src.step8_three_case import (
    run_three_case, multi_case_deltas, case_ebit_summary, spread_base_for,
    build_case_monthly_ebit, CASE_ORDER,
)

st.markdown(inject_css(), unsafe_allow_html=True)

DRIVER_MENU = [
    ("Revenue",           "Revenue growth"),
    ("COGS",              "COGS margin"),
    ("R&D Expense",       "R&D growth"),
    ("IT Infrastructure", "IT Infrastructure (fixed cost)"),
    ("Personnel Cost",    "Personnel hires"),
    ("Marketing Spend",   "Marketing customers"),
]
LABEL_TO_LI = {label: li for li, label in DRIVER_MENU}


def _band_for(line_item, level):
    kind = "schedule" if LINE_ITEMS[line_item] in SCHEDULE_DRIVER_TYPES else "value"
    return PRESET_BANDS[kind][level]


st.markdown("### Three-case analysis")
st.write(
    "Pick one to three drivers to flex across pessimistic, realistic, and "
    "optimistic cases. The tool derives the cases from historical data where "
    "possible, lets you adjust them, then runs all three.")

client = render_key_gate()


@st.cache_resource(show_spinner="Loading the sample model...")
def _load_base():
    from main import load_base_model
    return load_base_model()


try:
    base = _load_base()
except Exception as exc:
    st.error("Could not load the sample model. " + friendly_message(exc))
    st.stop()

# ── Session state (tc_ prefix) ──────────────────────────────────────────────
for key, default in [("tc_phase", "pick"), ("tc_spreads", None),
                     ("tc_result", None), ("tc_line_items", None),
                     ("tc_spread_level", "as derived")]:
    if key not in st.session_state:
        st.session_state[key] = default


def _tc_reset():
    st.session_state.tc_phase = "pick"
    st.session_state.tc_spreads = None
    st.session_state.tc_result = None
    st.session_state.tc_line_items = None
    st.session_state.tc_spread_level = "as derived"
    st.session_state.pop("tc_spread_editor", None)


st.write("")
tab_live, tab_example = st.tabs(["Run it yourself", "View a worked example"])

# ── Keyless worked example ───────────────────────────────────────────────────
with tab_example:
    ex = THREE_CASE_EXAMPLE
    st.caption("This is a saved example run. It needs no API key.")
    st.markdown("**Drivers flexed:** " + ", ".join(ex["drivers"]))

    e = ex["ebit"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Pessimistic", "{}{:,.0f}".format(CURRENCY_SYMBOL, e["pessimistic"]["value"]),
              delta="{}{:+,.0f}".format(CURRENCY_SYMBOL, e["pessimistic"]["delta"]))
    c2.metric("Realistic (base)", "{}{:,.0f}".format(CURRENCY_SYMBOL, e["realistic"]["value"]))
    c3.metric("Optimistic", "{}{:,.0f}".format(CURRENCY_SYMBOL, e["optimistic"]["value"]),
              delta="{}{:+,.0f}".format(CURRENCY_SYMBOL, e["optimistic"]["delta"]))

    if ex.get("case_monthly"):
        _fig = build_three_case_chart(ex["case_monthly"])
        st.pyplot(_fig)
        plt.close(_fig)

    st.divider()

    st.markdown("**Cases**")
    st.dataframe(pd.DataFrame(ex["spreads"]).rename(columns={
        "driver": "Driver", "pessimistic": "Pessimistic",
        "realistic": "Realistic", "optimistic": "Optimistic"}),
        use_container_width=True, hide_index=True)
    st.caption(ex["basis"])

    st.markdown("**EBIT by case**")
    ebit_row = ex["rows"][0]
    st.dataframe(pd.DataFrame([{
        "Line": ebit_row["line"],
        "Base": "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit_row["base"]),
        "Pessimistic": "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit_row["Pessimistic"]),
        "Realistic": "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit_row["Realistic"]),
        "Optimistic": "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit_row["Optimistic"]),
    }]), use_container_width=True, hide_index=True)

    st.divider()

    render_explanation_cards(ex.get("takeaways"), ex["explanation"])

    if ex.get("tokens_in") is not None:
        st.caption(format_cost(ex["tokens_in"], ex["tokens_out"]))

# ── Live path ────────────────────────────────────────────────────────────────
with tab_live:
    if client is None:
        st.info(
            "Paste your Anthropic API key in the sidebar to run your own "
            "three-case analysis, or open the worked example tab.")

    # ── Phase: pick ──────────────────────────────────────────────────────
    if st.session_state.tc_phase == "pick":
        picked_labels = st.multiselect(
            "Which drivers do you want to flex? (1 to 3)",
            options=[label for _, label in DRIVER_MENU])
        derive_btn = st.button("Derive cases", type="primary")
        if derive_btn:
            if not picked_labels:
                st.warning("Pick at least one driver.")
            elif len(picked_labels) > 3:
                st.warning("Keep it to three drivers or fewer.")
            elif client is None:
                st.warning("Paste your API key in the sidebar first.")
            else:
                line_items = [LABEL_TO_LI[l] for l in picked_labels]
                spreads = {}
                for li in line_items:
                    dtype = LINE_ITEMS[li]
                    direction = DRIVER_DIRECTION[li]
                    base_val = spread_base_for(li, base["drivers_df"])
                    rates = history_rates_for(li, dtype, base["actuals_df"])
                    sp = derive_spread(dtype, base_val, rates, direction)
                    if sp is None:
                        sp = preset_spread(base_val, _band_for(li, 0), direction)
                    spreads[li] = sp
                st.session_state.tc_line_items = line_items
                st.session_state.tc_spreads = spreads
                st.session_state.tc_spread_level = "as derived"
                st.session_state.tc_phase = "preview"
                st.session_state.pop("tc_spread_editor", None)
                st.rerun()

    # ── Phase: preview (editable spread table) ───────────────────────────
    if st.session_state.tc_phase == "preview" and st.session_state.tc_spreads:
        spreads = st.session_state.tc_spreads
        line_items = st.session_state.tc_line_items

        st.markdown("---")
        st.markdown("#### Cases to run")
        any_narrow = any(sp.get("narrow") for sp in spreads.values())

        spread_level = st.radio(
            "Spread",
            ["as derived", "moderate band", "wide band"],
            index=["as derived", "moderate band", "wide band"].index(
                st.session_state.tc_spread_level),
            horizontal=True)

        if spread_level != st.session_state.tc_spread_level:
            st.session_state.tc_spread_level = spread_level
            if spread_level != "as derived":
                level = 0 if spread_level == "moderate band" else 1
                for li in line_items:
                    base_val = spread_base_for(li, base["drivers_df"])
                    direction = DRIVER_DIRECTION[li]
                    spreads[li] = preset_spread(
                        base_val, _band_for(li, level), direction)
            else:
                for li in line_items:
                    dtype = LINE_ITEMS[li]
                    direction = DRIVER_DIRECTION[li]
                    base_val = spread_base_for(li, base["drivers_df"])
                    rates = history_rates_for(li, dtype, base["actuals_df"])
                    sp = derive_spread(dtype, base_val, rates, direction)
                    if sp is None:
                        sp = preset_spread(base_val, _band_for(li, 0), direction)
                    spreads[li] = sp
            st.session_state.tc_spreads = spreads
            st.session_state.pop("tc_spread_editor", None)
            st.rerun()

        rows = []
        for li, sp in spreads.items():
            rows.append({
                "Driver": li,
                "Pessimistic": format_case_value(li, sp["pessimistic"]),
                "Realistic": format_case_value(li, sp["realistic"]),
                "Optimistic": format_case_value(li, sp["optimistic"]),
            })
        display_df = pd.DataFrame(rows)

        if any_narrow:
            st.caption(
                "Some spreads come from very stable history and sit close "
                "together. Consider widening them.")

        _parts = []
        for li, sp in spreads.items():
            _dtype = LINE_ITEMS[li]
            _is_sched = _dtype in SCHEDULE_DRIVER_TYPES
            _spread = sp.get("std_dev") or sp.get("band")
            if _spread is not None:
                if _is_sched:
                    _parts.append("{} +/-{:.2f}x".format(li, _spread))
                else:
                    _parts.append("{} +/-{:.2%}".format(li, _spread))
        if all(sp.get("mode") == "data-derived"
               for sp in spreads.values()):
            _lead = ("Each case is one historical standard deviation "
                     "either side of the base, from the variation in "
                     "the actuals")
        elif all(sp.get("mode") == "preset-band"
                 for sp in spreads.values()):
            _lead = ("Each case uses a preset band either side of "
                     "the base")
        else:
            _lead = ("Cases are set from history where available, "
                     "preset band otherwise")
        if _parts:
            st.caption("{}: {}.".format(_lead, ", ".join(_parts)))
        else:
            st.caption(_lead + ".")

        edited = st.data_editor(
            display_df, key="tc_spread_editor",
            use_container_width=True, hide_index=True,
            column_config={
                "Driver": st.column_config.TextColumn(disabled=True),
                "Pessimistic": st.column_config.TextColumn(),
                "Realistic": st.column_config.TextColumn(),
                "Optimistic": st.column_config.TextColumn(),
            })

        st.write("")
        st.caption("The realistic case is the base plan. Edit any cell to "
                   "override a derived value. Confirm to run all three cases.")

        c1, c2 = st.columns([1, 1])
        confirm = c1.button("Confirm and run", type="primary")
        back = c2.button("Back")
        if back:
            _tc_reset(); st.rerun()

        if confirm:
            from streamlit_app.lib.threecase_table import (
                rebuild_cases_from_table, transpose_to_cases)
            cases_by_driver, errors = rebuild_cases_from_table(
                edited, spreads)
            if errors:
                for msg in errors:
                    st.warning(msg)
            else:
                cases = transpose_to_cases(cases_by_driver)
                with st.spinner("Running all three cases and explaining..."):
                    try:
                        from src.step6_explainer import (
                            build_three_case_takeaways,
                            call_claude_explain_three_case,
                        )
                        results = run_three_case(base, cases)
                        case_monthly = build_case_monthly_ebit(
                            results, base["forecast_periods"])
                        delta_rows = multi_case_deltas(results)
                        ebit = case_ebit_summary(delta_rows)
                        takeaways = build_three_case_takeaways(
                            delta_rows, list(spreads.keys()))
                        basis_text = "; ".join(
                            sorted({sp["basis"] for sp in spreads.values()}))
                        explanation, explain_in, explain_out = \
                            call_claude_explain_three_case(
                                delta_rows, spreads, basis_text, client=client)

                        cost_est = estimate_cost(explain_in, explain_out)
                        audit.record_run(
                            "Three-case: " + ", ".join(line_items),
                            "Flexed {} across three cases".format(
                                ", ".join(line_items)),
                            "approved",
                            ebit_delta=(ebit["Optimistic_delta"]
                                        if ebit else None),
                            analysis_type="three-case",
                            tokens_in=explain_in,
                            tokens_out=explain_out,
                            cost_estimate=cost_est)

                        st.session_state.tc_result = {
                            "spreads": spreads,
                            "delta_rows": delta_rows,
                            "ebit": ebit,
                            "case_monthly": case_monthly,
                            "takeaways": takeaways,
                            "explanation": explanation,
                            "basis_text": basis_text,
                            "line_items": line_items,
                            "tokens_in": explain_in,
                            "tokens_out": explain_out,
                        }
                        st.session_state.tc_phase = "result"
                        st.rerun()
                    except Exception as exc:
                        st.error(friendly_message(exc))

    # ── Phase: result ────────────────────────────────────────────────────
    if st.session_state.tc_phase == "result" and st.session_state.tc_result:
        res = st.session_state.tc_result
        st.markdown("---")
        st.markdown(badge_html("approved"), unsafe_allow_html=True)

        ebit = res["ebit"]
        if ebit:
            c1, c2, c3 = st.columns(3)
            c1.metric("Pessimistic",
                      "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit["Pessimistic"]),
                      delta="{}{:+,.0f}".format(CURRENCY_SYMBOL, ebit["Pessimistic_delta"]))
            c2.metric("Realistic (base)",
                      "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit["Realistic"]))
            c3.metric("Optimistic",
                      "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit["Optimistic"]),
                      delta="{}{:+,.0f}".format(CURRENCY_SYMBOL, ebit["Optimistic_delta"]))

        if res.get("case_monthly"):
            _fig = build_three_case_chart(res["case_monthly"])
            st.pyplot(_fig)
            plt.close(_fig)

        st.divider()
        st.markdown("#### Profit and loss across cases")

        df = pd.DataFrame([{
            "Line": r["line"],
            "Base": "{}{:,.0f}".format(CURRENCY_SYMBOL, r["base"]),
            "Pessimistic": "{}{:,.0f}".format(CURRENCY_SYMBOL, r["Pessimistic"]),
            "Realistic": "{}{:,.0f}".format(CURRENCY_SYMBOL, r["Realistic"]),
            "Optimistic": "{}{:,.0f}".format(CURRENCY_SYMBOL, r["Optimistic"]),
        } for r in res["delta_rows"]])
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.caption("The realistic case is the base plan, so its figures "
                   "match the base by design.")

        st.divider()

        render_explanation_cards(res["takeaways"], res["explanation"])

        if res.get("tokens_in") is not None:
            st.caption(format_cost(res["tokens_in"], res["tokens_out"]))

        col_pdf, col_csv = st.columns(2)
        try:
            pdf = three_case_pdf_bytes(
                res["spreads"], res["delta_rows"], res["explanation"],
                res["takeaways"], res["basis_text"],
                case_monthly=res.get("case_monthly"))
            col_pdf.download_button(
                "Download report (PDF)", data=pdf,
                file_name="three_case_analysis.pdf",
                mime="application/pdf")
        except Exception:
            col_pdf.caption("PDF download unavailable.")

        try:
            csv_data = three_case_csv_bytes(res["delta_rows"])
            col_csv.download_button(
                "Download data (CSV)", data=csv_data,
                file_name="three_case_deltas.csv",
                mime="text/csv")
        except Exception:
            col_csv.caption("CSV download unavailable.")

        if st.button("Run another analysis"):
            _tc_reset(); st.rerun()

# ── The session audit record ────────────────────────────────────────────────
runs = audit.get_runs()
if runs:
    st.markdown("---")
    st.markdown("#### This session's audit")
    st.caption("Every run you make is listed here for this session.")
    for r in runs:
        cols = st.columns([2, 5, 2])
        cols[0].markdown(badge_html(r["status"]), unsafe_allow_html=True)
        cols[1].write(r["raw_request"])
        if r.get("ebit_delta") is not None:
            cols[2].write("EBIT {}{:+,.0f}".format(CURRENCY_SYMBOL, r["ebit_delta"]))
        if r.get("tokens_in") is not None:
            st.caption(format_cost(r["tokens_in"], r["tokens_out"]))
