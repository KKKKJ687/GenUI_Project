"""
Phase 3: Datasheet RAG Engine

Responsible for ingesting PDF datasheets and retrieving evidence chunks
with Page/Section context for industrial traceability.

Key Features:
  - PDF ingestion with page-aware chunking
  - Metadata injection for better retrieval
  - Evidence linking back to source pages
"""
import re
from typing import List, Dict, Optional, Tuple, Any
from pydantic import BaseModel, Field

# Try importing pdfplumber, handle missing dependency gracefully
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    pdfplumber = None
    HAS_PDFPLUMBER = False

from src.modules.rag.table_extractor import extract_tables_from_page

# Import local RAG utilities
try:
    from src.modules.rag.local_rag import retrieve_top_k_chunks
except ImportError:
    # Fallback if local_rag not available
    def retrieve_top_k_chunks(corpus, queries, k=5):
        """Simple fallback retrieval using keyword matching."""
        results = []
        combined_query = " ".join(queries).lower()
        keywords = set(combined_query.split())
        
        for i, chunk in enumerate(corpus):
            chunk_lower = chunk.lower()
            score = sum(1 for kw in keywords if kw in chunk_lower) / max(len(keywords), 1)
            results.append({"chunk_id": i, "score": score, "text": chunk})
        
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:k]


class EvidenceChunk(BaseModel):
    """
    A chunk of text from the datasheet with traceability info.
    """
    text: str
    page: int
    source_file: str
    chunk_id: int
    section: Optional[str] = None
    score: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DatasheetIndex:
    """
    In-memory index of a datasheet's searchable chunks.
    """
    def __init__(self, filename: str, chunks: List[EvidenceChunk]):
        self.filename = filename
        self.chunks = chunks
    
    def __len__(self) -> int:
        return len(self.chunks)
    
    def get_page_chunks(self, page: int) -> List[EvidenceChunk]:
        """Get all chunks from a specific page."""
        return [c for c in self.chunks if c.page == page]


# Try importing PyMuPDF (fitz) for fast scanning
try:
    import fitz
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


_SECTION_ALIASES: Dict[str, List[str]] = {
    "Absolute Maximum Ratings": [
        r"absolute\s+maximum\s+ratings?",
        r"maximum\s+ratings?",
        r"绝对\s*最大\s*额定",
        r"最大\s*额定",
    ],
    "Recommended Operating Conditions": [
        r"recommended\s+operating\s+conditions?",
        r"operating\s+conditions?",
        r"recommended\s+operating",
        r"推荐\s*工作\s*条件",
        r"工作\s*条件",
    ],
    "Electrical Characteristics": [
        r"electrical\s+characteristics?",
        r"dc\s+characteristics?",
        r"ac\s+characteristics?",
        r"电气\s*特性",
        r"电学\s*特性",
    ],
    "Pin Description": [
        r"pin\s+description",
        r"pin\s+configuration",
        r"引脚\s*说明",
        r"管脚\s*定义",
    ],
    "Communication Interface": [
        r"communication\s+interface",
        r"serial\s+interface",
        r"spi|i2c|uart|can|rs485|modbus|mqtt",
        r"通信\s*接口",
        r"协议",
    ],
    "Package Dimensions": [
        r"package\s+dimensions?",
        r"mechanical\s+data",
        r"封装\s*尺寸",
        r"机械\s*尺寸",
    ],
    "Ordering Information": [
        r"ordering\s+information",
        r"part\s+number",
        r"订购\s*信息",
        r"型号\s*信息",
    ],
}


def _normalize_match_text(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    lowered = re.sub(r"[^\w\u4e00-\u9fff\s]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _heading_candidates(text: str, max_lines: int = 16) -> List[str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[:max_lines]

def ingest_pdf(
    pdf_path: str, 
    chunk_size: int = 1000, 
    overlap: int = 100,
    max_pages: int = None 
) -> DatasheetIndex:
    """
    Reads a PDF and splits it into searchable chunks, preserving page numbers.
    Uses 'Hybrid Strategy':
    1. Fast Scan (PyMuPDF): Identify pages with key electrical keywords.
    2. Deep Extract (pdfplumber): Extract tables/text only from relevant pages.
    """
    filename = pdf_path.split("/")[-1]
    all_chunks: List[EvidenceChunk] = []
    global_chunk_id = 0

    if not HAS_PDFPLUMBER:
        raise RuntimeError(
            "pdfplumber is not installed. Install dependencies with `pip install -r requirements.txt`."
        )

    # Phase 1: Fast Scan with PyMuPDF (if available)
    relevant_indices = set()
    
    # Heuristic keywords for electrical specifications (multilingual + aliases)
    target_headers = [
        "absolute maximum ratings",
        "maximum ratings",
        "electrical characteristics",
        "recommended operating conditions",
        "recommended operating",
        "pin configuration",
        "full scale range",
        "fs_sel",
        "operating temperature",
        "communication interface",
        "modbus",
        "mqtt",
        "绝对最大额定",
        "推荐工作条件",
        "电气特性",
        "通信接口",
        "保护阈值",
    ]

    if HAS_PYMUPDF:
        try:
            doc = fitz.open(pdf_path)
            # Limit scan if max_pages is set, otherwise scan all (it's fast)
            scan_limit = max_pages if max_pages else len(doc)
            
            for i in range(min(len(doc), scan_limit)):
                # Fast text extraction
                page_text = (doc[i].get_text() or "").lower()
                if any(h in page_text for h in target_headers):
                    relevant_indices.add(i)
            doc.close()
            print(f"[{filename}] Fast Scan found relevant pages: {[i+1 for i in sorted(list(relevant_indices))]}")
        except Exception as e:
            print(f"PyMuPDF scan warning: {e}. Falling back to default.")
    
    # Fallback / Default Policy
    # If no pages found (or PyMuPDF missing), process the first few pages (most likely to have specs)
    # or follow max_pages logic.
    if not relevant_indices:
        # Default to a wider window for long datasheets.
        # First 5 pages are often just cover/TOC and miss actual electrical tables.
        limit = max_pages if max_pages else 20
        relevant_indices = set(range(limit))
        print(f"[{filename}] No keywords found. Defaulting to first {limit} pages.")

    # Phase 2: Deep Extraction with pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        
        # Sort indices to process in order
        sorted_indices = sorted(list(relevant_indices))
        
        for i in sorted_indices:
            if i >= total_pages: 
                continue
                
            page = pdf.pages[i]
            page_num = i + 1
            
            # --- Step A: Extract Structured Tables (High Value) ---
            try:
                table_artifacts = extract_tables_from_page(page)
            except Exception as e:
                print(f"Warning: Table extraction failed on page {page_num}: {e}")
                table_artifacts = []
            
            for table in table_artifacts:
                enriched_text = f"[Page {page_num}] [TABLE DETECTED]\n{table.markdown}"
                all_chunks.append(EvidenceChunk(
                    text=enriched_text,
                    page=page_num,
                    source_file=filename,
                    chunk_id=global_chunk_id,
                    section="Structured_Table",
                    metadata={"type": "table", "headers": table.header_fingerprint}
                ))
                global_chunk_id += 1

            # --- Step B: Extract Regular Text (Context) ---
            text = page.extract_text() or ""
            section = _detect_section(text)
            
            start = 0
            while start < len(text):
                end = min(start + chunk_size, len(text))
                chunk_text = text[start:end].strip()
                if not chunk_text:
                    start += (chunk_size - overlap)
                    continue
                
                all_chunks.append(EvidenceChunk(
                    text=f"[Page {page_num}] {chunk_text}",
                    page=page_num,
                    source_file=filename,
                    chunk_id=global_chunk_id,
                    section=section,
                    metadata={"type": "text"}
                ))
                global_chunk_id += 1
                start += (chunk_size - overlap)
            
    return DatasheetIndex(filename, all_chunks)


def ingest_text(
    text: str,
    filename: str = "inline.txt",
    chunk_size: int = 500
) -> DatasheetIndex:
    """
    Creates a DatasheetIndex from raw text (testing/fallback).
    """
    chunks: List[EvidenceChunk] = []
    paragraphs = text.split("\n\n")
    
    chunk_id = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        start = 0
        while start < len(para):
            end = min(start + chunk_size, len(para))
            chunk_text = para[start:end]
            
            chunks.append(EvidenceChunk(
                text=f"[Page 1] {chunk_text}",
                page=1,
                source_file=filename,
                chunk_id=chunk_id,
                section=_detect_section(chunk_text),
                metadata={"type": "text"}
            ))
            
            chunk_id += 1
            start = end
    
    return DatasheetIndex(filename, chunks)


def retrieve_evidence(
    index: DatasheetIndex, 
    queries: List[str], 
    top_k: int = 5
) -> List[EvidenceChunk]:
    """
    Retrieves the most relevant chunks with Table-First boosting.
    """
    if not index.chunks:
        return []
    
    # Flatten index for retrieval
    corpus_texts = [c.text for c in index.chunks]
    
    # Base retrieval
    initial_results = retrieve_top_k_chunks(corpus_texts, queries, k=top_k * 2) # Get more candidates
    
    # Re-ranking Logic: Boost Tables
    reranked_results = []
    
    # Keywords that suggest technical parameters usually found in tables
    technical_terms = ["V", "A", "Hz", "Max", "Min", "Typ", "Range", "Output", "Input", "Voltage", "Current"]
    
    for res in initial_results:
        chunk_id = res.get("chunk_id", 0)
        base_score = res.get("score", 0.0)
        
        if 0 <= chunk_id < len(index.chunks):
            chunk = index.chunks[chunk_id]
            
            # Boost 1: It is a structured table
            if chunk.section == "Structured_Table" or chunk.metadata.get("type") == "table":
                # Boost 2: It actually contains relevant physical units or terms
                if any(term in chunk.text for term in technical_terms):
                    base_score *= 1.5  # 50% boost for relevant tables
            
            res["score"] = base_score
            reranked_results.append(res)
            
    # Sort by boosted score
    reranked_results.sort(key=lambda x: x["score"], reverse=True)
    
    # Map back to unique chunks
    evidence: List[EvidenceChunk] = []
    seen_ids = set()
    
    for res in reranked_results[:top_k]:
        chunk_id = res["chunk_id"]
        if chunk_id in seen_ids:
            continue
        seen_ids.add(chunk_id)
        
        chunk = index.chunks[chunk_id]
        chunk.score = res["score"]
        evidence.append(chunk)
    
    return evidence


def _detect_section(text: str) -> Optional[str]:
    """
    Attempts to detect common datasheet section headers.
    
    Returns the detected section name or None.
    """
    if not text:
        return None

    # Focus on heading-like region first to avoid false positives in long paragraphs.
    head_region = "\n".join(_heading_candidates(text))
    searchable = _normalize_match_text(head_region if head_region else text[:1200])

    best_name = None
    best_score = 0

    for canonical, patterns in _SECTION_ALIASES.items():
        score = 0
        for pat in patterns:
            try:
                if re.search(pat, searchable, re.IGNORECASE):
                    score += 2
            except re.error:
                continue

        canonical_tokens = [t for t in _normalize_match_text(canonical).split(" ") if len(t) >= 3]
        if canonical_tokens:
            overlap = sum(1 for t in canonical_tokens if t in searchable)
            score += overlap

        if score > best_score:
            best_score = score
            best_name = canonical

    if best_name and best_score >= 2:
        return best_name

    # Final heading heuristic for uncommon formatting (e.g., all caps section titles).
    for line in _heading_candidates(text, max_lines=8):
        norm_line = _normalize_match_text(line)
        if not norm_line:
            continue
        if len(norm_line) > 72:
            continue
        for canonical, patterns in _SECTION_ALIASES.items():
            if any(re.search(pat, norm_line, re.IGNORECASE) for pat in patterns):
                return canonical

    return None
