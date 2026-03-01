"""
Phase 2 Core: Closed-Loop Repair System
Orchestrates the LLM -> Verifier -> Feedback loop.
Ensures every step is logged for academic reproducibility.
"""
import json
import logging
import re
import ast
from typing import Tuple, Dict, Any, Optional

from src.models.schema import HMIPanel
from src.modules.verifier.constraints import ConstraintSet
from src.modules.verifier.verifier import verify_panel, apply_fixes
from src.agents.prompts_phase2 import get_dsl_correction_prompt
from src.utils.run_artifacts import RunArtifacts
from src.utils.streaming_utils import extract_json_from_text

# Configure logging
logger = logging.getLogger(__name__)


def _strip_code_fences(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return t


def _balance_braces(text: str) -> str:
    if not text:
        return text
    open_count = text.count("{")
    close_count = text.count("}")
    if open_count > close_count:
        text += "}" * (open_count - close_count)
    return text


def _repair_json_structure(candidate_text: str) -> Optional[str]:
    """
    Deterministic JSON recovery for common malformed LLM outputs:
      - missing trailing braces
      - trailing commas
      - python dict style single quotes / booleans
    Returns valid JSON text if repair succeeds, else None.
    """
    if not candidate_text:
        return None

    text = _strip_code_fences(candidate_text)
    text = extract_json_from_text(text)
    text = _balance_braces(text)
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass

    # Python-literal fallback: {'a': True, 'b': None} -> JSON
    try:
        py_obj = ast.literal_eval(text)
        return json.dumps(py_obj, ensure_ascii=False)
    except Exception:
        return None

def run_closed_loop(
    ra: RunArtifacts,
    initial_dsl_json: str,
    constraints: ConstraintSet,
    llm_client: Any,
    max_rounds: int = 2
) -> Tuple[Optional[HMIPanel], Dict[str, Any]]:
    """
    Executes the Neuro-Symbolic Self-Correction Loop.
    
    Returns:
        (Final_Panel_Object, Metrics_Dict)
    """
    metrics = {
        "closed_loop_rounds": 0,
        "initial_pass": False,
        "final_pass": False,
        "symbolic_clamp_triggered": False,
        "syntax_repair_applied": 0,
        "symbolic_clamp_warning": False,
    }

    current_json_text = initial_dsl_json
    current_panel = None

    for round_idx in range(max_rounds + 1):
        metrics["closed_loop_rounds"] = round_idx
        
        # Step 1: Parse JSON (Syntax Check)
        try:
            # Clean potential markdown with robust utility
            clean_text = extract_json_from_text(current_json_text)
            
            data = json.loads(clean_text)
            current_panel = HMIPanel.model_validate(data)
            
            # Log intermediate DSL
            ra.write_json(f"intermediate/round_{round_idx}_dsl.json", data)

        except Exception as e:
            logger.error(f"Round {round_idx} Syntax Error: {e}")
            ra.write_text(f"intermediate/round_{round_idx}_syntax_error.txt", str(e))

            repaired = _repair_json_structure(current_json_text)
            if repaired:
                metrics["syntax_repair_applied"] += 1
                current_json_text = repaired
                ra.write_text(f"intermediate/round_{round_idx}_syntax_repaired.json", repaired)
                continue

            # If deterministic repair fails, ask the LLM for strict JSON rewrite.
            if round_idx < max_rounds:
                syntax_prompt = (
                    "You output malformed JSON. Rewrite ONLY strict JSON for HMIPanel schema. "
                    "Do not include markdown, code fences, or explanations.\n\n"
                    f"Bad output:\n{current_json_text}\n\n"
                    f"Parser error:\n{e}"
                )
                try:
                    resp = llm_client.generate_content(syntax_prompt)
                    current_json_text = getattr(resp, "text", "") or ""
                    ra.safe_append_text_atomic(
                        "model_raw.txt",
                        f"\n[SYNTAX_REPAIR_PROMPT_R{round_idx}]\n{syntax_prompt}\n"
                        f"[SYNTAX_REPAIR_RESPONSE_R{round_idx}]\n{current_json_text}\n",
                    )
                    continue
                except Exception as llm_err:
                    logger.error(f"Syntax repair LLM call failed: {llm_err}")
            return None, metrics

        # Step 2: Verify (Safety Check)
        report = verify_panel(current_panel, constraints)
        ra.write_json(f"intermediate/round_{round_idx}_report.json", report.model_dump())
        
        if report.passed:
            logger.info(f"Round {round_idx} passed verification!")
            if round_idx == 0:
                metrics["initial_pass"] = True
            metrics["final_pass"] = True
            return current_panel, metrics
        
        # Step 3: Decision - Loop or Clamp?
        if round_idx < max_rounds:
            # -> Feedback to LLM (Neuro-Symbolic Repair)
            logger.info(f"Round {round_idx} failed. Requesting LLM correction...")
            
            prompt = get_dsl_correction_prompt(current_json_text, report.violations)
            prompt += """

CRITICAL: If you change a widget's `max` or `min` attribute, you MUST also update that widget's `value` so it remains within the new range.
Example: if max is clamped from 100 to 3.3, value must become 3.3 or lower.
"""
            ra.safe_append_text_atomic("model_raw.txt", f"\n[REPAIR_PROMPT_R{round_idx}]\n{prompt}\n")
            
            # Call LLM
            try:
                # Assuming model.generate_content(prompt).text interface
                resp = llm_client.generate_content(prompt)
                current_json_text = resp.text
                ra.safe_append_text_atomic("model_raw.txt", f"\n[REPAIR_RESPONSE_R{round_idx}]\n{current_json_text}\n")
            except Exception as e:
                logger.error(f"LLM Call Failed: {e}")
                break
        else:
            # -> Max rounds reached. Fallback to Symbolic Clamp.
            logger.warning("Max rounds reached. Triggering Symbolic Clamp.")
            metrics["symbolic_clamp_triggered"] = True
            
            clamped_panel, fix_report = apply_fixes(current_panel, report)
            post_report = verify_panel(clamped_panel, constraints)
            post_report.fixes = list(fix_report.fixes or [])
            post_report.residual_risks = list(
                dict.fromkeys((fix_report.residual_risks or []) + (post_report.residual_risks or []))
            )
            post_report.stats["fixes_applied"] = len(post_report.fixes)
            ra.write_json("intermediate/final_clamped_report.json", post_report.model_dump())

            # Clamp is now "qualified" only if it really passes re-verification.
            metrics["final_pass"] = bool(post_report.passed)
            if post_report.fixes:
                metrics["symbolic_clamp_warning"] = True
            return clamped_panel, metrics

    return current_panel, metrics
