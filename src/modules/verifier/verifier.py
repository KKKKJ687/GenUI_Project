"""
Phase 2 Core Engine: Constraint Verifier.

Implements the Neuro-Symbolic "Closed-Loop Verification" logic:
  1. verify_panel(): Check DSL against physical constraints, generate report
  2. apply_fixes(): Self-correct violations (CLAMP, REJECT, etc.)
  3. verify_and_fix(): Orchestrator combining both steps

This is the core of the "Hardware-Aware" generation system.

Smart Matching (v2):
  - Strategy A: Exact ID match (legacy selectors)
  - Strategy B: Type-based match (widgets[type='slider'])
  - Strategy C: Semantic/label match (voltage keywords + unit heuristics)
"""
import copy
import ast
import re
import math
from typing import Tuple, List, Optional, Any

from src.models.schema import HMIPanel, SelectWidget
from src.modules.verifier.constraints import (
    ConstraintSet,
    Constraint,
    ConstraintKind,
    Severity,
    SourceRef,
)
from src.modules.verifier.verification_report import (
    VerificationReport,
    Violation,
    FixAction,
    FixActionType,
)
from src.models.param_path import resolve_matching_paths, set_value_by_path


# ==========================================
# Pint Unit Library (Optional)
# ==========================================
try:
    import pint
    from pint.errors import DimensionalityError, UndefinedUnitError
    _ureg = pint.UnitRegistry()
    _ureg.define("rpm = [angular_velocity]")
    HAS_PINT = True
except ImportError:
    HAS_PINT = False
    _ureg = None


# ==========================================
# Semantic Keyword Map (unit -> related labels)
# ==========================================
_UNIT_KEYWORD_MAP = {
    "V":  ["volt", "voltage", "input", "power", "supply", "vcc", "vdd", "vin", "vm", "vbat", "vbatt", "vbus"],
    "mV": ["volt", "voltage", "millivolt"],
    "A":  ["current", "ampere", "amp", "iout", "iin", "ilim", "ipeak", "i_peak"],
    "mA": ["current", "milliamp"],
    "Hz": ["frequency", "freq", "clock", "speed", "baud"],
    "kHz": ["frequency", "freq", "clock", "pwm"],
    "MHz": ["frequency", "freq", "clock"],
    "C":  ["temperature", "temp", "thermal"],
    "s":  ["time", "delay", "interval", "period", "pulse", "width", "timing"],
    "ms": ["time", "delay", "interval", "period", "pulse"],
    "us": ["time", "delay", "interval", "period", "pulse", "width", "timing"],
    "ns": ["time", "delay", "interval", "period", "pulse", "width", "timing"],
}

# Widget types eligible for semantic matching per unit family
_UNIT_ELIGIBLE_TYPES = {
    "V": ["slider", "input", "gauge"],
    "mV": ["slider", "input", "gauge"],
    "A": ["slider", "input", "gauge"],
    "mA": ["slider", "input", "gauge"],
    "Hz": ["slider", "input", "gauge"],
    "kHz": ["slider", "input", "gauge"],
    "MHz": ["slider", "input", "gauge"],
    "C": ["slider", "input", "gauge"],
    "s": ["slider", "input"],
    "ms": ["slider", "input"],
    "us": ["slider", "input"],
    "ns": ["slider", "input"],
}

_SEMANTIC_HINTS = {
    "temperature": ["temp", "temperature", "thermal", "overheat", "over-temperature", "celsius", "℃", "°c", "温度", "过温", "热"],
    "voltage": ["volt", "voltage", "vcc", "vdd", "vin", "vm", "vbat", "vbatt", "vbus", "battery voltage", "supply", "电压", "供电"],
    "current": ["current", "amp", "ampere", "ma", "motor current", "ilim", "ipeak", "i_peak", "电流", "限流"],
    "frequency": ["frequency", "freq", "hz", "khz", "mhz", "baud", "pwm", "频率"],
    "acceleration": ["accel", "accelerometer", "g range", "full scale", "fsr", "加速度", "量程"],
    "timing": ["time", "timing", "delay", "period", "pulse", "pulse width", "ns", "us", "ms", "秒", "脉宽", "时序", "延时"],
}

_MQTT_TOPIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_\-.]{0,255}$")
# Note: switch can be either control or status indicator in generated HMIs,
# so we avoid hard-enforcing write access on switch to reduce false positives.
# Switch/Radio can represent read-only status indicators in HMIs.
# Keep strict write guard for clearly command-oriented numeric/discrete controls.
_WRITE_WIDGET_TYPES = {"slider", "input", "select"}
_READ_WIDGET_TYPES = {"gauge", "plot"}
_STATUS_HINTS = {
    "status",
    "state",
    "fault",
    "alarm",
    "warning",
    "telemetry",
    "monitor",
    "health",
    "状态",
    "故障",
    "告警",
    "监测",
}
_CONTROL_HINTS = {
    "set",
    "setting",
    "control",
    "command",
    "enable",
    "disable",
    "start",
    "stop",
    "write",
    "控制",
    "设定",
    "调节",
    "启停",
    "开关",
}
_SUPPLY_DOMAIN_HINTS = {"supply", "power", "vm", "vbat", "vbatt", "vbus", "battery", "电源", "供电", "母线"}
_LOGIC_DOMAIN_HINTS = {"logic", "input", "in1", "in2", "digital", "逻辑", "输入"}


def _normalize_access_mode(mode: Any) -> str:
    val = str(mode or "rw").strip().lower()
    if val == "read":
        return "r"
    if val == "write":
        return "w"
    if val in {"r", "w", "rw"}:
        return val
    return "rw"


def _widget_text_blob(widget: Any, binding: Any = None) -> str:
    parts = [
        str(getattr(widget, "id", "") or ""),
        str(getattr(widget, "label", "") or ""),
        str(getattr(widget, "description", "") or ""),
    ]
    if binding is not None:
        parts.extend(
            [
                str(getattr(binding, "address", "") or ""),
                str(getattr(binding, "topic", "") or ""),
                str(getattr(binding, "modbus_register", "") or ""),
            ]
        )
    return " ".join(parts).lower()


def _is_status_indicator_widget(widget: Any, binding: Any = None) -> bool:
    blob = _widget_text_blob(widget, binding)
    if not blob:
        return False
    has_status = any(k in blob for k in _STATUS_HINTS)
    has_control = any(k in blob for k in _CONTROL_HINTS)
    return has_status and not has_control


def _voltage_rule_domain(rule: Constraint) -> Optional[str]:
    blob = f"{rule.id or ''} {rule.name or ''} {rule.description or ''} {rule.applies_to or ''}".lower()
    if any(k in blob for k in _LOGIC_DOMAIN_HINTS):
        return "logic"
    if any(k in blob for k in _SUPPLY_DOMAIN_HINTS):
        return "supply"
    return None


def _widget_voltage_domain(widget: Any) -> Optional[str]:
    blob = _widget_text_blob(widget)
    if any(k in blob for k in _LOGIC_DOMAIN_HINTS):
        return "logic"
    if any(k in blob for k in _SUPPLY_DOMAIN_HINTS):
        return "supply"
    return None


def _split_identifier_tokens(text: str) -> List[str]:
    if not text:
        return []
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text))
    s = re.sub(r"[^a-zA-Z0-9_]+", " ", s)
    skip = {
        "max", "min", "value", "values", "widgets", "widget", "type", "id",
        "range", "rule", "constraint", "apply", "applies", "to",
    }
    out: List[str] = []
    seen = set()
    for token in re.split(r"[\s_]+", s.lower()):
        if not token:
            continue
        if len(token) < 2:
            continue
        if token in skip:
            continue
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _expand_semantic_alias(tokens: List[str]) -> List[str]:
    expanded: List[str] = []
    seen = set()

    alias_map = {
        "vbatt": ["vbat", "battery", "voltage", "supply"],
        "vbat": ["vbatt", "battery", "voltage", "supply"],
        "vm": ["motor", "supply", "voltage"],
        "vbus": ["bus", "voltage", "supply"],
        "ipeak": ["i_peak", "peak", "current", "amp"],
        "i_peak": ["ipeak", "peak", "current", "amp"],
        "ilim": ["limit", "current", "amp"],
    }

    for token in tokens:
        if token not in seen:
            seen.add(token)
            expanded.append(token)
        for alias in alias_map.get(token, []):
            if alias not in seen:
                seen.add(alias)
                expanded.append(alias)
    return expanded


def _infer_rule_keywords(rule: Constraint) -> List[str]:
    text = f"{rule.name or ''} {rule.description or ''}".lower()
    keywords: List[str] = []
    for words in _SEMANTIC_HINTS.values():
        if any(w in text for w in words):
            keywords.extend(words)
    keywords.extend(_split_identifier_tokens(rule.id or ""))
    keywords.extend(_split_identifier_tokens(rule.applies_to or ""))
    keywords.extend(_split_identifier_tokens(rule.name or ""))
    keywords = _expand_semantic_alias(keywords)
    return list(dict.fromkeys(keywords))


def _normalize_unit_token(unit: str) -> str:
    raw = (unit or "").strip().lower()
    aliases = {
        "°c": "c",
        "℃": "c",
        "celsius": "c",
        "vdc": "v",
        "volts": "v",
        "volt": "v",
        "amps": "a",
        "amp": "a",
        "μs": "us",
        "µs": "us",
        "usec": "us",
        "nsec": "ns",
        "sec": "s",
        "seconds": "s",
    }
    return aliases.get(raw, raw)


def _widget_semantic_match(widget: Any, rule: Constraint) -> bool:
    """
    Semantic filter to avoid applying broad type-based limits
    (e.g. slider.max) to unrelated parameters.
    """
    label_lower = (getattr(widget, "label", "") or "").lower()
    unit_lower = _normalize_unit_token(getattr(widget, "unit", "") or "")
    rule_unit = _normalize_unit_token(rule.unit or "")

    # Domain disambiguation for voltage-like rules: avoid applying logic-input
    # limits to supply-voltage widgets and vice versa.
    if "voltage" in (rule.applies_to or "").lower() or rule_unit in {"v", "mv"}:
        rd = _voltage_rule_domain(rule)
        wd = _widget_voltage_domain(widget)
        if rd and wd and rd != wd:
            return False

    # Exact unit agreement is strong evidence.
    if rule_unit and unit_lower and unit_lower == rule_unit:
        return True

    unit_keywords = _UNIT_KEYWORD_MAP.get(rule.unit or "", [])
    rule_keywords = _infer_rule_keywords(rule)
    keywords = list(dict.fromkeys(unit_keywords + rule_keywords))
    if not keywords:
        return True

    return any(k in label_lower for k in keywords)


def _to_float_or_none(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_invariant_violation(
    *,
    rule_id: str,
    param_path: str,
    observed_value: Any,
    expected_limit: Any,
    message: str,
    unit: str = "unitless",
) -> Violation:
    return Violation(
        rule_id=rule_id,
        param_path=param_path,
        observed_value=observed_value,
        expected_limit=str(expected_limit),
        unit=unit or "unitless",
        severity=Severity.HARD,
        message=message,
        source_ref=None,
    )


def _check_panel_invariants(panel: HMIPanel) -> List[Violation]:
    """
    Internal consistency checks that do not rely on external constraints.
    These catch structural issues like value > max even if RAG extraction is noisy.
    """
    violations: List[Violation] = []

    for i, widget in enumerate(panel.widgets):
        wtype = widget.type.value if hasattr(widget.type, "value") else str(widget.type)
        path_base = f"widgets[{i}]"
        unit = getattr(widget, "unit", None) or "unitless"

        w_min = _to_float_or_none(getattr(widget, "min", None))
        w_max = _to_float_or_none(getattr(widget, "max", None))
        w_val = _to_float_or_none(getattr(widget, "value", None))

        if w_min is not None and w_max is not None and w_min > w_max:
            violations.append(
                _build_invariant_violation(
                    rule_id="INVARIANT_RANGE_ORDER",
                    param_path=f"{path_base}.min",
                    observed_value=w_min,
                    expected_limit=f"<= {w_max}",
                    message=f"{wtype} has invalid range: min ({w_min}) > max ({w_max})",
                    unit=unit,
                )
            )

        if w_val is not None and w_min is not None and w_val < w_min:
            violations.append(
                _build_invariant_violation(
                    rule_id="INVARIANT_VALUE_BELOW_MIN",
                    param_path=f"{path_base}.value",
                    observed_value=w_val,
                    expected_limit=f">= {w_min}",
                    message=f"{wtype} value {w_val} is below min {w_min}",
                    unit=unit,
                )
            )

        if w_val is not None and w_max is not None and w_val > w_max:
            violations.append(
                _build_invariant_violation(
                    rule_id="INVARIANT_VALUE_ABOVE_MAX",
                    param_path=f"{path_base}.value",
                    observed_value=w_val,
                    expected_limit=f"<= {w_max}",
                    message=f"{wtype} value {w_val} exceeds max {w_max}",
                    unit=unit,
                )
            )

        if wtype in {"select", "radio"}:
            options = getattr(widget, "options", None)
            raw_value = getattr(widget, "value", None)
            if not isinstance(options, list) or len(options) == 0:
                violations.append(
                    _build_invariant_violation(
                        rule_id="INVARIANT_EMPTY_OPTIONS",
                        param_path=f"{path_base}.options",
                        observed_value=options,
                        expected_limit="non-empty list",
                        message=f"{wtype} widget requires non-empty options",
                        unit="unitless",
                    )
                )
            else:
                normalized_options = [str(x).strip() for x in options]
                if str(raw_value).strip() not in normalized_options:
                    violations.append(
                        _build_invariant_violation(
                            rule_id="INVARIANT_ENUM_VALUE",
                            param_path=f"{path_base}.value",
                            observed_value=raw_value,
                            expected_limit=normalized_options,
                            message=f"{wtype} value '{raw_value}' not in options {normalized_options}",
                            unit="unitless",
                        )
                    )

        binding = getattr(widget, "binding", None)
        if binding is not None:
            protocol_obj = getattr(binding, "protocol", "")
            protocol = protocol_obj.value if hasattr(protocol_obj, "value") else str(protocol_obj)
            protocol = protocol.strip().lower()
            access_mode = _normalize_access_mode(getattr(binding, "access_mode", "rw"))

            if wtype in _WRITE_WIDGET_TYPES and access_mode not in {"w", "rw"}:
                if wtype == "select" and _is_status_indicator_widget(widget, binding):
                    pass
                else:
                    violations.append(
                        _build_invariant_violation(
                            rule_id="INVARIANT_PROTOCOL_ACCESS_WRITE",
                            param_path=f"{path_base}.binding.access_mode",
                            observed_value=access_mode,
                            expected_limit="w/rw",
                            message=f"{wtype} is writable UI but binding access_mode='{access_mode}' blocks write",
                        )
                    )

            if wtype in _READ_WIDGET_TYPES and access_mode not in {"r", "rw"}:
                violations.append(
                    _build_invariant_violation(
                        rule_id="INVARIANT_PROTOCOL_ACCESS_READ",
                        param_path=f"{path_base}.binding.access_mode",
                        observed_value=access_mode,
                        expected_limit="r/rw",
                        message=f"{wtype} is telemetry-oriented UI but binding access_mode='{access_mode}' blocks read",
                    )
                )

            if protocol == "mqtt":
                topic = str(
                    getattr(binding, "topic", None)
                    or getattr(binding, "address", "")
                    or ""
                ).strip()
                if not topic:
                    violations.append(
                        _build_invariant_violation(
                            rule_id="INVARIANT_MQTT_TOPIC_EMPTY",
                            param_path=f"{path_base}.binding.topic",
                            observed_value=topic,
                            expected_limit="non-empty topic path",
                            message="MQTT binding requires a non-empty topic",
                        )
                    )
                else:
                    has_wildcard = ("#" in topic) or ("+" in topic)
                    if has_wildcard and access_mode in {"w", "rw"}:
                        violations.append(
                            _build_invariant_violation(
                                rule_id="INVARIANT_MQTT_TOPIC_WILDCARD_WRITE",
                                param_path=f"{path_base}.binding.topic",
                                observed_value=topic,
                                expected_limit="publish topic without wildcard (+/#)",
                                message="MQTT write topic cannot contain wildcard '+' or '#'",
                            )
                        )
                    if "//" in topic or not _MQTT_TOPIC_RE.match(topic):
                        violations.append(
                            _build_invariant_violation(
                                rule_id="INVARIANT_MQTT_TOPIC_FORMAT",
                                param_path=f"{path_base}.binding.topic",
                                observed_value=topic,
                                expected_limit="^[A-Za-z0-9][A-Za-z0-9/_\\-.]{0,255}$ and no double slash",
                                message="MQTT topic format is invalid",
                            )
                        )

            if protocol == "modbus":
                reg = getattr(binding, "modbus_register", None)
                if reg is None:
                    raw_addr = getattr(binding, "address", None)
                    try:
                        reg = int(raw_addr) if raw_addr is not None else None
                    except (TypeError, ValueError):
                        reg = None

                if reg is None or reg < 0 or reg > 65535:
                    violations.append(
                        _build_invariant_violation(
                            rule_id="INVARIANT_MODBUS_REGISTER_SPACE",
                            param_path=f"{path_base}.binding.modbus_register",
                            observed_value=reg,
                            expected_limit="0..65535",
                            message="Modbus register out of valid address space",
                        )
                    )

    return violations


# ==========================================
# Core Public API
# ==========================================

def verify_panel(panel: HMIPanel, constraints: ConstraintSet) -> VerificationReport:
    """
    Pure verification: Checks all rules against the panel, generates report.
    Uses smart matching (type + semantic + ID) to find target widgets.

    This function does NOT modify the panel - it only reports violations.
    """
    report = VerificationReport(passed=True, score=100.0)

    total_checks = 0
    violations: List[Violation] = []

    for rule in constraints.constraints:
        # 1. Smart match: find all (widget, attr) pairs
        targets = _find_targets_smart(panel, rule)

        for widget, attr, path_str in targets:
            total_checks += 1
            val = getattr(widget, attr, None)
            if val is None:
                continue

            violation = _check_single_value(val, rule, path_str)

            if violation:
                violations.append(violation)

                if rule.severity == Severity.HARD:
                    report.passed = False
                    report.score -= 10.0
                else:
                    report.score -= 2.0

    # Enforce schema-level safety invariants even when external constraints are incomplete.
    invariant_violations = _check_panel_invariants(panel)
    for v in invariant_violations:
        violations.append(v)
        report.passed = False
        report.score -= 10.0

    report.violations = violations
    report.stats['total_checks'] = total_checks
    report.stats['violations_found'] = len(violations)
    report.stats['critical_errors'] = len([v for v in violations if v.severity == Severity.HARD])
    report.stats['warnings'] = len([v for v in violations if v.severity == Severity.SOFT])

    report.score = max(0.0, min(100.0, report.score))
    return report


def apply_fixes(panel: HMIPanel, report: VerificationReport) -> Tuple[HMIPanel, VerificationReport]:
    """
    Self-Correction: Applies fixes to a DEEP COPY of the panel.

    Fix Strategies:
      - CLAMP: Numeric values clamped to limits
      - REJECT: Widget removed (future)
      - EDIT: String values replaced (future)
      - NONE: Cannot auto-fix, requires manual review
    """
    fixed_panel = copy.deepcopy(panel)

    fixes: List[FixAction] = []
    residual_risks: List[str] = []

    for v in report.violations:
        fix_action = _attempt_fix(fixed_panel, v)
        if fix_action:
            fixes.append(fix_action)
            if fix_action.action_type == FixActionType.NONE:
                residual_risks.append(f"Cannot auto-fix: {v.param_path} ({v.rule_id})")

    report.fixes = fixes
    report.residual_risks.extend(residual_risks)
    report.stats['fixes_applied'] = len([f for f in fixes if f.action_type != FixActionType.NONE])

    hard_violation_ids = {v.rule_id for v in report.violations if v.severity == Severity.HARD}
    fixed_hard_ids = {f.violation_rule_id for f in fixes
                      if f.action_type in (FixActionType.CLAMP, FixActionType.EDIT)}
    unfixed_hard = hard_violation_ids - fixed_hard_ids

    if not unfixed_hard:
        report.passed = True

    return fixed_panel, report


def verify_and_fix(panel: HMIPanel, constraints: ConstraintSet) -> Tuple[HMIPanel, VerificationReport]:
    """
    Orchestrator: Verify panel and apply fixes if needed.
    """
    report = verify_panel(panel, constraints)

    if not report.passed:
        fixed_panel, fixed_report = apply_fixes(panel, report)
        return fixed_panel, fixed_report

    return panel, report


# ==========================================
# Smart Matching Engine (v2)
# ==========================================

def _find_targets_smart(panel: HMIPanel, constr: Constraint) -> List[tuple]:
    """
    Smart matching: ID match + Type match + Semantic/label match.
    
    Handles three selector formats:
      1. Explicit: "widgets[type='slider'].max" → resolved by param_path
      2. Abstract: "*.voltage" → maps to real widget attrs via semantic matching
      3. Legacy: "widgets[id='xxx'].value" → resolved by param_path
    
    Returns: [(widget_obj, attribute_name, path_string), ...]
    """
    targets = []
    selector = constr.applies_to  # e.g. "*.voltage" or "widgets[type='slider'].max"
    matched_widget_ids = set()

    # Determine if this is an abstract selector (*.xxx) or a concrete one
    is_abstract = selector.startswith("*.")
    
    # --- Resolve the target attribute(s) to check based on constraint kind ---
    def _get_target_attrs(constr_kind, selector_str):
        """Map constraint kind to actual widget attributes to check."""
        if not is_abstract:
            # Concrete selector: extract attribute from the selector itself
            if "." in selector_str:
                attr = selector_str.rsplit(".", 1)[-1]
                return [attr]
            return ["value"]
        
        # Abstract selector: map by constraint kind
        if constr_kind == ConstraintKind.MAX:
            return ["max"]
        elif constr_kind == ConstraintKind.MIN:
            return ["min"]
        elif constr_kind == ConstraintKind.RANGE:
            return ["max", "min"]  # Check both ends
        else:
            return ["value"]
    
    target_attrs = _get_target_attrs(constr.kind, selector)

    # --- Phase 1: Try existing param_path resolver for concrete selectors ---
    if not is_abstract:
        try:
            resolved = resolve_matching_paths(panel, selector)
            resolved_targets: List[tuple] = []
            for path_str, value in resolved:
                idx_match = re.search(r'widgets\[(\d+)\]', path_str)
                if idx_match:
                    idx = int(idx_match.group(1))
                    if 0 <= idx < len(panel.widgets):
                        widget = panel.widgets[idx]
                        attr = path_str.rsplit('.', 1)[-1] if '.' in path_str else 'value'
                        resolved_targets.append((widget, attr, path_str))

            # For explicit type selectors, keep concrete path matches by default.
            # Semantic filtering is only used for `.value` selectors where cross-domain
            # ambiguity is common. For `.max/.min` constraints, dropping targets causes
            # under-counting and hides unsafe ranges.
            attr_name = selector.rsplit(".", 1)[-1] if "." in selector else "value"
            if (
                (selector.startswith("widgets[type='") or selector.startswith('widgets[type="'))
                and (
                    attr_name == "value"
                    or any(bool(getattr(t[0], "unit", None)) for t in resolved_targets)
                )
            ):
                semantic_targets = [t for t in resolved_targets if _widget_semantic_match(t[0], constr)]
                targets = semantic_targets if semantic_targets else resolved_targets
            else:
                targets = resolved_targets

            for widget, _, _ in targets:
                matched_widget_ids.add(widget.id)
        except Exception:
            pass

    # --- Phase 2: Semantic/label matching (always runs for abstract, fallback for concrete) ---
    unit = constr.unit or ""
    keywords = _UNIT_KEYWORD_MAP.get(unit, [])
    inferred_keywords = _infer_rule_keywords(constr)
    eligible_types = _UNIT_ELIGIBLE_TYPES.get(unit, [])

    # For abstract selectors OR if no matches found in Phase 1
    if is_abstract or not targets:
        for i, widget in enumerate(panel.widgets):
            if widget.id in matched_widget_ids:
                continue

            wtype = widget.type.value if hasattr(widget.type, 'value') else str(widget.type)

            # Check if widget is eligible by type
            if eligible_types and wtype not in eligible_types:
                continue

            # Semantic matching: check label and constraint description for keyword overlap
            label_lower = (widget.label or "").lower()
            widget_unit_lower = (widget.unit or "").lower() if hasattr(widget, 'unit') else ""
            label_match = any(k in label_lower for k in keywords) if keywords else False
            semantic_text_match = any(k in label_lower for k in inferred_keywords) if inferred_keywords else False
            unit_match = widget_unit_lower == unit.lower() if widget_unit_lower else False

            # Match requires WIDGET-SIDE evidence: label contains relevant keyword
            # OR widget's unit field matches the constraint's unit.
            # This prevents false positives like a voltage slider matching a temperature constraint.
            if (label_match or semantic_text_match or unit_match) and _widget_semantic_match(widget, constr):
                for attr in target_attrs:
                    if hasattr(widget, attr):
                        path_str = f"widgets[{i}].{attr}"
                        targets.append((widget, attr, path_str))
                matched_widget_ids.add(widget.id)

    return targets


# ==========================================
# Physics-Aware Value Checking
# ==========================================

def _strict_physical_check(val: float, val_unit: str, limit: float, limit_unit: str) -> bool:
    """
    Performs strict physical dimensionality checking.
    Returns True if Safe (val <= limit), False if Violation.
    """
    if not HAS_PINT:
        return val <= limit

    if not val_unit or val_unit == "unitless":
        return val <= limit

    try:
        q_val = _ureg.Quantity(val, val_unit)
        q_lim = _ureg.Quantity(limit, limit_unit)

        if q_val.dimensionality != q_lim.dimensionality:
            return False

        return q_val.to_base_units() <= q_lim.to_base_units()

    except (DimensionalityError, UndefinedUnitError, ValueError):
        return False
    except Exception:
        return val <= limit


def check_threshold(dsl_val: float, dsl_unit: str, limit_val: float, limit_unit: str) -> bool:
    """
    Public API for physics-aware threshold checking.
    Returns True if Safe (val <= limit), False if Violation.
    """
    return _strict_physical_check(dsl_val, dsl_unit, limit_val, limit_unit)


def _check_single_value(value: Any, rule: Constraint, path: str) -> Optional[Violation]:
    """
    Check a single value against a constraint rule.
    Returns a Violation if the rule is broken, None if compliant.
    """
    msg: Optional[str] = None
    expected: Any = None

    if value is None:
        return None

    def is_greater(val, limit, unit):
        if HAS_PINT and unit and unit != "unitless":
            try:
                q_val = _ureg.Quantity(val, unit)
                q_lim = _ureg.Quantity(limit, unit)
                if q_val.dimensionality != q_lim.dimensionality:
                    return True
                return q_val > q_lim
            except Exception:
                pass
        return val > limit

    def is_less(val, limit, unit):
        if HAS_PINT and unit and unit != "unitless":
            try:
                q_val = _ureg.Quantity(val, unit)
                q_lim = _ureg.Quantity(limit, unit)
                if q_val.dimensionality != q_lim.dimensionality:
                    return True
                return q_val < q_lim
            except Exception:
                pass
        return val < limit

    def validate_numeric(v, r_name):
        if not isinstance(v, (int, float)):
            return f"Value '{v}' (type {type(v).__name__}) is not numeric, cannot check {r_name}"
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return f"Value {v} is not a finite number"
        return None

    if rule.kind == ConstraintKind.MAX:
        type_err = validate_numeric(value, "MAX")
        if type_err:
            msg = type_err
            expected = rule.max_val
        elif is_greater(value, rule.max_val, rule.unit):
            msg = f"Value {value} exceeds maximum {rule.max_val}"
            expected = rule.max_val

    elif rule.kind == ConstraintKind.MIN:
        type_err = validate_numeric(value, "MIN")
        if type_err:
            msg = type_err
            expected = rule.min_val
        elif is_less(value, rule.min_val, rule.unit):
            msg = f"Value {value} is below minimum {rule.min_val}"
            expected = rule.min_val

    elif rule.kind == ConstraintKind.RANGE:
        type_err = validate_numeric(value, "RANGE")
        if type_err:
            msg = type_err
            expected = f"{rule.min_val}-{rule.max_val}"
        elif is_greater(value, rule.max_val, rule.unit):
            msg = f"Value {value} exceeds range maximum {rule.max_val}"
            expected = rule.max_val
        elif is_less(value, rule.min_val, rule.unit):
            msg = f"Value {value} is below range minimum {rule.min_val}"
            expected = rule.min_val

    elif rule.kind == ConstraintKind.ENUM:
        # Strong ENUM check:
        # 1) Try numeric normalization first with tolerance for float noise.
        # 2) Fallback to strict string membership for discrete text enums.
        allowed_values = list(rule.allowed_values or [])
        legacy_enum_values = getattr(rule, "enum_values", None)
        if legacy_enum_values:
            allowed_values = list(legacy_enum_values)

        if allowed_values:
            raw_val = value.value if hasattr(value, "value") else value
            expected = allowed_values

            try:
                val_float = float(raw_val)
                allowed_float = [float(x) for x in allowed_values]
                is_valid = any(abs(val_float - a) < 0.01 for a in allowed_float)
                if not is_valid:
                    msg = f"Value {raw_val} not in allowed values: {allowed_values}"
            except (TypeError, ValueError):
                val_text = str(raw_val).strip()
                allowed_text = [str(x).strip() for x in allowed_values]
                if val_text not in allowed_text:
                    msg = f"Value '{val_text}' not in allowed values: {allowed_values}"

    elif rule.kind == ConstraintKind.REGEX:
        if rule.pattern and isinstance(value, str):
            if not re.match(rule.pattern, value):
                msg = f"Value '{value}' does not match pattern: {rule.pattern}"
                expected = rule.pattern

    if msg:
        source = None
        if rule.source:
            source = SourceRef(
                datasheet_name=rule.source.datasheet_name,
                page=getattr(rule.source, 'page', None),
                section=getattr(rule.source, 'section', None),
                snippet=getattr(rule.source, 'snippet', None),
                confidence=getattr(rule.source, 'confidence', 1.0),
            )
        return Violation(
            rule_id=rule.id,
            param_path=path,
            observed_value=value,
            expected_limit=str(expected),
            unit=rule.unit,
            severity=rule.severity,
            message=msg,
            source_ref=source
        )

    return None


# ==========================================
# Auto-Fix Strategies
# ==========================================

def _parse_widget_index_from_path(path: str) -> Optional[int]:
    match = re.search(r"widgets\[(\d+)\]", path or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _sync_widget_value_after_bound_change(panel: HMIPanel, path: str) -> Optional[Tuple[Any, Any]]:
    """
    Keep widget `value` consistent after min/max edits.
    """
    if not (path.endswith(".min") or path.endswith(".max")):
        return None

    idx = _parse_widget_index_from_path(path)
    if idx is None or idx < 0 or idx >= len(panel.widgets):
        return None

    widget = panel.widgets[idx]
    if not hasattr(widget, "value"):
        return None

    min_val = _to_float_or_none(getattr(widget, "min", None))
    max_val = _to_float_or_none(getattr(widget, "max", None))
    cur_val = _to_float_or_none(getattr(widget, "value", None))

    if cur_val is None:
        return None

    if min_val is not None and max_val is not None and min_val > max_val:
        low, high = min(min_val, max_val), max(min_val, max_val)
        set_value_by_path(panel, f"widgets[{idx}].min", low)
        set_value_by_path(panel, f"widgets[{idx}].max", high)
        min_val, max_val = low, high

    low = min_val if min_val is not None else cur_val
    high = max_val if max_val is not None else cur_val
    if low > high:
        return None

    clamped_val = max(low, min(cur_val, high))
    if clamped_val != cur_val:
        set_value_by_path(panel, f"widgets[{idx}].value", clamped_val)
        return cur_val, clamped_val
    return None


def _parse_allowed_values(raw_expected: Any) -> List[Any]:
    if isinstance(raw_expected, list):
        return raw_expected
    if raw_expected is None:
        return []
    text = str(raw_expected).strip()
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def _extract_numeric_token(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass

    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not m:
        return None
    try:
        return float(m.group(0))
    except (TypeError, ValueError):
        return None


def _pick_nearest_enum_value(current_val: Any, allowed: List[Any]) -> Any:
    if not allowed:
        return current_val

    cur_num = _extract_numeric_token(current_val)
    numeric_allowed = [(_extract_numeric_token(v), v) for v in allowed]
    usable = [(num, raw) for num, raw in numeric_allowed if num is not None]
    if cur_num is not None and usable:
        _, nearest_raw = min(usable, key=lambda p: abs(cur_num - p[0]))
        return nearest_raw
    return allowed[0]


def _sync_widget_enum_options(panel: HMIPanel, path: str, allowed: List[Any]) -> Optional[str]:
    idx = _parse_widget_index_from_path(path)
    if idx is None or idx < 0 or idx >= len(panel.widgets):
        return None

    widget = panel.widgets[idx]
    if not hasattr(widget, "options"):
        return None

    allowed_text = [str(v) for v in allowed]
    set_value_by_path(panel, f"widgets[{idx}].options", allowed_text)

    current = str(getattr(widget, "value", "")).strip()
    norm_allowed = [str(x).strip() for x in allowed_text]
    if current not in norm_allowed and allowed_text:
        set_value_by_path(panel, f"widgets[{idx}].value", allowed_text[0])
        return f"value reset to {allowed_text[0]} and options aligned"
    return "options aligned with allowed values"


def _coerce_widget_to_select_for_enum(panel: HMIPanel, path: str, allowed: List[Any]) -> Optional[str]:
    """
    Convert non-discrete widgets (e.g., numeric input) into a select widget when
    enum constraints prove the parameter is discrete.
    """
    idx = _parse_widget_index_from_path(path)
    if idx is None or idx < 0 or idx >= len(panel.widgets):
        return None
    if not allowed:
        return None

    widget = panel.widgets[idx]
    wtype = widget.type.value if hasattr(widget.type, "value") else str(widget.type)
    if wtype in {"select", "radio"}:
        return None
    if wtype not in {"input", "slider"}:
        return None

    new_val = str(_pick_nearest_enum_value(getattr(widget, "value", ""), allowed))
    try:
        new_widget = SelectWidget(
            id=getattr(widget, "id"),
            label=getattr(widget, "label", "Discrete Option"),
            description=getattr(widget, "description", None),
            unit=getattr(widget, "unit", None),
            disabled=getattr(widget, "disabled", False),
            binding=getattr(widget, "binding", None),
            safety=getattr(widget, "safety", None),
            options=[str(v) for v in allowed],
            value=new_val,
        )
        panel.widgets[idx] = new_widget
        return f"widget type coerced from {wtype} to select"
    except Exception:
        return None


def _attempt_fix(panel: HMIPanel, violation: Violation) -> Optional[FixAction]:
    """
    Attempt to auto-fix a violation.
    """
    try:
        current_val = violation.observed_value
        path = violation.param_path
        severity = violation.severity

        # Strategy 1: CLAMP for numeric violations
        if "exceeds" in violation.message or "below" in violation.message:
            try:
                limit = float(violation.expected_limit)
            except (ValueError, TypeError):
                if severity == Severity.HARD:
                    return _apply_reject_strategy(panel, violation, path, current_val)
                return FixAction(
                    fix_id=f"fix_{id(violation)}",
                    violation_rule_id=violation.rule_id,
                    action_type=FixActionType.NONE,
                    param_path=path,
                    value_before=current_val,
                    value_after=current_val,
                    reason="Cannot parse limit for clamping"
                )

            numeric_current = _to_float_or_none(current_val)
            if numeric_current is not None:
                new_val = limit
                set_value_by_path(panel, path, new_val)
                sync_note = None
                synced = _sync_widget_value_after_bound_change(panel, path)
                if synced:
                    sync_note = f"[CONSISTENCY] Related value clamped from {synced[0]} to {synced[1]}"

                return FixAction(
                    fix_id=f"fix_{id(violation)}",
                    violation_rule_id=violation.rule_id,
                    action_type=FixActionType.CLAMP,
                    param_path=path,
                    value_before=current_val,
                    value_after=new_val,
                    reason=f"Clamped to {limit} based on rule {violation.rule_id}",
                    diff_note=(
                        f"[SAFETY] Value adjusted from {current_val} to {new_val}"
                        + (f"; {sync_note}" if sync_note else "")
                    )
                )

        # Strategy 2: EDIT for ENUM violations (deterministic nearest legal value)
        if "not in allowed values" in violation.message:
            allowed = _parse_allowed_values(violation.expected_limit)
            if allowed:
                repaired = _pick_nearest_enum_value(current_val, allowed)
                coercion_note = _coerce_widget_to_select_for_enum(panel, path, allowed)
                set_value_by_path(panel, path, repaired)
                options_note = _sync_widget_enum_options(panel, path, allowed)
                return FixAction(
                    fix_id=f"fix_{id(violation)}",
                    violation_rule_id=violation.rule_id,
                    action_type=FixActionType.EDIT,
                    param_path=path,
                    value_before=current_val,
                    value_after=repaired,
                    reason="ENUM mismatch repaired to nearest allowed value",
                    diff_note=(
                        f"[ENUM] Replaced {current_val} with {repaired}"
                        + (f"; [ENUM] {coercion_note}" if coercion_note else "")
                        + (f"; [ENUM] {options_note}" if options_note else "")
                    ),
                )

            if severity == Severity.HARD:
                return _apply_reject_strategy(panel, violation, path, current_val)
            return FixAction(
                fix_id=f"fix_{id(violation)}",
                violation_rule_id=violation.rule_id,
                action_type=FixActionType.WARN,
                param_path=path,
                value_before=current_val,
                value_after=current_val,
                reason="ENUM mismatch (SOFT). Manual review recommended.",
                diff_note="[WARNING] Value not in allowed set and allowed-values list unavailable",
            )

        # Strategy 3: REGEX violations
        if "does not match pattern" in violation.message:
            if severity == Severity.HARD:
                return _apply_reject_strategy(panel, violation, path, current_val)
            return FixAction(
                fix_id=f"fix_{id(violation)}",
                violation_rule_id=violation.rule_id,
                action_type=FixActionType.WARN,
                param_path=path,
                value_before=current_val,
                value_after=current_val,
                reason="REGEX mismatch. Manual review recommended."
            )

        # Default: unfixable
        return FixAction(
            fix_id=f"fix_{id(violation)}",
            violation_rule_id=violation.rule_id,
            action_type=FixActionType.NONE,
            param_path=path,
            value_before=current_val,
            value_after=current_val,
            reason="No auto-fix strategy available"
        )

    except Exception as e:
        return FixAction(
            fix_id=f"fix_error_{id(violation)}",
            violation_rule_id=violation.rule_id,
            action_type=FixActionType.NONE,
            param_path=violation.param_path,
            value_before=violation.observed_value,
            value_after=violation.observed_value,
            reason=f"Fix failed: {str(e)}"
        )


def _apply_reject_strategy(panel: HMIPanel, violation: Violation, path: str, current_val: Any) -> FixAction:
    """
    Apply REJECT strategy: Remove the offending widget from the panel.
    """
    widget_id = None
    widget_idx = None

    try:
        if "widgets[" in path:
            match = re.search(r'widgets\[(\d+)\]', path)
            if match:
                widget_idx = int(match.group(1))
                if 0 <= widget_idx < len(panel.widgets):
                    widget_id = panel.widgets[widget_idx].id
                    removed_widget = panel.widgets.pop(widget_idx)
                    panel.layout = [item for item in panel.layout if item.i != widget_id]

                    return FixAction(
                        fix_id=f"fix_reject_{id(violation)}",
                        violation_rule_id=violation.rule_id,
                        action_type=FixActionType.REJECT,
                        param_path=path,
                        value_before=current_val,
                        value_after="[REMOVED]",
                        reason=f"Widget '{widget_id}' removed due to unfixable HARD violation",
                        diff_note=f"[REJECT] Removed widget type={removed_widget.type.value}"
                    )
    except Exception:
        pass

    return FixAction(
        fix_id=f"fix_confirm_{id(violation)}",
        violation_rule_id=violation.rule_id,
        action_type=FixActionType.NONE,
        param_path=path,
        value_before=current_val,
        value_after=current_val,
        reason=f"REQUIRE_CONFIRM: Cannot auto-fix. User must review widget at '{path}'",
        diff_note="[CRITICAL] Manual intervention required"
    )
