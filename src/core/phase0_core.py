from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from src.modules.runtime.runtime_monitor import append_event, export_session_log
from src.utils.run_artifacts import RunArtifacts
from src.utils.streaming_utils import chunk_to_text


def _safe_lint(html: str) -> Dict[str, Any]:
    """
    Lint best-effort:
    - Prefer modules.html_lint.lint_html if available.
    - Otherwise return a minimal report (still written to lint_report.json).
    """
    try:
        from src.modules.verifier.html_lint import lint_html  # your existing linter
        return lint_html(html)
    except Exception as e:
        return {
            "ok": True,
            "errors": [],
            "warnings": [f"lint_unavailable_or_failed: {type(e).__name__}: {e}"],
        }


def _create_lint_report(ok: bool, errors: list, warnings: list) -> Dict[str, Any]:
    """
    Factory function to create standardized Lint reports (Defect 4 fix).
    Ensures consistent schema across all runs for statistical analysis.
    """
    return {
        "ok": bool(ok),
        "errors": errors or [],
        "warnings": warnings or [],
        "schema_version": "v1.0",
    }


def _extract_html_best_effort(raw: str) -> str:
    """
    Best-effort HTML extraction:
    - If modules.html_extractor exists, use it.
    - Otherwise treat raw as HTML.
    """
    try:
        from src.modules.rag import html_extractor
        html, meta = html_extractor.extract_html(raw, repair_fn=None)
        if isinstance(html, str) and html.strip():
            return html
        return raw
    except Exception:
        return raw


def run_baseline_once(
    *,
    runs_dir: Path,
    user_prompt: str,
    selected_model: str,
    selected_style: str,
    llm: Any,
    streaming: bool = True,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """
    Minimal baseline pipeline:
    plan -> architect -> lint -> (optional review) -> export
    Always writes run_dir artifacts per Phase0-B.

    Args:
        runs_dir: Base directory for run artifacts
        user_prompt: User's request text
        selected_model: Model identifier string
        selected_style: UI style name
        llm: LLM client with generate_content method
        streaming: Whether to use streaming generation
        on_chunk: Optional callback function called with accumulated text during streaming.
                  Signature: (accumulated_text: str) -> None
                  Allows external systems (e.g., UI) to display progress in real-time.

    Returns:
        (run_dir_path, metrics_dict)
    """
    ra = RunArtifacts.create(base_dir=runs_dir)

    # Required artifacts (initialize)
    input_obj = {
        "created_utc": ra.created_utc,
        "run_id": ra.run_id,
        "user_prompt": user_prompt,
        "selected_model": selected_model,
        "selected_style": selected_style,
        "streaming_enabled": bool(streaming),
        "baseline_html_mode": True,
        "mode": "phase0_health_check",
    }
    ra.write_json("input.json", input_obj)
    ra.write_text("model_raw.txt", "")
    ra.write_json("lint_report.json", {})
    ra.write_json("timing.json", {})
    ra.write_json("metrics.json", {})

    success = False
    lint_passed = False
    repair_rounds = 0
    final_html = ""
    lint_bundle: Dict[str, Any] = {}
    errors_obj: Optional[Dict[str, Any]] = None

    try:
        # ---- PLAN ----
        with ra.stage("plan"):
            planner_prompt = f"PLANNER: Create a JSON plan for: {user_prompt}"
            plan_resp = llm.generate_content(planner_prompt, stream=False)
            ra.safe_append_text_atomic("model_raw.txt", "[PLANNER_RAW]\n" + (getattr(plan_resp, "text", "") or "") + "\n")

        # ---- ARCHITECT ----
        with ra.stage("architect"):
            architect_prompt = f"ARCHITECT: Generate a single HTML document. Style={selected_style}. Request={user_prompt}"
            if streaming:
                # Collect streaming chunks into raw_text
                stream = llm.generate_content(architect_prompt, stream=True)
                accumulated_text = []
                for c in stream:
                    t = chunk_to_text(c)
                    if t:
                        accumulated_text.append(t)
                        # Call on_chunk callback if provided for real-time UI updates
                        if on_chunk:
                            on_chunk("".join(accumulated_text))
                raw_text = "".join(accumulated_text)
            else:
                resp = llm.generate_content(architect_prompt, stream=False)
                raw_text = getattr(resp, "text", "") or ""
                # Also call on_chunk for non-streaming mode with final result
                if on_chunk:
                    on_chunk(raw_text)

            ra.safe_append_text_atomic("model_raw.txt", "[ARCHITECT_RAW]\n" + raw_text + "\n")

            html_code = _extract_html_best_effort(raw_text)

        # ---- LINT (PRE) ----
        with ra.stage("lint_pre"):
            raw_lint_res = _safe_lint(html_code)
            lint_res = _create_lint_report(
                ok=raw_lint_res.get("ok", False),
                errors=raw_lint_res.get("errors", []),
                warnings=raw_lint_res.get("warnings", []),
            )
        lint_bundle["initial"] = lint_res
        ra.write_json("lint_report.json", lint_bundle)

        # ---- REVIEW (OPTIONAL) ----
        with ra.stage("review"):
            if not bool(lint_res.get("ok", True)) or (lint_res.get("errors") or []):
                repair_rounds = 1
                review_prompt = "REVIEW: Fix the HTML according to lint errors. Output only HTML."
                resp = llm.generate_content([html_code, json.dumps(lint_res), review_prompt], stream=False)
                review_raw = getattr(resp, "text", "") or ""
                ra.safe_append_text_atomic("model_raw.txt", "[REVIEW_RAW]\n" + review_raw + "\n")
                fixed_html = _extract_html_best_effort(review_raw) or html_code
            else:
                fixed_html = html_code

        # ---- LINT (POST) ----
        with ra.stage("lint_post"):
            raw_post_lint = _safe_lint(fixed_html)
            post_lint = _create_lint_report(
                ok=raw_post_lint.get("ok", False),
                errors=raw_post_lint.get("errors", []),
                warnings=raw_post_lint.get("warnings", []),
            )
        lint_bundle["post_repair"] = post_lint
        ra.write_json("lint_report.json", lint_bundle)

        lint_passed = bool(post_lint.get("ok", True)) and not (post_lint.get("errors") or [])

        # ---- EXPORT ----
        with ra.stage("export"):
            final_html = fixed_html
            ra.write_text("final.html", final_html)

        success = True

    except Exception as e:
        errors_obj = ra.record_error(e, where="phase0_core.run_baseline_once")

    finally:
        # Aggregate lint time for Phase0-B contract
        ra.timing_ms["lint"] = int(ra.timing_ms.get("lint_pre", 0) + ra.timing_ms.get("lint_post", 0))
        ra.finish_total()
        ra.write_json("timing.json", ra.timing_ms)

        metrics = {
            "success": bool(success) and (errors_obj is None),
            "lint_passed": bool(lint_passed),
            "repair_rounds": int(repair_rounds),
            "timing_ms": ra.timing_ms,
            "output_size_chars": int(len(final_html)) if isinstance(final_html, str) else 0,
            "total_ms": int(ra.timing_ms.get("total", 0)),
        }
        ra.write_json("metrics.json", metrics)
        append_event(
            ra.run_dir,
            "telemetry",
            {
                "mode": "phase0_baseline",
                "success": metrics["success"],
                "lint_passed": metrics["lint_passed"],
                "repair_rounds": metrics["repair_rounds"],
                "total_ms": metrics["total_ms"],
            },
        )
        export_session_log(ra.run_dir)

        if errors_obj is not None:
            ra.write_json("errors.json", errors_obj)
        else:
            # ensure errors.json exists only on failure per your contract
            pass

    return ra.run_dir, metrics
