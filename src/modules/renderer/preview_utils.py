# preview_utils.py
"""Preview utilities (Phase 4.3).

Provides a stable "open in new tab" link for Streamlit using a Data URI.
We keep this logic as a small, testable helper.
"""

from __future__ import annotations

import base64
from typing import Dict


def build_data_uri_link(
    html_str: str,
    *,
    label: str = "Open in new tab",
    max_bytes: int = 1_500_000,
) -> Dict[str, object]:
    """Build a target=_blank anchor using a `data:text/html;base64,...` URL.

    Returns:
      {
        "ok": bool,
        "html": str (anchor html, only when ok=True),
        "error": str | None,
        "byte_len": int,
      }
    """
    if not isinstance(html_str, str):
        return {"ok": False, "html": "", "error": "html_str must be a string", "byte_len": 0}

    raw = html_str.encode("utf-8", errors="replace")
    byte_len = len(raw)

    if byte_len == 0:
        return {"ok": False, "html": "", "error": "empty HTML", "byte_len": 0}

    if byte_len > max_bytes:
        return {
            "ok": False,
            "html": "",
            "error": f"HTML too large for Data URI ({byte_len} bytes > {max_bytes} bytes). Use download instead.",
            "byte_len": byte_len,
        }

    b64 = base64.b64encode(raw).decode("ascii")
    href = f"data:text/html;base64,{b64}"

    anchor = (
        f'<a href="{href}" target="_blank" rel="noopener noreferrer" '
        f'style="display:inline-block;width:100%;text-align:center;'
        f'padding:0.5rem 0.75rem;border-radius:0.5rem;'
        f'border:1px solid rgba(255,255,255,0.15);'
        f'text-decoration:none;color:inherit;">{label}</a>'
    ) # Added color:inherit to make it look decent in st.markdown default theme

    return {"ok": True, "html": anchor, "error": None, "byte_len": byte_len}
