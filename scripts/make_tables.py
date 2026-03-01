from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_report(report_dir: str) -> Dict[str, Any]:
    obj = json.loads((Path(report_dir) / "report.json").read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return {
            "per_mode": obj,
            "summary": {
                "success_rate": sum(x.get("success_rate", 0.0) for x in obj) / max(len(obj), 1),
                "avg_latency": sum(x.get("latency", 0.0) for x in obj) / max(len(obj), 1),
                "hard_violation_rate": sum(x.get("violation_rate", 0.0) for x in obj) / max(len(obj), 1),
            },
        }
    return obj


def generate_tables(report_dir: str) -> None:
    report_path = Path(report_dir)
    tables_dir = report_path / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    report = _load_report(report_dir)
    summary = report.get("summary", {})
    per_mode: List[Dict[str, Any]] = report.get("per_mode", [])

    # summary.csv
    with (tables_dir / "summary.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["success_rate", "avg_latency", "hard_violation_rate"])
        w.writerow(
            [
                summary.get("success_rate", 0.0),
                summary.get("avg_latency", summary.get("total_ms", 0.0)),
                summary.get("hard_violation_rate", summary.get("violation_rate", 0.0)),
            ]
        )

    # per_case.csv (per_mode granularity for compatibility)
    with (tables_dir / "per_case.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "success_rate", "hard_violation_rate"])
        for row in per_mode:
            w.writerow([row.get("mode", "unknown"), row.get("success_rate", 0.0), row.get("violation_rate", 0.0)])

    print("Tables generated successfully!")


if __name__ == "__main__":
    generate_tables("runs/latest")
