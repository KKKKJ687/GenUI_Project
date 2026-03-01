# context_splitter.py
"""
Smart text chunking module for RAG-style document processing.
Implements recursive splitting with structural awareness.
"""

import re


def split_text_recursive(text: str, chunk_size: int = 1200, chunk_overlap: int = 150) -> list:
    """
    Recursively split text into chunks respecting structural boundaries.
    
    Split hierarchy (highest to lowest priority):
    1. Double newlines (paragraphs)
    2. Bullet/list markers
    3. Single newlines
    4. Sentence endings (. ! ?)
    5. Character fallback
    
    Args:
        text: Input text to split
        chunk_size: Maximum characters per chunk
        chunk_overlap: Number of characters to overlap between chunks
    
    Returns:
        List of text chunks
    """
    if not text or not text.strip():
        return []
    
    # If text is short enough, return as single chunk
    if len(text) <= chunk_size:
        return [text.strip()]
    
    # Separators in priority order
    separators = [
        r'\n\n+',           # Double+ newlines (paragraphs)
        r'\n(?=[-*•]|\d+\.)',  # Before bullet/list items
        r'\n',              # Single newlines
        r'(?<=[.!?])\s+',   # After sentence endings
    ]
    
    chunks = []
    current_text = text
    
    for sep_pattern in separators:
        if len(current_text) <= chunk_size:
            break
            
        # Try splitting with this separator
        parts = re.split(sep_pattern, current_text)
        
        if len(parts) > 1:
            # Merge parts into chunks of appropriate size
            merged_chunks = _merge_parts(parts, chunk_size, chunk_overlap)
            if merged_chunks:
                return merged_chunks
    
    # Fallback: character-based splitting
    return _split_by_chars(text, chunk_size, chunk_overlap)


def _merge_parts(parts: list, chunk_size: int, chunk_overlap: int) -> list:
    """
    Merge split parts into chunks respecting size limits.
    """
    chunks = []
    current_chunk = ""
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # If adding this part exceeds limit
        if current_chunk and len(current_chunk) + len(part) + 1 > chunk_size:
            chunks.append(current_chunk.strip())
            
            # Apply overlap: take tail of current chunk
            if chunk_overlap > 0 and len(current_chunk) > chunk_overlap:
                overlap_text = current_chunk[-chunk_overlap:]
                # Find a good break point in overlap
                break_point = overlap_text.rfind(' ')
                if break_point > chunk_overlap // 3:
                    overlap_text = overlap_text[break_point + 1:]
                current_chunk = overlap_text + " " + part
            else:
                current_chunk = part
        else:
            if current_chunk:
                current_chunk += "\n" + part
            else:
                current_chunk = part
    
    # Add remaining content
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # If we only got one chunk that's still too large, split further
    if len(chunks) == 1 and len(chunks[0]) > chunk_size:
        return None  # Signal to try next separator
    
    return chunks if chunks else None


def _split_by_chars(text: str, chunk_size: int, chunk_overlap: int) -> list:
    """
    Fallback: split by character count with overlap.
    Try to break at word boundaries.
    """
    chunks = []
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = min(start + chunk_size, text_len)
        
        # Try to find a word boundary near the end
        if end < text_len:
            # Look for last space in final 20% of chunk
            search_start = max(start, end - chunk_size // 5)
            last_space = text.rfind(' ', search_start, end)
            if last_space > start:
                end = last_space
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # Calculate next start position
        if end >= text_len:
            break
        
        # Move start with overlap, but ensure progress
        next_start = end - chunk_overlap
        if next_start <= start:
            next_start = end  # Ensure forward progress
        start = next_start
    
    return chunks


def format_chunks_for_prompt(chunks: list, max_chunks: int = 6, max_chars: int = 6000) -> str:
    """
    Format chunks for injection into prompt.
    
    Args:
        chunks: List of text chunks
        max_chunks: Maximum number of chunks to include
        max_chars: Maximum total characters
    
    Returns:
        Formatted string for prompt injection
    """
    if not chunks:
        return "No text content available."
    
    result_parts = []
    total_chars = 0
    
    for i, chunk in enumerate(chunks[:max_chunks]):
        if total_chars + len(chunk) > max_chars:
            # Truncate this chunk if needed
            remaining = max_chars - total_chars
            if remaining > 200:
                chunk = chunk[:remaining] + "..."
            else:
                break
        
        result_parts.append(f"[Chunk {i+1}]\n{chunk}")
        total_chars += len(chunk)
    
    result = "\n\n".join(result_parts)
    
    if len(chunks) > max_chunks:
        result += f"\n\n[NOTE: {len(chunks) - max_chunks} additional chunks available for retrieval]"
    
    return result
