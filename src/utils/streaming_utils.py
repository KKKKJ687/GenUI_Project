from __future__ import annotations

from typing import Any, Optional


def chunk_to_text(chunk: Any) -> str:
    """
    Robustly extract text from google.generativeai streaming chunks.

    This function is intentionally defensive because different SDK versions / models
    may return slightly different chunk shapes.

    Returns:
        str: extracted text (may be empty).
    """
    if chunk is None:
        return ""

    # If already a string
    if isinstance(chunk, str):
        return chunk

    # Common case: chunk.text
    t = getattr(chunk, "text", None)
    if isinstance(t, str):
        return t

    # Some SDKs: chunk.candidates[0].content.parts[*].text
    candidates = getattr(chunk, "candidates", None)
    if candidates and isinstance(candidates, (list, tuple)):
        try:
            c0 = candidates[0]
            content = getattr(c0, "content", None)
            parts = getattr(content, "parts", None)
            if parts and isinstance(parts, (list, tuple)):
                out = []
                for p in parts:
                    pt = getattr(p, "text", None)
                    if isinstance(pt, str) and pt:
                        out.append(pt)
                return "".join(out)
        except Exception:
            pass

    # Dict-like fallback
    if isinstance(chunk, dict):
        for k in ("text", "output_text", "content"):
            v = chunk.get(k)
            if isinstance(v, str):
                return v

    return ""


import re
import json

def extract_json_from_text(text: str) -> str:
    """
    Robustly extracts JSON object from a string which may contain markdown or conversational text.
    Strategies:
      1. Regex for ```json ... ``` block
      2. Regex for ``` ... ``` block
      3. Find outer-most { ... } pair
    """
    if not text:
        return "{}"

    # 1. Try ```json ... ```
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 2. Try ``` ... ``` (any code block)
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 3. Try finding outermost { }
    # This is a simple heuristic: first '{' to last '}'
    start = text.find('{')
    end = text.rfind('}')
    
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        # Quick validation check: try strict parse? 
        # Or just return it and let the caller fail if it's bad.
        return candidate
    
    # 4. Fallback: maybe the whole text is JSON?
    return text.strip()
