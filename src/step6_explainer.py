# =============================================================================
# step6_explainer.py — EXPLAIN and OUTPUT
# =============================================================================
# Claude narrates the computed result: what changed, what happened to Revenue
# and EBIT, whether it was a sensitivity or a scenario, and any assumption
# held constant. Claude never invents or recomputes a number. This layer also
# owns the output files and the audit trail.
#
#   build_explain_prompt(), call_claude_explain()
#   write_pdf(), export_deltas_csv()   (added in Step 5)
#   write_audit()
# =============================================================================

import json
import hashlib
import re
import anthropic
import pandas as pd
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.units     import cm
from reportlab.lib           import colors
from reportlab.lib.styles    import ParagraphStyle
from reportlab.lib.enums     import TA_CENTER, TA_RIGHT
from reportlab.platypus      import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle,
)

from config import (
    ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, OUTPUT_DIR, AUDIT_LOG, DEFAULT_ENTITY,
    ACTUALS_FILE, DRIVERS_FILE, OPERATIONAL_FILE, HEADCOUNT_FILE, CUSTOMER_FILE,
    EBIT_LABEL,
)

# ── Page geometry and palette (same system as Projects 1 to 3) ────────────────
PAGE_W = A4[0] - 4 * cm

DARK_BLUE  = colors.HexColor("#1A3A5C")
MID_BLUE   = colors.HexColor("#2D6A9F")
LIGHT_BLUE = colors.HexColor("#EAF2FB")
FLAG_RED   = colors.HexColor("#A32D2D")
GREEN      = colors.HexColor("#1D6B0F")
AMBER      = colors.HexColor("#854F0B")
AMBER_BG   = colors.HexColor("#FAEEDA")
BODY_DARK  = colors.HexColor("#1A1A19")
MUTED      = colors.HexColor("#898781")
RULE_COLOR = colors.HexColor("#D3D1C7")
ROW_ALT    = colors.HexColor("#F8F7F2")
TBL_HEADER = colors.HexColor("#E6F1FB")

S_BODY    = ParagraphStyle("Body",   fontName="Helvetica", fontSize=10,
                textColor=BODY_DARK, leading=15)
S_BODY_JUST = ParagraphStyle("BodyJust", fontName="Helvetica", fontSize=10,
                textColor=BODY_DARK, leading=15, alignment=4)  # 4 = justified
S_META    = ParagraphStyle("Meta",   fontName="Helvetica", fontSize=8,
                textColor=MUTED, leading=12, alignment=TA_CENTER)
S_TBL     = ParagraphStyle("Tbl",    fontName="Helvetica", fontSize=8.5,
                textColor=BODY_DARK, leading=11)
S_TBL_HDR = ParagraphStyle("TblHdr", fontName="Helvetica-Bold", fontSize=8.5,
                textColor=DARK_BLUE, leading=11)
S_TBL_NUM = ParagraphStyle("TblNum", fontName="Helvetica", fontSize=8.5,
                textColor=BODY_DARK, leading=11, alignment=TA_RIGHT)

SUBTOTAL_LINES = {"Gross Profit", "Total OpEx", EBIT_LABEL}


def clean_markdown(text):
    """Strip markdown artefacts before PDF rendering. Escape & first."""
    text = text.replace("&", "&amp;")
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'^#{1,3}\s*', '', text, flags=re.MULTILINE)
    return text.strip()


def describe_base_change(ctx):
    """Describe one change relative to its base assumption, phrased per driver
    type. ctx has target, driver_type, base_value, operation, new_value."""
    dt   = ctx["driver_type"]
    bv   = ctx["base_value"]
    nv   = ctx["new_value"]
    op   = ctx["operation"]
    tgt  = ctx["target"]

    if op == "set_driver":
        if dt == "seasonal_yoy":
            return "The base plan assumes {:.0%} annual growth for {}; this scenario sets it to {:.0%}.".format(bv, tgt, nv)
        if dt == "margin_pct":
            return "The base assumes a {:.1%} margin for {}; this scenario sets it to {:.1%}.".format(bv, tgt, nv)
        if dt == "growth_pct":
            return "The base assumes {:.0%} monthly growth for {}; this scenario sets it to {:.0%}.".format(bv, tgt, nv)
        if dt == "fixed":
            return "The base assumes EUR {:,.0f} for {}; this scenario sets it to EUR {:,.0f}.".format(bv, tgt, nv)
    if op == "scale_driver":
        pct = (nv - 1) * 100
        direction = "increases" if pct > 0 else "reduces"
        return "The base value for {} is scaled: this scenario {} it by {:.0f} percent.".format(tgt, direction, abs(pct))
    return ""


def build_base_context_text(base_context):
    """Join the per-change base descriptions into one block for the prompt."""
    lines = [describe_base_change(c) for c in base_context]
    lines = [l for l in lines if l]
    return " ".join(lines) if lines else ""


def describe_driver_value(driver_type, value):
    """Human-readable driver value, per driver type. Used in every table cell."""
    if driver_type == "seasonal_yoy":
        return "{:.0%} YoY growth".format(value)
    if driver_type == "margin_pct":
        return "{:.1%} of revenue".format(value)
    if driver_type == "growth_pct":
        return "{:.0%} MoM growth".format(value)
    if driver_type == "fixed":
        return "EUR {:,.0f}".format(value)
    if driver_type == "headcount_driven":
        return "per hiring schedule"
    if driver_type == "cac_driven":
        return "per customer targets"
    return str(value)


def build_assumptions_rows(base_context):
    """Build assumptions-table rows from the base context of one scenario.
    Each row: the changed driver, its base value, and its scenario value,
    both described in the driver's own units. Structured to extend to
    several scenarios later by adding more value columns."""
    rows = []
    for ctx in base_context:
        dt = ctx["driver_type"]
        rows.append({
            "line":       ctx["target"],
            "base":       describe_driver_value(dt, ctx["base_value"]),
            "scenario":   describe_driver_value(dt, ctx["new_value"]),
        })
    return rows


def build_takeaways(deltas, analysis_type):
    """Derive the key takeaways from the computed deltas. Pure Python, no AI:
    the numbers come from Python's own calculation, the block only states them.
    Returns a list of plain strings, most material first."""
    by_line = {d["line"]: d for d in deltas}
    rev  = by_line.get("Revenue")
    ebit = by_line.get(EBIT_LABEL)
    takeaways = []

    # 1. Headline: the EBIT impact, with its percentage if the base is meaningful
    if ebit and ebit["delta"] != 0:
        if ebit["pct"] is not None and abs(ebit["base"]) > 1000:
            takeaways.append(
                "EBIT changes by {:+,.0f} ({:+.1%}), from {:,.0f} to {:,.0f}.".format(
                    ebit["delta"], ebit["pct"], ebit["base"], ebit["scenario"]))
        else:
            takeaways.append(
                "EBIT changes by {:+,.0f}, from {:,.0f} to {:,.0f}.".format(
                    ebit["delta"], ebit["base"], ebit["scenario"]))

    # 2. Operating leverage: EBIT percentage versus revenue percentage
    if rev and ebit and rev["pct"] and ebit["pct"] and rev["delta"] != 0:
        if abs(rev["base"]) > 1000 and abs(ebit["base"]) > 1000:
            ratio = abs(ebit["pct"] / rev["pct"]) if rev["pct"] else None
            if ratio and ratio >= 1.5:
                takeaways.append(
                    "Operating leverage: revenue moves {:+.1%} but EBIT moves {:+.1%}, "
                    "about {:.0f} times larger, because the cost base is largely fixed.".format(
                        rev["pct"], ebit["pct"], ratio))

    # 3. What flowed through and what stayed flat, straight from the data
    exclude = {"Gross Profit", "Total OpEx", EBIT_LABEL}
    moved = [d["line"] for d in deltas if d["delta"] != 0 and d["line"] not in exclude]
    flat  = [d["line"] for d in deltas if d["delta"] == 0 and d["line"] not in exclude]
    if moved and flat:
        takeaways.append(
            "Lines that moved: {}. Lines held flat: {}.".format(
                ", ".join(moved), ", ".join(flat)))

    return takeaways


client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def build_explain_prompt(request, echo, headline, analysis_type, held_constant, base_context_text):
    """Build the prompt for the explanation. headline is the Revenue and EBIT
    delta dict. The numbers are already computed; Claude only explains."""
    hc = " ".join(held_constant) if held_constant else "None stated."
    delta_lines = []
    for label, d in headline.items():
        if d is None:
            continue
        delta_lines.append("  {}: base {:,.0f}, scenario {:,.0f}, change {:+,.0f}".format(
            label, d["base"], d["scenario"], d["delta"]))
    delta_block = "\n".join(delta_lines)

    system_prompt = (
        "You are an FP&A analyst explaining a what-if result to a finance "
        "team. The numbers are already computed. You explain them clearly and "
        "you never invent or recompute a figure.\n\n"
        "<rules>\n"
        "- Lead with the single most important result. State what changed and "
        "what happened to Revenue and EBIT over the forecast horizon, and name "
        "the percentage magnitude in context, not just the euro figure.\n"
        "- Say whether this was a sensitivity (one driver moved) or a scenario "
        "(several moved together).\n"
        "- If any assumption was held constant, state it clearly as a "
        "limitation. This matters: a reader must know what the model did not "
        "capture.\n"
        "- Keep it to a short, professional paragraph. Standard ASCII only, "
        "no arrows or dashes.\n"
        "</rules>"
    )

    user_prompt = (
        "Original request: \"{request}\"\n"
        "Parsed as: {echo}\n"
        "Analysis type: {atype}\n\n"
        "Base assumption versus this scenario:\n{base_ctx}\n\n"
        "Computed impact over the forecast horizon:\n"
        "{deltas}\n\n"
        "Assumptions held constant: {hc}\n\n"
        "Explain the result. Begin by stating what the base assumed and what "
        "this scenario changed it to, so the reader understands why the "
        "numbers move in the direction they do."
    ).format(request=request, echo=echo, atype=analysis_type,
             base_ctx=base_context_text or "Not applicable.",
             deltas=delta_block, hc=hc)

    return system_prompt, user_prompt


def call_claude_explain(request, echo, headline, analysis_type, held_constant, base_context_text):
    """Call Claude to explain the result. Returns (text, tok_in, tok_out)."""
    system_prompt, user_prompt = build_explain_prompt(
        request, echo, headline, analysis_type, held_constant, base_context_text)
    print("\n[..] Explaining result with Claude...")
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text
    return text, response.usage.input_tokens, response.usage.output_tokens


def write_audit(request, parsed, normalised, classification,
                analysis_type, headline, held_constant):
    """Append one audit record. headline is the Revenue and EBIT delta dict."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    def file_hash(path):
        with open(path, "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()

    rev_delta  = headline["Revenue"]["delta"] if headline.get("Revenue") else None
    ebit_delta = headline["EBIT"]["delta"]    if headline.get("EBIT")    else None

    audit = {
        "run_id":          now.isoformat(),
        "project":         "nl-scenario-copilot",
        "entity":          DEFAULT_ENTITY,
        "raw_request":     request,
        "parsed_scenario": parsed,
        "classification":  classification,
        "analysis_type":   analysis_type,
        "changes_applied": normalised,
        "revenue_delta":   rev_delta,
        "ebit_delta":      ebit_delta,
        "held_constant":   held_constant,
        "pdf_file":        None,
        "csv_file":        None,
        "input_hashes": {
            "actuals":     file_hash(ACTUALS_FILE),
            "drivers":     file_hash(DRIVERS_FILE),
            "operational": file_hash(OPERATIONAL_FILE),
            "headcount":   file_hash(HEADCOUNT_FILE),
            "customer":    file_hash(CUSTOMER_FILE),
        },
        "human_reviewed":  False,
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(audit) + "\n")
    print("\n[OK] Audit record written to {}".format(AUDIT_LOG.name))
    return audit


def _cover_block(entity, request, analysis_type, ts):
    rows = [
        [Paragraph('<font color="white"><b>WHAT-IF SCENARIO ANALYSIS</b></font>',
            ParagraphStyle("CT", fontName="Helvetica-Bold", fontSize=15,
                textColor=colors.white, alignment=TA_CENTER))],
        [Paragraph('<font color="#AACCEE">{}  ·  {}  ·  {}</font>'.format(
                entity, analysis_type.capitalize(), ts[:10]),
            ParagraphStyle("CS", fontName="Helvetica", fontSize=9,
                textColor=colors.HexColor("#AACCEE"), alignment=TA_CENTER))],
    ]
    t = Table(rows, colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), DARK_BLUE),
        ("TOPPADDING",    (0, 0), (0, 0),   16),
        ("BOTTOMPADDING", (0, 0), (0, 0),   5),
        ("TOPPADDING",    (0, 1), (0, 1),   3),
        ("BOTTOMPADDING", (0, 1), (0, 1),   14),
        ("LEFTPADDING",   (0, 0), (-1, -1), 20),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 20),
    ]))
    return t


def _section_header(title):
    t = Table([[Paragraph('<font color="white"><b>{}</b></font>'.format(title),
        ParagraphStyle("SH", fontName="Helvetica-Bold", fontSize=11,
            textColor=colors.white, leading=14))]], colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), MID_BLUE),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _delta_table(deltas):
    rows = [[
        Paragraph("<b>Line</b>", S_TBL_HDR),
        Paragraph("<b>Base</b>", S_TBL_HDR),
        Paragraph("<b>Scenario</b>", S_TBL_HDR),
        Paragraph("<b>Change</b>", S_TBL_HDR),
        Paragraph("<b>%</b>", S_TBL_HDR),
    ]]
    for d in deltas:
        dc = "#A32D2D" if d["delta"] < 0 else ("#1D6B0F" if d["delta"] > 0 else "#898781")
        pct = "" if d["pct"] is None else "{:+.1%}".format(d["pct"])
        safe = d["line"].replace("&", "&amp;")
        name = "<b>{}</b>".format(safe) if d["line"] in SUBTOTAL_LINES else safe
        rows.append([
            Paragraph(name, S_TBL),
            Paragraph("{:,.0f}".format(d["base"]), S_TBL_NUM),
            Paragraph("{:,.0f}".format(d["scenario"]), S_TBL_NUM),
            Paragraph('<font color="{}">{:+,.0f}</font>'.format(dc, d["delta"]), S_TBL_NUM),
            Paragraph('<font color="{}">{}</font>'.format(dc, pct), S_TBL_NUM),
        ])
    t = Table(rows, colWidths=[PAGE_W - 13*cm, 3.25*cm, 3.25*cm, 3.25*cm, 3.25*cm])
    style = [
        ("BACKGROUND",    (0, 0), (-1, 0), TBL_HEADER),
        ("LINEBELOW",     (0, 0), (-1, 0), 0.75, MID_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]
    for i, d in enumerate(deltas, start=1):
        if d["line"] in SUBTOTAL_LINES:
            style.append(("LINEABOVE", (0, i), (-1, i), 0.5, RULE_COLOR))
        if d["line"] == EBIT_LABEL:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_BLUE))
    t.setStyle(TableStyle(style))
    return t


def _held_constant_box(held_constant):
    if held_constant:
        txt = " ".join(held_constant)
    else:
        txt = "No cross-driver assumptions were held constant for this scenario."
    inner = Paragraph(
        '<font color="#854F0B"><b>Assumptions held constant.</b></font> ' + txt,
        ParagraphStyle("hc", fontName="Helvetica", fontSize=9,
            textColor=BODY_DARK, leading=13))
    t = Table([[inner]], colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), AMBER_BG),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEABOVE",     (0, 0), (-1, -1), 2, AMBER),
        ("LINEBELOW",     (0, 0), (-1, -1), 2, AMBER),
    ]))
    return t


def _esc(text):
    """Escape bare ampersands for ReportLab XML."""
    return text.replace("&", "&amp;")


def _esc_text(text):
    """Escape ampersands in free text that may contain line names like R&D."""
    return str(text).replace("&", "&amp;")


def _takeaways_box(takeaways):
    """Render the key takeaways as a highlighted block above the prose."""
    if not takeaways:
        return None
    bullets = []
    for t in takeaways:
        bullets.append(Paragraph(
            '<font color="#1A3A5C">•</font>  {}'.format(_esc_text(t)),
            ParagraphStyle("tk", fontName="Helvetica", fontSize=9.5,
                textColor=BODY_DARK, leading=14, leftIndent=4)))
    inner = [[b] for b in bullets]
    t = Table(inner, colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), LIGHT_BLUE),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE",     (0, 0), (-1, 0), 2, MID_BLUE),
        ("LINEBELOW",     (0, -1), (-1, -1), 2, MID_BLUE),
    ]))
    return t


def _assumptions_table(assumption_rows):
    """Render the base-versus-scenario assumptions comparison. Rows are the
    changed drivers; columns are the base and the scenario value."""
    if not assumption_rows:
        return None
    rows = [[
        Paragraph("<b>Driver</b>", S_TBL_HDR),
        Paragraph("<b>Base assumption</b>", S_TBL_HDR),
        Paragraph("<b>Scenario</b>", S_TBL_HDR),
    ]]
    for r in assumption_rows:
        rows.append([
            Paragraph(_esc(r["line"]), S_TBL),
            Paragraph(_esc(r["base"]), S_TBL),
            Paragraph('<font color="#2D6A9F"><b>{}</b></font>'.format(_esc(r["scenario"])), S_TBL),
        ])
    t = Table(rows, colWidths=[PAGE_W - 12*cm, 6*cm, 6*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), TBL_HEADER),
        ("LINEBELOW",     (0, 0), (-1, 0), 0.75, MID_BLUE),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.25, RULE_COLOR),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _update_audit_field(field, value):
    """Patch a field on the most recent audit record."""
    if not AUDIT_LOG.exists():
        return
    lines = AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n")
    if not lines or not lines[-1].strip():
        return
    last = json.loads(lines[-1])
    last[field] = value
    lines[-1] = json.dumps(last)
    AUDIT_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pdf(request, echo, deltas, analysis_type, held_constant, explanation, assumption_rows, takeaways):
    """Build the scenario report: cover, the parse, the base vs scenario delta
    table, the explanation, and the held-constant box."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now      = datetime.now(timezone.utc)
    ts_file  = now.strftime("%Y-%m-%d_%H-%M-%S")
    ts_log   = now.isoformat()
    pdf_path = OUTPUT_DIR / "scenario_{}.pdf".format(ts_file)

    story = []
    story.append(_cover_block(DEFAULT_ENTITY, request, analysis_type, ts_log))
    story.append(Spacer(1, 0.4 * cm))

    story.append(_section_header("THE REQUEST"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph('<b>Asked:</b> "{}"'.format(clean_markdown(request)), S_BODY))
    story.append(Paragraph('<b>Parsed as:</b> {}'.format(clean_markdown(echo)), S_BODY))
    story.append(Spacer(1, 0.35 * cm))

    assumptions_tbl = _assumptions_table(assumption_rows)
    if assumptions_tbl is not None:
        story.append(_section_header("ASSUMPTIONS"))
        story.append(Spacer(1, 0.2 * cm))
        story.append(assumptions_tbl)
        story.append(Spacer(1, 0.35 * cm))

    story.append(_section_header("BASE VERSUS SCENARIO"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_delta_table(deltas))
    story.append(Spacer(1, 0.35 * cm))

    story.append(_section_header("ANALYSIS"))
    story.append(Spacer(1, 0.25 * cm))
    takeaways_box = _takeaways_box(takeaways)
    if takeaways_box is not None:
        story.append(Paragraph("<b>Key takeaways</b>", S_BODY))
        story.append(Spacer(1, 0.15 * cm))
        story.append(takeaways_box)
        story.append(Spacer(1, 0.45 * cm))

    # Split the explanation into paragraphs on blank lines, and give each
    # paragraph its own spacing so the prose reads cleanly.
    para_text = clean_markdown(explanation)
    paragraphs = re.split(r'\n\s*\n', para_text)
    for i, para in enumerate(paragraphs):
        para = " ".join(para.split())   # collapse internal single newlines
        if not para:
            continue
        story.append(Paragraph(para, S_BODY_JUST))
        if i < len(paragraphs) - 1:
            story.append(Spacer(1, 0.22 * cm))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_held_constant_box(held_constant))
    story.append(Spacer(1, 0.4 * cm))

    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR))
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(
        "NL Scenario Modelling Copilot  ·  {}  ·  {}  ·  The request is parsed "
        "by AI, validated and computed deterministically, then explained. "
        "Figures are illustrative.".format(MODEL, ts_log[:10]), S_META))

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title="What-if Scenario Analysis", author="NL Scenario Modelling Copilot")
    doc.build(story)
    _update_audit_field("pdf_file", str(pdf_path))

    print("[OK] PDF written")
    print("     PDF:  {}".format(pdf_path.name))
    return pdf_path


def export_deltas_csv(deltas, request, echo, held_constant):
    """Export the full P&L delta as the working artefact. Uses pandas to_csv
    so any commas are quoted correctly."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now      = datetime.now(timezone.utc)
    ts_file  = now.strftime("%Y-%m-%d_%H-%M-%S")
    csv_path = OUTPUT_DIR / "scenario_deltas_{}.csv".format(ts_file)

    rows = []
    for d in deltas:
        rows.append({
            "line":     d["line"],
            "base":     round(d["base"], 2),
            "scenario": round(d["scenario"], 2),
            "delta":    round(d["delta"], 2),
            "pct":      "" if d["pct"] is None else round(d["pct"], 4),
        })
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    _update_audit_field("csv_file", str(csv_path))

    print("[OK] Deltas CSV exported")
    print("     CSV:  {}".format(csv_path.name))
    return csv_path
