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
    EBIT_LABEL, LINE_ITEMS, SCHEDULE_DRIVER_TYPES,
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


# =============================================================================
# THREE-CASE REPORT — multi-column PDF, CSV, audit, explanation
# =============================================================================
CASE_NAMES = ["Pessimistic", "Realistic", "Optimistic"]

S_CARD = ParagraphStyle("Card", fontName="Helvetica", fontSize=9,
            textColor=MUTED, leading=12)


def format_case_value(line_item, value):
    """Show a case value in the driver's own units. Schedule drivers carry a
    scale factor, value drivers carry their real driver value."""
    dtype = LINE_ITEMS[line_item]
    if dtype in SCHEDULE_DRIVER_TYPES:
        return "{:.2f}x".format(value)
    if dtype == "fixed":
        return "EUR {:,.0f}".format(value)
    return "{:.2%}".format(value)


def _assumptions_table_multi(spreads):
    """Drivers down the side, cases across the top."""
    hdr = [Paragraph("<b>Driver</b>", S_TBL_HDR),
           Paragraph("<b>Base</b>", S_TBL_HDR)]
    for name in CASE_NAMES:
        hdr.append(Paragraph("<b>{}</b>".format(name), S_TBL_HDR))
    rows = [hdr]

    for line_item, sp in spreads.items():
        base_val = sp["realistic"]
        cells = [Paragraph(_esc(line_item), S_TBL),
                 Paragraph(_esc(format_case_value(line_item, base_val)), S_TBL)]
        for key, name in zip(("pessimistic", "realistic", "optimistic"), CASE_NAMES):
            txt = _esc(format_case_value(line_item, sp[key]))
            if name == "Realistic":
                cells.append(Paragraph(txt, S_TBL))
            else:
                cells.append(Paragraph(
                    '<font color="#2D6A9F">{}</font>'.format(txt), S_TBL))
        rows.append(cells)

    col = (PAGE_W - 3.4*cm) / 4
    t = Table(rows, colWidths=[3.4*cm, col, col, col, col])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), TBL_HEADER),
        ("LINEBELOW",     (0, 0), (-1, 0), 0.75, MID_BLUE),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.25, RULE_COLOR),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
    ]))
    return t


def _multicase_table(delta_rows):
    """The full P&L: base plus the three cases, levels."""
    hdr = [Paragraph("<b>Line</b>", S_TBL_HDR),
           Paragraph("<b>Base</b>", S_TBL_HDR)]
    for name in CASE_NAMES:
        hdr.append(Paragraph("<b>{}</b>".format(name), S_TBL_HDR))
    rows = [hdr]

    for r in delta_rows:
        name = "<b>{}</b>".format(_esc(r["line"])) if r["line"] in SUBTOTAL_LINES \
               else _esc(r["line"])
        cells = [Paragraph(name, S_TBL),
                 Paragraph("{:,.0f}".format(r["base"]), S_TBL_NUM)]
        for case in CASE_NAMES:
            cells.append(Paragraph("{:,.0f}".format(r[case]), S_TBL_NUM))
        rows.append(cells)

    label_w = 5.0 * cm
    num_w   = (PAGE_W - label_w) / 4
    t = Table(rows, colWidths=[label_w, num_w, num_w, num_w, num_w])
    style = [
        ("BACKGROUND",    (0, 0), (-1, 0), TBL_HEADER),
        ("LINEBELOW",     (0, 0), (-1, 0), 0.75, MID_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
    ]
    for i, r in enumerate(delta_rows, start=1):
        if r["line"] in SUBTOTAL_LINES:
            style.append(("LINEABOVE", (0, i), (-1, i), 0.5, RULE_COLOR))
        if r["line"] == EBIT_LABEL:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_BLUE))
    t.setStyle(TableStyle(style))
    return t


def _ebit_strip(ebit_row):
    """A highlighted strip showing EBIT against the base for each case."""
    if not ebit_row:
        return None
    label_w = 5.0 * cm
    num_w   = (PAGE_W - label_w) / 4
    cells = [Paragraph("<b>EBIT versus base</b>", S_TBL_HDR),
             Paragraph("", S_TBL_NUM)]
    for case in CASE_NAMES:
        d = ebit_row[case + "_delta"]
        c = "#A32D2D" if d < 0 else ("#1D6B0F" if d > 0 else "#898781")
        cells.append(Paragraph(
            '<font color="{}"><b>{:+,.0f}</b></font>'.format(c, d), S_TBL_NUM))
    t = Table([cells], colWidths=[label_w, num_w, num_w, num_w, num_w])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), AMBER_BG),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("LINEABOVE",     (0, 0), (-1, -1), 1.5, AMBER),
        ("LINEBELOW",     (0, 0), (-1, -1), 1.5, AMBER),
    ]))
    return t


def build_three_case_takeaways(delta_rows, driver_names):
    """Derive the key takeaways for a three-case run. Pure Python."""
    by = {r["line"]: r for r in delta_rows}
    e  = by.get(EBIT_LABEL)
    if not e:
        return []
    b, p, o = e["base"], e["Pessimistic"], e["Optimistic"]
    down, up = p - b, o - b
    t = [
        "EBIT ranges from {:,.0f} in the pessimistic case to {:,.0f} in the "
        "optimistic case, against a base of {:,.0f}.".format(p, o, b),
        "Downside {:+,.0f}, upside {:+,.0f}, a total spread of {:,.0f}.".format(
            down, up, o - p),
    ]
    if down != 0 and up != 0:
        skew = abs(down) / abs(up)
        if skew > 1.15:
            t.append("The downside is about {:.0%} of the upside in size, so the "
                     "risk outweighs the reward here.".format(skew))
        elif skew < 0.87:
            t.append("The upside exceeds the downside by roughly {:.0%}.".format(1 / skew))
        else:
            t.append("The downside and upside are broadly symmetric.")
    t.append("Drivers flexed together: {}.".format(", ".join(driver_names)))
    return t


def call_claude_explain_three_case(delta_rows, spreads, basis_text):
    """Claude narrates across the three cases. Numbers already computed."""
    by = {r["line"]: r for r in delta_rows}
    e  = by.get(EBIT_LABEL)
    driver_lines = []
    for line_item, sp in spreads.items():
        driver_lines.append("  {}: pessimistic {}, realistic {}, optimistic {}".format(
            line_item,
            format_case_value(line_item, sp["pessimistic"]),
            format_case_value(line_item, sp["realistic"]),
            format_case_value(line_item, sp["optimistic"])))

    system_prompt = (
        "You are an FP&A analyst explaining a three case scenario analysis to a "
        "finance team. The numbers are already computed. You explain them and "
        "you never invent or recompute a figure.\n\n"
        "<rules>\n"
        "- Lead with the EBIT range across the three cases and what it means "
        "for planning.\n"
        "- Note whether the downside or the upside is larger, and say what that "
        "implies.\n"
        "- Name the drivers that were flexed and how they move together.\n"
        "- State clearly that the realistic case is the base plan, so its "
        "figures match the base.\n"
        "- State what the model holds constant: drivers not selected do not "
        "move, and this model does not link marketing or customers to revenue.\n"
        "- Two short paragraphs. Standard ASCII only, no arrows or dashes.\n"
        "</rules>"
    )
    user_prompt = (
        "Drivers flexed:\n{drivers}\n\n"
        "How the cases were set: {basis}\n\n"
        "EBIT: base {b:,.0f}, pessimistic {p:,.0f}, realistic {r:,.0f}, "
        "optimistic {o:,.0f}\n\n"
        "Explain the analysis."
    ).format(
        drivers="\n".join(driver_lines), basis=basis_text,
        b=e["base"], p=e["Pessimistic"], r=e["Realistic"], o=e["Optimistic"],
    )

    print("\n[..] Explaining the three cases with Claude...")
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


def write_three_case_pdf(spreads, delta_rows, explanation, takeaways, basis_text):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now      = datetime.now(timezone.utc)
    ts_file  = now.strftime("%Y-%m-%d_%H-%M-%S")
    ts_log   = now.isoformat()
    pdf_path = OUTPUT_DIR / "three_case_{}.pdf".format(ts_file)

    ebit_row = next((r for r in delta_rows if r["line"] == EBIT_LABEL), None)

    story = []
    story.append(_cover_block(DEFAULT_ENTITY, "three case analysis", "three-case", ts_log))
    story.append(Spacer(1, 0.4 * cm))

    story.append(_section_header("CASES"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_assumptions_table_multi(spreads))
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(
        '<font color="#898781">How the cases were set: {}. The realistic case '
        'is the base plan.</font>'.format(_esc(basis_text)), S_CARD))
    story.append(Spacer(1, 0.35 * cm))

    story.append(_section_header("PROFIT AND LOSS ACROSS CASES"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_multicase_table(delta_rows))
    strip = _ebit_strip(ebit_row)
    if strip is not None:
        story.append(Spacer(1, 0.2 * cm))
        story.append(strip)
    story.append(Spacer(1, 0.4 * cm))

    story.append(_section_header("ANALYSIS"))
    story.append(Spacer(1, 0.25 * cm))
    tk = _takeaways_box(takeaways)
    if tk is not None:
        story.append(Paragraph("<b>Key takeaways</b>", S_BODY))
        story.append(Spacer(1, 0.15 * cm))
        story.append(tk)
        story.append(Spacer(1, 0.45 * cm))
    for i, para in enumerate(re.split(r'\n\s*\n', clean_markdown(explanation))):
        para = " ".join(para.split())
        if para:
            story.append(Paragraph(para, S_BODY_JUST))
            story.append(Spacer(1, 0.22 * cm))

    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR))
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(
        "NL Scenario Modelling Copilot  ·  {}  ·  {}  ·  Cases are set "
        "deterministically and computed by the model; the commentary explains "
        "them. Figures are illustrative.".format(MODEL, ts_log[:10]), S_META))

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title="Three Case Scenario Analysis", author="NL Scenario Modelling Copilot")
    doc.build(story)
    _update_audit_field("pdf_file", str(pdf_path))
    print("[OK] PDF written")
    print("     PDF:  {}".format(pdf_path.name))
    return pdf_path


def export_three_case_csv(delta_rows):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts_file  = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    csv_path = OUTPUT_DIR / "three_case_{}.csv".format(ts_file)
    rows = []
    for r in delta_rows:
        row = {"line": r["line"], "base": round(r["base"], 2)}
        for case in CASE_NAMES:
            row[case.lower()]                = round(r[case], 2)
            row[case.lower() + "_delta"]     = round(r[case + "_delta"], 2)
        rows.append(row)
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    _update_audit_field("csv_file", str(csv_path))
    print("[OK] CSV exported")
    print("     CSV:  {}".format(csv_path.name))
    return csv_path


def write_three_case_audit(spreads, delta_rows, basis_text):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    def file_hash(path):
        with open(path, "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()

    ebit = next((r for r in delta_rows if r["line"] == EBIT_LABEL), None)
    audit = {
        "run_id":        now.isoformat(),
        "project":       "nl-scenario-copilot",
        "entity":        DEFAULT_ENTITY,
        "analysis_type": "three-case",
        "drivers_flexed": list(spreads.keys()),
        "cases": {li: {k: sp[k] for k in ("pessimistic", "realistic", "optimistic")}
                  for li, sp in spreads.items()},
        "basis":         basis_text,
        "ebit_base":         ebit["base"] if ebit else None,
        "ebit_pessimistic":  ebit["Pessimistic"] if ebit else None,
        "ebit_realistic":    ebit["Realistic"] if ebit else None,
        "ebit_optimistic":   ebit["Optimistic"] if ebit else None,
        "pdf_file":      None,
        "csv_file":      None,
        "input_hashes": {
            "actuals":     file_hash(ACTUALS_FILE),
            "drivers":     file_hash(DRIVERS_FILE),
            "operational": file_hash(OPERATIONAL_FILE),
            "headcount":   file_hash(HEADCOUNT_FILE),
            "customer":    file_hash(CUSTOMER_FILE),
        },
        "human_reviewed": False,
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(audit) + "\n")
    print("\n[OK] Audit record written")
    return audit
