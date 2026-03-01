#!/usr/bin/env python3
"""
Utility Script: Export JSON Schemas for Phase 2 Models.

Usage: python scripts/export_phase2_schemas.py

Outputs:
  - schemas/constraint_set_schema.json
  - schemas/verification_report_schema.json

These schemas can be used by:
  - Frontend validation
  - LLM context (schema-constrained generation)
  - API documentation
"""
import json
import os
import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config import SCHEMAS_DIR

from src.modules.verifier.constraints import ConstraintSet, Constraint
from src.modules.verifier.verification_report import VerificationReport, Violation, FixAction


def export_schema(model_class, filename: str, output_dir: Path = SCHEMAS_DIR) -> str:
    """Export a Pydantic model's JSON Schema to a file."""
    schema = model_class.model_json_schema()
    
    # Ensure output directory exists
    out_path = output_dir
    out_path.mkdir(parents=True, exist_ok=True)
    
    file_path = out_path / filename
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    
    return str(file_path)


def main():
    print("=" * 50)
    print("Phase 2 Schema Export")
    print("=" * 50)
    
    exports = [
        (ConstraintSet, "constraint_set_schema.json"),
        (Constraint, "constraint_schema.json"),
        (VerificationReport, "verification_report_schema.json"),
        (Violation, "violation_schema.json"),
        (FixAction, "fix_action_schema.json"),
    ]
    
    for model, filename in exports:
        path = export_schema(model, filename)
        print(f"✅ Exported: {path}")
        
        # Also print key properties
        schema = model.model_json_schema()
        props = list(schema.get("properties", {}).keys())
        print(f"   Properties: {', '.join(props[:5])}{'...' if len(props) > 5 else ''}")
    
    print()
    print("Done. Schemas exported to ./schemas/")


if __name__ == "__main__":
    main()
