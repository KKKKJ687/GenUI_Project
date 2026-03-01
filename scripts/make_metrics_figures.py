from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

import matplotlib.pyplot as plt


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


def _plot_bar(data: List[Dict[str, Any]], key: str, ylabel: str, title: str, out_file: Path) -> None:
    modes = [str(item.get("mode", "unknown")) for item in data]
    values = [float(item.get(key, 0.0)) for item in data]
    fig, ax = plt.subplots()
    ax.bar(modes, values)
    ax.set_xlabel("Mode")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_file)
    plt.close(fig)


def generate_success_rate_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _plot_bar(data, "success_rate", "Success Rate", "Success Rate by Mode", out / "success_rate_by_mode.png")


def generate_violation_rate_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _plot_bar(
        data,
        "violation_rate",
        "Violation Rate",
        "Hard Violation Rate by Mode",
        out / "hard_violation_rate_by_mode.png",
    )


def generate_fix_rounds_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _plot_bar(
        data,
        "avg_fix_rounds",
        "Average Fix Rounds",
        "Average Fix Rounds by Mode",
        out / "average_fix_rounds_by_mode.png",
    )


def generate_latency_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _plot_bar(data, "latency", "Latency (ms)", "Latency per Mode", out / "latency_per_mode.png")


def generate_closed_loop_rounds_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _plot_bar(data, "closed_loop_rounds", "Closed Loop Rounds", "Closed Loop Rounds", out / "closed_loop_rounds.png")


def generate_traceability_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _plot_bar(
        data,
        "evidence_traceability_rate",
        "Traceability Rate",
        "Evidence Traceability by Mode",
        out / "evidence_traceability_rate_by_mode.png",
    )


def generate_latency_breakdown_chart(data_or_report_dir, output_dir) -> None:
    data = _normalize_data(data_or_report_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    import matplotlib.pyplot as plt

    modes = [str(item.get("mode", "unknown")) for item in data]
    plan = [float(item.get("latency_plan_ms", 0.0)) for item in data]
    arch = [float(item.get("latency_architect_ms", 0.0)) for item in data]
    verify = [float(item.get("latency_verify_ms", 0.0)) for item in data]
    render = [float(item.get("latency_render_ms", 0.0)) for item in data]

    fig, ax = plt.subplots()
    ax.bar(modes, plan, label="plan")
    ax.bar(modes, arch, bottom=plan, label="architect")
    bottom_v = [plan[i] + arch[i] for i in range(len(modes))]
    ax.bar(modes, verify, bottom=bottom_v, label="verify")
    bottom_r = [bottom_v[i] + verify[i] for i in range(len(modes))]
    ax.bar(modes, render, bottom=bottom_r, label="render")
    ax.set_title("Latency Breakdown by Mode")
    ax.set_ylabel("Latency (ms)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "latency_breakdown_by_mode.png")
    plt.close(fig)


def make_metrics_figures(report_dir, output_dir) -> None:
    data = _normalize_data(report_dir)
    generate_success_rate_chart(data, output_dir)
    generate_violation_rate_chart(data, output_dir)
    generate_fix_rounds_chart(data, output_dir)
    generate_latency_chart(data, output_dir)
    generate_closed_loop_rounds_chart(data, output_dir)
    generate_traceability_chart(data, output_dir)
    generate_latency_breakdown_chart(data, output_dir)
