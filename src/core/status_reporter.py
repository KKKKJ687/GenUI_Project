from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.modules.runtime.runtime_monitor import read_events, replay_events


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def generate_report(run_dir: Union[str, Path]) -> Dict[str, Any]:
    """Generate a consolidated runtime report from a run_dir.

    Data sources (best-effort, optional):
      - input.json
      - metrics.json
      - dsl_metrics.json
      - verifier_report*.json
      - errors.json
      - runtime_events.jsonl
    """
    run_dir_p = Path(run_dir)
    report: Dict[str, Any] = {
        "generated_utc": _utc_now_iso(),
        "run_dir": str(run_dir_p),
        "artifacts": {},
        "summary": {},
        "protocols": {},
        "commands": {},
        "events": {},
        "errors": {},
    }

    # Core artifacts
    input_obj = _read_json(run_dir_p / "input.json")
    metrics_obj = _read_json(run_dir_p / "metrics.json")
    dsl_metrics = _read_json(run_dir_p / "dsl_metrics.json")
    errors_obj = _read_json(run_dir_p / "errors.json")

    report["artifacts"]["input"] = input_obj
    report["artifacts"]["metrics"] = metrics_obj
    if dsl_metrics is not None:
        report["artifacts"]["dsl_metrics"] = dsl_metrics
    if errors_obj is not None:
        report["errors"]["errors_json"] = errors_obj

    # Verifier reports may be multiple
    verifier_reports: List[Dict[str, Any]] = []
    for p in sorted(run_dir_p.glob("verifier_report*.json")):
        obj = _read_json(p)
        if obj is not None:
            verifier_reports.append({"path": p.name, "report": obj})
    if verifier_reports:
        report["artifacts"]["verifier_reports"] = verifier_reports

    # Runtime events
    events = read_events(run_dir_p)
    report["artifacts"]["runtime_events_count"] = len(events)

    # Derive protocol snapshots and command stream
    protocols: List[Dict[str, Any]] = []
    commands: List[Dict[str, Any]] = []
    telemetry_events: List[Dict[str, Any]] = []
    ack_events: List[Dict[str, Any]] = []
    error_events: List[Dict[str, Any]] = []
    for ev in events:
        et = ev.get("event_type")
        payload = ev.get("payload") or {}
        if et == "protocol_status":
            protocols.append(payload)
        elif et == "telemetry":
            telemetry_events.append(payload)
        elif et == "ack":
            ack_events.append(payload)
        elif et == "error":
            error_events.append(payload)
        elif et == "command":
            commands.append(payload)
        elif et == "command_guard":
            # Flatten command_guard into commands list or treat separate?
            # User wants to count denied. usage of check below rely on 'guard' key in command dict?
            # Or payload IS the guard result + command?
            # payload structure from runtime_monitor: {command, allowed, reason, rule_source...}
            # We can adapt it to fit into 'commands' list or just count it.
            # Let's append to commands but structured differently?
            # The existing logic below iterates 'commands' and looks for 'guard' key.
            # Let's map command_guard payload to { ..., guard: {allowed: ..., reason: ...} }
            cmd_data = payload.get("command", {}).copy()
            cmd_data["guard"] = {
                "allowed": payload.get("allowed"),
                "reason": payload.get("reason"),
                "rule_source": payload.get("rule_source")
            }
            commands.append(cmd_data)

    report["protocols"]["snapshots"] = protocols
    report["commands"]["events"] = commands
    report["events"]["telemetry"] = telemetry_events
    report["events"]["ack"] = ack_events
    report["events"]["error"] = error_events
    report["events"]["replay"] = replay_events(events)

    # Summary metrics
    success = None
    hard_violations = None
    total_violations = None
    closed_loop_rounds = None
    total_ms = None

    if isinstance(metrics_obj, dict):
        success = metrics_obj.get("success")
        total_ms = metrics_obj.get("total_ms") or (metrics_obj.get("timing_ms", {}) or {}).get("total")
        # Phase2 metrics convention: violations_count / hard_violations_count
        total_violations = metrics_obj.get("violations_count") or metrics_obj.get("violations", {}).get("count") if isinstance(metrics_obj.get("violations"), dict) else None
        hard_violations = metrics_obj.get("hard_violations_count") or metrics_obj.get("violations", {}).get("hard_count") if isinstance(metrics_obj.get("violations"), dict) else None
        closed_loop_rounds = metrics_obj.get("closed_loop_rounds")

    # Command-level guard stats (if present)
    guard_denied = 0
    guard_total = 0
    for c in commands:
        g = c.get("guard")
        if isinstance(g, dict) and "allowed" in g:
            guard_total += 1
            if not bool(g.get("allowed")):
                guard_denied += 1

    report["summary"].update({
        "success": success,
        "total_ms": total_ms,
        "total_violations": total_violations,
        "hard_violations": hard_violations,
        "closed_loop_rounds": closed_loop_rounds,
        "guard_total": guard_total,
        "guard_denied": guard_denied,
        "telemetry_events": len(telemetry_events),
        "ack_events": len(ack_events),
        "runtime_errors": len(error_events),
    })

    return report


def export_report(report: Dict[str, Any], output_path: Union[str, Path]) -> None:
    """Export report to JSON or CSV depending on suffix."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.suffix.lower() in {".json"}:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    if out.suffix.lower() in {".csv"}:
        # Flatten a conservative subset for CSV
        rows = []
        summary = report.get("summary") or {}
        rows.append({"key": "generated_utc", "value": report.get("generated_utc")})
        rows.append({"key": "run_dir", "value": report.get("run_dir")})
        for k, v in summary.items():
            rows.append({"key": k, "value": v})
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["key", "value"])
            w.writeheader()
            w.writerows(rows)
        return

    # Default: JSON
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
