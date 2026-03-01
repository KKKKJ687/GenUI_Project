"""
Prompt-vs-datasheet conflict detection for Phase 2.

This module extracts numeric/enum claims from user prompts and compares them
against the loaded ConstraintSet. It is used to:
1) Surface contradictions early in run artifacts/metrics.
2) Inject strict conflict guidance into the Architect prompt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Dict, List, Optional, Set

from src.modules.verifier.constraints import Constraint, ConstraintKind, ConstraintSet


_UNIT_ALIASES = {
    "℃": "c",
    "°c": "c",
    "celsius": "c",
    "vdc": "v",
    "volt": "v",
    "volts": "v",
    "amp": "a",
    "amps": "a",
}

_CATEGORY_KEYWORDS = {
    "temperature": ["temperature", "temp", "thermal", "over-temperature", "过温", "温度", "热"],
    "voltage": ["voltage", "volt", "vin", "vcc", "vdd", "vm", "supply", "电压", "供电"],
    "current": ["current", "amp", "ampere", "ilim", "电流", "限流"],
    "frequency": ["frequency", "freq", "hz", "khz", "mhz", "pwm", "baud", "频率"],
    "acceleration": ["accel", "accelerometer", "full scale", "fsr", "g range", "加速度", "量程"],
}

_VOLTAGE_SUPPLY_HINTS = {"supply", "power", "vm", "vbat", "vbatt", "vbus", "battery", "电源", "供电", "母线"}
_VOLTAGE_LOGIC_HINTS = {"logic", "input", "in1", "in2", "digital", "逻辑", "输入"}


@dataclass
class PromptConflict:
    kind: str
    category: str
    requested: str
    allowed: str
    source_rule_id: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class _Envelope:
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    min_rule_id: str = ""
    max_rule_id: str = ""
    enum_values: Optional[Set[str]] = None
    enum_rule_id: str = ""


def _normalize_unit(unit: str) -> str:
    token = str(unit or "").strip().lower()
    return _UNIT_ALIASES.get(token, token)


def _normalize_enum_token(value: Any) -> str:
    text = str(value).strip().lower().replace(" ", "").replace("±", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if m and text.endswith("g"):
        try:
            fval = float(m.group(0))
            if fval.is_integer():
                num = str(int(fval))
            else:
                num = f"{fval:.6g}"
        except (TypeError, ValueError):
            num = m.group(0)
        return f"{num}g"
    return text


def _infer_category(text: str, unit: str = "") -> str:
    norm_unit = _normalize_unit(unit)
    # For numeric claims, explicit unit is the strongest signal.
    if norm_unit in {"v", "mv"}:
        return "voltage"
    if norm_unit in {"a", "ma"}:
        return "current"
    if norm_unit in {"c"}:
        return "temperature"
    if norm_unit in {"hz", "khz", "mhz"}:
        return "frequency"
    if norm_unit in {"g"}:
        return "acceleration"

    blob = f"{text or ''} {norm_unit}".lower()
    for category, words in _CATEGORY_KEYWORDS.items():
        if any(w in blob for w in words):
            return category
    return "unknown"


def _infer_domain(category: str, text: str) -> Optional[str]:
    if category != "voltage":
        return None
    blob = str(text or "").lower()
    if any(k in blob for k in _VOLTAGE_LOGIC_HINTS):
        return "logic"
    if any(k in blob for k in _VOLTAGE_SUPPLY_HINTS):
        return "supply"
    return None


def _envelope_key(category: str, domain: Optional[str]) -> str:
    if not domain:
        return category
    return f"{category}:{domain}"


def _build_envelopes(constraints: ConstraintSet) -> Dict[str, _Envelope]:
    envelopes: Dict[str, _Envelope] = {}

    def _merge_rule(env: _Envelope, rule: Constraint) -> None:
        if rule.kind == ConstraintKind.MAX and rule.max_val is not None:
            if env.max_val is None or float(rule.max_val) < env.max_val:
                env.max_val = float(rule.max_val)
                env.max_rule_id = rule.id
        elif rule.kind == ConstraintKind.MIN and rule.min_val is not None:
            if env.min_val is None or float(rule.min_val) > env.min_val:
                env.min_val = float(rule.min_val)
                env.min_rule_id = rule.id
        elif rule.kind == ConstraintKind.RANGE:
            if rule.max_val is not None and (env.max_val is None or float(rule.max_val) < env.max_val):
                env.max_val = float(rule.max_val)
                env.max_rule_id = rule.id
            if rule.min_val is not None and (env.min_val is None or float(rule.min_val) > env.min_val):
                env.min_val = float(rule.min_val)
                env.min_rule_id = rule.id
        elif rule.kind == ConstraintKind.ENUM and rule.allowed_values:
            norm_values = {_normalize_enum_token(v) for v in rule.allowed_values}
            if env.enum_values is None:
                env.enum_values = set(norm_values)
                env.enum_rule_id = rule.id
            else:
                env.enum_values &= norm_values
                if not env.enum_rule_id:
                    env.enum_rule_id = rule.id

    for rule in constraints.constraints:
        blob = f"{rule.id or ''} {rule.name or ''} {rule.description or ''} {rule.applies_to or ''}"
        category = _infer_category(blob, rule.unit or "")
        if category == "unknown":
            continue
        domain = _infer_domain(category, blob)
        _merge_rule(envelopes.setdefault(category, _Envelope()), rule)
        if domain:
            _merge_rule(envelopes.setdefault(_envelope_key(category, domain), _Envelope()), rule)

    return envelopes


def _extract_numeric_claims(prompt: str) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    text = prompt or ""
    units = r"(?:℃|°C|C|mV|V|mA|A|kHz|MHz|Hz|g)"

    range_pattern = re.compile(
        rf"(-?\d+(?:\.\d+)?)\s*({units})\s*(?:到|to|~|～|-|—)\s*(-?\d+(?:\.\d+)?)(?:\s*({units}))?",
        re.IGNORECASE,
    )
    default_pattern = re.compile(
        rf"(?:默认|default)[^\d-]{{0,12}}(-?\d+(?:\.\d+)?)\s*({units})",
        re.IGNORECASE,
    )
    max_pattern = re.compile(
        rf"(?:max|maximum|最大)[^\d-]{{0,10}}(-?\d+(?:\.\d+)?)\s*({units})",
        re.IGNORECASE,
    )

    def _ctx(start: int, end: int) -> str:
        left = max(0, start - 24)
        right = min(len(text), end + 24)
        return text[left:right]

    for m in range_pattern.finditer(text):
        u1 = m.group(2)
        u2 = m.group(4) or u1
        unit = u1 if _normalize_unit(u1) == _normalize_unit(u2) else u1
        category = _infer_category(_ctx(m.start(), m.end()), unit)
        if category == "unknown":
            continue
        ctx = _ctx(m.start(), m.end())
        claims.append(
            {
                "kind": "range",
                "category": category,
                "domain": _infer_domain(category, ctx),
                "min_val": float(m.group(1)),
                "max_val": float(m.group(3)),
                "unit": unit,
            }
        )

    for m in default_pattern.finditer(text):
        unit = m.group(2)
        category = _infer_category(_ctx(m.start(), m.end()), unit)
        if category == "unknown":
            continue
        ctx = _ctx(m.start(), m.end())
        claims.append(
            {
                "kind": "default",
                "category": category,
                "domain": _infer_domain(category, ctx),
                "value": float(m.group(1)),
                "unit": unit,
            }
        )

    for m in max_pattern.finditer(text):
        unit = m.group(2)
        category = _infer_category(_ctx(m.start(), m.end()), unit)
        if category == "unknown":
            continue
        ctx = _ctx(m.start(), m.end())
        claims.append(
            {
                "kind": "max",
                "category": category,
                "domain": _infer_domain(category, ctx),
                "value": float(m.group(1)),
                "unit": unit,
            }
        )

    accel_candidates = re.findall(r"[±]?\s*-?\d+(?:\.\d+)?\s*g", text, flags=re.IGNORECASE)
    accel_set = {_normalize_enum_token(x) for x in accel_candidates}
    if len(accel_set) >= 3 and _infer_category(text, "g") == "acceleration":
        claims.append(
            {
                "kind": "enum",
                "category": "acceleration",
                "values": accel_set,
                "unit": "g",
            }
        )

    return claims


def detect_prompt_constraint_conflicts(user_prompt: str, constraints: ConstraintSet) -> List[PromptConflict]:
    conflicts: List[PromptConflict] = []
    seen = set()

    envelopes = _build_envelopes(constraints)
    claims = _extract_numeric_claims(user_prompt)

    for claim in claims:
        category = claim["category"]
        domain = claim.get("domain")
        env = envelopes.get(_envelope_key(category, domain)) or envelopes.get(category)
        if not env:
            continue

        if claim["kind"] == "range":
            req_min = float(claim["min_val"])
            req_max = float(claim["max_val"])
            if env.max_val is not None and req_max > env.max_val + 1e-9:
                key = ("range_max", category, req_max, env.max_val)
                if key not in seen:
                    seen.add(key)
                    conflicts.append(
                        PromptConflict(
                            kind="range_max",
                            category=category,
                            requested=f"max={req_max}",
                            allowed=f"max<={env.max_val}",
                            source_rule_id=env.max_rule_id,
                            message=f"Prompt requests {category} max {req_max}, exceeding datasheet limit {env.max_val}.",
                        )
                    )
            if env.min_val is not None and req_min < env.min_val - 1e-9:
                key = ("range_min", category, req_min, env.min_val)
                if key not in seen:
                    seen.add(key)
                    conflicts.append(
                        PromptConflict(
                            kind="range_min",
                            category=category,
                            requested=f"min={req_min}",
                            allowed=f"min>={env.min_val}",
                            source_rule_id=env.min_rule_id,
                            message=f"Prompt requests {category} min {req_min}, below datasheet limit {env.min_val}.",
                        )
                    )

        elif claim["kind"] in {"default", "max"}:
            req_val = float(claim["value"])
            if env.max_val is not None and req_val > env.max_val + 1e-9:
                key = (claim["kind"], category, req_val, env.max_val)
                if key not in seen:
                    seen.add(key)
                    conflicts.append(
                        PromptConflict(
                            kind=f"{claim['kind']}_above_max",
                            category=category,
                            requested=f"value={req_val}",
                            allowed=f"value<={env.max_val}",
                            source_rule_id=env.max_rule_id,
                            message=f"Prompt requests {category} {req_val}, exceeding datasheet limit {env.max_val}.",
                        )
                    )
            if env.min_val is not None and req_val < env.min_val - 1e-9:
                key = (f"{claim['kind']}_min", category, req_val, env.min_val)
                if key not in seen:
                    seen.add(key)
                    conflicts.append(
                        PromptConflict(
                            kind=f"{claim['kind']}_below_min",
                            category=category,
                            requested=f"value={req_val}",
                            allowed=f"value>={env.min_val}",
                            source_rule_id=env.min_rule_id,
                            message=f"Prompt requests {category} {req_val}, below datasheet limit {env.min_val}.",
                        )
                    )

        elif claim["kind"] == "enum" and env.enum_values:
            requested = {str(v) for v in claim["values"]}
            if not requested.issubset(env.enum_values):
                invalid = sorted(requested - env.enum_values)
                key = ("enum", category, tuple(invalid), tuple(sorted(env.enum_values)))
                if key not in seen:
                    seen.add(key)
                    conflicts.append(
                        PromptConflict(
                            kind="enum_mismatch",
                            category=category,
                            requested=f"options={sorted(requested)}",
                            allowed=f"options={sorted(env.enum_values)}",
                            source_rule_id=env.enum_rule_id,
                            message=f"Prompt requests unsupported {category} options: {invalid}.",
                        )
                    )

    return conflicts


def summarize_constraints_for_prompt(constraints: ConstraintSet, max_items: int = 12) -> str:
    lines: List[str] = []
    for rule in constraints.constraints[:max_items]:
        limit = "n/a"
        if rule.kind == ConstraintKind.MAX:
            limit = f"max={rule.max_val}"
        elif rule.kind == ConstraintKind.MIN:
            limit = f"min={rule.min_val}"
        elif rule.kind == ConstraintKind.RANGE:
            limit = f"range=[{rule.min_val}, {rule.max_val}]"
        elif rule.kind == ConstraintKind.ENUM:
            limit = f"enum={rule.allowed_values}"
        elif rule.kind == ConstraintKind.REGEX:
            limit = f"regex={rule.pattern}"

        source = ""
        if rule.source and rule.source.page:
            source = f" (p.{rule.source.page})"
        lines.append(
            f"- [{rule.id}] {rule.name}: {limit}; target={rule.applies_to}; unit={rule.unit}{source}"
        )

    remaining = len(constraints.constraints) - max_items
    if remaining > 0:
        lines.append(f"- ... and {remaining} more constraints")
    return "\n".join(lines) if lines else "- No constraints loaded."


def summarize_conflicts_for_prompt(conflicts: List[PromptConflict], max_items: int = 10) -> str:
    if not conflicts:
        return "- No direct prompt-vs-datasheet conflict detected."
    lines: List[str] = []
    for c in conflicts[:max_items]:
        lines.append(f"- {c.message} (requested: {c.requested}; datasheet: {c.allowed}; rule: {c.source_rule_id})")
    remaining = len(conflicts) - max_items
    if remaining > 0:
        lines.append(f"- ... and {remaining} more conflicts")
    return "\n".join(lines)
