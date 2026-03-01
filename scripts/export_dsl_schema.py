import json
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.schema import HMIPanel

def export_schema():
    """
    Exports the JSON Schema of the HMIPanel Pydantic model.
    This schema is fed to the LLM to strictly constrain its output.
    """
    output_path = ROOT / "dsl_schema.json"
    
    # Generate schema compliant with OpenAI/Gemini function calling or strict JSON mode
    schema = HMIPanel.model_json_schema()
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
    
    print(f"[SUCCESS] DSL Schema exported to: {output_path}")
    print(f"Model Root: {schema.get('title')}")
    print(f"Properties: {list(schema.get('properties', {}).keys())}")

if __name__ == "__main__":
    export_schema()
