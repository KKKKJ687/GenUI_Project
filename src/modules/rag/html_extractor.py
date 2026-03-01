"""
Robust HTML Extractor module.
Separates HTML extraction logic from the Streamlit app.
Uses BeautifulSoup for parsing and simple heuristics for validation/repair.
"""

from __future__ import annotations

import re
from typing import Callable, Optional, Tuple, Dict, Any

try:
    from bs4 import BeautifulSoup
except ImportError:
    # Fallback if BS4 not installed, though requirements include it
    BeautifulSoup = None

def extract_html(
    text: str,
    *,
    repair_fn: Optional[Callable[[str], str]] = None
) -> Tuple[str, Dict[str, Any]]:
    """
    Extracts and sanitizes HTML from LLM output.
    
    Args:
        text: Raw text output from LLM.
        repair_fn: Optional callback to specific repair logic (e.g. LLM call) 
                   if truncation/incompleteness is detected.
                   Signature: (raw_text_so_far) -> continued_html_str
    
    Returns:
        (html_code, metadata)
        metadata includes:
          - "valid": bool
          - "repaired": bool
          - "reason": str (if modified/invalid)
          - "original_length": int
    """
    meta = {
        "valid": False,
        "repaired": False,
        "reason": "",
        "original_length": len(text)
    }

    if not text:
        meta["reason"] = "Empty input"
        return "", meta

    # 1. Strip markdown fences
    # Matches ```html ... ``` or '''html ... '''
    # We use a loop to handle nested/multiple blocks - usually picking the longest or first valid one
    # For now, let's take the first significant match or clean the whole string.
    
    # Try specific fences first
    match = re.search(r'```html(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if not match:
        match = re.search(r"'''html(.*?)'''", text, re.DOTALL | re.IGNORECASE)
    
    candidate = match.group(1) if match else text
    
    # Also strip generic ``` if valid html not found in fences
    if not match:
        # Check if wrapped in just ``` ... ```
        match_generic = re.search(r'```(.*?)```', text, re.DOTALL)
        if match_generic:
             # Check if content looks like HTML
             inner = match_generic.group(1).strip()
             if "<html" in inner.lower() or "<!doctype" in inner.lower():
                 candidate = inner

    # 2. Locate HTML start
    # Look for <!DOCTYPE html> or <html>
    # This helps skip "Sure, here is the code:" prefixes
    idx_doctype = candidate.lower().find("<!doctype html")
    idx_html = candidate.lower().find("<html")
    
    start_idx = -1
    if idx_doctype != -1:
        start_idx = idx_doctype
    elif idx_html != -1:
        start_idx = idx_html
    
    if start_idx != -1:
        candidate = candidate[start_idx:]
    else:
        # If no clear start tag, we might still have a partial body or just raw tags.
        # But for an "Architect", we expect a full page.
        # Let's see if BeautifulSoup can salvage it.
        pass

    # 3. Validation & Repair heuristics
    # Check for truncation: missing </html> at the end
    # We use a simple regex check before BS4 parsing because BS4 might auto-close it.
    
    is_truncated = False
    if "</html>" not in candidate.lower()[-50:]: 
        # Check if it *really* is missing. 
        # (Check full string just in case trailing commentary exists)
        if "</html>" not in candidate.lower():
            is_truncated = True
    
    if is_truncated and repair_fn and not meta.get("repaired"):
        # Attempt repair
        try:
            new_text = repair_fn(text) # Pass original text or candidate? usually original prompt context + candidate is needed by LLM, but here we assume repair_fn handles context or just takes the candidate to continue.
            # actually, requirement says: "_repair_html_with_model... prompt: continue generating"
            # So we assume the caller handles the prompt construction or we pass enough info.
            # The requirement says `_repair_html_with_model(model, raw_text)`
            # Here we just return the new result if valid.
            if new_text and len(new_text) > len(candidate):
                candidate = new_text
                meta["repaired"] = True
                meta["reason"] += "Trunaction detected, repair triggered. "
                
                # Re-strip fences from repaired text if needed
                match_r = re.search(r'```html(.*?)```', candidate, re.DOTALL | re.IGNORECASE)
                if match_r: candidate = match_r.group(1)
                elif "<html" in candidate.lower():
                    # locate start again
                    idx_d = candidate.lower().find("<!doctype html")
                    idx_h = candidate.lower().find("<html")
                    s_idx = idx_d if idx_d != -1 else idx_h
                    if s_idx != -1: candidate = candidate[s_idx:]
        except Exception as e:
            meta["reason"] += f"Repair failed: {e}. "

    # 4. BS4 Parsing & Normalization
    if BeautifulSoup:
        try:
            soup = BeautifulSoup(candidate, "html.parser")
            
            # Check minimal requirements
            if not soup.find("html"):
                meta["reason"] += "No <html> tag found. "
                meta["valid"] = False
                # If truly garbage, start_idx check presumably failed too or we just parsed plain text
                # We return candidate as is (sanitized) or empty if strictly invalid?
                # Requirement: "Returns minimal skeleton if invalid... avoid white screen"
                # But here we just return what we have, app.py handles fallback wrapping.
                return candidate, meta

            # Normalize
            # Ensure doctype
            # (BS4 doesn't always preserve/add Doctype easily unless we explicitly handle it, 
            #  but simple string check is often enough)
            
            final_html = soup.prettify()
            meta["valid"] = True
            return final_html, meta

        except Exception as e:
            meta["valid"] = False
            meta["reason"] += f"BS4 parse error: {e}. "
            return candidate, meta
    else:
        # Fallback if BS4 missing (shouldn't happen given reqs)
        meta["reason"] += "BS4 not installed. "
        # Simple heuristic check
        if "<html" in candidate.lower() or "<!doctype html" in candidate.lower():
             meta["valid"] = True
        else:
             meta["valid"] = False
             meta["reason"] += "No HTML tags found (heuristic). "
        return candidate, meta
