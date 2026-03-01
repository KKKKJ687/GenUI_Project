from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

import pandas as pd


def _normalize_data(data_or_report_dir: Union[str, Path, List[Dict[str, Any]], Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(data_or_report_dir, list):
        return data_or_report_dir
    if isinstance(data_or_report_dir, dict):
        if "per_mode" in data_or_report_dir:
            return list(data_or_report_dir["per_mode"])
        return [data_or_report_dir]

    report_path = Path(data_or_report_dir) / "report.json"
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    return list(raw.get("per_mode", []))


def generate_summary_table(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not data:
        summary = {
            "Success Rate": [0.0],
            "Violation Rate": [0.0],
            "Average Fix Rounds": [0.0],
            "Average Latency": [0.0],
            "Average Closed Loop Rounds": [0.0],
            "Evidence Traceability Rate": [0.0],
        }
    else:
        summary = {
            "Success Rate": [sum(float(item.get("success_rate", 0.0)) for item in data) / len(data)],
            "Violation Rate": [sum(float(item.get("violation_rate", 0.0)) for item in data) / len(data)],
            "Average Fix Rounds": [sum(float(item.get("avg_fix_rounds", 0.0)) for item in data) / len(data)],
            "Average Latency": [sum(float(item.get("latency", 0.0)) for item in data) / len(data)],
            "Average Closed Loop Rounds": [sum(float(item.get("closed_loop_rounds", 0.0)) for item in data) / len(data)],
            "Evidence Traceability Rate": [
                sum(float(item.get("evidence_traceability_rate", 0.0)) for item in data) / len(data)
            ],
        }

    pd.DataFrame(summary).to_csv(out / "summary.csv", index=False)


def generate_per_case_table(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_csv(out / "per_case.csv", index=False)


def make_metrics_tables(report_dir, output_dir) -> None:
    generate_summary_table(report_dir, output_dir)
    generate_per_case_table(report_dir, output_dir)
