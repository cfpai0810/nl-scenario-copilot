# =============================================================================
# pages/your_own_data.py: bring-your-own-data documentation (docs page)
# =============================================================================
# Renders docs/bring_your_own_data.md at runtime (single source) with heading
# levels shifted to match the other docs pages. No key gate.
# =============================================================================

import re
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css

st.markdown(inject_css(), unsafe_allow_html=True)

st.markdown("### Your own data")

_doc_path = ROOT / "docs" / "bring_your_own_data.md"
_doc = _doc_path.read_text(encoding="utf-8")

_lines = _doc.splitlines()
if _lines and _lines[0].startswith("# "):
    _lines = _lines[1:]
_doc = "\n".join(_lines).lstrip("\n")

_doc = re.sub(
    r"^(#{2,3}) ",
    lambda m: "#" * (len(m.group(1)) + 2) + " ",
    _doc,
    flags=re.MULTILINE,
)

_doc = re.sub(r"\n(#### )", r"\n---\n\1", _doc)

st.markdown(_doc)

st.divider()
st.caption(
    "This page describes the sample model's data layout. Your own data "
    "follows the same structure with your own figures.")
