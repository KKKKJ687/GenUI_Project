from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, Optional


def _read_json(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def generate_case_study(report_dir: str, case_id: str) -> Path:
    src = Path(report_dir)
    case_dir = Path("case_studies") / case_id
    artifacts_dir = case_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    artifact_names = ["input.json", "constraints.json", "verifier_report.json", "final.html", "evidence.json"]
    copied = []
    for name in artifact_names:
        p = src / name
        if p.exists():
            shutil.copy2(p, artifacts_dir / name)
            copied.append(name)

    input_obj = _read_json(src / "input.json") or {}
    constraints_obj = _read_json(src / "constraints.json") or {}
    verifier_obj = _read_json(src / "verifier_report.json") or {}

    md = [
        f"# Case Study: {case_id}",
        "",
        "## Experiment Description",
        f"- Source run directory: `{src}`",
        f"- Copied artifacts: {', '.join(copied) if copied else '(none)'}",
        "",
        "## Input Prompt",
        "```json",
        json.dumps(input_obj, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Constraints",
        "```json",
        json.dumps(constraints_obj, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Violations and Fixes",
        "```json",
        json.dumps(verifier_obj, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Final Output",
        f"- HTML file: `{artifacts_dir / 'final.html'}`",
    ]
    case_md_path = case_dir / "case.md"
    case_md_path.write_text("\n".join(md), encoding="utf-8")
    return case_md_path


def export_case_study(report_dir: str, case_id: str) -> Path:
    out = generate_case_study(report_dir, case_id)
    print(f"Case study for {case_id} exported successfully: {out}")
    return out
