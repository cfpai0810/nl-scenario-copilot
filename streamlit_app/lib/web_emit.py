# =============================================================================
# lib/web_emit.py — web-safe output (PDF to bytes, no disk, no shared file)
# =============================================================================
# The CLI's write_pdf builds a story from pure reportlab helpers, writes it to
# a file, and mutates the shared audit JSONL. On the web we want the SAME
# report as downloadable bytes, with no server-side file writes and no shared-
# file mutation (which would corrupt across users). This reuses step6's pure
# story-builders and targets a BytesIO buffer instead of a path.
# =============================================================================

from io import BytesIO
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

# Reuse the PURE builders from the existing explainer — no rebuild.
from src.step6_explainer import (
    _cover_block, _section_header, _assumptions_table, _delta_table,
    _held_constant_box, _takeaways_box, _monthly_table, _quarterly_table,
    _three_case_story,
    clean_markdown,
    S_BODY, S_BODY_JUST, S_META, RULE_COLOR,
)
from config import MODEL, DEFAULT_ENTITY, EBIT_LABEL

import re


def scenario_pdf_bytes(request, echo, deltas, analysis_type, held_constant,
                       explanation, assumption_rows, takeaways,
                       monthly=None, quarterly=None):
    """Build the single-scenario report and return it as PDF bytes for a
    download button. Reuses the exact story-builders the CLI PDF uses; only
    the output target (a BytesIO buffer) and the skipped audit-file mutation
    differ."""
    now = datetime.now(timezone.utc)
    ts_log = now.isoformat()
    buffer = BytesIO()

    story = []
    story.append(_cover_block(DEFAULT_ENTITY, request, analysis_type, ts_log))
    story.append(Spacer(1, 0.4 * cm))

    story.append(_section_header("THE REQUEST"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph('<b>Asked:</b> "{}"'.format(clean_markdown(request)), S_BODY))
    story.append(Paragraph('<b>Parsed as:</b> {}'.format(clean_markdown(echo)), S_BODY))
    story.append(Spacer(1, 0.35 * cm))

    ebit_d = next((d for d in deltas if d["line"] == EBIT_LABEL), None)
    if ebit_d and ebit_d["delta"] != 0:
        _pct = (" ({:+.1%})".format(ebit_d["pct"])
                if ebit_d["pct"] is not None else "")
        story.append(Paragraph(
            '<b>EBIT moves {:+,.0f}{}, from {:,.0f} to {:,.0f} over the '
            'forecast horizon.</b>'.format(
                ebit_d["delta"], _pct,
                ebit_d["base"], ebit_d["scenario"]),
            S_BODY))
        story.append(Spacer(1, 0.35 * cm))

    atbl = _assumptions_table(assumption_rows)
    if atbl is not None:
        story.append(_section_header("ASSUMPTIONS"))
        story.append(Spacer(1, 0.2 * cm))
        story.append(atbl)
        story.append(Spacer(1, 0.35 * cm))

    story.append(_section_header("BASE VERSUS SCENARIO"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_delta_table(deltas))
    story.append(Spacer(1, 0.35 * cm))

    if quarterly:
        story.append(_section_header("FULL YEAR (QUARTERLY)"))
        story.append(Spacer(1, 0.2 * cm))
        story.append(_quarterly_table(quarterly))
        story.append(Spacer(1, 0.1 * cm))
        story.append(Paragraph(
            '<font color="#898781">Columns marked (A) are actuals; '
            '(F) are scenario forecast. FY combines them.</font>',
            S_META))
        story.append(Spacer(1, 0.35 * cm))

    if monthly:
        story.append(_section_header("MONTHLY CHANGE VS BASE"))
        story.append(Spacer(1, 0.2 * cm))
        story.append(_monthly_table(monthly, "delta"))
        story.append(Spacer(1, 0.35 * cm))

    story.append(_section_header("ANALYSIS"))
    story.append(Spacer(1, 0.25 * cm))
    tbox = _takeaways_box(takeaways)
    if tbox is not None:
        story.append(Paragraph("<b>Key takeaways</b>", S_BODY))
        story.append(Spacer(1, 0.15 * cm))
        story.append(tbox)
        story.append(Spacer(1, 0.45 * cm))

    for i, para in enumerate(re.split(r'\n\s*\n', clean_markdown(explanation))):
        para = " ".join(para.split())
        if para:
            story.append(Paragraph(para, S_BODY_JUST))
            story.append(Spacer(1, 0.22 * cm))

    story.append(Spacer(1, 0.1 * cm))
    story.append(_held_constant_box(held_constant))
    story.append(Spacer(1, 0.4 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE_COLOR))
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(
        "Scenario Modelling Copilot  ·  {}  ·  {}  ·  The request is parsed by "
        "the model, validated and computed deterministically, then explained. "
        "Figures are illustrative.".format(MODEL, ts_log[:10]), S_META))

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title="Scenario Analysis", author="Scenario Modelling Copilot")
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def three_case_pdf_bytes(spreads, delta_rows, explanation, takeaways, basis_text):
    """Build the three-case report and return it as PDF bytes. Reuses the same
    shared story builder the CLI PDF uses; only the output target differs."""
    buffer = BytesIO()
    story = _three_case_story(spreads, delta_rows, explanation, takeaways,
                              basis_text)
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title="Three Case Scenario Analysis",
        author="Scenario Modelling Copilot")
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def three_case_csv_bytes(delta_rows):
    """Build the three-case CSV and return it as bytes for a download button."""
    import csv
    from io import StringIO
    from src.step6_explainer import CASE_NAMES
    buf = StringIO()
    fields = ["line", "base"]
    for case in CASE_NAMES:
        fields.extend([case.lower(), case.lower() + "_delta"])
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in delta_rows:
        row = {"line": r["line"], "base": round(r["base"], 2)}
        for case in CASE_NAMES:
            row[case.lower()] = round(r[case], 2)
            row[case.lower() + "_delta"] = round(r[case + "_delta"], 2)
        w.writerow(row)
    return buf.getvalue().encode("utf-8")
