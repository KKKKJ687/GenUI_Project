"""
Phase 2 Pipeline: NL -> DSL -> Verifier -> (Closed-Loop Repair) -> Render.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.agents.prompts_phase2 import get_architect_dsl_prompt
from src.core.agent_driver import EngineeringAgent
from src.core.prompt_conflict_guard import (
    detect_prompt_constraint_conflicts,
    summarize_conflicts_for_prompt,
    summarize_constraints_for_prompt,
)
from src.models.schema import HMIPanel
from src.modules.rag.constraint_extractor import (
    extract_and_resolve_conflicts,
    extract_constraints,
    detect_conflicts,
    normalize_constraint_semantics,
    resolve_conflicts,
)
from src.modules.rag.datasheet_rag import ingest_pdf, retrieve_evidence
from src.modules.runtime.runtime_monitor import append_event, export_session_log, monitor_command_with_context
from src.modules.renderer.renderer import render_panel
from src.modules.verifier.constraints import Constraint, ConstraintKind, ConstraintSet, Severity, SourceRef
from src.modules.verifier.verifier import verify_panel, apply_fixes
from src.utils.run_artifacts import RunArtifacts

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> str:
    """Extract first JSON object from mixed LLM text."""
    try:
        match = re.search(r"\{[\s\S]*\}", (text or "").strip())
        if match:
            return match.group(0)
    except Exception:
        pass
    return text or ""


def get_sample_constraints() -> ConstraintSet:
    """Default constraint set for demo / no-upload workflows."""
    return ConstraintSet(
        device_name="ESP32-WROOM-32 (Sample)",
        constraints=[
            Constraint(
                id="ESP32_ADC_LIMIT",
                name="ESP32 Input Voltage Limit",
                kind=ConstraintKind.MAX,
                applies_to="widgets[type='slider'].max",
                max_val=3.3,
                unit="V",
                severity=Severity.HARD,
                description="ESP32 ADC pins are 3.3V tolerant.",
            ),
            Constraint(
                id="PWM_FREQ_LIMIT",
                name="PWM Frequency Limit",
                kind=ConstraintKind.MAX,
                applies_to="widgets[id='pwm_fan'].frequency",
                max_val=40000.0,
                unit="Hz",
                severity=Severity.SOFT,
                description="Standard PWM frequency limit for fan control.",
            ),
        ],
    )


def _load_latest_report(run_dir: Path) -> Optional[Dict[str, Any]]:
    candidates = sorted((run_dir / "intermediate").glob("round_*_report.json"))
    if (run_dir / "intermediate/final_clamped_report.json").exists():
        candidates.append(run_dir / "intermediate/final_clamped_report.json")
    for p in reversed(candidates):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _load_first_report(run_dir: Path) -> Optional[Dict[str, Any]]:
    candidates = sorted((run_dir / "intermediate").glob("round_*_report.json"))
    for p in candidates:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _write_round_diffs(run_dir: Path) -> None:
    inter = run_dir / "intermediate"
    if not inter.exists():
        return
    rounds = sorted(inter.glob("round_*_dsl.json"))
    if len(rounds) < 2:
        return

    import difflib

    def _round_id(path: Path) -> int:
        m = re.search(r"round_(\d+)_dsl\.json$", path.name)
        return int(m.group(1)) if m else -1

    rounds = sorted(rounds, key=_round_id)
    for a, b in zip(rounds, rounds[1:]):
        try:
            a_text = json.dumps(json.loads(a.read_text(encoding="utf-8")), ensure_ascii=False, indent=2).splitlines()
            b_text = json.dumps(json.loads(b.read_text(encoding="utf-8")), ensure_ascii=False, indent=2).splitlines()
        except Exception:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                a_text,
                b_text,
                fromfile=a.name,
                tofile=b.name,
                lineterm="",
            )
        )
        out_name = f"{a.stem}_to_{b.stem}.diff"
        (inter / out_name).write_text(diff, encoding="utf-8")


def _source_ref_to_text(source_ref: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(source_ref, dict):
        return None
    name = source_ref.get("datasheet_name", "unknown")
    page = source_ref.get("page")
    if page is None:
        return str(name)
    return f"{name}, Page {page}"


def _log_verifier_events(run_dir: Path, report_obj: Dict[str, Any]) -> None:
    violations = list(report_obj.get("violations") or [])
    fixes = list(report_obj.get("fixes") or [])

    for v in violations:
        command = {
            "target": v.get("param_path"),
            "value": v.get("observed_value"),
            "unit": v.get("unit"),
            "action": "set_value",
        }
        monitor_command_with_context(
            command,
            {
                "allowed": False,
                "reason": v.get("message"),
                "source_ref": _source_ref_to_text(v.get("source_ref")),
                "target": v.get("param_path"),
                "action": "REJECT",
                "constraint": {
                    "expected_limit": v.get("expected_limit"),
                    "unit": v.get("unit"),
                },
            },
            run_dir,
        )

    for f in fixes:
        append_event(
            run_dir,
            "ack",
            {
                "rule_id": f.get("violation_rule_id"),
                "param_path": f.get("param_path"),
                "action": f.get("action_type"),
                "value_before": f.get("value_before"),
                "value_after": f.get("value_after"),
                "reason": f.get("reason"),
            },
        )


def _normalize_panel_validation_report(panel: Optional[HMIPanel]) -> int:
    """
    Normalize metadata.validation_report key names for stable downstream metrics.
    Returns number of normalized report rows.
    """
    if panel is None or not isinstance(panel.metadata, dict):
        return 0
    raw_rows = panel.metadata.get("validation_report")
    rows: List[Any] = []
    if isinstance(raw_rows, list):
        rows = raw_rows
    elif isinstance(raw_rows, dict):
        rows = [raw_rows]
    elif isinstance(raw_rows, str):
        text = raw_rows.strip()
        if text:
            rows = [{"message": text}]
    elif raw_rows is not None:
        rows = [{"message": str(raw_rows)}]
    else:
        return 0

    normalized_rows: List[Dict[str, Any]] = []

    count = 0
    for row in rows:
        if not isinstance(row, dict):
            row = {"message": str(row)}
        row = dict(row)
        if "before" not in row and "requested" in row:
            row["before"] = row.get("requested")
        if "after" not in row and "actual" in row:
            row["after"] = row.get("actual")
        if "reason" not in row and isinstance(row.get("message"), str):
            row["reason"] = row.get("message")
        normalized_rows.append(row)
        count += 1
    panel.metadata["validation_report"] = normalized_rows
    return count


def _theme_from_style(style: str) -> str:
    key = (style or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "dark_mode": "dark",
        "light_mode": "light",
        "industrial_blue_mode": "industrial_blue",
        "industrial_blue": "industrial_blue",
        "classic": "light",
        "minimalist": "light",
        "cyberpunk": "dark",
        "neon": "dark",
        "wizard_green": "dark",
    }
    theme = mapping.get(key, key)
    if theme not in {"dark", "light", "industrial_blue"}:
        theme = "dark"
    return theme


def _safe_widget_id(text: str, idx: int) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", (text or "").strip().lower()).strip("_")
    if not raw:
        raw = f"fallback_{idx}"
    return f"{raw[:20]}_{idx}"


def _to_canonical_unit(value: float, unit: str) -> Tuple[float, str]:
    key = (unit or "").strip().lower()
    if key == "mv":
        return float(value) / 1000.0, "V"
    if key == "ma":
        return float(value) / 1000.0, "A"
    if key == "khz":
        return float(value) * 1000.0, "Hz"
    if key == "mhz":
        return float(value) * 1_000_000.0, "Hz"
    if key in {"v", "a", "hz"}:
        return float(value), key.upper() if key in {"v", "a"} else "Hz"
    return float(value), unit or "unitless"


def _range_step(min_v: float, max_v: float) -> float:
    span = float(max_v) - float(min_v)
    if span <= 0:
        return 0.1
    return max(span / 200.0, 0.01)


def _infer_domain_from_clause(clause: str, unit: str) -> Optional[str]:
    text = (clause or "").lower()
    unit_l = (unit or "").lower()
    if any(k in text for k in ["电压", "voltage", "supply", "vin", "vcc", "vdd", "vm"]) or unit_l in {"v", "mv"}:
        if any(k in text for k in ["logic", "输入", "input", "in1", "in2", "digital", "逻辑"]):
            return "logic_voltage"
        if any(k in text for k in ["supply", "power", "电源", "供电", "vm", "vbat", "vbatt", "vbus", "battery"]):
            return "supply_voltage"
        return "voltage"
    if any(k in text for k in ["电流", "current", "amp", "限流"]) or unit_l in {"a", "ma"}:
        return "current"
    if any(k in text for k in ["pwm", "频率", "frequency", "switching"]) or unit_l in {"hz", "khz", "mhz"}:
        return "frequency"
    return None


def _extract_prompt_ranges(user_prompt: str) -> Dict[str, Dict[str, float | str]]:
    """
    Parse semantic ranges from user prompt, e.g.:
      "电源电压 0~50V, PWM 频率 0~300kHz, 电流限制 0~5A"
    """
    out: Dict[str, Dict[str, float | str]] = {}
    clauses = [c.strip() for c in re.split(r"[,\uFF0C;\uFF1B。\n]+", user_prompt or "") if c.strip()]
    range_re = re.compile(
        r"([+\-]?\d+(?:\.\d+)?)\s*(mv|v|ma|a|mhz|khz|hz)?\s*(?:~|～|to|TO|-|–|—)\s*([+\-]?\d+(?:\.\d+)?)\s*(mv|v|ma|a|mhz|khz|hz)",
        re.IGNORECASE,
    )
    for clause in clauses:
        m = range_re.search(clause)
        if not m:
            continue
        left = float(m.group(1))
        right = float(m.group(3))
        unit = (m.group(4) or m.group(2) or "").lower()
        if not unit:
            continue
        min_v = min(left, right)
        max_v = max(left, right)
        min_c, unit_c = _to_canonical_unit(min_v, unit)
        max_c, _ = _to_canonical_unit(max_v, unit)
        if max_c <= min_c:
            continue

        domain = _infer_domain_from_clause(clause, unit_c)
        if not domain:
            continue
        out[domain] = {"min": float(min_c), "max": float(max_c), "unit": unit_c}
    return out


def _extract_constraint_ranges(constraints: Optional[ConstraintSet]) -> Dict[str, Dict[str, float | str]]:
    out: Dict[str, Dict[str, float | str]] = {}
    if not constraints:
        return out
    for c in constraints.constraints:
        blob = f"{c.applies_to} {c.name} {c.description or ''}".lower()
        unit = c.unit or "unitless"
        domain: Optional[str] = None
        if "voltage" in blob or "电压" in blob or unit.lower() in {"v", "mv"}:
            if any(k in blob for k in ["logic", "input", "in1", "in2", "digital", "逻辑", "输入"]):
                domain = "logic_voltage"
            elif any(k in blob for k in ["supply", "power", "电源", "供电", "vm", "vbat", "vbatt", "vbus", "battery"]):
                domain = "supply_voltage"
            else:
                domain = "voltage"
        elif "current" in blob or "电流" in blob or unit.lower() in {"a", "ma"}:
            domain = "current"
        elif "frequency" in blob or "freq" in blob or "pwm" in blob or "频率" in blob or unit.lower() in {"hz", "khz", "mhz"}:
            domain = "frequency"
        if not domain:
            continue

        if c.kind == ConstraintKind.RANGE and c.min_val is not None and c.max_val is not None:
            min_v, unit_c = _to_canonical_unit(float(c.min_val), unit)
            max_v, _ = _to_canonical_unit(float(c.max_val), unit)
        elif c.kind == ConstraintKind.MAX and c.max_val is not None:
            max_v, unit_c = _to_canonical_unit(float(c.max_val), unit)
            min_v = 0.0
        elif c.kind == ConstraintKind.MIN and c.min_val is not None:
            min_v, unit_c = _to_canonical_unit(float(c.min_val), unit)
            max_v = max(min_v * 2.0, min_v + 1.0)
        else:
            continue

        if max_v <= min_v:
            continue
        # Keep conservative/usable bounds when repeated.
        if domain in out:
            old_min = float(out[domain]["min"])
            old_max = float(out[domain]["max"])
            new_min = max(old_min, float(min_v))
            new_max = min(old_max, float(max_v))
            if new_max <= new_min:
                # Fallback to narrower absolute span.
                old_span = old_max - old_min
                new_span = float(max_v) - float(min_v)
                if new_span < old_span:
                    out[domain] = {"min": float(min_v), "max": float(max_v), "unit": unit_c}
            else:
                out[domain] = {"min": float(new_min), "max": float(new_max), "unit": unit_c}
        else:
            out[domain] = {"min": float(min_v), "max": float(max_v), "unit": unit_c}
        # Keep backward-compatible generic voltage envelope for ambiguous prompts.
        if domain in {"supply_voltage", "logic_voltage"}:
            generic_key = "voltage"
            if generic_key in out:
                old_min = float(out[generic_key]["min"])
                old_max = float(out[generic_key]["max"])
                merged_min = max(old_min, float(min_v))
                merged_max = min(old_max, float(max_v))
                if merged_max > merged_min:
                    out[generic_key] = {
                        "min": merged_min,
                        "max": merged_max,
                        "unit": unit_c,
                    }
            else:
                out[generic_key] = {"min": float(min_v), "max": float(max_v), "unit": unit_c}
    return out


def _device_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (raw or "").lower())


def _build_device_profile_constraints(device_name: str) -> List[Constraint]:
    """
    Curated fallback constraints for known devices when PDF extraction returns empty.
    These rules are conservative and traceable, and only used as last-resort backfill.
    """
    key = _device_key(device_name)
    if "drv8871" in key:
        ds_name = device_name or "drv8871.pdf"
        return [
            Constraint(
                id="DRV8871_VM_RECOMMENDED_RANGE",
                name="DRV8871 Motor Supply Voltage Range",
                description="Recommended operating VM range.",
                kind=ConstraintKind.RANGE,
                applies_to="*.supply_voltage",
                min_val=6.5,
                max_val=45.0,
                unit="V",
                severity=Severity.HARD,
                source=SourceRef(
                    datasheet_name=ds_name,
                    page=4,
                    section="Recommended Operating Conditions",
                    snippet="VM operating range: 6.5 V to 45 V",
                    confidence=0.95,
                ),
            ),
            Constraint(
                id="DRV8871_PWM_FREQ_MAX",
                name="DRV8871 PWM Frequency Limit",
                description="Practical PWM frequency ceiling for stable operation.",
                kind=ConstraintKind.MAX,
                applies_to="*.frequency",
                max_val=200000.0,
                unit="Hz",
                severity=Severity.HARD,
                source=SourceRef(
                    datasheet_name=ds_name,
                    page=8,
                    section="PWM Control",
                    snippet="PWM operating frequency guidance up to 200 kHz",
                    confidence=0.7,
                ),
            ),
            Constraint(
                id="DRV8871_IOUT_PEAK_MAX",
                name="DRV8871 Peak Output Current Limit",
                description="Peak output current protection limit.",
                kind=ConstraintKind.MAX,
                applies_to="*.current",
                max_val=3.6,
                unit="A",
                severity=Severity.HARD,
                source=SourceRef(
                    datasheet_name=ds_name,
                    page=1,
                    section="Features",
                    snippet="Up to 3.6-A peak current control",
                    confidence=0.9,
                ),
            ),
        ]

    if "adxl345" in key:
        ds_name = device_name or "adxl345.pdf"
        return [
            Constraint(
                id="ADXL345_RANGE_ENUM",
                name="ADXL345 Full-Scale Range Enum",
                description="Discrete full-scale range options for ADXL345.",
                kind=ConstraintKind.ENUM,
                applies_to="*.acceleration",
                allowed_values=["±2g", "±4g", "±8g", "±16g"],
                unit="g",
                severity=Severity.HARD,
                source=SourceRef(
                    datasheet_name=ds_name,
                    page=1,
                    section="Electrical Characteristics",
                    snippet="Selectable full-scale ranges: ±2 g, ±4 g, ±8 g, ±16 g",
                    confidence=0.9,
                ),
            )
        ]

    return []


def _normalize_enum_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    text = text.replace("+/-", "±")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if m and "g" in text:
        num = m.group(0)
        try:
            f = float(num)
            num = str(int(f)) if f.is_integer() else f"{f:.6g}"
        except (TypeError, ValueError):
            pass
        return f"{num}g"
    return text


def _merge_profile_constraints(constraints: ConstraintSet) -> ConstraintSet:
    """
    Add missing high-value device-profile rules even when extraction is non-empty.
    This is used to harden fragile datasheet cases (e.g., ADXL345 range enum).
    """
    if not constraints:
        return constraints

    profile_rules = _build_device_profile_constraints(constraints.device_name)
    if not profile_rules:
        return constraints

    existing_ids = {c.id for c in constraints.constraints}
    injected: List[str] = []

    for rule in profile_rules:
        if rule.id in existing_ids:
            continue

        if rule.kind == ConstraintKind.ENUM:
            same_domain = [
                c for c in constraints.constraints
                if c.kind == ConstraintKind.ENUM and str(c.applies_to or "") == str(rule.applies_to or "")
            ]
            expected = {_normalize_enum_token(v) for v in (rule.allowed_values or [])}
            has_compatible = False
            for c in same_domain:
                existing = {_normalize_enum_token(v) for v in (c.allowed_values or [])}
                # Require substantial overlap to treat it as equivalent.
                if expected and len(expected & existing) >= max(2, len(expected) - 1):
                    has_compatible = True
                    break
            if has_compatible:
                continue

        constraints.constraints.append(rule)
        injected.append(rule.id)

    if injected:
        constraints.metadata["profile_augmented_rules"] = injected
        constraints.metadata["profile_augmented_count"] = len(injected)

    return constraints


def _backfill_empty_constraints(constraints: Optional[ConstraintSet], user_prompt: str) -> Optional[ConstraintSet]:
    if not constraints:
        return constraints
    if constraints.constraints:
        return constraints

    profile_constraints = _build_device_profile_constraints(constraints.device_name)
    if profile_constraints:
        constraints.constraints = profile_constraints
        constraints.metadata["source"] = "device_profile_fallback"
        constraints.metadata["manual"] = True
        constraints.metadata["fallback_reason"] = "empty_extraction_profile_backfill"
        constraints.metadata["profile_constraints_count"] = len(profile_constraints)
        return constraints

    # Last-resort generic fallback from prompt ranges (still marked manual).
    prompt_ranges = _extract_prompt_ranges(user_prompt)
    generic_constraints: List[Constraint] = []
    for domain, spec in prompt_ranges.items():
        min_v = float(spec["min"])
        max_v = float(spec["max"])
        if max_v <= min_v:
            continue
        unit = str(spec["unit"])
        applies_to = f"*.{domain}"
        generic_constraints.append(
            Constraint(
                id=f"PROMPT_{domain.upper()}_RANGE",
                name=f"Prompt Derived {domain.title()} Range",
                description="Fallback range derived from user prompt due missing datasheet constraints.",
                kind=ConstraintKind.RANGE,
                applies_to=applies_to,
                min_val=min_v,
                max_val=max_v,
                unit=unit,
                severity=Severity.SOFT,
                source=SourceRef(
                    datasheet_name=constraints.device_name or "unknown",
                    page=None,
                    section="Prompt fallback",
                    snippet=f"{domain}: {min_v}~{max_v}{unit}",
                    confidence=0.3,
                ),
            )
        )
    if generic_constraints:
        constraints.constraints = generic_constraints
        constraints.metadata["source"] = "prompt_range_fallback"
        constraints.metadata["manual"] = True
        constraints.metadata["fallback_reason"] = "empty_extraction_prompt_backfill"
        constraints.metadata["profile_constraints_count"] = 0
    return constraints


def _build_offline_fallback_panel(
    *,
    user_prompt: str,
    style: str,
    constraints: Optional[ConstraintSet],
) -> HMIPanel:
    widgets: list[dict] = []
    layout: list[dict] = []

    prompt_ranges = _extract_prompt_ranges(user_prompt)
    constraint_ranges = _extract_constraint_ranges(constraints)

    # Prompt intent has higher priority; constraints fill missing domains.
    merged_ranges: Dict[str, Dict[str, float | str]] = dict(constraint_ranges)
    merged_ranges.update(prompt_ranges)

    range_specs = [
        ("supply_voltage", "supply_voltage", "Supply Voltage"),
        ("voltage", "supply_voltage", "Supply Voltage"),
        ("frequency", "pwm_frequency", "PWM Frequency"),
        ("current", "current_limit", "Current Limit"),
    ]
    for domain, wid, label in range_specs:
        if any(w.get("id") == wid for w in widgets):
            continue
        r = merged_ranges.get(domain)
        if not r:
            continue
        min_v = float(r["min"])
        max_v = float(r["max"])
        if max_v <= min_v:
            continue
        widgets.append(
            {
                "id": wid,
                "type": "slider",
                "label": label,
                "min": min_v,
                "max": max_v,
                "step": _range_step(min_v, max_v),
                "value": min_v,
                "unit": str(r["unit"]),
            }
        )

    prompt_l = (user_prompt or "").lower()
    if any(k in prompt_l for k in ["启停", "开关", "switch", "enable", "start", "stop"]):
        widgets.append(
            {
                "id": "motor_enable",
                "type": "switch",
                "label": "Motor Enable",
                "on_label": "START",
                "off_label": "STOP",
                "value": False,
            }
        )

    if any(k in prompt_l for k in ["表盘", "仪表", "gauge", "dial", "状态", "status"]):
        gauge_range = merged_ranges.get("current") or merged_ranges.get("voltage") or {"min": 0.0, "max": 100.0, "unit": "%"}
        g_min = float(gauge_range["min"])
        g_max = float(gauge_range["max"])
        if g_max <= g_min:
            g_max = g_min + 1.0
        g_mid = (g_min + g_max) / 2.0
        widgets.append(
            {
                "id": "status_gauge",
                "type": "gauge",
                "label": "Status Gauge",
                "min": g_min,
                "max": g_max,
                "value": g_mid,
                "thresholds": [g_min + (g_max - g_min) * 0.6, g_min + (g_max - g_min) * 0.85],
                "safety": {"unit": str(gauge_range["unit"])},
            }
        )

    # If prompt-driven parse still yielded no widgets, degrade to constraint-only fallback.
    if not widgets:
        x_slots = [0, 6]
        y = 0
        usable_constraints = list((constraints.constraints if constraints else [])[:4])
        for idx, c in enumerate(usable_constraints):
            wid = _safe_widget_id(c.id or c.name or "constraint", idx)
            label = c.name or c.id or f"Constraint {idx + 1}"
            unit = "" if (c.unit or "unitless") == "unitless" else (c.unit or "")

            if c.kind == ConstraintKind.ENUM and c.allowed_values:
                options = [str(v) for v in c.allowed_values if str(v).strip()]
                if options:
                    widgets.append(
                        {
                            "id": wid,
                            "type": "select",
                            "label": label,
                            "options": options,
                            "value": options[0],
                            "unit": unit,
                        }
                    )
                else:
                    continue
            elif c.kind in {ConstraintKind.RANGE, ConstraintKind.MAX, ConstraintKind.MIN}:
                min_v = c.min_val if c.min_val is not None else 0.0
                max_v = c.max_val if c.max_val is not None else (min_v * 2 if min_v > 0 else 100.0)
                if max_v <= min_v:
                    max_v = min_v + 1.0
                value = min_v
                widgets.append(
                    {
                        "id": wid,
                        "type": "slider",
                        "label": label,
                        "min": float(min_v),
                        "max": float(max_v),
                        "step": _range_step(float(min_v), float(max_v)),
                        "value": float(value),
                        "unit": unit,
                    }
                )
            else:
                continue

            layout.append({"i": wid, "x": x_slots[idx % len(x_slots)], "y": y, "w": 6, "h": 2})
            if idx % len(x_slots) == len(x_slots) - 1:
                y += 2

    if not widgets:
        widgets = [
            {
                "id": "fallback_voltage",
                "type": "slider",
                "label": "Fallback Voltage",
                "min": 0.0,
                "max": 12.0,
                "step": 0.1,
                "value": 6.0,
                "unit": "V",
            },
            {
                "id": "fallback_enable",
                "type": "switch",
                "label": "Fallback Enable",
                "value": False,
            },
        ]
        layout = [
            {"i": "fallback_voltage", "x": 0, "y": 0, "w": 6, "h": 2},
            {"i": "fallback_enable", "x": 6, "y": 0, "w": 6, "h": 2},
        ]
    elif not layout:
        for idx, w in enumerate(widgets):
            layout.append({"i": w["id"], "x": 0 if idx % 2 == 0 else 6, "y": (idx // 2) * 2, "w": 6, "h": 2})

    payload = {
        "project_name": f"Offline Fallback Panel",
        "description": f"Generated in degraded mode. Prompt: {user_prompt[:80]}",
        "theme": _theme_from_style(style),
        "widgets": widgets,
        "layout": layout,
        "metadata": {
            "degraded_mode": True,
            "fallback_strategy": "prompt_driven" if prompt_ranges else "constraints_only",
            "prompt_ranges": prompt_ranges,
            "constraint_ranges": constraint_ranges,
        },
    }
    return HMIPanel.model_validate(payload)


def load_constraints(
    ra: RunArtifacts,
    source_type: str,
    file_path: Optional[str],
    llm_client: Any = None,
    rag_no_extractor: bool = False,
) -> Optional[ConstraintSet]:
    """Load constraints from sample/manual/datasheet sources."""
    constraints: Optional[ConstraintSet] = None
    with ra.stage("acquire_constraints"):
        if source_type == "sample":
            constraints = get_sample_constraints()
            ra.write_json("constraints.json", constraints.model_dump(mode='json'))

        elif source_type == "manual_json" and file_path:
            constraints = ConstraintSet.load_from_json(file_path)
            ra.write_json("constraints.json", constraints.model_dump(mode='json'))

        elif source_type == "datasheet_pdf" and file_path and llm_client:
            index = ingest_pdf(file_path)
            queries = [
                "Absolute Maximum Ratings",
                "Recommended Operating Conditions",
                "Electrical Characteristics",
                "Full Scale Range",
                "Selectable options",
                "Configuration register",
                "Communication Interface",
            ]
            evidence = retrieve_evidence(index, queries, top_k=12)
            ra.write_json("evidence.json", [c.model_dump(mode='json') for c in evidence])

            device_name = Path(file_path).name
            conflicts = []
            if rag_no_extractor:
                constraints = get_sample_constraints()
                constraints.metadata["source"] = "manual_fallback"
                constraints.metadata["manual"] = True
                constraints.metadata["fallback_reason"] = "rag_no_extractor_enabled"
                constraints.metadata["evidence_chunks"] = len(evidence)
            else:
                constraints, conflicts = extract_and_resolve_conflicts(
                    evidence,
                    llm_client,
                    device_name,
                    file_path,
                    output_dir=str(ra.run_dir),
                )
                # Keep backward-compatible extractor output too
                if not constraints.constraints:
                    constraints = extract_constraints(evidence, llm_client, device_name, file_path)
                    constraints.metadata["manual"] = True
                    constraints.metadata["fallback_reason"] = "extractor_empty_manual_required"
            ra.write_json("constraints.json", constraints.model_dump(mode='json'))
            if conflicts and not (ra.run_dir / "constraint_conflicts.json").exists():
                ra.write_json(
                    "constraint_conflicts.json",
                    {"total_conflicts": len(conflicts), "conflicts": conflicts, "resolution_strategy": "conservative"},
                )
    return constraints


def run_phase2_pipeline(
    ra: RunArtifacts,
    user_prompt: str,
    model: Any,
    config: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Main entry point for Phase 2."""
    # Sanitize config for JSON serialization (constraints_override is a Pydantic object)
    safe_config = {k: v for k, v in config.items() if k != "constraints_override"}
    if config.get("constraints_override"):
        safe_config["constraints_override"] = "[ConstraintSet: cached]"
    ra.write_json(
        "input.json",
        {
            "created_utc": ra.created_utc,
            "run_id": ra.run_id,
            "mode": "phase2_neuro_symbolic",
            "user_prompt": user_prompt,
            "config": safe_config,
        },
    )
    ra.write_text("model_raw.txt", "")
    ra.write_json("timing.json", {})
    ra.write_json("metrics.json", {})

    metrics: Dict[str, Any] = {
        "mode": "phase2_neuro_symbolic",
        "verifier_enabled": bool(config.get("enable_verifier", False)),
        "closed_loop_enabled": bool(config.get("enable_closed_loop", False)),
        "constraints_source": config.get("constraints_source", "none"),
        "ablation_mode": config.get("ablation_mode", "full_pipeline"),
        "verifier_no_rag": bool(config.get("verifier_no_rag", False)),
        "rag_no_extractor": bool(config.get("rag_no_extractor", False)),
        "violations_count": 0,
        "fixes_count": 0,
        "closed_loop_rounds": 0,
        "closed_loop_success": False,
        "verifier_passed": False,
        "prompt_conflicts_detected": 0,
        "error_type": "",
        "error_message": "",
        "quota_exceeded": False,
    }

    errors_obj: Optional[Dict[str, Any]] = None
    recoverable_error_obj: Optional[Dict[str, Any]] = None
    final_panel: Optional[HMIPanel] = None
    final_html = ""
    constraints: Optional[ConstraintSet] = None
    initial_dsl_text = ""
    constraints_summary = ""
    conflict_summary = ""

    try:
        # 1) Acquire Constraints
        if metrics["verifier_enabled"]:
            # Check for preloaded constraints in config to skip heavy PDF parsing
            if config.get("constraints_override"):
                constraints = config["constraints_override"]
                # 记录一下来源，方便调试
                ra.write_json("constraints.json", constraints.model_dump(mode='json'))
            else:
                constraints_source = config.get("constraints_source")
                if config.get("verifier_no_rag") and constraints_source == "datasheet_pdf":
                    constraints_source = "sample"
                constraints = load_constraints(
                    ra,
                    constraints_source,
                    config.get("constraints_file_path"),
                    model,
                    rag_no_extractor=bool(config.get("rag_no_extractor", False)),
                )

            # If datasheet extraction/cache yields empty constraints, backfill known profiles.
            if constraints and config.get("constraints_source") == "datasheet_pdf":
                constraints = _backfill_empty_constraints(constraints, user_prompt)
                constraints = _merge_profile_constraints(constraints)
                constraints.constraints = normalize_constraint_semantics(constraints.constraints)
                ra.write_json("constraints.json", constraints.model_dump(mode="json"))

            # Guard against stale/legacy cached constraints that still contain infeasible combinations.
            if constraints and constraints.constraints:
                constraints.constraints = normalize_constraint_semantics(constraints.constraints)
                conflicts = detect_conflicts(constraints.constraints)
                if conflicts:
                    constraints.constraints = resolve_conflicts(constraints.constraints, conflicts)
                    constraints.metadata["conflicts_resolved"] = len(conflicts)
                    constraints.metadata["conflict_targets"] = len({c.get("target") for c in conflicts})
                    ra.write_json(
                        "constraint_conflicts.json",
                        {
                            "total_conflicts": len(conflicts),
                            "conflict_targets": len({c.get("target") for c in conflicts}),
                            "conflicts": conflicts,
                            "resolution_strategy": "priority_then_conservative",
                            "source": "pipeline_sanity_pass",
                        },
                    )
                    ra.write_json("constraints.json", constraints.model_dump(mode="json"))

            if constraints:
                metrics["constraints_count"] = len(constraints.constraints)
                constraints_summary = summarize_constraints_for_prompt(constraints)
                prompt_conflicts = detect_prompt_constraint_conflicts(user_prompt, constraints)
                metrics["prompt_conflicts_detected"] = len(prompt_conflicts)
                if prompt_conflicts:
                    ra.write_json("prompt_conflicts.json", [c.to_dict() for c in prompt_conflicts])
                conflict_summary = summarize_conflicts_for_prompt(prompt_conflicts)

        # 2) Architect Agent (Initial DSL Generation)
        with ra.stage("architect_gen"):
            append_event(
                ra.run_dir,
                "phase2_architect_start",
                {
                    "style": config.get("style", "dark"),
                    "constraints_count": len(constraints.constraints) if constraints else 0,
                },
            )
            prompt = get_architect_dsl_prompt(
                user_prompt,
                style=config.get("style", "dark"),
                constraints_summary=constraints_summary,
                conflict_summary=conflict_summary,
            )
            resp = model.generate_content(prompt)
            initial_dsl_text = getattr(resp, "text", "") or ""
            ra.write_text("dsl_raw_r0.txt", initial_dsl_text)
            ra.safe_append_text_atomic("model_raw.txt", f"\n[DSL_RAW_R0]\n{initial_dsl_text}\n")
            append_event(
                ra.run_dir,
                "phase2_architect_done",
                {"dsl_chars": len(initial_dsl_text)},
            )

        # 3) Verification & Repair Loop
        if metrics["verifier_enabled"] and constraints:
            with ra.stage("verification_loop"):
                if metrics["closed_loop_enabled"]:
                    agent = EngineeringAgent(
                        ra=ra,
                        llm_client=model,
                        constraints=constraints,
                        config=config,
                    )
                    final_panel, loop_metrics = agent.run(
                        initial_prompt=user_prompt,
                        initial_text=initial_dsl_text,
                    )
                    metrics.update(loop_metrics)
                    metrics["closed_loop_success"] = bool(loop_metrics.get("final_pass", False))
                    metrics["closed_loop_rounds"] = int(loop_metrics.get("closed_loop_rounds", 0))

                    report_obj = _load_latest_report(ra.run_dir)
                    if report_obj:
                        metrics["violations_count"] = len(report_obj.get("violations", []))
                        metrics["fixes_count"] = len(report_obj.get("fixes", []))
                        metrics["verifier_passed"] = bool(report_obj.get("passed", False))
                        if metrics["fixes_count"] == 0:
                            first_report = _load_first_report(ra.run_dir)
                            if first_report:
                                first_v = len(first_report.get("violations", []))
                                last_v = len(report_obj.get("violations", []))
                                if first_v > last_v:
                                    metrics["fixes_count"] = first_v - last_v
                        ra.write_json("verifier_report.json", report_obj)
                        _log_verifier_events(ra.run_dir, report_obj)
                else:
                    clean = extract_json_from_text(initial_dsl_text)
                    data = json.loads(clean)
                    panel = HMIPanel.model_validate(data)
                    ra.write_json("dsl_validated.json", data)

                    report = verify_panel(panel, constraints)
                    
                    # [核心修复] Apply fixes (CLAMP) to correct violations
                    if not report.passed:
                        fixed_panel, report = apply_fixes(panel, report)
                        final_panel = fixed_panel
                        ra.write_json("dsl_fixed.json", fixed_panel.model_dump(mode='json'))
                    else:
                        final_panel = panel
                    
                    report_obj = report.model_dump(mode='json')
                    
                    # --- Dynamic Adversarial Simulation ---
                    from src.modules.verifier.adversarial import run_simulation
                    adv_results = run_simulation(final_panel, constraints.constraints)
                    metrics["adversarial_survived"] = adv_results["survived"]
                    metrics["adversarial_failed"] = adv_results["failed"]
                    if adv_results["failed"] > 0:
                        report_obj["adversarial_vulnerabilities"] = adv_results["logs"]
                    # -------------------------------------

                    ra.write_json("verifier_report.json", report_obj)
                    _log_verifier_events(ra.run_dir, report_obj)
                    metrics["violations_count"] = len(report.violations)
                    metrics["fixes_count"] = len(report.fixes)
                    metrics["verifier_passed"] = bool(report.passed)
                    metrics["closed_loop_success"] = bool(report.passed)
                    metrics["adversarial_robustness_score"] = (adv_results["survived"] / adv_results["total"] * 100) if adv_results["total"] > 0 else 100.0
        else:
            # Fallback Parse (No Constraints)
            clean = extract_json_from_text(initial_dsl_text)
            data = json.loads(clean)
            final_panel = HMIPanel.model_validate(data)
            ra.write_json("dsl_validated.json", data)
            metrics["closed_loop_success"] = True
            metrics["verifier_passed"] = True

        # Persist final validated panel when available
        if final_panel:
            normalized_rows = _normalize_panel_validation_report(final_panel)
            if normalized_rows > 0 and metrics.get("fixes_count", 0) == 0:
                metrics["proactive_fixes_count"] = normalized_rows
                metrics["fixes_count"] = normalized_rows
            if (
                metrics.get("closed_loop_enabled")
                and metrics.get("closed_loop_rounds", 0) == 0
                and metrics.get("fixes_count", 0) > 0
            ):
                # Make proactive/symbolic repair visible in telemetry even when
                # no additional LLM retry round was needed.
                metrics["closed_loop_rounds"] = 1
            ra.write_json("dsl_validated.json", final_panel.model_dump())

        _write_round_diffs(ra.run_dir)

        # 4) Deterministic Rendering
        if final_panel:
            with ra.stage("render"):
                append_event(ra.run_dir, "phase2_render_start", {})
                final_html = render_panel(final_panel)
                ra.write_text("final.html", final_html)
                append_event(
                    ra.run_dir,
                    "phase2_render_done",
                    {"output_chars": len(final_html)},
                )
        else:
            final_html = """
            <div style='color:#ef4444; padding:2rem; background:#1e293b; font-family:sans-serif; border:1px solid #334155; border-radius:8px;'>
                <h2 style='margin-top:0'>⚠️ Generation Interrupted</h2>
                <p>The system failed to generate a valid UI structure.</p>
                <p><strong>Possible reasons:</strong></p>
                <ul style='padding-left:1.5rem'>
                    <li>LLM output was not valid JSON</li>
                    <li>Safety verification failed critically</li>
                    <li>Pydantic validation error</li>
                </ul>
                <p style='margin-top:1rem; font-size:0.9em; color:#94a3b8'>Check the <code>run_artifacts</code> folder for logs.</p>
            </div>
            """
            ra.write_text("final.html", final_html)

    except Exception as e:
        recorded_error = ra.record_error(e, where="phase2_pipeline.run_phase2_pipeline")
        errors_obj = recorded_error
        err_type = type(e).__name__
        err_msg = str(e)
        err_msg_lower = err_msg.lower()
        is_quota = (
            err_type == "ResourceExhausted"
            or "quota exceeded" in err_msg_lower
            or ("429" in err_msg and "quota" in err_msg_lower)
        )
        is_network = (
            err_type in {"RetryError", "ServiceUnavailable", "DeadlineExceeded", "TimeoutError"}
            or "failed to connect" in err_msg_lower
            or "timed out" in err_msg_lower
            or "unavailable" in err_msg_lower
        )

        metrics["error_type"] = err_type
        metrics["error_message"] = err_msg
        metrics["quota_exceeded"] = is_quota
        metrics["degraded_fallback"] = False

        if is_network and not is_quota:
            try:
                final_panel = _build_offline_fallback_panel(
                    user_prompt=user_prompt,
                    style=config.get("style", "dark"),
                    constraints=constraints,
                )
                fallback_meta = final_panel.metadata or {}
                report_obj: Dict[str, Any]
                has_constraints = bool(constraints and constraints.constraints)

                if metrics["verifier_enabled"] and has_constraints:
                    # Offline symbolic loop: verify -> clamp -> verify
                    report = verify_panel(final_panel, constraints)
                    if not report.passed:
                        fixed_panel, fixed_report = apply_fixes(final_panel, report)
                        final_panel = fixed_panel
                        post_report = verify_panel(final_panel, constraints)
                        post_report.fixes = fixed_report.fixes
                        post_report.residual_risks = list(
                            dict.fromkeys((fixed_report.residual_risks or []) + (post_report.residual_risks or []))
                        )
                        post_report.stats["fixes_applied"] = len(post_report.fixes)
                        report = post_report

                    report_obj = report.model_dump(mode="json")
                    metrics["violations_count"] = len(report_obj.get("violations", []))
                    metrics["fixes_count"] = len(report_obj.get("fixes", []))
                    metrics["verifier_passed"] = bool(report_obj.get("passed", False))
                    metrics["closed_loop_success"] = bool(report_obj.get("passed", False))
                    metrics["closed_loop_rounds"] = 1 if metrics["fixes_count"] > 0 else 0
                    _log_verifier_events(ra.run_dir, report_obj)
                    ra.write_json("dsl_validated.json", final_panel.model_dump(mode="json"))
                    append_event(
                        ra.run_dir,
                        "phase2_symbolic_repair_done",
                        {
                            "violations_count": metrics["violations_count"],
                            "fixes_count": metrics["fixes_count"],
                            "passed": metrics["verifier_passed"],
                        },
                    )
                else:
                    report_obj = {
                        "passed": False,
                        "score": 0.0,
                        "violations": [],
                        "fixes": [],
                        "residual_risks": [
                            "degraded_mode_network_error",
                            "missing_constraints_for_verifier",
                        ],
                        "stats": {
                            "total_checks": 0,
                            "violations_found": 0,
                            "fixes_applied": 0,
                            "critical_errors": 0,
                            "warnings": 0,
                        },
                        "metadata": {
                            "degraded_mode": True,
                            "fallback_strategy": fallback_meta.get("fallback_strategy", "unknown"),
                            "constraints_count": len(constraints.constraints) if constraints else 0,
                        },
                    }
                    metrics["verifier_passed"] = False
                    metrics["closed_loop_success"] = False

                ra.write_json("verifier_report.json", report_obj)
                final_html = render_panel(final_panel)
                ra.write_json("dsl_fallback.json", final_panel.model_dump(mode="json"))
                ra.write_text("final.html", final_html)
                metrics["degraded_fallback"] = True
                metrics["degraded_reason"] = err_type
                metrics["degraded_fallback_strategy"] = fallback_meta.get("fallback_strategy", "unknown")
                append_event(
                    ra.run_dir,
                    "phase2_degraded_fallback",
                    {
                        "reason": err_type,
                        "strategy": metrics["degraded_fallback_strategy"],
                        "constraints_count": len(constraints.constraints) if constraints else 0,
                    },
                )
                recoverable_error_obj = recorded_error
                errors_obj = None
            except Exception:
                final_html = """
                <div style='color:#ef4444; padding:2rem; background:#1e293b; font-family:sans-serif; border:1px solid #334155; border-radius:8px;'>
                    <h2 style='margin-top:0'>⚠️ Generation Interrupted</h2>
                    <p>Network timeout and fallback generation also failed.</p>
                </div>
                """
                ra.write_text("final.html", final_html)
        elif is_quota:
            final_html = """
            <div style='color:#ef4444; padding:2rem; background:#1e293b; font-family:sans-serif; border:1px solid #334155; border-radius:8px;'>
                <h2 style='margin-top:0'>⚠️ Generation Interrupted</h2>
                <p>Gemini API quota has been exhausted for the current model.</p>
                <p>Try a lower-tier model (for example <code>gemini-2.5-flash</code>) or retry later.</p>
            </div>
            """
        else:
            final_html = """
            <div style='color:#ef4444; padding:2rem; background:#1e293b; font-family:sans-serif; border:1px solid #334155; border-radius:8px;'>
                <h2 style='margin-top:0'>⚠️ Generation Interrupted</h2>
                <p>The system encountered an internal error.</p>
            </div>
            """
        ra.write_text("final.html", final_html)
        append_event(
            ra.run_dir,
            "error",
            {
                "type": err_type,
                "message": err_msg,
                "quota_exceeded": bool(is_quota),
                "recovered_by_fallback": bool(metrics.get("degraded_fallback", False)),
            },
        )
    finally:
        ra.finish_total()
        ra.write_json("timing.json", ra.timing_ms)

        if metrics["verifier_enabled"]:
            success = bool(final_panel) and bool(metrics.get("closed_loop_success", False))
            if not metrics["closed_loop_enabled"]:
                success = bool(final_panel) and bool(metrics.get("verifier_passed", False))
        else:
            success = bool(final_panel)

        metrics["success"] = success and errors_obj is None
        metrics["output_size_chars"] = len(final_html)
        metrics["total_ms"] = int(ra.timing_ms.get("total", 0))
        ra.write_json("metrics.json", metrics)
        append_event(
            ra.run_dir,
            "telemetry",
            {
                "success": metrics["success"],
                "mode": metrics.get("mode"),
                "violations_count": metrics.get("violations_count", 0),
                "fixes_count": metrics.get("fixes_count", 0),
                "closed_loop_rounds": metrics.get("closed_loop_rounds", 0),
                "total_ms": metrics.get("total_ms", 0),
            },
        )
        export_session_log(ra.run_dir)
        if recoverable_error_obj:
            ra.write_json("recoverable_error.json", recoverable_error_obj)
        if errors_obj:
            ra.write_json("errors.json", errors_obj)

    return final_html, metrics
