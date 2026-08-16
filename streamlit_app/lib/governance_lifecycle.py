# =============================================================================
# lib/governance_lifecycle.py -- the approve-lock-read-only lifecycle
# =============================================================================
# A visual walkthrough of the full governance lifecycle as intended for
# production deployment. This is a documentation diagram only: it does not
# change any app behaviour, store anything, or add persistence. The diagram
# shows where the live demo stops (at flag-for-review) and how the production
# model continues (approve, lock, read only, re-issue).
# =============================================================================

from streamlit_app.lib.theme import (
    DARK_BLUE, MID_BLUE, LIGHT_BLUE, MUTED, RULE,
)

PERSON       = DARK_BLUE
LOCKED_STROKE = MID_BLUE
LOCKED_BG    = "#E6F1FB"
PERSON_BG    = LIGHT_BLUE


def _box(x, w, stroke, bg, title, l1, l2, foot, foot_color):
    """One stage box with a title, two content lines, and an ownership foot."""
    cx = x + w / 2
    return f"""
  <g>
    <rect x="{x}" y="66" width="{w}" height="128" rx="12"
          fill="{bg}" stroke="{stroke}" stroke-width="1.1"/>
    <text x="{cx}" y="96" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="12" font-weight="700" fill="{stroke}"
          letter-spacing="0.03em">{title}</text>
    <text x="{cx}" y="124" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11.5" fill="{DARK_BLUE}">{l1}</text>
    <text x="{cx}" y="141" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11.5" fill="{DARK_BLUE}">{l2}</text>
    <text x="{cx}" y="176" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="10" font-style="italic" fill="{foot_color}">{foot}</text>
  </g>"""


def _arrow(x1, x2):
    return (f'<line x1="{x1}" y1="130" x2="{x2}" y2="130" stroke="{MUTED}" '
            f'stroke-width="1.6" marker-end="url(#lc-arrow)"/>')


def governance_lifecycle_svg():
    """Return the governance lifecycle diagram as an inline SVG string."""
    boxes = (
        _box(14, 148, PERSON, PERSON_BG, "YOU REVIEW",
             "Check the flagged", "output and data", "the person", PERSON) +
        _arrow(162, 216) +
        _box(220, 150, PERSON, PERSON_BG, "YOU APPROVE",
             "Sign off the", "report as final", "the person", PERSON) +
        _arrow(370, 396) +
        _box(404, 148, LOCKED_STROKE, LOCKED_BG, "LOCKED",
             "The approved report", "becomes immutable", "the record",
             LOCKED_STROKE) +
        _arrow(552, 578) +
        _box(586, 148, LOCKED_STROKE, LOCKED_BG, "READ ONLY",
             "Everyone reads it,", "no one edits it", "the record",
             LOCKED_STROKE) +
        _arrow(734, 760) +
        _box(768, 138, PERSON, PERSON_BG, "RE-ISSUE",
             "A new version; the", "original still stands", "the person",
             PERSON)
    )

    boundary_x = 189

    return f"""
<svg viewBox="0 0 920 300" width="100%" role="img"
     xmlns="http://www.w3.org/2000/svg"
     aria-label="The governance lifecycle: you review the flagged output,
     approve the report, the approved version is locked and becomes
     immutable, everyone reads the locked record, and a change requires
     a new separately approved version.">
  <defs>
    <marker id="lc-arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M2 1 L8 5 L2 9" fill="none" stroke="{MUTED}"
            stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <text x="460" y="22" text-anchor="middle" font-family="'Inter', sans-serif"
        font-size="13.5" font-weight="600" fill="{PERSON}">
    Review is the start of the record, not the end.
  </text>

  <text x="88" y="48" text-anchor="middle" font-family="'Inter', sans-serif"
        font-size="10" fill="{MUTED}">Live demo</text>
  <text x="566" y="48" text-anchor="middle" font-family="'Inter', sans-serif"
        font-size="10" fill="{MUTED}">Production workflow</text>

  <line x1="{boundary_x}" y1="54" x2="{boundary_x}" y2="198"
        stroke="{RULE}" stroke-width="1.2" stroke-dasharray="4 4"/>

  {boxes}

  <line x1="14" y1="234" x2="906" y2="234" stroke="{RULE}"
        stroke-width="1" stroke-dasharray="3 4"/>
  <text x="460" y="256" text-anchor="middle" font-family="'Inter', sans-serif"
        font-size="11" fill="{MUTED}">
    The live demo stops at review. The stages beyond show the production
    model this design is built towards.
  </text>

  <g font-family="'Inter', sans-serif" font-size="11">
    <rect x="270" y="276" width="11" height="11" rx="2" fill="{PERSON_BG}"
          stroke="{PERSON}"/>
    <text x="287" y="285" fill="{DARK_BLUE}">You</text>
    <rect x="340" y="276" width="11" height="11" rx="2" fill="{LOCKED_BG}"
          stroke="{LOCKED_STROKE}"/>
    <text x="357" y="285" fill="{DARK_BLUE}">The immutable record</text>
  </g>
</svg>
"""
