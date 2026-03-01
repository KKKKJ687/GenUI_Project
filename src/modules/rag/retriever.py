"""
Phase 3: Unified Retriever Interface

Provides a clean API for retrieving evidence chunks from datasheets.
This module unifies the local_rag and datasheet_rag modules.
"""
from typing import List, Optional, Any
from pathlib import Path

# Import from sibling modules
from .datasheet_rag import (
    EvidenceChunk,
    DatasheetIndex,
    ingest_pdf,
    ingest_text,
    retrieve_evidence
)
from .local_rag import (
    retrieve_top_k_chunks,
    extract_keywords_fallback,
    format_retrieved_chunks_for_prompt
)


def retrieve_chunks(
    source: str,
    queries: List[str],
    top_k: int = 5,
    source_type: str = "auto"
) -> List[EvidenceChunk]:
    """
    Unified retrieval interface for datasheet evidence.
    
    Args:
        source: Path to PDF file, or raw text content
        queries: List of query strings to search for
        top_k: Number of top results to return
        source_type: "pdf", "text", or "auto" (detect from source)
        
    Returns:
        List of EvidenceChunk objects with relevance scores
        
    Example:
        >>> chunks = retrieve_chunks(
        ...     "docs/esp32_datasheet.pdf",
        ...     ["maximum voltage", "GPIO current limit"],
        ...     top_k=5
        ... )
        >>> for c in chunks:
        ...     print(f"Page {c.page}: {c.text[:100]}...")
    """
    # Determine source type
    if source_type == "auto":
        if Path(source).exists() and source.lower().endswith(".pdf"):
            source_type = "pdf"
        else:
            source_type = "text"
    
    # Ingest based on type
    if source_type == "pdf":
        index = ingest_pdf(source)
    else:
        index = ingest_text(source)
    
    # Retrieve evidence
    return retrieve_evidence(index, queries, top_k=top_k)


def retrieve_from_index(
    index: DatasheetIndex,
    queries: List[str],
    top_k: int = 5
) -> List[EvidenceChunk]:
    """
    Retrieve from an already-ingested index.
    
    Use this when you've already called ingest_pdf/ingest_text
    and want to run multiple queries against the same index.
    """
    return retrieve_evidence(index, queries, top_k=top_k)


def format_evidence_for_prompt(chunks: List[EvidenceChunk]) -> str:
    """
    Format retrieved evidence chunks for LLM prompt injection.
    
    Returns a structured string with page citations.
    """
    if not chunks:
        return ""
    
    lines = []
    for chunk in chunks:
        lines.append(f"[Page {chunk.page}, Score: {chunk.score:.2f}]")
        if chunk.section:
            lines.append(f"Section: {chunk.section}")
        lines.append(chunk.text)
        lines.append("")  # Empty line separator
    
    return "\n".join(lines)


# Re-export key classes and functions for convenience
__all__ = [
    # Main retrieval functions
    "retrieve_chunks",
    "retrieve_from_index",
    "format_evidence_for_prompt",
    
    # Data structures
    "EvidenceChunk",
    "DatasheetIndex",
    
    # Lower-level functions
    "ingest_pdf",
    "ingest_text",
    "retrieve_evidence",
    "retrieve_top_k_chunks",
    "extract_keywords_fallback",
]
