# =============================================================================
# lib/theme.py — one visual identity, shared with the PDF
# =============================================================================
# The exact palette the PDFs already use (step6_explainer), so the web app and
# the report it produces read as a single product. Governance status colours
# do real work here as badges: green approved, amber caution, red refused.
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

# Status badge colours (background, text) — the governance state made visible
BADGE = {
    "draft":    (LIGHT_BLUE, DARK_BLUE),
    "approved": ("#E1F5EE",  GREEN),
    "locked":   ("#E6F1FB",  MID_BLUE),
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

      /* Body, UI, and data: Inter, with tabular figures so numbers align */
      html, body, [class*="css"], .stMarkdown, .stText, p, span, div, label,
      input, button, table, td, th {{
        font-family: {SANS};
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
    """Return an HTML status badge for one of the governance states."""
    status = status.lower()
    bg, fg = BADGE.get(status, BADGE["draft"])
    label = status.upper()
    return (f'<span class="sc-badge" style="background:{bg};color:{fg};">'
            f'{label}</span>')
