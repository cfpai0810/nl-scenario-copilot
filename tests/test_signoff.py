"""The shared sign-off rule (src/signoff.py): the same tests in every project."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.signoff import (ALREADY_SIGNED, DRAFT_PDF_LINE, NAME_LIMIT,  # noqa: E402
                         NAME_REFUSAL, SIGN_WORD, WORD_REFUSAL, clean_name, is_sign_word,
                         pdf_status_line, sign_off)

WHEN = datetime(2026, 10, 21, 9, 5, tzinfo=timezone.utc)


class TestSignOffRule:
    def test_signoff_needs_a_name(self):
        assert sign_off("   ", "SIGN", now=WHEN) == (None, NAME_REFUSAL)

    def test_signoff_needs_the_word_exactly(self):
        for word in ("", "sign", "Sign", "SIGNED", "S I G N", None):
            assert sign_off("A. Reviewer", word, now=WHEN) == (None, WORD_REFUSAL)

    def test_spaces_around_the_word_are_ignored(self):
        assert is_sign_word("  SIGN ")

    def test_a_good_signoff_is_final_with_its_lines(self):
        signed, refusal = sign_off("A. Reviewer", "SIGN", now=WHEN)
        assert refusal is None
        assert signed.line == "Signed off by A. Reviewer on 21 October 2026 at 09:05 UTC."
        assert signed.pdf_line == ("FINAL: signed off by A. Reviewer on 21 October 2026 "
                                   "at 09:05 UTC")
        assert signed.record(["one flag"])["acknowledged_at_sign_off"] == ["one flag"]

    def test_a_second_signoff_is_refused(self):
        assert sign_off("A. Reviewer", "SIGN", already_signed=True,
                        now=WHEN) == (None, ALREADY_SIGNED)

    def test_the_name_is_cleaned_and_capped(self):
        assert clean_name("  José Núñez\n\t ") == "José Núñez"
        assert clean_name("A\x00B\x1fC") == "ABC"
        assert len(clean_name("x" * 200)) == NAME_LIMIT

    def test_a_name_outside_latin_1_prints_safely_in_the_pdf(self):
        signed, _ = sign_off("Zoë 李", "SIGN", now=WHEN)
        assert signed.name == "Zoë 李"
        assert signed.pdf_line.startswith("FINAL: signed off by Zoë ? on")

    def test_draft_and_final_pdf_lines(self):
        assert pdf_status_line(None) == DRAFT_PDF_LINE == "DRAFT: not signed off"
        signed, _ = sign_off("José Núñez", SIGN_WORD, now=WHEN)
        assert pdf_status_line(signed) == ("FINAL: signed off by José Núñez on "
                                           "21 October 2026 at 09:05 UTC")
