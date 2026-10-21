# =============================================================================
# lib/signoff_section.py: the Sign-off section of both tool pages
# =============================================================================
# After Project 6. The rule, its messages and the signed lines live in
# src/signoff.py; this module only draws the section, the same way on the
# Single what-if and the Three-case page, each under its own key prefix
# (swi_ and tc_), so the two pages never share a sign-off.
#
# A sign-off belongs to the one result it was made for. The page keeps that
# result's fingerprint (everything its PDF prints, and the audit run id), so
# a new run is a new DRAFT: the name stays filled in and SIGN is typed again.
# Sign off is an on_click callback, as in Project 6, so it runs before the
# boxes are drawn.
# =============================================================================

import hashlib
import html
import json

import streamlit as st

from src.signoff import (
    SCOPE_NOTE, STALE_CAPTION, WORD_HELP, WORD_LABEL, pdf_status_line, sign_off,
)
from streamlit_app.lib import audit
from streamlit_app.lib.theme import status_chip_html

# What signing means, under the heading (the same on both pages)
SENTENCE = ("Signing says you have read the result against the figures above. "
            "The figures come from Python. This version does not check the "
            "numbers in the model's wording.")


def fingerprint(result):
    """The result as one hash: every field its PDF prints (the request or
    the cases, the interpretation, the figures, the model's wording) and its
    audit run id, so a new run is a new result even when it repeats the
    figures and the wording."""
    text = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_signoff(prefix, result):
    """The sign-off that stands for this result, or None. One made for
    another result no longer applies: it is dropped, the reviewer's name
    stays filled in and the SIGN box is cleared."""
    signed = st.session_state.get(prefix + "signed_off")
    if signed is not None and \
            st.session_state.get(prefix + "signed_for") != fingerprint(result):
        del st.session_state[prefix + "signed_off"]
        del st.session_state[prefix + "signed_for"]
        st.session_state[prefix + "reviewer_kept"] = signed.name
        st.session_state[prefix + "reviewer"] = signed.name
        st.session_state[prefix + "sign"] = ""
        signed = None
    return signed


def _keep_name(prefix):
    """Keep the name as typed under a key of its own: the box's own state is
    dropped while the page shows no result, between one run and the next."""
    st.session_state[prefix + "reviewer_kept"] = st.session_state.get(
        prefix + "reviewer", "")


def _sign_off(prefix, result, outstanding):
    """The Sign off button (on_click). The rule decides; the page keeps the
    sign-off, the result it was signed for, and its audit row."""
    _keep_name(prefix)
    signed, refusal = sign_off(
        st.session_state.get(prefix + "reviewer"),
        st.session_state.get(prefix + "sign"),
        already_signed=prefix + "signed_off" in st.session_state)
    if refusal:
        st.session_state[prefix + "signoff_refusal"] = refusal
        return
    st.session_state[prefix + "signed_off"] = signed
    st.session_state[prefix + "signed_for"] = fingerprint(result)
    audit.sign_run(result.get("run_id"), signed, outstanding)


def _download(build_pdf, signed, file_name, unavailable, key):
    """The report download: the DRAFT PDF, or the FINAL PDF once signed. The
    PDF carries the status line; if it cannot be built, the page's own plain
    line shows instead and the rest of the page still works."""
    try:
        pdf = build_pdf(pdf_status_line(signed))
    except Exception:
        st.caption(unavailable)
        return
    label = "Download the FINAL PDF" if signed is not None else "Download the DRAFT PDF"
    st.download_button(label, data=pdf, file_name=file_name,
                       mime="application/pdf", key=key)


def render_signoff(prefix, result, outstanding, build_pdf, *, file_name,
                   unavailable, stale=False):
    """The Sign-off section, after the result and before the session audit:
    DRAFT with the outstanding warnings, the two boxes and the button, or
    FINAL and read only. build_pdf(status_line) returns the report's bytes.
    stale: the inputs on screen no longer match the result, so Sign off waits
    for a new run."""
    signed = current_signoff(prefix, result)
    refusal = st.session_state.pop(prefix + "signoff_refusal", None)
    key = prefix + "dl_pdf"

    st.markdown("#### Sign-off")
    st.markdown(SENTENCE)

    if signed is not None:
        st.markdown("{}&nbsp; {}".format(
            status_chip_html(audit.FINAL), html.escape(signed.line)),
            unsafe_allow_html=True)
        if refusal:
            st.warning(refusal)
        _download(build_pdf, signed, file_name, unavailable, key)
        st.caption(SCOPE_NOTE)
        return

    st.markdown(status_chip_html(audit.DRAFT), unsafe_allow_html=True)
    if outstanding:
        st.markdown("Outstanding before you sign:")
        st.markdown("\n".join("{}. {}".format(i, line) for i, line
                              in enumerate(outstanding, start=1)))
    else:
        st.markdown("Nothing is outstanding.")
    if prefix + "reviewer" not in st.session_state and \
            prefix + "reviewer_kept" in st.session_state:
        st.session_state[prefix + "reviewer"] = st.session_state[prefix + "reviewer_kept"]
    st.text_input("Reviewer name", key=prefix + "reviewer",
                  on_change=_keep_name, args=(prefix,))
    st.text_input(WORD_LABEL, key=prefix + "sign", help=WORD_HELP)
    st.button("Sign off", key=prefix + "btn_signoff", type="primary",
              on_click=_sign_off, args=(prefix, result, outstanding),
              disabled=stale)
    if stale:
        st.caption(STALE_CAPTION)
    st.caption(SCOPE_NOTE)
    if refusal:
        st.warning(refusal)
    _download(build_pdf, None, file_name, unavailable, key)
