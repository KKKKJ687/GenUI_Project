from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.pyplot as plt


def _load_report(report_dir: str) -> Dict[str, Any]:
    p = Path(report_dir) / "report.json"
    obj = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(obj, list):
        # legacy report: list of mode rows
        return {"per_mode": obj, "summary": {}, "per_case": {}}
    return obj


def _ensure_fig_dir(report_dir: Path) -> Path:
    fig_dir = report_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    return fig_dir


def generate_figures(report_dir: str) -> None:
    report_path = Path(report_dir)
    report = _load_report(str(report_path))
    fig_dir = _ensure_fig_dir(report_path)

    per_mode: List[Dict[str, Any]] = report.get("per_mode", [])
    if not per_mode and isinstance(report.get("summary"), dict):
        # backfill single-point plot from summary
        per_mode = [
            {
                "mode": "all",
                "success_rate": float(report["summary"].get("success_rate", 0.0)),
                "violation_rate": float(report["summary"].get("hard_violation_rate", 0.0)),
                "avg_fix_rounds": float(report["summary"].get("avg_fix_rounds", 0.0)),
                "latency": float(report["summary"].get("avg_latency", 0.0)),
                "closed_loop_rounds": float(report["summary"].get("avg_fix_rounds", 0.0)),
            }
        ]

    modes = [x.get("mode", "unknown") for x in per_mode]

    def _bar(values: List[float], title: str, ylabel: str, filename: str) -> None:
        fig, ax = plt.subplots()
        ax.bar(modes, values)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        fig.tight_layout()
        fig.savefig(fig_dir / filename)
        plt.close(fig)

    _bar(
        [float(x.get("success_rate", 0.0)) for x in per_mode],
        "Success Rate by Mode",
        "Success Rate",
        "success_rate_by_experiment.png",
    )
    _bar(
        [float(x.get("violation_rate", 0.0)) for x in per_mode],
        "Hard Violation Rate by Mode",
        "Violation Rate",
        "hard_violation_rate_by_experiment.png",
    )
    _bar(
        [float(x.get("avg_fix_rounds", 0.0)) for x in per_mode],
        "Average Fix Rounds by Mode",
        "Rounds",
        "avg_closed_loop_rounds.png",
    )
    _bar(
        [float(x.get("latency", 0.0)) for x in per_mode],
        "Latency by Mode",
        "Latency (ms)",
        "latency_breakdown.png",
    )
    _bar(
        [float(x.get("success_rate", 0.0)) for x in per_mode],
        "Checks Pass Rate",
        "Pass Rate",
        "checks_pass_rate.png",
    )

    print("Figures generated successfully!")


if __name__ == "__main__":
    generate_figures("runs/latest")
