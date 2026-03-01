"""Lightweight Local RAG utilities (Phase 2.2).

This module intentionally avoids embeddings/vector DBs.
It implements a small, explainable keyword-based retriever over text chunks.

Key design goals:
- Deterministic + testable
- Robust to empty keywords/chunks
- Simple scoring with optional phrase weighting
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple
from .context_splitter import split_text_recursive


_STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "at", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "being", "it", "this", "that",
    "these", "those", "i", "you", "we", "they", "he", "she", "them", "his", "her", "our",
    "your", "my", "me", "can", "could", "should", "would", "please", "help", "make", "build",
    "create", "design", "about", "into", "using", "use",
}

_SEMANTIC_EXPANSIONS = {
    "voltage": ["volt", "vcc", "vdd", "vin", "vm", "vbatt", "vbat", "supply", "电压"],
    "current": ["amp", "ampere", "iout", "iin", "ilim", "i_peak", "电流"],
    "frequency": ["freq", "hz", "khz", "mhz", "clock", "pwm", "baud", "频率"],
    "temperature": ["temp", "thermal", "celsius", "°c", "℃", "温度"],
    "protocol": ["mqtt", "modbus", "topic", "register", "协议"],
}

_UNIT_TOKENS = {
    "v", "mv",
    "a", "ma",
    "hz", "khz", "mhz",
    "c", "°c", "℃",
}


def _normalize_text(s: str) -> str:
    # Lowercase + collapse whitespace
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(s: str) -> List[str]:
    s = _normalize_text(s)
    # Keep latin/cjk tokens, numbers, underscore, hyphen
    tokens = re.findall(r"[a-z0-9_\-\u4e00-\u9fff]+", s)
    return tokens


def _expand_keywords_semantic(keywords: Sequence[str]) -> List[str]:
    expanded: List[str] = []
    seen = set()
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            expanded.append(kw)

        for root, aliases in _SEMANTIC_EXPANSIONS.items():
            all_terms = [root] + aliases
            if kw in all_terms:
                for term in all_terms:
                    term_norm = _normalize_text(term)
                    if term_norm and term_norm not in seen:
                        seen.add(term_norm)
                        expanded.append(term_norm)
    return expanded


def extract_keywords_fallback(query: str, *, min_len: int = 3, max_keywords: int = 10) -> List[str]:
    """Rule-based keyword fallback when LLM keywords are missing.

    - Extract alnum tokens
    - Filter stopwords
    - Keep unique order
    """
    tokens = _tokenize(query)
    out: List[str] = []
    seen = set()
    for t in tokens:
        if len(t) < min_len:
            continue
        if t in _STOPWORDS_EN:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_keywords:
            break
    return out


def _prepare_keywords(keywords: Sequence[str] | None) -> List[str]:
    if not keywords:
        return []
    out: List[str] = []
    seen = set()
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        kw_norm = _normalize_text(kw)
        if kw_norm in seen:
            continue
        seen.add(kw_norm)
        out.append(kw_norm)
    return _expand_keywords_semantic(out)


def score_chunk(chunk_text: str, keywords: Sequence[str]) -> float:
    """Simple TF-like keyword scoring.

    Heuristics:
    - Count occurrences (substring) of each keyword
    - Phrase keywords (contain space) get extra weight
    - Matches in the first 200 chars get a small boost
    """
    text = _normalize_text(chunk_text)
    if not text:
        return 0.0
    score = 0.0
    head = text[:200]
    text_tokens = set(_tokenize(text))
    keyword_tokens = set()

    for kw in keywords:
        if not kw:
            continue
        # Phrase weighting
        weight = 2.0 if " " in kw else 1.0
        # Occurrence count (substring)
        count = text.count(kw)
        if count:
            score += weight * float(count)
            # Head boost
            if head.count(kw) > 0:
                score += 0.5 * weight

        for tk in _tokenize(kw):
            if tk:
                keyword_tokens.add(tk)

    # Token overlap improves stability when formatting/parsing differs.
    if keyword_tokens:
        overlap = len(text_tokens.intersection(keyword_tokens))
        score += 0.8 * float(overlap)

        query_units = keyword_tokens.intersection(_UNIT_TOKENS)
        text_units = text_tokens.intersection(_UNIT_TOKENS)
        if query_units and text_units:
            score += 1.2 * float(len(query_units.intersection(text_units)))

        # Domain-level semantic boost (e.g., "voltage" query vs "VCC" chunk).
        for root, aliases in _SEMANTIC_EXPANSIONS.items():
            group = {_normalize_text(root)} | {_normalize_text(a) for a in aliases}
            if group.intersection(keyword_tokens) and group.intersection(text_tokens):
                score += 1.5

    return score


def retrieve_top_k_chunks(
    chunks: Sequence[str],
    keywords: Sequence[str] | None,
    *,
    k: int = 5,
    max_chunk_chars: int = 1200,
) -> List[Dict[str, object]]:
    """Return Top-K chunks with ids and scores.

    Output items:
      {"chunk_id": int, "score": float, "text": str}
    """

    if not chunks:
        return []
    k = int(k) if k is not None else 5
    if k <= 0:
        return []
    # hard cap to keep prompts bounded
    k = min(k, 10)

    kws = _prepare_keywords(keywords)

    scored: List[Tuple[int, float, str]] = []
    for idx, ch in enumerate(chunks):
        s = score_chunk(ch, kws) if kws else 0.0
        scored.append((idx, s, ch))

    # If all scores are zero (or no keywords), fallback to first chunk(s)
    if (not kws) or (max(s for _, s, _ in scored) <= 0.0):
        out = []
        for idx in range(min(k, len(chunks))):
            text = chunks[idx]
            if max_chunk_chars and len(text) > max_chunk_chars:
                text = text[:max_chunk_chars].rstrip() + "…"
            out.append({"chunk_id": idx, "score": 0.0, "text": text})
        return out

    # Sort by score desc, then prefer shorter chunks (less prompt weight) when scores tie
    scored.sort(key=lambda t: (t[1], -len(t[2])), reverse=True)
    top = scored[: min(k, len(scored))]
    out: List[Dict[str, object]] = []
    for idx, s, text in top:
        if max_chunk_chars and len(text) > max_chunk_chars:
            text = text[:max_chunk_chars].rstrip() + "…"
        out.append({"chunk_id": idx, "score": float(s), "text": text})
    return out


def format_retrieved_chunks_for_prompt(
    retrieved: Sequence[Dict[str, object]],
    *,
    total_chunks: int | None = None,
) -> str:
    """Format retrieved chunks for prompt injection with ids."""
    if not retrieved:
        return ""
    lines: List[str] = []
    for item in retrieved:
        cid = item.get("chunk_id")
        score = item.get("score")
        text = (item.get("text") or "").strip()
        lines.append(f"[CHUNK id={cid} score={score}]\n{text}")
    if total_chunks is not None:
        lines.append(f"\n[NOTE] Injected {len(retrieved)} chunks out of {total_chunks} total chunks.")
    return "\n\n".join(lines).strip()
