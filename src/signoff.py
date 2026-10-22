# =============================================================================
# src/signoff.py: the sign-off rule, the same in every project of the series
# =============================================================================
# A result is a DRAFT until a named reviewer types the word SIGN exactly. The
# page only draws the section: the rule, the messages and the signed lines
# live here, so the web app, the PDF and the tests share one definition
# (after Project 6's rule in its step6_review.py).
# =============================================================================

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

SIGN_WORD = "SIGN"
NAME_LIMIT = 80
DRAFT = "draft"
FINAL = "final"

NAME_REFUSAL = "Sign-off needs a reviewer name, so this stays a DRAFT."
WORD_REFUSAL = "Sign-off needs the word SIGN typed exactly, so this stays a DRAFT."
ALREADY_SIGNED = "This result is already signed off. A new run starts a new DRAFT."
STALE_CAPTION = "Sign-off waits for a run made from the inputs as they now stand."
WORD_LABEL = "Type SIGN to sign off"
WORD_HELP = "Exactly SIGN, in capitals. Anything else leaves this a DRAFT."
SCOPE_NOTE = ("The name and time are kept for this session and go into this PDF. They are "
              "not stored on the server, and the name is not verified.")
DRAFT_PDF_LINE = "DRAFT: not signed off"


def clean_name(name) -> str:
    """The reviewer's name as it is kept and shown: control characters removed,
    spaces around it trimmed, at most NAME_LIMIT characters."""
    if name is None:
        return ""
    text = "".join(ch for ch in str(name) if unicodedata.category(ch)[0] != "C")
    return text.strip()[:NAME_LIMIT].strip()


def pdf_name(name: str) -> str:
    """The name as the PDF's standard fonts can print it: a character outside
    Latin-1 becomes '?'. The screen and the audit keep the name as typed.
    Escape the result with the app's PDF text helper before a Paragraph."""
    return "".join(ch if ord(ch) < 256 else "?" for ch in name)


def is_sign_word(text) -> bool:
    """The one SIGN rule: the word SIGN exactly, spaces around it ignored.
    'sign' is not SIGN."""
    return text is not None and str(text).strip() == SIGN_WORD


def when_text(moment: datetime) -> str:
    """A UTC time as a person reads it: '9 October 2026 at 08:04 UTC'."""
    dt = moment.astimezone(timezone.utc)
    return f"{dt.day} {dt:%B %Y} at {dt:%H:%M} UTC"


@dataclass(frozen=True)
class SignOff:
    """A sign-off that stands: who, and when (UTC)."""
    name: str
    signed_at: datetime

    @property
    def line(self) -> str:
        """The screen's signed line."""
        return f"Signed off by {self.name} on {when_text(self.signed_at)}."

    @property
    def pdf_line(self) -> str:
        """The PDF's status line for a FINAL."""
        return f"FINAL: signed off by {pdf_name(self.name)} on {when_text(self.signed_at)}"

    def record(self, acknowledged) -> dict:
        """What the session audit keeps for this sign-off."""
        return {"status": FINAL, "signed_off_by": self.name,
                "signed_off_at": self.signed_at.isoformat(),
                "acknowledged_at_sign_off": list(acknowledged)}


def sign_off(name, word, *, already_signed: bool = False, now: datetime | None = None):
    """(SignOff, None) when the sign-off stands, or (None, refusal) when the
    result stays a DRAFT. The checks run in this order: already signed, name,
    word."""
    if already_signed:
        return None, ALREADY_SIGNED
    cleaned = clean_name(name)
    if not cleaned:
        return None, NAME_REFUSAL
    if not is_sign_word(word):
        return None, WORD_REFUSAL
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return SignOff(cleaned, moment), None


def pdf_status_line(signed) -> str:
    """The PDF's status line: DRAFT or FINAL."""
    return signed.pdf_line if signed is not None else DRAFT_PDF_LINE
