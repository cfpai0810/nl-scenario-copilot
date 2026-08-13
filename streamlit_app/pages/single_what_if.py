# =============================================================================
# pages/single_what_if.py - the single what-if tool (Stage 3)
# =============================================================================
# Two phases, matching the governance diagram:
#   Phase A: parse + validate + echo_back -> show the INTENT card, then STOP.
#   Phase B: on Confirm -> apply + forecast + deltas + explain -> render.
# Nothing is calculated until the user confirms the interpretation. Results are
# stored in session state and rendered from there, so a rerun (e.g. clicking
# the PDF download) never loses the result. Every run is written to the
# per-session audit record. A keyless example tab shows a full worked result
# with no API key.
#
# All heavy logic is the existing, tested pipeline (src/step3..step6). This
# page only orchestrates it non-blocking and renders; it never recomputes.
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
from streamlit_app.lib.web_emit import scenario_pdf_bytes
from streamlit_app.lib.example_run import EXAMPLE
from streamlit_app.lib.cost import estimate_cost, format_cost
from streamlit_app.lib.charts import (
    build_quarterly_chart, extract_quarterly_ebit, chart_png_bytes,
)
from streamlit_app.lib.explanation_cards import render_explanation_cards

from config import EBIT_LABEL, CURRENCY_SYMBOL

st.markdown(inject_css(), unsafe_allow_html=True)

EXAMPLES = {
    "Cut marketing spend by 20 percent": "Cut marketing spend by 20 percent",
    "Set revenue growth to 8 percent": "Set revenue growth to 8 percent",
    "Cut marketing 20 percent and delay hiring one month":
        "Cut marketing spend by 20 percent and delay hiring by one month",
}

st.markdown("### Single what-if")
st.write(
    "Describe one change to the plan in plain language. The model shows you "
    "how it understood the request, you confirm it, and only then is anything "
    "calculated.")

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

# ── Session state ────────────────────────────────────────────────────────────
if "phase" not in st.session_state:
    st.session_state.phase = "input"     # input -> confirm -> result
if "parsed" not in st.session_state:
    st.session_state.parsed = None
if "result" not in st.session_state:
    st.session_state.result = None       # the persisted computed result


def _reset():
    st.session_state.phase = "input"
    st.session_state.parsed = None
    st.session_state.result = None
    st.session_state.pop("permonth_editor", None)
    st.session_state.pop("permonth_editor_uni", None)


st.write("")
tab_live, tab_example = st.tabs(["Run it yourself", "View a worked example"])

# ── Keyless worked example ───────────────────────────────────────────────────
with tab_example:
    st.caption("This is a saved example run. It needs no API key.")
    st.markdown(f"**Request:** {EXAMPLE['request']}")
    st.markdown(
        f'**The model understood:** {EXAMPLE["echo"]} '
        + badge_html("approved"), unsafe_allow_html=True)

    e = EXAMPLE["ebit"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Base EBIT (forecast)", "{}{:,.0f}".format(CURRENCY_SYMBOL, e["base"]))
    c2.metric("Scenario EBIT (forecast)", "{}{:,.0f}".format(CURRENCY_SYMBOL, e["scenario"]))
    c3.metric("Change", "{}{:+,.0f}".format(CURRENCY_SYMBOL, e["delta"]),
              delta="{:+.1%}".format(e["pct"]))

    if EXAMPLE.get("chart_quarterly"):
        _cq = EXAMPLE["chart_quarterly"]
        _fig = build_quarterly_chart(_cq)
        st.pyplot(_fig)
        plt.close(_fig)
        if _cq.get("fy_base") is not None:
            st.caption(
                "Full-year EBIT moves from {}{:,.0f} to {}{:,.0f} "
                "({}{:+,.0f}).".format(
                    CURRENCY_SYMBOL, _cq["fy_base"],
                    CURRENCY_SYMBOL, _cq["fy_scenario"],
                    CURRENCY_SYMBOL,
                    _cq["fy_scenario"] - _cq["fy_base"]))

    st.divider()

    st.markdown("**Impact on the plan**")
    ex_df = pd.DataFrame([{
        "Line": d["line"],
        "Base": "{}{:,.0f}".format(CURRENCY_SYMBOL, d["base"]),
        "Scenario": "{}{:,.0f}".format(CURRENCY_SYMBOL, d["scenario"]),
        "Change": "{}{:+,.0f}".format(CURRENCY_SYMBOL, d["delta"]),
        "%": "" if d["pct"] is None else "{:+.1%}".format(d["pct"]),
    } for d in EXAMPLE["deltas"]])
    st.dataframe(ex_df, use_container_width=True, hide_index=True)

    st.divider()

    render_explanation_cards(
        EXAMPLE.get("takeaways"),
        EXAMPLE["explanation"],
        EXAMPLE.get("held_constant"))

    if EXAMPLE.get("tokens_in") is not None:
        st.caption(format_cost(EXAMPLE["tokens_in"], EXAMPLE["tokens_out"]))

# ── Live path ────────────────────────────────────────────────────────────────
with tab_live:
    if client is None:
        st.info(
            "Paste your Anthropic API key in the sidebar to run your own "
            "what-if, or open the worked example tab to see a full result with "
            "no key.")

    with st.form("whatif_form", clear_on_submit=False):
        picked = st.selectbox(
            "Start from an example, or write your own below",
            ["Write my own"] + list(EXAMPLES.keys()))
        typed = st.text_input(
            "Your what-if",
            value="" if picked == "Write my own" else EXAMPLES[picked],
            placeholder="e.g. reduce marketing spend by 20 percent")
        submitted = st.form_submit_button("Interpret this", type="primary")

    if submitted:
        request = typed.strip()
        if not request:
            st.warning("Type a what-if, or pick an example, first.")
        elif client is None:
            st.warning("Paste your API key in the sidebar to run a live analysis.")
        else:
            with st.spinner("Reading your request..."):
                try:
                    from src.step3_scenario_parser import call_claude_parse
                    from src.step4_validator import validate_change, classify_request

                    scenario, parse_in, parse_out = call_claude_parse(
                        request, base["forecast_periods"], client=client)
                    if not scenario or not scenario.get("changes"):
                        st.error(
                            "The request could not be read as a scenario. Try "
                            "rephrasing it, for example 'reduce marketing spend "
                            "by 20 percent'.")
                        _reset()
                    else:
                        results = [validate_change(c, forecast_periods=base["forecast_periods"])
                                   for c in scenario["changes"]]
                        classification = classify_request(results)
                        st.session_state.parsed = {
                            "request": request,
                            "results": results,
                            "classification": classification,
                            "parse_tokens_in": parse_in,
                            "parse_tokens_out": parse_out,
                        }
                        st.session_state.result = None
                        st.session_state.phase = "confirm"
                        st.session_state.pop("permonth_editor", None)
                        st.session_state.pop("permonth_editor_uni", None)
                except Exception as exc:
                    st.error(friendly_message(exc))
                    _reset()

    # ── Intent card + confirm (only when we have a parse and no result yet) ──
    if st.session_state.phase == "confirm" and st.session_state.parsed:
        p = st.session_state.parsed
        classification = p["classification"]

        st.markdown("---")
        st.markdown("#### How the request was understood")

        if classification == "IMPOSSIBLE":
            st.markdown(badge_html("refused"), unsafe_allow_html=True)
            st.error(
                "This request cannot be run as written. The model reads it as "
                "an operation that is not allowed on these figures.")
            for status, reason, _ in p["results"]:
                if status == "ILLEGAL":
                    st.write("- " + reason)
            if not p.get("recorded"):
                cost_est = estimate_cost(
                    p["parse_tokens_in"], p["parse_tokens_out"])
                audit.record_run(
                    p["request"], "Refused before running", "refused",
                    tokens_in=p["parse_tokens_in"],
                    tokens_out=p["parse_tokens_out"],
                    cost_estimate=cost_est)
                p["recorded"] = True
            if st.button("Try another request"):
                _reset(); st.rerun()

        elif classification == "AMBIGUOUS":
            st.markdown(badge_html("caution"), unsafe_allow_html=True)
            st.warning(
                "The request is unclear in a way that could change the result, "
                "so it is not run automatically. Please rephrase it.")
            for status, reason, _ in p["results"]:
                if status == "NEEDS_CLARIFICATION":
                    st.write("- " + reason)
            if not p.get("recorded"):
                cost_est = estimate_cost(
                    p["parse_tokens_in"], p["parse_tokens_out"])
                audit.record_run(
                    p["request"], "Ambiguous, not run", "caution",
                    tokens_in=p["parse_tokens_in"],
                    tokens_out=p["parse_tokens_out"],
                    cost_estimate=cost_est)
                p["recorded"] = True
            if st.button("Rephrase"):
                _reset(); st.rerun()

        else:
            from src.step4_validator import echo_back
            from streamlit_app.lib.permonth_table import (
                _vectored_change, _to_display, _column_caption,
            )
            normalised = [r[2] for r in p["results"] if r[0] == "OK"]
            echo = echo_back(normalised)

            st.markdown(
                f'<div style="background:#F0ECF8;border-left:3px solid #6B4FA8;'
                f'border-radius:8px;padding:14px 16px;">'
                f'<b style="color:#6B4FA8;">The model understood:</b><br>'
                f'{echo}</div>', unsafe_allow_html=True)
            if classification == "TOO_MANY":
                st.caption("That is a lot of changes for one scenario. You can "
                           "still run it.")

            # ── Per-month table (when the parse produced a vector) ──────
            vec_idx, vec_change = _vectored_change(normalised)
            uni_idx = None
            uni_edited = None
            uni_display_df = None
            if vec_change is not None:
                vec_op = vec_change["operation"]
                vec_vbp = vec_change["value_by_period"]
                periods = base["forecast_periods"]
                rows = []
                for pl in periods:
                    machine = vec_vbp.get(pl)
                    rows.append({"Month": pl,
                                 "Change": _to_display(vec_op, machine)})
                display_df = pd.DataFrame(rows)

                st.write("")
                st.markdown("**Per-month changes** "
                            "(edit any month; blank means no change)")
                st.caption(_column_caption(vec_op))
                edited = st.data_editor(
                    display_df, key="permonth_editor",
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Month": st.column_config.TextColumn(disabled=True),
                        "Change": st.column_config.TextColumn()})
            else:
                if len(normalised) == 1:
                    c0 = normalised[0]
                    op0 = c0.get("operation")
                    if (op0 in ("scale_driver", "set_driver", "shift_driver")
                            and c0.get("value") is not None
                            and c0.get("value_by_period") is None):
                        uni_idx = 0
                        periods = base["forecast_periods"]
                        uni_val = c0["value"]
                        rows = [{"Month": pl,
                                 "Change": _to_display(op0, uni_val)}
                                for pl in periods]
                        uni_display_df = pd.DataFrame(rows)
                        with st.expander("Edit this change month by month"):
                            st.caption(
                                "Optional. Each month starts at the value "
                                "you asked for; edit any month to make the "
                                "change vary over the forecast. "
                                + _column_caption(op0))
                            uni_edited = st.data_editor(
                                uni_display_df,
                                key="permonth_editor_uni",
                                use_container_width=True, hide_index=True,
                                column_config={
                                    "Month": st.column_config.TextColumn(
                                        disabled=True),
                                    "Change": st.column_config.TextColumn()})

            st.write("")
            st.caption("Nothing has been calculated yet. Confirm to run the "
                       "figures, or rephrase.")

            c1, c2 = st.columns([1, 1])
            confirm = c1.button("Confirm and run", type="primary")
            rephrase = c2.button("Rephrase")
            if rephrase:
                _reset(); st.rerun()

            if confirm:
                run_normalised = normalised
                run_ok = True

                if vec_change is not None:
                    from streamlit_app.lib.permonth_table import (
                        resolve_run_normalised)
                    run_normalised, errors = resolve_run_normalised(
                        normalised, vec_idx, vec_op, edited, vec_vbp,
                        base["forecast_periods"])
                    if errors:
                        for msg in errors:
                            st.warning(msg)
                        run_ok = False

                elif uni_idx is not None:
                    from streamlit_app.lib.permonth_table import (
                        resolve_run_normalised)
                    baseline = uni_display_df["Change"].tolist()
                    current = uni_edited["Change"].tolist()
                    edited_any = [
                        (str(a).strip() if a is not None else "")
                        != (str(b).strip() if b is not None else "")
                        for a, b in zip(baseline, current)]
                    if any(edited_any):
                        periods = base["forecast_periods"]
                        original_vbp = {pl: normalised[uni_idx]["value"]
                                        for pl in periods}
                        run_normalised, errors = resolve_run_normalised(
                            normalised, uni_idx,
                            normalised[uni_idx]["operation"],
                            uni_edited, original_vbp,
                            base["forecast_periods"])
                        if errors:
                            for msg in errors:
                                st.warning(msg)
                            run_ok = False

                if run_ok:
                    with st.spinner(
                            "Running the forecast and explaining the result..."):
                        try:
                            from src.step5_scenario_engine import (
                                apply_changes, run_forecast, compute_deltas,
                                classify_analysis, headline_deltas)
                            from src.step2_forecast_engine import build_pnl
                            from src.step6_explainer import (
                                build_base_context_text,
                                build_assumptions_rows,
                                build_takeaways, build_monthly_rows,
                                build_quarterly_rows,
                                call_claude_explain)

                            scenario_model, held_constant, base_context, \
                                po, vo = apply_changes(
                                    base, run_normalised)
                            base_pnl = run_forecast(base)
                            scenario_pnl = run_forecast(
                                scenario_model, po, vo)
                            deltas = compute_deltas(base_pnl, scenario_pnl)
                            monthly = build_monthly_rows(
                                base_pnl, scenario_pnl,
                                base["forecast_periods"])

                            fy = base["forecast_periods"][0][:4]
                            adf = base["actuals_df"]
                            actual_periods = sorted(
                                p for p in adf["period"].unique()
                                if p.startswith(fy))
                            actuals_frame = pd.DataFrame([{
                                "period": r["period"],
                                "line_item": r["line_item"],
                                "value": r["actual"],
                                "type": "actual",
                            } for _, r in adf[
                                adf["period"].isin(actual_periods)
                            ].iterrows()])
                            actuals_pnl = build_pnl(
                                actuals_frame, actual_periods,
                                row_type="actual")
                            quarterly = build_quarterly_rows(
                                actuals_pnl, base_pnl, scenario_pnl,
                                actual_periods,
                                base["forecast_periods"],
                                fy_label="FY " + fy)

                            analysis = classify_analysis(run_normalised)
                            headline = headline_deltas(deltas)

                            echo = echo_back(run_normalised)
                            base_ctx_text = build_base_context_text(
                                base_context)
                            assumption_rows = build_assumptions_rows(
                                base_context)
                            takeaways = build_takeaways(deltas, analysis)
                            explanation, explain_in, explain_out = \
                                call_claude_explain(
                                    p["request"], echo, headline, analysis,
                                    held_constant, base_ctx_text, client=client)

                            total_in = p["parse_tokens_in"] + explain_in
                            total_out = p["parse_tokens_out"] + explain_out

                            rev = headline.get("Revenue")
                            ebit = headline.get("EBIT")
                            cost_est = estimate_cost(total_in, total_out)
                            audit.record_run(
                                p["request"], echo, "approved",
                                revenue_delta=(rev["delta"]
                                               if rev else None),
                                ebit_delta=(ebit["delta"]
                                            if ebit else None),
                                held_constant=held_constant,
                                analysis_type=analysis,
                                tokens_in=total_in,
                                tokens_out=total_out,
                                cost_estimate=cost_est)

                            st.session_state.result = {
                                "request": p["request"],
                                "echo": echo,
                                "analysis": analysis,
                                "deltas": deltas,
                                "monthly": monthly,
                                "quarterly": quarterly,
                                "takeaways": takeaways,
                                "explanation": explanation,
                                "held_constant": held_constant,
                                "assumption_rows": assumption_rows,
                                "tokens_in": total_in,
                                "tokens_out": total_out,
                            }
                            st.session_state.phase = "result"
                            st.rerun()

                        except Exception as exc:
                            st.error(friendly_message(exc))

    # ── Render the persisted result (survives reruns) ────────────────────────
    if st.session_state.phase == "result" and st.session_state.result:
        res = st.session_state.result
        st.markdown("---")
        st.markdown(badge_html("approved"), unsafe_allow_html=True)

        ebit_row = next((d for d in res["deltas"]
                         if d["line"] == EBIT_LABEL), None)
        if ebit_row:
            c1, c2, c3 = st.columns(3)
            c1.metric("Base EBIT (forecast)",
                      "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit_row["base"]))
            c2.metric("Scenario EBIT (forecast)",
                      "{}{:,.0f}".format(CURRENCY_SYMBOL, ebit_row["scenario"]))
            c3.metric("Change",
                      "{}{:+,.0f}".format(CURRENCY_SYMBOL, ebit_row["delta"]),
                      delta=("{:+.1%}".format(ebit_row["pct"])
                             if ebit_row["pct"] is not None
                             else None))

        q = res.get("quarterly")
        if q:
            _qebit = extract_quarterly_ebit(q)
            _fig = build_quarterly_chart(_qebit)
            st.pyplot(_fig)
            plt.close(_fig)
            _ebit_ln = next((l for l in q["lines"]
                             if l["line"] == EBIT_LABEL), None)
            if _ebit_ln:
                _fy = q["columns"][-1]["key"]
                st.caption(
                    "Full-year EBIT moves from {}{:,.0f} to {}{:,.0f} "
                    "({}{:+,.0f}).".format(
                        CURRENCY_SYMBOL, _ebit_ln["base"][_fy],
                        CURRENCY_SYMBOL, _ebit_ln["scenario"][_fy],
                        CURRENCY_SYMBOL,
                        _ebit_ln["scenario"][_fy] - _ebit_ln["base"][_fy]))

        st.divider()

        _fp = base["forecast_periods"]
        _mn = ("Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec")
        _horizon = "{}-{} {}".format(
            _mn[int(_fp[0][5:7])-1], _mn[int(_fp[-1][5:7])-1],
            _fp[0][:4])
        st.markdown("#### Impact on the forecast ({})".format(_horizon))

        moved = [d for d in res["deltas"] if d["delta"] != 0]
        show = moved if moved else res["deltas"]
        df = pd.DataFrame([{
            "Line": d["line"],
            "Base": "{}{:,.0f}".format(CURRENCY_SYMBOL, d["base"]),
            "Scenario": "{}{:,.0f}".format(CURRENCY_SYMBOL, d["scenario"]),
            "Change": "{}{:+,.0f}".format(CURRENCY_SYMBOL, d["delta"]),
            "%": "" if d["pct"] is None else "{:+.1%}".format(d["pct"]),
        } for d in show])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("These figures cover the six-month forecast horizon; "
                   "the full-year view below adds the settled H1 actuals.")

        q = res.get("quarterly")
        if q:
            st.markdown("#### Full year with your scenario")
            qcols = q["columns"]

            def _qgrid(which, fmt):
                data = []
                for ln in q["lines"]:
                    row = {"Line": ln["line"]}
                    for c in qcols:
                        row[c["key"]] = fmt(ln[which][c["key"]])
                    data.append(row)
                return pd.DataFrame(data)

            st.dataframe(_qgrid("scenario", lambda v: "{}{:,.0f}".format(CURRENCY_SYMBOL, v)),
                         use_container_width=True, hide_index=True)
            st.caption("Q1-Q2 are actuals; Q3-Q4 are your scenario "
                       "forecast. FY combines them.")

            ebit_ln = next((l for l in q["lines"]
                            if l["line"] == EBIT_LABEL), None)
            if ebit_ln:
                fy_key = qcols[-1]["key"]
                fy_base = ebit_ln["base"][fy_key]
                fy_scen = ebit_ln["scenario"][fy_key]
                st.caption(
                    "Full-year EBIT moves from {}{:,.0f} to {}{:,.0f}, the "
                    "same {}{:+,.0f} as the forecast, because the H1 "
                    "actuals do not change.".format(
                        CURRENCY_SYMBOL, fy_base,
                        CURRENCY_SYMBOL, fy_scen,
                        CURRENCY_SYMBOL, fy_scen - fy_base))

        mo = res.get("monthly")
        if mo:
            with st.expander("Month-by-month detail (forecast H2)"):
                periods = mo["periods"]

                def _grid(which, fmt):
                    data = []
                    for ln in mo["lines"]:
                        row = {"Line": ln["line"]}
                        for p in periods:
                            row[p] = fmt(ln[which][p])
                        data.append(row)
                    return pd.DataFrame(data)

                st.markdown("**Scenario by month**")
                st.dataframe(_grid("scenario", lambda v: "{}{:,.0f}".format(CURRENCY_SYMBOL, v)),
                             use_container_width=True, hide_index=True)

                st.markdown("**Change vs base, by month**")
                st.dataframe(_grid("delta", lambda v: "{}{:+,.0f}".format(CURRENCY_SYMBOL, v)),
                             use_container_width=True, hide_index=True)

                st.markdown("**Base by month**")
                st.dataframe(_grid("base", lambda v: "{}{:,.0f}".format(CURRENCY_SYMBOL, v)),
                             use_container_width=True, hide_index=True)

        st.divider()

        render_explanation_cards(
            res["takeaways"], res["explanation"], res["held_constant"])

        if res.get("tokens_in") is not None:
            st.caption(format_cost(res["tokens_in"], res["tokens_out"]))

        try:
            _qe = None
            if res.get("quarterly"):
                _qe = extract_quarterly_ebit(res["quarterly"])
            pdf = scenario_pdf_bytes(
                res["request"], res["echo"], res["deltas"], res["analysis"],
                res["held_constant"], res["explanation"],
                res["assumption_rows"], res["takeaways"],
                monthly=res.get("monthly"),
                quarterly=res.get("quarterly"),
                quarterly_ebit=_qe)
            st.download_button(
                "Download the report (PDF)", data=pdf,
                file_name="scenario_analysis.pdf", mime="application/pdf")
        except Exception:
            st.caption("The report is shown above; the PDF download is "
                       "unavailable in this session.")

        if st.button("Run another what-if"):
            _reset(); st.rerun()

# ── The session audit record (visible governance) ──────────────────────────
runs = audit.get_runs()
if runs:
    st.markdown("---")
    st.markdown("#### This session's audit")
    st.caption("Every run you make is listed here for this session. It resets "
               "when the session ends. The downloadable version keeps a "
               "permanent record.")
    for r in runs:
        cols = st.columns([2, 5, 2])
        cols[0].markdown(badge_html(r["status"]), unsafe_allow_html=True)
        cols[1].write(r["raw_request"])
        if r.get("ebit_delta") is not None:
            cols[2].write("EBIT {}{:+,.0f}".format(CURRENCY_SYMBOL, r["ebit_delta"]))
        if r.get("tokens_in") is not None:
            st.caption(format_cost(r["tokens_in"], r["tokens_out"]))
