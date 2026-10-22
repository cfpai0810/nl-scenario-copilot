# =============================================================================
# lib/theme.py: one visual identity, shared with the PDF
# =============================================================================
# The exact palette the PDFs already use (step6_explainer), so the web app and
# the report it produces read as a single product. Governance status colours
# do real work here: the amber DRAFT and green FINAL chips on a finished run,
# and the badges for a request that did not run (amber caution, red refused).
#
# Typography: Source Serif 4 for headings (institutional authority) and Inter
# for body, UI, and data (screen-first, with tabular figures so numbers align
# in tables). Both are free Google Fonts, loaded by CSS import.
# =============================================================================

# Core palette — identical to the PDF (step6_explainer.py)
DARK_BLUE  = "#1A3A5C"
MID_BLUE   = "#2D6A9F"
LIGHT_BLUE = "#EAF2FB"
GREEN      = "#1D6B0F"
AMBER      = "#854F0B"
AMBER_BG   = "#FAEEDA"
FLAG_RED   = "#A32D2D"
BODY_DARK  = "#1A1A19"
MUTED      = "#898781"
RULE       = "#D3D1C7"
NEAR_WHITE = "#FBFAF7"

# Status chips: amber DRAFT and green FINAL, the series' sign-off colours (as
# Project 6's screen and PDF use them)
DRAFT_CHIP = "#B45F06"
FINAL_CHIP = "#2E6B3F"

# Status badge colours (background, text) for a request that did not run. A
# finished run shows the DRAFT or FINAL chip instead; the web has no approved
# or locked state (those belong to the production lifecycle).
BADGE = {
    "draft":    (LIGHT_BLUE, DARK_BLUE),
    "refused":  ("#F7E4E4",  FLAG_RED),
    "caution":  (AMBER_BG,   AMBER),
}

# Font families (referenced in CSS and available to the SVG diagram)
SERIF = "'Source Serif 4', Georgia, 'Times New Roman', serif"
SANS  = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"


def inject_css():
    """Return the app's CSS, including the font imports. Kept in one place so
    every page is consistent. The boldness is spent on the governance UI and
    the type pairing, not on chrome."""
    return f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap');

      /* Body, UI, and data: Inter. No tabular figures here: they make Inter
         draw a hyphen as wide as a digit in ordinary text. */
      html, body, [class*="css"], .stMarkdown, .stText, p, span, div, label,
      input, button, table, td, th, ul, ol, li, strong, em, caption {{
        font-family: {SANS};
        font-feature-settings: "cv05" 1;
      }}
      /* Tabular figures only where numbers line up: tables, metrics, figures */
      table, table *, [data-testid="stMetricValue"], [data-testid="stMetricValue"] *,
      [data-testid="stMetricDelta"], [data-testid="stMetricDelta"] * {{
        font-feature-settings: "tnum" 1, "cv05" 1;
      }}

      /* Headings: Source Serif 4 for institutional authority */
      h1, h2, h3, h4 {{
        font-family: {SERIF};
        color: {DARK_BLUE};
        letter-spacing: -0.01em;
        font-weight: 600;
      }}

      /* The app header band */
      .sc-header {{
        background: {DARK_BLUE};
        color: white;
        padding: 22px 26px;
        border-radius: 10px;
        margin-bottom: 8px;
      }}
      .sc-header h1 {{
        font-family: {SERIF};
        color: white; margin: 0; font-size: 1.7rem; font-weight: 700;
        letter-spacing: -0.02em;
      }}
      .sc-header p  {{
        font-family: {SANS};
        color: #AACCEE; margin: 6px 0 0; font-size: 0.92rem; font-weight: 400;
      }}

      /* Status chip: amber DRAFT, green FINAL */
      .sc-status {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 4px;
        font-size: 0.74rem;
        font-weight: 700;
        letter-spacing: 0.06em;
        color: white;
      }}
      .sc-status.draft {{ background: {DRAFT_CHIP}; }}
      .sc-status.final {{ background: {FINAL_CHIP}; }}

      /* Page links wrap rather than clip on a phone */
      [data-testid="stPageLink-NavLink"], [data-testid="stPageLink-NavLink"] * {{
        white-space: normal !important; height: auto !important;
      }}

      /* Status badge */
      .sc-badge {{
        display: inline-block;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        font-family: {SANS};
      }}

      /* Honest key-disclosure note */
      .sc-keynote {{
        font-size: 0.8rem;
        color: {MUTED};
        border-left: 2px solid {RULE};
        padding-left: 10px;
        margin-top: 6px;
        font-family: {SANS};
      }}

      /* Explanation cards (takeaways callout, analysis body, caution note) */
      .sc-exp-kicker {{
        font-size: 0.72rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        font-weight: 700;
        color: {MID_BLUE};
        margin-bottom: 2px;
      }}
      .sc-exp-callout {{
        background: {LIGHT_BLUE};
        border-left: 4px solid {DARK_BLUE};
        border-radius: 6px;
        padding: 12px 15px;
        margin-bottom: 4px;
      }}
      .sc-exp-callout ul {{
        margin: 4px 0 0 0;
        padding-left: 18px;
      }}
      .sc-exp-callout li {{
        color: {BODY_DARK};
        margin-bottom: 4px;
        font-size: 0.92rem;
        line-height: 1.5;
      }}
      .sc-exp-caution {{
        background: {AMBER_BG};
        color: {AMBER};
        border: 1px solid #E8C88A;
        border-radius: 6px;
        padding: 8px 12px;
        margin: 9px 0 0;
        font-size: 0.85rem;
        line-height: 1.45;
      }}
    </style>
    """


def badge_html(status):
    """Return an HTML status badge for a request that did not run (refused
    or caution). A finished run shows status_chip_html instead."""
    status = status.lower()
    bg, fg = BADGE.get(status, BADGE["draft"])
    return (f'<span class="sc-badge" style="background:{bg};color:{fg};">'
            f'{status.upper()}</span>')


def header_html(title, subtitle):
    """The page banner: the series navy band with the page's name and one line."""
    import html
    return (f'<div class="sc-header"><h1>{html.escape(title)}</h1>'
            f'<p>{html.escape(subtitle)}</p></div>')


def status_chip_html(status):
    """The DRAFT or FINAL chip. Anything but "final" reads DRAFT."""
    label = "FINAL" if status == "final" else "DRAFT"
    return f'<span class="sc-status {label.lower()}">{label}</span>'
