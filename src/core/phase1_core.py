"""
Phase 1 DSL Pipeline Core Module

Implements the strict JSON DSL generation pipeline:
LLM -> JSON DSL -> Pydantic Validation -> (Repair Loop) -> HTML Renderer
"""
from __future__ import annotations

import json
import hashlib
from typing import Tuple, Dict, Any, Optional

from pydantic import ValidationError

from src.utils.run_artifacts import RunArtifacts
from src.modules.runtime.runtime_monitor import append_event, export_session_log
from src.models.schema import HMIPanel
from src.modules.renderer.renderer import render_panel
from src.agents.prompts import get_architect_dsl_prompt, get_json_repair_prompt


def _clean_json_string(text: str) -> str:
    """Extract JSON from potential markdown code blocks."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def run_dsl_pipeline(
    ra: RunArtifacts,
    user_prompt: str,
    model: Any,
    selected_style: str,
    file_context: str = "",
    max_repair_rounds: int = 2,
) -> Tuple[str, Dict[str, Any]]:
    """
    Phase 1 Pipeline:
    LLM -> JSON DSL -> Pydantic Validation -> (Repair Loop) -> HTML Renderer

    Args:
        ra: RunArtifacts instance for logging and file writes
        user_prompt: User's request text
        model: LLM client with generate_content method
        selected_style: UI style preference
        file_context: Optional file context for the prompt
        max_repair_rounds: Maximum number of repair attempts on validation failure

    Returns:
        Tuple of (final_html, dsl_metrics_dict)
    """

    ra.write_json(
        "input.json",
        {
            "created_utc": ra.created_utc,
            "run_id": ra.run_id,
            "mode": "phase1_dsl",
            "user_prompt": user_prompt,
            "selected_style": selected_style,
            "max_repair_rounds": max_repair_rounds,
        },
    )
    ra.write_text("model_raw.txt", "")
    ra.write_json("timing.json", {})
    ra.write_json("metrics.json", {})

    dsl_metrics: Dict[str, Any] = {
        "mode": "dsl",
        "dsl_parse_rounds": 0,
        "render_hash": None,
        "validation_success": False,
    }

    raw_text = ""
    current_text = ""
    final_html = ""
    errors_obj: Optional[Dict[str, Any]] = None
    panel_obj: Optional[HMIPanel] = None

    try:
        # 1) Architect Step (Generate DSL)
        with ra.stage("architect_dsl"):
            prompt = get_architect_dsl_prompt(user_prompt, file_context, style=selected_style)
            resp = model.generate_content(prompt)
            raw_text = getattr(resp, "text", "") or ""
            current_text = raw_text

            ra.safe_append_text_atomic("model_raw.txt", f"\n[DSL_RAW_R0]\n{raw_text}\n")
            ra.write_text("dsl_raw.txt", raw_text)

        # 2) Validation & Repair Loop
        for round_idx in range(max_repair_rounds + 1):
            dsl_metrics["dsl_parse_rounds"] = round_idx
            try:
                clean_json = _clean_json_string(current_text)
                data = json.loads(clean_json)
                panel_obj = HMIPanel.model_validate(data)
                dsl_metrics["validation_success"] = True
                ra.write_json("dsl_validated.json", data)
                break
            except (json.JSONDecodeError, ValidationError) as e:
                error_msg = str(e)
                ra.write_json(
                    "dsl_schema_errors.json",
                    {
                        "round": round_idx,
                        "error": error_msg,
                        "bad_json_snippet": current_text[:500],
                    },
                )
                if round_idx < max_repair_rounds:
                    with ra.stage(f"dsl_repair_r{round_idx + 1}"):
                        repair_prompt = get_json_repair_prompt(current_text, error_msg)
                        repair_resp = model.generate_content(repair_prompt)
                        current_text = getattr(repair_resp, "text", "") or ""
                        ra.safe_append_text_atomic(
                            "model_raw.txt",
                            f"\n[DSL_REPAIR_R{round_idx + 1}]\n{current_text}\n",
                        )

        # 3) Renderer Step
        if panel_obj:
            with ra.stage("render"):
                final_html = render_panel(panel_obj)
                render_hash = hashlib.sha256(final_html.encode("utf-8")).hexdigest()
                dsl_metrics["render_hash"] = render_hash
                ra.write_text("render_hash.txt", render_hash)
                ra.write_text("final.html", final_html)
        else:
            final_html = f"""<!DOCTYPE html>
<html>
<head><title>DSL Generation Error</title></head>
<body style="background: #1e293b; color: #e2e8f0; font-family: sans-serif; padding: 2rem;">
    <h2 style="color: #ef4444;">⚠️ DSL Generation Failed</h2>
    <p>Could not generate valid JSON after {max_repair_rounds} repair attempts.</p>
    <p>Check <code>dsl_schema_errors.json</code> in run artifacts for details.</p>
    <details style="margin-top: 1rem;">
        <summary style="cursor: pointer;">Raw Output Preview</summary>
        <pre style="background: #0f172a; padding: 1rem; overflow: auto; max-height: 300px;">{current_text[:1000]}</pre>
    </details>
</body>
</html>"""
            ra.write_text("final.html", final_html)
    except Exception as e:
        errors_obj = ra.record_error(e, where="phase1_core.run_dsl_pipeline")
        final_html = """<!DOCTYPE html>
<html><head><title>Generation Error</title></head>
<body><h2>Generation Failed</h2></body></html>"""
        ra.write_text("final.html", final_html)
    finally:
        ra.finish_total()
        ra.write_json("timing.json", ra.timing_ms)

        metrics = {
            **dsl_metrics,
            "success": bool(dsl_metrics.get("validation_success")) and errors_obj is None,
            "output_size_chars": len(final_html),
            "total_ms": int(ra.timing_ms.get("total", 0)),
        }
        ra.write_json("metrics.json", metrics)
        ra.write_json("dsl_metrics.json", dsl_metrics)
        append_event(
            ra.run_dir,
            "telemetry",
            {
                "mode": "phase1_dsl",
                "success": metrics["success"],
                "validation_success": dsl_metrics.get("validation_success", False),
                "dsl_parse_rounds": dsl_metrics.get("dsl_parse_rounds", 0),
                "total_ms": metrics["total_ms"],
            },
        )
        export_session_log(ra.run_dir)
        if errors_obj:
            ra.write_json("errors.json", errors_obj)

    return final_html, metrics
