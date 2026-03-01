#!/usr/bin/env python3
"""
Script: Extract Constraints from PDF Datasheet

Usage: python scripts/build_constraints_from_pdf.py <pdf_path> [--output constraints.json]

This script demonstrates the full RAG pipeline:
  1. Ingest PDF into searchable chunks
  2. Retrieve relevant sections (Absolute Max Ratings, etc.)
  3. Extract constraints via LLM
  4. Save to JSON file
"""
import sys
import os
import argparse
import json

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.modules.rag.datasheet_rag import ingest_pdf, ingest_text, retrieve_evidence
from src.modules.rag.constraint_extractor import extract_constraints, extract_constraints_heuristic


def main():
    parser = argparse.ArgumentParser(description="Extract constraints from PDF datasheet")
    parser.add_argument("pdf_path", help="Path to PDF datasheet")
    parser.add_argument("--output", "-o", default="constraints.json", help="Output JSON file")
    parser.add_argument("--use-llm", action="store_true", help="Use LLM for extraction (requires API key)")
    parser.add_argument("--top-k", type=int, default=10, help="Number of evidence chunks to retrieve")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.pdf_path):
        print(f"❌ File not found: {args.pdf_path}")
        return 1
    
    device_name = os.path.basename(args.pdf_path).replace(".pdf", "").replace("_", " ")
    
    # 1. Ingest PDF
    print(f"🚀 Ingesting {args.pdf_path}...")
    try:
        index = ingest_pdf(args.pdf_path)
        print(f"   Created {len(index)} chunks from {index.filename}")
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("   Install with: pip install pypdf")
        return 1
    except Exception as e:
        print(f"❌ Ingestion failed: {e}")
        return 1

    # 2. Retrieve Evidence
    print(f"🔍 Retrieving relevant sections...")
    queries = [
        "Absolute Maximum Ratings", 
        "Recommended Operating Conditions", 
        "Electrical Characteristics", 
        "Input Voltage Range",
        "DC Characteristics",
        "Operating Temperature"
    ]
    evidence = retrieve_evidence(index, queries, top_k=args.top_k)
    
    print(f"   Found {len(evidence)} relevant chunks:")
    for i, c in enumerate(evidence[:5]):
        preview = c.text[:60].replace('\n', ' ')
        print(f"   {i+1}. [Page {c.page}] {preview}...")
    if len(evidence) > 5:
        print(f"   ... and {len(evidence) - 5} more")

    # 3. Extract Constraints
    print(f"\n🧠 Extracting constraints...")
    
    if args.use_llm:
        # Use real LLM client
        try:
            import google.generativeai as genai
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                print("❌ GOOGLE_API_KEY environment variable not set")
                return 1
            
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-pro')
            
            constraint_set = extract_constraints(
                evidence, model, device_name, index.filename
            )
        except ImportError:
            print("❌ google-generativeai not installed")
            return 1
    else:
        # Use heuristic extraction (no LLM)
        print("   Using heuristic extraction (no LLM)...")
        all_text = "\n".join([c.text for c in evidence])
        constraints = extract_constraints_heuristic(all_text, index.filename)
        
        from src.modules.verifier.constraints import ConstraintSet
        constraint_set = ConstraintSet(
            device_name=device_name,
            constraints=constraints,
            metadata={"source": "Heuristic Extraction", "chunks_processed": len(evidence)}
        )

    # 4. Save Results
    output_path = args.output
    constraint_set.save_to_json(output_path)
    
    print(f"\n✅ Extracted {len(constraint_set.constraints)} constraints")
    print(f"   Saved to: {output_path}")
    
    # Print summary
    if constraint_set.constraints:
        print("\n📋 Constraint Summary:")
        for c in constraint_set.constraints[:10]:
            limit = c.max_val if c.max_val else c.min_val if c.min_val else "N/A"
            print(f"   • {c.id}: {c.name} ({c.kind.value}) = {limit} {c.unit}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
