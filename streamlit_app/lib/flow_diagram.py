# =============================================================================
# lib/flow_diagram.py: a flow of stages in HTML and CSS, readable on a phone
# =============================================================================
# One renderer and its CSS for the flow diagrams of the series. A diagram is an
# ordered list of stages: in a window 640 px wide or more, a row of boxes with an
# arrow between each; below that, one box under another with a down arrow
# between them. The row also needs 600 px for the diagram itself (a 640 px window
# gives it 608): where an open sidebar leaves less, the stages stay stacked
# rather than break words. The text wraps inside its box, so it keeps its size
# at any width, and the page keeps no fixed-height frame.
#
# The words, the order and the colours all come from the caller: this module
# holds none of an app's own words, so the same file serves every app byte for
# byte. Each box takes its role's colours (border, title and foot in the
# stroke, the box in the fill). An optional boundary between two stages marks
# where one zone ends and the next begins, with a label for each zone, in both
# layouts. The list's accessible name says what the diagram shows.
#
# Four further kinds of diagram, each asked for by an optional field or
# argument whose default draws exactly what was drawn before; their CSS rules
# are added after the rules above, under class names of their own:
# - words on an arrow (Stage.arrow): above the arrow in a row, beside it when
#   stacked, with a wider gap between the boxes of a row to hold them;
# - a path back (flow_html(back=...)): a dashed path from the last stage back
#   to the first, with its words, under a row or down the side of a stack;
# - layers (flow_html(layers=True)): the stages one above another at every
#   width, text to the left, with no arrows;
# - a larger row minimum (flow_html(row_min=...)), for a diagram whose words
#   need more room than ROW_MIN gives them: stacked, as on a phone, until the
#   diagram has that width; from there a row in which no box is narrower than
#   its longest word. The values on offer are ROW_MINS, so the CSS is fixed.
# =============================================================================

from dataclasses import dataclass
from html import escape
from urllib.parse import quote

BREAKPOINT = 640      # px of window width: a row from here up, stacked below
ROW_MIN = 600         # px the diagram itself needs for a row of boxes
ROW_MINS = (660, 680)   # px: the larger row minimums a diagram may ask for
ARROW_GAP = 64        # px between two boxes of a row with words on the arrows


@dataclass(frozen=True)
class Role:
    """The colours of one kind of stage: the stroke for its border, title and
    foot, and the fill for its box."""
    stroke: str
    fill: str


@dataclass(frozen=True)
class Stage:
    """One box: a title, a line of text, and a foot that says who owns the
    stage. An empty text or foot is left out. `arrow` is the words on the
    arrow into the stage, if any: a few short words, as they sit on one line."""
    title: str
    text: str
    foot: str
    role: Role
    arrow: str = ""


@dataclass(frozen=True)
class Boundary:
    """Where one zone ends and the next begins: just before stage `index`.
    `before` names the zone up to there and `after` the zone from there."""
    index: int
    before: str
    after: str


def _span(cls, words, colour=""):
    style = f' style="color:{colour}"' if colour else ""
    return f'<span class="{cls}"{style}>{escape(words)}</span>'


def _whole(words):
    """Words for a line that may wrap, each hyphenated word kept on one line."""
    return " ".join(f'<span class="fd-nobr">{escape(word)}</span>' if "-" in word
                    else escape(word) for word in words.split(" "))


def flow_html(label, stages, *, heading="", note="", key=(), boundary=None,
              back="", layers=False, row_min=None):
    """The diagram as one line of HTML, for st.markdown(unsafe_allow_html=True).

    `label` is the accessible name of the list of stages; `heading` is the line
    above the boxes and `note` the line below them; `key` is the colour key, as
    (role, words) pairs; `boundary` is an optional Boundary.

    The optional kinds: `back` is the words on a dashed path from the last
    stage back to the first; `layers` draws the stages as layers, one above
    another with no arrows; `row_min`, one of ROW_MINS, keeps the stages
    stacked until the diagram has that many px, and lets no box of the row be
    narrower than its longest word. Words on the arrows and a path back are
    drawn in a flow with no boundary and no larger row minimum; layers take
    none of the other kinds."""
    if boundary is not None and not 0 < boundary.index < len(stages):
        raise ValueError("a boundary sits between two stages")
    if row_min is not None and (type(row_min) is not int or row_min not in ROW_MINS):
        raise ValueError(f"a larger row minimum is one of {ROW_MINS} px")
    worded = any(stage.arrow for stage in stages)
    if stages and stages[0].arrow:
        raise ValueError("words on an arrow need a stage before it")
    if back and len(stages) < 2:
        raise ValueError("a path back runs from the last stage to the first")
    if layers and (worded or back or row_min is not None or boundary is not None):
        raise ValueError("layers have no arrows, path back, boundary or row")
    if (worded or back) and (boundary is not None or row_min is not None):
        raise ValueError("words on the arrows and a path back are drawn in a "
                         "flow with no boundary and no larger row minimum")
    items = []
    for i, stage in enumerate(stages):
        cls, parts = "fd-stage", []
        if boundary is not None and i == 0:
            parts.append(_span("fd-zone", boundary.before))
        if boundary is not None and i == boundary.index:
            cls = "fd-stage fd-cross"
            parts.append(_span("fd-zone", boundary.after))
        if layers:
            cls = "fd-layer"
        if stage.arrow:
            parts.append(_span("fd-arrow", stage.arrow))
        parts.append(_span("fd-title", stage.title, stage.role.stroke))
        if stage.text:
            parts.append(_span("fd-text", stage.text))
        if stage.foot:
            parts.append(_span("fd-foot", stage.foot, stage.role.stroke))
        style = f"border-color:{stage.role.stroke};background:{stage.role.fill}"
        items.append(f'<li class="{cls}" style="{style}">{" ".join(parts)}</li>')
    zoned = " fd-zoned" if boundary is not None else ""
    kinds = "".join(name for name, on in ((" fd-worded", worded), (" fd-loop", back),
                                          (f" fd-row{row_min}", row_min)) if on)
    ol = "fd-layers" if layers else f"fd-flow{zoned}{kinds}"
    out = ['<figure class="fd">']
    if heading:
        out.append(f'<p class="fd-head">{escape(heading)}</p>')
    out.append(f'<ol class="{ol}" aria-label="{escape(label)}">'
               f'{"".join(items)}</ol>')
    if back:
        out.append(f'<p class="fd-return" style="--fd-n:{len(stages)}">'
                   f'{_whole(back)}</p>')
    if note:
        out.append(f'<p class="fd-note">{escape(note)}</p>')
    if key:
        swatches = "".join(
            f'<li><span class="fd-swatch" aria-hidden="true" style="border-color:'
            f'{role.stroke};background:{role.fill}"></span> {escape(words)}</li>'
            for role, words in key)
        out.append(f'<ul class="fd-key">{swatches}</ul>')
    out.append("</figure>")
    return "".join(out)


def _chevron(colour, down, up=False):
    """An arrow head as a CSS image, pointing right or down (or, with `up`,
    up)."""
    box, path = ("0 0 10 8", "M1 1L5 7L9 1") if down else ("0 0 8 10", "M1 1L7 5L1 9")
    if up:
        box, path = "0 0 10 8", "M1 7L5 1L9 7"
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{box}'>"
           f"<path d='{path}' fill='none' stroke='{colour}' stroke-width='1.6' "
           f"stroke-linecap='round' stroke-linejoin='round'/></svg>")
    return f'url("data:image/svg+xml,{quote(svg, safe="")}")'


def flow_css(ink, muted, rule):
    """The CSS for flow_html, for the app's inject_css(). `ink` is the text
    colour, `muted` the arrows and the quiet lines, `rule` the dashed lines.
    The rules for the optional kinds come after the first block, so that block
    stays as it was, rule for rule."""
    line = f"linear-gradient({muted}, {muted})"
    down, right = _chevron(muted, True), _chevron(muted, False)
    up = _chevron(muted, False, up=True)
    larger = "".join(_row_min_css(width, down, line, rule) for width in ROW_MINS)
    return f"""
      /* Flow diagrams (lib/flow_diagram.py): one box under another on a
         phone, a row of boxes from {BREAKPOINT} px */
      figure.fd {{ container: fd / inline-size; margin: 4px 0 20px; padding: 0; }}
      .fd p.fd-head {{
        margin: 0 0 14px; text-align: center; font-size: 15px; font-weight: 600;
        line-height: 1.4; color: {ink};
      }}
      .fd ol.fd-flow {{
        list-style: none; margin: 0; padding: 0;
        display: flex; flex-direction: column;
      }}
      .fd ol.fd-flow.fd-zoned {{ padding-top: 24px; }}
      .fd ol.fd-flow > li.fd-stage {{
        position: relative; display: flex; flex-direction: column; min-width: 0;
        margin: 0; padding: 10px 12px; border: 1px solid; border-radius: 12px;
        text-align: center; color: {ink}; line-height: 1.35;
        overflow-wrap: break-word;
      }}
      .fd ol.fd-flow > li.fd-stage + li.fd-stage {{ margin-top: 30px; }}
      .fd ol.fd-flow > li.fd-stage + li.fd-stage::before {{
        content: ""; position: absolute; left: 50%; bottom: 100%;
        width: 10px; height: 31px; margin: 0 0 0 -5px;
        background: {down} center bottom 2px / 10px 8px no-repeat,
                    {line} center top / 1.6px calc(100% - 4px) no-repeat;
      }}
      .fd ol.fd-flow > li.fd-stage + li.fd-cross {{ margin-top: 58px; }}
      .fd ol.fd-flow > li.fd-stage + li.fd-cross::before {{ height: 59px; }}
      .fd ol.fd-flow > li.fd-cross::after {{
        content: ""; position: absolute; left: -1px; right: -1px; top: -36px;
        border-top: 1.2px dashed {rule};
      }}
      .fd .fd-zone {{
        position: absolute; left: -1px; bottom: 100%; margin-bottom: 4px;
        font-size: 13px; line-height: 1.3; color: {muted}; white-space: nowrap;
        text-align: left;
      }}
      .fd .fd-title {{
        font-size: 14px; font-weight: 700; letter-spacing: 0.03em; line-height: 1.3;
      }}
      .fd .fd-text {{ margin-top: 4px; font-size: 15px; color: {ink}; }}
      .fd .fd-foot {{
        margin-top: auto; padding-top: 6px; font-size: 13px; font-style: italic;
      }}
      .fd p.fd-note {{
        margin: 16px 0 0; padding-top: 10px; border-top: 1px dashed {rule};
        text-align: center; font-size: 13px; line-height: 1.45; color: {muted};
      }}
      .fd ul.fd-key {{
        list-style: none; margin: 8px 0 0; padding: 0;
        display: flex; flex-wrap: wrap; justify-content: center; gap: 4px 18px;
        font-size: 13px; line-height: 1.4; color: {ink};
      }}
      .fd ul.fd-key li {{
        display: flex; align-items: center; gap: 6px; margin: 0; padding: 0;
        font-size: 13px;
      }}
      .fd .fd-swatch {{
        flex: none; width: 11px; height: 11px; border: 1px solid; border-radius: 2px;
      }}
      @media (min-width: {BREAKPOINT}px) {{ @container fd (min-width: {ROW_MIN}px) {{
        .fd ol.fd-flow {{ flex-direction: row; align-items: stretch; }}
        .fd ol.fd-flow > li.fd-stage {{ flex: 1 1 0; padding: 12px 6px; }}
        .fd ol.fd-flow > li.fd-stage + li.fd-stage {{ margin: 0 0 0 22px; }}
        .fd ol.fd-flow > li.fd-stage + li.fd-stage::before {{
          left: auto; right: 100%; bottom: auto; top: 50%;
          width: 22px; height: 10px; margin: -5px 0 0;
          background: {right} right 2px center / 8px 10px no-repeat,
                      {line} left 1px center / calc(100% - 5px) 1.6px no-repeat;
        }}
        .fd ol.fd-flow > li.fd-cross::after {{
          left: -12px; right: auto; top: -24px; bottom: -1px;
          border-top: 0; border-left: 1.2px dashed {rule};
        }}
        .fd .fd-title {{ font-size: 13px; letter-spacing: 0.02em; }}
        .fd .fd-text {{ font-size: 14px; }}
      }} }}

      /* Flow diagrams, the optional kinds, each under class names of its own:
         words on the arrows (fd-worded, fd-arrow), a path back from the last
         stage to the first (fd-loop, fd-return, fd-nobr), layers with no
         arrows (fd-layers, fd-layer), and a larger row minimum (fd-row660
         and the like, one for each width in ROW_MINS) */
      .fd ol.fd-flow .fd-arrow {{
        position: absolute; left: calc(50% + 12px); bottom: 100%; height: 30px;
        margin-bottom: 1px; display: flex; align-items: center;
        font-size: 13px; line-height: 1.3; color: {muted}; white-space: nowrap;
      }}
      .fd ol.fd-flow.fd-loop {{ position: relative; padding-left: 26px; }}
      .fd ol.fd-flow.fd-loop::before {{
        content: ""; position: absolute; left: 8px; top: 20px; bottom: 0;
        width: 18px; border: 0 dashed {muted}; border-width: 1.2px 0 0 1.2px;
      }}
      .fd ol.fd-flow.fd-loop::after {{
        content: ""; position: absolute; left: 18px; top: 15.6px;
        width: 8px; height: 10px; background: {right} center / 8px 10px no-repeat;
      }}
      .fd p.fd-return {{
        position: relative; margin: 0 0 0 8px; padding: 30px 0 0 12px;
        text-align: left; font-size: 13px; line-height: 1.4; color: {muted};
      }}
      .fd p.fd-return::after {{
        content: ""; position: absolute; left: 0; top: 0;
        width: calc(50% + 9.6px); height: 20px;
        border: 0 dashed {muted}; border-width: 0 1.2px 1.2px;
      }}
      .fd .fd-nobr {{ white-space: nowrap; }}
      .fd ol.fd-layers {{
        list-style: none; margin: 0; padding: 0;
        display: flex; flex-direction: column; gap: 8px;
      }}
      .fd ol.fd-layers > li.fd-layer {{
        display: flex; flex-direction: column; min-width: 0;
        margin: 0; padding: 10px 14px; border: 1px solid; border-radius: 12px;
        text-align: left; color: {ink}; line-height: 1.35;
        overflow-wrap: break-word;
      }}
      .fd ol.fd-layers .fd-title {{ font-size: 14px; letter-spacing: 0.03em; }}
      .fd ol.fd-layers .fd-text {{ font-size: 15px; }}
      @media (min-width: {BREAKPOINT}px) {{ @container fd (min-width: {ROW_MIN}px) {{
        .fd ol.fd-flow.fd-worded > li.fd-stage + li.fd-stage {{
          margin: 0 0 0 {ARROW_GAP}px;
        }}
        .fd ol.fd-flow.fd-worded > li.fd-stage + li.fd-stage::before {{
          width: {ARROW_GAP}px;
        }}
        .fd ol.fd-flow .fd-arrow {{
          left: auto; right: 100%; bottom: 50%; width: {ARROW_GAP}px; height: auto;
          margin: 0 1px 6px 0; justify-content: center;
        }}
        .fd ol.fd-flow.fd-loop {{ padding-left: 0; }}
        .fd ol.fd-flow.fd-loop::before, .fd ol.fd-flow.fd-loop::after {{
          content: none;
        }}
        .fd p.fd-return {{
          margin: 0 calc((100% - (var(--fd-n) - 1) * 22px) / var(--fd-n) / 2 - 0.6px);
          padding: 8px 12px 6px; text-align: center;
          border: 0 dashed {muted}; border-width: 0 1.2px 1.2px;
        }}
        .fd ol.fd-flow.fd-worded + p.fd-return {{
          margin: 0 calc((100% - (var(--fd-n) - 1) * {ARROW_GAP}px) / var(--fd-n) / 2
                         - 0.6px);
        }}
        .fd p.fd-return::after {{ content: none; }}
        .fd p.fd-return::before {{
          content: ""; position: absolute; left: -5.6px; top: 0;
          width: 10px; height: 8px; background: {up} center / 10px 8px no-repeat;
        }}
      }} }}
{larger}"""


def _row_min_css(width, down, line, rule):
    """The rules for a diagram that asks for a row minimum of `width` px: from
    ROW_MIN px up to `width`, the rules of a stack, as on a phone, put back
    over those of the row (a boundary included); from `width`, a row in which
    no box is narrower than its longest word."""
    flow = f".fd ol.fd-flow.fd-row{width}"
    return f"""      @media (min-width: {BREAKPOINT}px) {{
        @container fd (min-width: {ROW_MIN}px) and (width < {width}px) {{
        {flow} {{ flex-direction: column; }}
        {flow} > li.fd-stage {{ flex: 0 1 auto; padding: 10px 12px; }}
        {flow} > li.fd-stage + li.fd-stage {{ margin: 30px 0 0; }}
        {flow} > li.fd-stage + li.fd-stage::before {{
          left: 50%; right: auto; bottom: 100%; top: auto;
          width: 10px; height: 31px; margin: 0 0 0 -5px;
          background: {down} center bottom 2px / 10px 8px no-repeat,
                      {line} center top / 1.6px calc(100% - 4px) no-repeat;
        }}
        {flow} > li.fd-stage + li.fd-cross {{ margin-top: 58px; }}
        {flow} > li.fd-stage + li.fd-cross::before {{ height: 59px; }}
        {flow} > li.fd-cross::after {{
          left: -1px; right: -1px; top: -36px; bottom: auto;
          border-top: 1.2px dashed {rule}; border-left: 0;
        }}
        {flow} .fd-title {{ font-size: 14px; letter-spacing: 0.03em; }}
        {flow} .fd-text {{ font-size: 15px; }}
      }} }}
      @media (min-width: {BREAKPOINT}px) {{ @container fd (min-width: {width}px) {{
        {flow} > li.fd-stage {{ min-width: auto; }}
      }} }}
"""
