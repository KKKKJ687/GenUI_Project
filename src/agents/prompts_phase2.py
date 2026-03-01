"""
Phase 2 Prompts: Correction & Verification
Dedicated prompts for the Neuro-Symbolic Feedback Loop.
"""
import json
from typing import List, Any

def get_dsl_correction_prompt(current_dsl_json: str, violations: List[Any]) -> str:
    """
    Constructs the prompt that forces the LLM to fix specific violations.
    Input violations should be a list of dicts or Violation objects.
    """
    
    # 1. Summarize Violations for the LLM
    error_report = []
    for v in violations:
        # Handle both Pydantic model and dict
        v_data = v.model_dump() if hasattr(v, 'model_dump') else v
        
        entry = (
            f"- RULE BROKEN: {v_data.get('rule_id')} (Severity: {v_data.get('severity')})\n"
            f"  LOCATION: {v_data.get('param_path')}\n"
            f"  PROBLEM: {v_data.get('message')}\n"
            f"  REQUIREMENT: Must be {v_data.get('expected_limit')}"
        )
        error_report.append(entry)
    
    report_text = "\n".join(error_report)

    # 2. Build the System Prompt
    return f"""
You are a Senior Industrial HMI Engineer.
Your previous JSON output failed safety verification against the hardware datasheet.

### 🔴 VERIFICATION FAILURE REPORT
{report_text}

### 🔧 YOUR TASK
Fix the JSON below to comply with the safety requirements.
1. MODIFY ONLY the fields listed in the failure report.
2. DO NOT change the structure or other valid fields.
3. OUTPUT ONLY valid JSON. No markdown, no comments.
4. CRITICAL: If you change any widget `max` or `min`, you MUST also update that widget `value` so it remains within the new range.
5. CRITICAL: Keep each widget `value` and the runtime `values` mapping logically consistent with the same safe bounds.
   Example: if `max` is clamped from 100 to 3.3, `value` must be updated to <= 3.3.
6. CRITICAL: For enum/discrete constraints, use `type="select"` or `type="radio"` with explicit `options`, and ensure `value` is one of those options.

### CURRENT JSON (To Fix)
{current_dsl_json}
"""


def _canonical_theme_from_style(style: str) -> str:
    key = (style or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "dark_mode": "dark",
        "light_mode": "light",
        "industrial_blue_mode": "industrial_blue",
        "industrialblue": "industrial_blue",
        "classic": "light",
        "minimalist": "light",
        "cyberpunk": "dark",
        "neon": "dark",
        "wizard_green": "dark",
    }
    canonical = mapping.get(key, key)
    return canonical if canonical in {"dark", "light", "industrial_blue"} else "dark"

# ==========================================
# [追加内容] 核心生成 Prompt
# ==========================================

def get_architect_dsl_prompt(
    user_request: str,
    style: str = "dark",
    constraints_summary: str = "",
    conflict_summary: str = "",
) -> str:
    """
    Constructs the initial generation prompt for Phase 2.
    Enhanced to strictly follow Domain-Specific Constraints.
    """
    theme = _canonical_theme_from_style(style)
    return f"""
### 🟢 SYSTEM ROLE
You are a **Domain-Agnostic HMI Architect**. 
You are NOT limited to generic industrial motors. You adapt strictly to the specific domain provided in the user request (e.g., Medical, Aerospace, Power Electronics, Agricultural).

### 🎯 OBJECTIVE
Design a professional HMI Panel JSON structure.
1. **Language Priority**: If the user asks for Chinese, ALL visible labels (title, widget labels, units) MUST be in Chinese.
2. **Constraint Adherence**: You will receive a `CRITICAL INPUT` block containing hardware constraints. You MUST align `min`, `max`, and `unit` fields exactly with these constraints.
3. **No Hallucination**: Do not invent widgets (like 'Motor Speed') if the user asks for 'Solar Voltage'. Stick to the requested widget list.
4. **Conflict Resolution**: If user request conflicts with datasheet constraints, datasheet constraints ALWAYS win.
5. **Strict Schema**: Do NOT add root-level fields outside HMIPanel schema. If you need notes/audit details, place them under `metadata`.

### 🎨 Design Style
- Visual style: {style}
- JSON `theme` must be one of: `dark`, `light`, `industrial_blue`.
- Use this exact value for `theme`: `{theme}`.
- Layout: Logical grouping (Power vs Control vs Monitoring).

### UI WIDGET SELECTION RULES
1. If the user asks for "options", "selection", or "dropdown", you MUST use `type="select"` or `type="radio"`. NEVER use `type="input"` for these cases.
2. If a parameter has discrete values (for example: `[2g, 4g, 8g, 16g]`), you MUST use `select` or `radio`.
3. Use `input` only for free-form numeric/text entry.
4. For constrained enum parameters, `options` MUST exactly match the allowed hardware values. Do not invent intermediate values.
5. Use widget-specific fields only; avoid adding unrelated fields to a widget type.
   Example: `plot` should use chart-related fields (`title`, `x_label`, `y_label`, `duration_seconds`, optional `min/max`), not control-only fields.

### CRITICAL INPUT: DATASHEET CONSTRAINTS
{constraints_summary if constraints_summary else "- No explicit constraints provided in this run."}

### PRECHECK: USER REQUEST CONTRADICTIONS
{conflict_summary if conflict_summary else "- No direct prompt-vs-datasheet conflict detected."}

### 📋 JSON DSL Schema (Strict Enforcement)
```json
{{
  "project_name": "String (Must match device name)",
  "version": "1.0",
  "theme": "{theme}",
  "layout": [
    {{ "i": "widget_id", "x": int, "y": int, "w": int, "h": int }}
  ],
  "widgets": [
    {{
      "id": "unique_string_id",
      "type": "slider | switch | gauge | plot | input | select | radio",
      "label": "Display Label (Localized)",
      "min": float (optional),
      "max": float (optional),
      "step": float (optional),
      "value": "string | float | bool (depends on widget type)",
      "options": ["string_option_1", "string_option_2"] (required for select/radio),
      "unit": "string (e.g. V, A, kW, ℃)",
      "binding": {{
        "protocol": "mqtt | modbus | mock",
        "address": "legacy register_or_topic",
        "host": "broker_or_device_host",
        "port": 1883,
        "topic": "factory/device/metric",
        "register": 40001,
        "qos": 0,
        "access": "read | write | rw"
      }}
    }}
  ]
}}
📝 USER REQUEST & CONSTRAINTS

{user_request}

🚀 YOUR JSON RESPONSE:
Output ONLY JSON, with no markdown fences and no explanation text.

"""
