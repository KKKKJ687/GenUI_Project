"""
Phase 3 Prompts: Constraint Extraction

Prompts designed to teach LLMs how to:
  1. Read datasheet tables
  2. Identify safety constraints
  3. Map to our ConstraintSet schema
"""


def get_extraction_prompt(device_name: str, context: str) -> str:
    """
    Generates the prompt for extracting constraints from datasheet text.
    
    Args:
        device_name: Name of the device being analyzed
        context: Concatenated text chunks from the datasheet
        
    Returns:
        Complete prompt string for the LLM
    """
    return f"""You are a Datasheet Extraction Expert specializing in embedded systems and industrial hardware.
Your task is to extract SAFETY CONSTRAINTS for the device "{device_name}" from the provided text chunks.

### TARGET INFORMATION
Extract the following types of constraints:
1. **Absolute Maximum Ratings** - Voltage, Current, Temperature limits that must NEVER be exceeded
2. **Recommended Operating Conditions** - Normal operating ranges
3. **Communication Protocol Limits** - Baud rate, I2C speed, SPI frequency
4. **Timing Constraints** - Min/max pulse widths, setup/hold times

### OUTPUT FORMAT (JSON ONLY)
Return a JSON object with this exact structure:
{{
  "constraints": [
    {{
      "id": "Short unique ID (e.g., VCC_MAX, BAUD_RATE_RANGE)",
      "name": "Human readable name",
      "kind": "max" | "min" | "range" | "enum",
      "applies_to": "Target param path — MUST use TYPE selector (see rules below)",
      "max_val": 3.6,
      "min_val": 0.0,
      "allowed_values": ["115200", "9600"],
      "unit": "V" | "A" | "Hz" | "C" | "ms" | "us",
      "description": "Brief description of what this constraint protects",
      "source_quote": "Exact text snippet from the chunk used as evidence"
    }}
  ]
}}

### CONTEXT (DATASHEET CHUNKS)
{context}

### EXTRACTION RULES
1. **RANGE extraction**: If you see "0V to 3.6V", extract as kind="range", min_val=0, max_val=3.6
2. **MAX extraction**: If you see "Max Supply Voltage: 5.5V", extract as kind="max", max_val=5.5
3. **MIN extraction**: If you see "Min Input Voltage: 1.8V", extract as kind="min", min_val=1.8
4. **ENUM extraction**: If you see "Supported Baud Rates: 9600, 115200", extract as kind="enum", allowed_values=["9600", "115200"]
5. **CRITICAL — applies_to RULES**:
   - NEVER target a specific widget ID (e.g., NO "widgets[id='GPIO_1']")
   - ALWAYS use TYPE-based selectors:
     - For voltage limits: "widgets[type='slider'].max"
     - For current limits: "widgets[type='slider'].max"
     - For frequency limits: "widgets[type='slider'].max"
     - For temperature: "widgets[type='gauge'].max"
     - For general numeric limits: "widgets[type='input'].value"
   - The Verifier engine will use semantic matching to find the correct widgets
6. **Evidence**: Always include the "source_quote" with the exact text that supports your extraction
7. **Description**: Include a short description that mentions the parameter type (e.g., "input voltage", "supply current") — this helps the semantic matcher
8. **NO HALLUCINATION**: Only extract constraints explicitly stated in the text. Do not infer or guess.

### IMPORTANT
- Return ONLY the JSON object, no markdown formatting, no explanation
- If no constraints are found, return {{"constraints": []}}
- Ensure all numeric values are actual numbers, not strings
"""



def get_validation_repair_prompt(constraint_json: str, error_message: str) -> str:
    """
    Prompt for LLM to fix invalid constraint JSON.
    
    Args:
        constraint_json: The invalid JSON that needs fixing
        error_message: Pydantic validation error message
        
    Returns:
        Repair prompt string
    """
    return f"""The following constraint extraction has validation errors. Please fix them.

### INVALID JSON
{constraint_json}

### ERROR MESSAGE
{error_message}

### RULES FOR FIXING
1. Ensure "kind" is one of: "max", "min", "range", "enum", "regex", "type"
2. For "range" kind, both "min_val" and "max_val" must be present
3. For "max" kind, "max_val" must be present
4. For "min" kind, "min_val" must be present
5. For "enum" kind, "allowed_values" must be a non-empty list
6. "id" and "name" are required strings
7. "applies_to" is a required string
8. Numeric values (min_val, max_val) must be numbers, not strings

### OUTPUT
Return ONLY the corrected JSON object, no explanation.
"""


def get_safety_summary_prompt(constraints_json: str) -> str:
    """
    Prompt to generate a human-readable safety summary from constraints.
    
    Useful for documentation and reports.
    """
    return f"""Given the following hardware constraints, generate a brief safety summary for engineers.

### CONSTRAINTS
{constraints_json}

### OUTPUT FORMAT
Generate a markdown summary with:
1. **Critical Limits** - Things that will damage hardware if exceeded
2. **Operating Range** - Normal safe operating conditions
3. **Notes** - Any special considerations

Keep it concise (under 200 words).
"""
