# =============================================================================
# lib/explanation_cards.py - card treatment for the explanation surface
# =============================================================================
# Three cards, matching Project 1's visual grammar mapped onto Project 4's
# explanation structure:
#   1. Takeaways callout (leading, tinted)
#   2. Explanation body  (clean paragraphs in a bordered container)
#   3. Held-constant caution note (amber, only when present)
#
# Escaping approach: the takeaways and held-constant strings are deterministic
# Python text (not model-generated) and are HTML-escaped into styled HTML
# blocks. The explanation is Claude-generated free text and is rendered
# through st.markdown WITHOUT unsafe_allow_html, so markdown formatting
# survives and no HTML injection is possible.
# =============================================================================

import html as _html

import streamlit as st


def render_explanation_cards(takeaways, explanation, held_constant=None):
    """Render the explanation surface as cards.

    takeaways:      list[str], 2-4 deterministic plain-text items
    explanation:    str, Claude-generated prose with paragraph breaks
    held_constant:  list[str] with 0-1 items, or None (three-case)
    """

    # 1. Takeaways callout (deterministic text, HTML-escaped for safety)
    if takeaways:
        bullets = "".join(
            "<li>{}</li>".format(_html.escape(t)) for t in takeaways)
        st.markdown(
            '<div class="sc-exp-callout">'
            '<p class="sc-exp-kicker">Key takeaways</p>'
            '<ul>{}</ul>'
            '</div>'.format(bullets),
            unsafe_allow_html=True)

    # 2. Explanation body (Claude text through safe st.markdown)
    if explanation:
        with st.container(border=True):
            st.markdown(
                '<p class="sc-exp-kicker">Analysis</p>',
                unsafe_allow_html=True)
            for para in explanation.split("\n\n"):
                para = para.strip()
                if para:
                    st.markdown(para)

    # 3. Held-constant caution note (deterministic text, HTML-escaped)
    if held_constant:
        text = " ".join(held_constant)
        st.markdown(
            '<div class="sc-exp-caution">'
            '<strong>Assumption held constant</strong><br>'
            '{}</div>'.format(_html.escape(text)),
            unsafe_allow_html=True)
