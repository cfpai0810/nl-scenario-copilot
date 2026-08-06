# =============================================================================
# lib/pipeline_flow.py — the pipeline diagram (matches governance_flow's language)
# =============================================================================
# One SVG, same visual grammar as governance_flow.py: purple = the model (used
# only for language, never numbers), green = deterministic engine, dark blue =
# data. Inter font, theme palette. Shows the AI boundary: the model appears in
# exactly two of five stages; everything numeric is deterministic. Kept as a
# pure function so any page can render it and it stays consistent.
# =============================================================================


def pipeline_flow_svg():
    """Return the pipeline diagram as an inline SVG string."""
    return """<svg viewBox="0 0 882 250" width="100%" role="img"
     xmlns="http://www.w3.org/2000/svg"
     aria-label="The pipeline: a deterministic data stage loads inputs and finds
     the forecast horizon; the model parses words into structure; a deterministic
     validator checks, classifies and confirms; a deterministic engine computes
     the forecast on a copy and the deltas; and the model explains the figures in
     words. Only the parse and explain stages use the model; everything numeric is
     deterministic.">
  <defs>
    <marker id="pl-arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M2 1 L8 5 L2 9" fill="none" stroke="#898781"
            stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <text x="441.0" y="30" text-anchor="middle" font-family="'Inter', sans-serif"
        font-size="13" font-weight="600" fill="#1A3A5C">
    The model appears in two stages only. Everything numeric is deterministic.
  </text>


  <g>
    <rect x="14" y="70" width="150" height="108" rx="12"
          fill="#EAF2FB" stroke="#1A3A5C" stroke-width="1.1"/>
    <text x="89.0" y="98" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="12.5" font-weight="700" fill="#1A3A5C" letter-spacing="0.03em">Data</text>
    <text x="89.0" y="122" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">load inputs,</text>
    <text x="89.0" y="138" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">find horizon</text>
    <text x="89.0" y="165" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="9.5" font-style="italic" fill="#1A3A5C">deterministic</text>
  </g><line x1="164" y1="124.0" x2="190" y2="124.0" stroke="#898781" stroke-width="1.6" marker-end="url(#pl-arrow)"/>
  <g>
    <rect x="190" y="70" width="150" height="108" rx="12"
          fill="#F0ECF8" stroke="#6B4FA8" stroke-width="1.1"/>
    <text x="265.0" y="98" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="12.5" font-weight="700" fill="#6B4FA8" letter-spacing="0.03em">Parse</text>
    <text x="265.0" y="122" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">words to</text>
    <text x="265.0" y="138" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">structure</text>
    <text x="265.0" y="165" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="9.5" font-style="italic" fill="#6B4FA8">model, language</text>
  </g><line x1="340" y1="124.0" x2="366" y2="124.0" stroke="#898781" stroke-width="1.6" marker-end="url(#pl-arrow)"/>
  <g>
    <rect x="366" y="70" width="150" height="108" rx="12"
          fill="#E8F3E9" stroke="#1D6B0F" stroke-width="1.1"/>
    <text x="441.0" y="98" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="12.5" font-weight="700" fill="#1D6B0F" letter-spacing="0.03em">Validate</text>
    <text x="441.0" y="122" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">check, classify,</text>
    <text x="441.0" y="138" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">confirm</text>
    <text x="441.0" y="165" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="9.5" font-style="italic" fill="#1D6B0F">deterministic</text>
  </g><line x1="516" y1="124.0" x2="542" y2="124.0" stroke="#898781" stroke-width="1.6" marker-end="url(#pl-arrow)"/>
  <g>
    <rect x="542" y="70" width="150" height="108" rx="12"
          fill="#E8F3E9" stroke="#1D6B0F" stroke-width="1.1"/>
    <text x="617.0" y="98" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="12.5" font-weight="700" fill="#1D6B0F" letter-spacing="0.03em">Compute</text>
    <text x="617.0" y="122" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">forecast on</text>
    <text x="617.0" y="138" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">a copy, deltas</text>
    <text x="617.0" y="165" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="9.5" font-style="italic" fill="#1D6B0F">deterministic</text>
  </g><line x1="692" y1="124.0" x2="718" y2="124.0" stroke="#898781" stroke-width="1.6" marker-end="url(#pl-arrow)"/>
  <g>
    <rect x="718" y="70" width="150" height="108" rx="12"
          fill="#F0ECF8" stroke="#6B4FA8" stroke-width="1.1"/>
    <text x="793.0" y="98" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="12.5" font-weight="700" fill="#6B4FA8" letter-spacing="0.03em">Explain</text>
    <text x="793.0" y="122" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">figures to</text>
    <text x="793.0" y="138" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="11" fill="#1A3A5C">words</text>
    <text x="793.0" y="165" text-anchor="middle" font-family="'Inter', sans-serif"
          font-size="9.5" font-style="italic" fill="#6B4FA8">model, language</text>
  </g>

  <line x1="14" y1="196" x2="868" y2="196" stroke="#D3D1C7"
        stroke-width="1" stroke-dasharray="3 4"/>
  <text x="441.0" y="218" text-anchor="middle" font-family="'Inter', sans-serif"
        font-size="10.5" fill="#898781">
    A separate spread calculator and three-case runner extend the engine for the
    three-case tool, and are deterministic too.
  </text>

  <g font-family="'Inter', sans-serif" font-size="11">
    <rect x="231.0" y="232" width="11" height="11" rx="2" fill="#F0ECF8" stroke="#6B4FA8"/>
    <text x="248.0" y="241" fill="#1A3A5C">Model, for language</text>
    <rect x="451.0" y="232" width="11" height="11" rx="2" fill="#E8F3E9" stroke="#1D6B0F"/>
    <text x="468.0" y="241" fill="#1A3A5C">Deterministic code, for numbers</text>
  </g>
</svg>"""
