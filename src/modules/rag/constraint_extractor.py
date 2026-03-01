"""
Phase 3: Constraint Extractor

Converts unstructured text chunks into a structured ConstraintSet.
Implements the Hybrid Extraction Strategy:
  1. Rule-Based (Heuristics): Fast regex matching for common patterns
  2. LLM-Based (Extraction): Deep understanding for complex cases

This is the Neuro-Symbolic core - combining neural (LLM) and symbolic (regex) approaches.
"""
import re
import json
import logging
from typing import List, Any, Optional, Tuple, Dict
from pydantic import ValidationError

from src.modules.verifier.constraints import (
    ConstraintSet, Constraint, ConstraintKind, Severity, SourceRef
)
from src.modules.rag.datasheet_rag import EvidenceChunk
from src.agents.prompts_phase3 import get_extraction_prompt, get_validation_repair_prompt

logger = logging.getLogger(__name__)

_NUMERIC_EPSILON = 1e-3

_SECTION_PRIORITY = {
    "absolute maximum ratings": 4,
    "absolute maximum": 4,
    "electrical characteristics": 3,
    "dc characteristics": 3,
    "ac characteristics": 3,
    "recommended operating conditions": 2,
    "recommended": 2,
}

_SUPPLY_VOLTAGE_HINTS = {
    "supply",
    "power",
    "vm",
    "motor supply",
    "bus",
    "vbus",
    "vbat",
    "vbatt",
    "battery",
    "电源",
    "供电",
    "母线",
}

_LOGIC_VOLTAGE_HINTS = {
    "logic",
    "input",
    "in1",
    "in2",
    "digital",
    "logic input",
    "control input",
    "逻辑",
    "输入",
}

_TIMING_HINTS = {
    "pulse",
    "pulse width",
    "timing",
    "delay",
    "period",
    "setup",
    "hold",
    "sleep time",
    "sleep",
    "latency",
    "duration",
    "秒",
    "脉宽",
    "时序",
    "延时",
}

_TIMING_NUMERIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ns|us|ms|s)\b",
    re.IGNORECASE,
)


class ExtractionResult:
    """
    Result of the extraction process.
    
    Attributes:
        constraint_set: Successfully extracted constraints
        residuals: Text snippets that couldn't be processed
        raw_response: Original LLM response for debugging
    """
    def __init__(
        self, 
        constraint_set: ConstraintSet, 
        residuals: Optional[List[str]] = None,
        raw_response: str = ""
    ):
        self.constraint_set = constraint_set
        self.residuals = residuals or []
        self.raw_response = raw_response


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _parse_float_token(token: Any) -> float:
    text = (
        str(token)
        .strip()
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    return float(text)


def _is_close(a: Optional[float], b: Optional[float], eps: float = _NUMERIC_EPSILON) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= eps


def _source_section_priority(c: Constraint) -> int:
    section = _norm_text(getattr(c.source, "section", "") if c.source else "")
    snippet = _norm_text(getattr(c.source, "snippet", "") if c.source else "")
    blob = f"{section} {snippet}"
    for key, score in _SECTION_PRIORITY.items():
        if key in blob:
            return score
    return 1


def _normalize_allowed_values(values: Optional[List[Any]]) -> List[str]:
    if not values:
        return []
    return [str(v).strip() for v in values]


def _constraint_value_repr(c: Constraint) -> Any:
    if c.kind == ConstraintKind.RANGE:
        return [c.min_val, c.max_val]
    if c.kind == ConstraintKind.MAX:
        return c.max_val
    if c.kind == ConstraintKind.MIN:
        return c.min_val
    if c.kind == ConstraintKind.ENUM:
        return c.allowed_values
    return c.pattern


def _constraint_bounds(c: Constraint) -> Tuple[Optional[float], Optional[float]]:
    """
    Return effective [lower, upper] for numeric constraints.
    Non-numeric constraints return (None, None).
    """
    if c.kind == ConstraintKind.RANGE:
        return c.min_val, c.max_val
    if c.kind == ConstraintKind.MIN:
        return c.min_val, None
    if c.kind == ConstraintKind.MAX:
        return None, c.max_val
    return None, None


def _rule_priority_score(c: Constraint) -> int:
    """
    Rank reliability for tie-breakers:
    - source section priority first
    - HARD constraints above SOFT
    - explicit absolute/recommended hints in rule text
    - trip/protection thresholds are usually secondary control hints
    """
    score = _source_section_priority(c) * 10
    if c.severity == Severity.HARD:
        score += 4
    text = _norm_text(f"{c.id} {c.name} {c.description or ''}")
    if "absolute" in text or "abs_max" in text:
        score += 3
    if "recommended" in text or "operating" in text:
        score += 1
    if "trip" in text or "protection" in text or "shutdown" in text:
        score -= 2
    return score


def _infer_semantic_selector(item: Dict[str, Any], default_selector: str) -> str:
    """
    Convert overly broad type selectors into semantic selectors,
    so verifier can match by label/unit and avoid cross-parameter confusion.
    """
    selector = str(default_selector or "*.value")

    text = _norm_text(
        " ".join(
            [
                str(item.get("id", "")),
                str(item.get("name", "")),
                str(item.get("description", "")),
                str(item.get("source_quote", "")),
            ]
        )
    )
    unit = _norm_text(item.get("unit", ""))

    def _has_any(hints: set[str]) -> bool:
        return any(h in text for h in hints)

    def _has_timing_cue() -> bool:
        if _has_any(_TIMING_HINTS):
            return True
        return bool(_TIMING_NUMERIC_RE.search(text))

    def _voltage_domain_selector() -> str:
        if _has_any(_LOGIC_VOLTAGE_HINTS):
            return "*.logic_voltage"
        if _has_any(_SUPPLY_VOLTAGE_HINTS):
            return "*.supply_voltage"
        return "*.voltage"

    def _sanitize_selector(candidate: str) -> str:
        # Only sanitize abstract selectors; explicit path selectors remain unchanged.
        if not str(candidate or "").startswith("*."):
            return candidate

        # Unit-family sanity: avoid impossible mappings like V->timing or Hz->timing.
        if unit in {"v", "mv"}:
            return _voltage_domain_selector()
        if unit in {"a", "ma"}:
            return "*.current"
        if unit in {"hz", "khz", "mhz"}:
            return "*.frequency"
        if unit in {"c", "°c", "℃"}:
            return "*.temperature"
        if unit in {"s", "ms", "us", "ns"}:
            return "*.timing"

        # No clear unit: keep candidate, but timing must still have timing cues.
        if candidate == "*.timing" and not _has_timing_cue():
            return "*.value"
        return candidate

    # Timing-like constraints (pulse width / setup-hold / delay) must not be
    # collapsed into voltage semantics even if source text contains "voltages".
    if unit in {"s", "ms", "us", "ns"} or _has_timing_cue():
        return _sanitize_selector("*.timing")

    if selector.startswith("*."):
        if selector == "*.voltage":
            return _sanitize_selector(_voltage_domain_selector())
        return _sanitize_selector(selector)

    if not (selector.startswith("widgets[type='") or selector.startswith('widgets[type="')):
        return _sanitize_selector(selector)

    if "temp" in text or "thermal" in text or unit in {"c", "°c", "℃"}:
        return _sanitize_selector("*.temperature")
    if "volt" in text or "vcc" in text or "vin" in text or unit in {"v", "mv"}:
        return _sanitize_selector(_voltage_domain_selector())
    if "current" in text or "amp" in text or unit in {"a", "ma"}:
        return _sanitize_selector("*.current")
    if "freq" in text or "baud" in text or "clock" in text or unit in {"hz", "khz", "mhz"}:
        return _sanitize_selector("*.frequency")
    if "accelerometer" in text or "accel" in text or "g range" in text:
        return _sanitize_selector("*.acceleration")
    return _sanitize_selector(selector)


def normalize_constraint_semantics(constraints: List[Constraint]) -> List[Constraint]:
    """
    Secondary normalization pass used by both extraction and pipeline-level
    cache sanitation. It hardens selector semantics against noisy LLM outputs.
    """
    for c in constraints:
        try:
            item = {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "source_quote": getattr(c.source, "snippet", "") if c.source else "",
                "unit": c.unit,
            }
            c.applies_to = _infer_semantic_selector(item, c.applies_to)
        except Exception:
            continue
    return constraints


def _infer_severity(item: Dict[str, Any], section_hint: str) -> Severity:
    sev_raw = _norm_text(item.get("severity", ""))
    if sev_raw in {"hard", "high", "critical"}:
        return Severity.HARD
    if sev_raw in {"soft", "warn", "warning", "medium", "low"}:
        return Severity.SOFT
    # Default by section hint: recommended operating conditions are softer.
    section_norm = _norm_text(section_hint)
    if "recommended operating" in section_norm or "recommended" in section_norm:
        return Severity.SOFT
    return Severity.HARD


def _dedupe_constraints(constraints: List[Constraint]) -> List[Constraint]:
    """
    Remove near-duplicates to reduce spurious pairwise conflict explosion.
    """
    deduped: List[Constraint] = []
    seen: Dict[Tuple[Any, ...], Constraint] = {}

    for c in constraints:
        key = (
            c.kind.value if hasattr(c.kind, "value") else str(c.kind),
            c.applies_to,
            _norm_text(c.unit),
            round(c.min_val, 6) if c.min_val is not None else None,
            round(c.max_val, 6) if c.max_val is not None else None,
            tuple(_normalize_allowed_values(c.allowed_values)),
            c.pattern or "",
        )
        existing = seen.get(key)
        if not existing:
            seen[key] = c
            deduped.append(c)
            continue

        # Keep the one with stronger source confidence/priority.
        exist_score = _source_section_priority(existing)
        new_score = _source_section_priority(c)
        if new_score > exist_score:
            seen[key] = c
            deduped = [x for x in deduped if x.id != existing.id]
            deduped.append(c)

    return deduped


def extract_constraints_heuristic(text: str, filename: str) -> List[Constraint]:
    """
    Stage 1: Regex-based extraction for high-confidence patterns.
    
    This is fast and reliable for common datasheet patterns like:
      - "VCC Max: 3.6V"
      - "Operating Temperature: -40°C to +85°C"
      - "Input Voltage: 1.8V to 3.6V"
    
    Returns list of Constraints (may be empty if no patterns match).
    """
    constraints: List[Constraint] = []
    constraint_id = 0

    def _voltage_applies_to(context_text: str) -> str:
        ctx = _norm_text(context_text)
        if any(h in ctx for h in _LOGIC_VOLTAGE_HINTS):
            return "*.logic_voltage"
        if any(h in ctx for h in _SUPPLY_VOLTAGE_HINTS):
            return "*.supply_voltage"
        return "*.voltage"

    def _canonical(value: float, unit: str) -> tuple[float, str]:
        key = (unit or "").strip().lower()
        if key == "mv":
            return value / 1000.0, "V"
        if key == "ma":
            return value / 1000.0, "A"
        if key == "khz":
            return value * 1000.0, "Hz"
        if key == "mhz":
            return value * 1_000_000.0, "Hz"
        if key in {"v", "a"}:
            return value, key.upper()
        if key == "hz":
            return value, "Hz"
        return value, unit
    
    # Pattern 1: "X Max: Y.YV" or "Maximum X: Y.YV"
    max_patterns = [
        (
            r"(?:VCC|VDD|Supply|VM)\s*(?:Max|Maximum)[:\s]+(\d+(?:\.\d+)?)\s*V",
            "VCC_MAX",
            "Max Supply Voltage",
            "*.supply_voltage",
            "V",
        ),
        (
            r"Input Voltage\s*(?:Max|Maximum)[:\s]+(\d+(?:\.\d+)?)\s*V",
            "VIN_MAX",
            "Max Input Voltage",
            "*.logic_voltage",
            "V",
        ),
        (
            r"Output Current\s*(?:Max|Maximum)[:\s]+(\d+(?:\.\d+)?)\s*(?:mA|A)",
            "IOUT_MAX",
            "Max Output Current",
            "*.current",
            "A",
        ),
    ]
    
    for pattern, rule_id, name, applies_to, unit in max_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            constraints.append(Constraint(
                id=f"{rule_id}_{constraint_id}",
                name=name,
                kind=ConstraintKind.MAX,
                applies_to=applies_to,
                max_val=value,
                unit=unit,
                severity=Severity.HARD,
                source=SourceRef(
                    datasheet_name=filename,
                    page=1,
                    snippet=match.group(0),
                    confidence=0.95
                )
            ))
            constraint_id += 1
    
    # Pattern 2: "X.XV to Y.YV" (Range)
    range_pattern = r"([+\-−–—]?\d+(?:\.\d+)?)\s*V?\s*(?:to|~|～|-|–|—)\s*([+\-−–—]?\d+(?:\.\d+)?)\s*V"
    for match in re.finditer(range_pattern, text, re.IGNORECASE):
        min_val = _parse_float_token(match.group(1))
        max_val = _parse_float_token(match.group(2))
        context_start = max(0, match.start() - 48)
        context_end = min(len(text), match.end() + 48)
        context = text[context_start:context_end]
        
        if min_val < max_val:  # Sanity check
            constraints.append(Constraint(
                id=f"RANGE_{constraint_id}",
                name="Operating Voltage Range",
                kind=ConstraintKind.RANGE,
                applies_to=_voltage_applies_to(context),
                min_val=min_val,
                max_val=max_val,
                unit="V",
                severity=Severity.SOFT,
                source=SourceRef(
                    datasheet_name=filename,
                    page=1,
                    snippet=match.group(0),
                    confidence=0.85
                )
            ))
            constraint_id += 1

    # Pattern 3: temperature range with Celsius units
    temp_range_pattern = r"([+\-−–—]?\d+(?:\.\d+)?)\s*(?:°?C|℃)\s*(?:to|~|～|-|–|—)\s*([+\-−–—]?\d+(?:\.\d+)?)\s*(?:°?C|℃)"
    for match in re.finditer(temp_range_pattern, text, re.IGNORECASE):
        min_val = _parse_float_token(match.group(1))
        max_val = _parse_float_token(match.group(2))
        if min_val < max_val:
            constraints.append(Constraint(
                id=f"TEMP_RANGE_{constraint_id}",
                name="Temperature Range",
                kind=ConstraintKind.RANGE,
                applies_to="*.temperature",
                min_val=min_val,
                max_val=max_val,
                unit="C",
                severity=Severity.SOFT,
                source=SourceRef(
                    datasheet_name=filename,
                    page=1,
                    snippet=match.group(0),
                    confidence=0.85
                )
            ))
            constraint_id += 1

    # Pattern 4: semantic range extraction with units (voltage/current/frequency)
    # e.g. "电源电压 0~50V", "PWM 频率 0~300kHz", "Current limit 0-5A"
    semantic_range_pattern = re.compile(
        r"([+\-−–—]?\d+(?:\.\d+)?)\s*(mv|v|ma|a|mhz|khz|hz)?\s*(?:to|~|～|-|–|—)\s*([+\-−–—]?\d+(?:\.\d+)?)\s*(mv|v|ma|a|mhz|khz|hz)",
        re.IGNORECASE,
    )
    for match in semantic_range_pattern.finditer(text):
        raw_min = _parse_float_token(match.group(1))
        raw_max = _parse_float_token(match.group(3))
        unit_raw = (match.group(4) or match.group(2) or "").lower()
        if not unit_raw:
            continue

        min_raw, max_raw = sorted((raw_min, raw_max))
        min_val, canon_unit = _canonical(float(min_raw), unit_raw)
        max_val, _ = _canonical(float(max_raw), unit_raw)
        if max_val <= min_val:
            continue

        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        context = text[start:end]
        context_l = context.lower()

        if canon_unit == "V":
            applies_to = _voltage_applies_to(context)
            if applies_to == "*.logic_voltage":
                name = "Logic Input Voltage Range"
            elif applies_to == "*.supply_voltage":
                name = "Supply Voltage Range"
            else:
                name = "Operating Voltage Range"
        elif canon_unit == "A":
            name = "Current Range"
            applies_to = "*.current"
        elif canon_unit == "Hz":
            name = "Frequency Range"
            applies_to = "*.frequency"
        elif any(k in context_l for k in ["voltage", "电压", "supply", "vin", "vcc", "vdd", "vm"]):
            applies_to = _voltage_applies_to(context)
            if applies_to == "*.logic_voltage":
                name = "Logic Input Voltage Range"
            elif applies_to == "*.supply_voltage":
                name = "Supply Voltage Range"
            else:
                name = "Operating Voltage Range"
        elif any(k in context_l for k in ["current", "电流", "amp", "限流"]):
            name = "Current Range"
            applies_to = "*.current"
        elif any(k in context_l for k in ["pwm", "frequency", "频率", "switching"]):
            name = "Frequency Range"
            applies_to = "*.frequency"
        else:
            continue

        constraints.append(
            Constraint(
                id=f"SEM_RANGE_{constraint_id}",
                name=name,
                kind=ConstraintKind.RANGE,
                applies_to=applies_to,
                min_val=float(min_val),
                max_val=float(max_val),
                unit=canon_unit,
                severity=Severity.HARD if "maximum" in context_l or "绝对" in context_l else Severity.SOFT,
                source=SourceRef(
                    datasheet_name=filename,
                    page=1,
                    snippet=match.group(0),
                    confidence=0.9,
                ),
            )
        )
        constraint_id += 1

    # Pattern 5: semantic MAX extraction with units
    semantic_max_patterns = [
        (
            r"(?:pwm|frequency|switching frequency|频率)[^\n]{0,30}?(?:max|maximum|up to|≤|<)\s*([+\-]?\d+(?:\.\d+)?)\s*(mhz|khz|hz)",
            "MAX_PWM_FREQ",
            "Max PWM Frequency",
            "*.frequency",
        ),
        (
            r"(?:current limit|max current|peak current|电流限制|最大电流)[^\n]{0,30}?(?:max|maximum|up to|≤|<)?\s*([+\-]?\d+(?:\.\d+)?)\s*(ma|a)",
            "MAX_CURRENT",
            "Max Current",
            "*.current",
        ),
    ]
    for pattern, rid, name, applies_to in semantic_max_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            raw_v = float(match.group(1))
            raw_u = match.group(2)
            max_v, unit = _canonical(raw_v, raw_u)
            constraints.append(
                Constraint(
                    id=f"{rid}_{constraint_id}",
                    name=name,
                    kind=ConstraintKind.MAX,
                    applies_to=applies_to,
                    max_val=float(max_v),
                    unit=unit,
                    severity=Severity.HARD,
                    source=SourceRef(
                        datasheet_name=filename,
                        page=1,
                        snippet=match.group(0),
                        confidence=0.9,
                    ),
                )
            )
            constraint_id += 1
    
    deduped: List[Constraint] = []
    seen = set()
    for c in constraints:
        key = (
            c.kind.value if hasattr(c.kind, "value") else str(c.kind),
            c.applies_to,
            round(c.min_val, 6) if c.min_val is not None else None,
            round(c.max_val, 6) if c.max_val is not None else None,
            tuple(str(v) for v in (c.allowed_values or [])),
            c.unit,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    return deduped


def _format_number_token(raw: str) -> str:
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return str(raw).strip()
    if num.is_integer():
        return str(int(num))
    return f"{num:.6g}"


_ACCEL_ENUM_CONTEXT_RE = re.compile(
    r"accelerometer|accel|full[\s-]?scale|fsr|g[\s-]*range|range[\s-]*(?:select|setting|bits)|量程|加速度",
    re.IGNORECASE,
)
_ACCEL_ENUM_TOKEN_RE = re.compile(r"([±]|\+/-)?\s*(-?\d+(?:\.\d+)?)\s*g\b", re.IGNORECASE)
_ACCEL_ENUM_STRONG_HINTS = {
    "full scale",
    "full-scale",
    "fsr",
    "range select",
    "range setting",
    "range bits",
    "selectable range",
    "measurement range",
    "g range",
    "量程",
}
_ACCEL_ENUM_NOISE_HINTS = {
    "output change",
    "sensitivity",
    "offset",
    "noise",
    "cross-axis",
    "typ",
    "table",
}


def _normalize_accel_magnitude(raw_val: float) -> Optional[float]:
    """
    Normalize raw g tokens for enum extraction:
    - use absolute magnitude (drop +/- sign semantics)
    - reject non-physical/irrelevant values (e.g., 0g, 10,000g shock rating)
    """
    val = abs(float(raw_val))
    if val <= 0:
        return None
    if val > 256.0:
        return None
    return val


def _is_power_of_two_progression(values: List[float]) -> bool:
    uniq = sorted(set(values))
    if len(uniq) < 3:
        return False
    ratios: List[float] = []
    for i in range(1, len(uniq)):
        prev = uniq[i - 1]
        curr = uniq[i]
        if prev <= 0:
            return False
        ratios.append(curr / prev)
    near_two = sum(1 for r in ratios if 1.6 <= r <= 2.6)
    return near_two >= max(2, len(ratios) - 1)


def _extract_accel_enum_values(text: str) -> List[str]:
    """
    Extract discrete accelerometer full-scale options from noisy chunk text.
    Guardrails:
      - require acceleration/range context
      - reject measurement-stat rows (e.g. output change -0.2g~3.4g)
      - prefer ± notation or power-of-two range progressions
    """
    if not text:
        return []

    segments = [seg.strip() for seg in re.split(r"[\n;；]+", text) if seg.strip()]
    if text.strip() not in segments:
        segments.append(text.strip())

    best_vals: List[float] = []
    best_score = float("-inf")

    for seg in segments:
        seg_l = seg.lower()
        if not _ACCEL_ENUM_CONTEXT_RE.search(seg):
            continue

        vals: List[float] = []
        has_pm = ("±" in seg) or ("+/-" in seg_l) or ("plus/minus" in seg_l)
        for m in _ACCEL_ENUM_TOKEN_RE.finditer(seg):
            if m.group(1) in {"±", "+/-"}:
                has_pm = True
            try:
                raw = float(m.group(2))
            except (TypeError, ValueError):
                continue
            norm = _normalize_accel_magnitude(raw)
            if norm is None:
                continue
            vals.append(norm)

        uniq_vals = sorted(set(vals))
        if len(uniq_vals) < 3:
            continue

        strong_ctx = any(h in seg_l for h in _ACCEL_ENUM_STRONG_HINTS)
        noisy_ctx = any(h in seg_l for h in _ACCEL_ENUM_NOISE_HINTS)
        pow2_like = _is_power_of_two_progression(uniq_vals)

        accepted = False
        if strong_ctx and (has_pm or pow2_like):
            accepted = True
        elif has_pm and pow2_like:
            accepted = True

        # Reject typical measurement-stat lines unless context is very strong.
        if noisy_ctx and not (strong_ctx and (has_pm or pow2_like)):
            accepted = False

        if not accepted:
            continue

        score = (
            len(uniq_vals)
            + (3 if strong_ctx else 0)
            + (2 if has_pm else 0)
            + (2 if pow2_like else 0)
            - (2 if noisy_ctx else 0)
        )
        if score > best_score:
            best_score = score
            best_vals = uniq_vals

    return [f"{_format_number_token(v)}g" for v in best_vals]


def extract_enum_constraints_from_chunks(chunks: List[EvidenceChunk], filename: str) -> List[Constraint]:
    """
    Evidence-level fallback for discrete mode enums.
    This specifically improves cases where LLM extraction misses table-like enum rows.
    """
    out: List[Constraint] = []
    seen = set()

    for c in chunks:
        text = c.text or ""
        if not _ACCEL_ENUM_CONTEXT_RE.search(text):
            continue

        uniq_vals = _extract_accel_enum_values(text)
        if len(uniq_vals) < 3:
            continue

        key = ("accel", tuple(uniq_vals))
        if key in seen:
            continue
        seen.add(key)

        snippet = text.strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        out.append(
            Constraint(
                id=f"AUTO_ACCEL_ENUM_P{c.page}_{len(out)}",
                name="Accelerometer Full Scale Range Enum",
                description="Detected discrete accelerometer full-scale options from datasheet evidence.",
                kind=ConstraintKind.ENUM,
                applies_to="*.acceleration",
                allowed_values=uniq_vals,
                unit="unitless",
                severity=Severity.HARD,
                source=SourceRef(
                    datasheet_name=filename,
                    page=c.page,
                    section=c.section,
                    snippet=snippet,
                    confidence=0.92,
                ),
            )
        )

    return out


def extract_constraints(
    chunks: List[EvidenceChunk], 
    llm_client: Any, 
    device_name: str,
    datasheet_filename: str,
    max_repair_attempts: int = 2
) -> ConstraintSet:
    """
    Main Extraction Pipeline: Evidence -> LLM -> JSON -> Validation -> ConstraintSet
    
    Args:
        chunks: Evidence chunks from the datasheet
        llm_client: LLM client with generate_content() method
        device_name: Name of the device being analyzed
        datasheet_filename: Original PDF filename for traceability
        max_repair_attempts: Max LLM repair attempts for invalid JSON
        
    Returns:
        ConstraintSet with extracted constraints
    """
    if not chunks:
        return ConstraintSet(
            device_name=device_name,
            constraints=[],
            metadata={"error": "No evidence chunks provided"}
        )
    
    # 1. Prepare Context from chunks
    context_parts = []
    for c in chunks:
        context_parts.append(f"--- CHUNK {c.chunk_id} (Page {c.page}) ---\n{c.text}")
    context_text = "\n\n".join(context_parts)
    
    # 2. Call LLM for extraction
    prompt = get_extraction_prompt(device_name, context_text)
    raw_response = ""
    
    try:
        resp = llm_client.generate_content(prompt)
        raw_response = resp.text if hasattr(resp, 'text') else str(resp)
    except Exception as e:
        logger.error(f"LLM Extraction Failed: {e}")
        return ConstraintSet(
            device_name=device_name,
            constraints=[],
            metadata={"error": f"LLM call failed: {str(e)}"}
        )

    # 3. Parse JSON (with repair loop)
    valid_constraints = _parse_and_validate(
        raw_response, chunks, datasheet_filename, 
        llm_client, max_repair_attempts
    )
    
    return ConstraintSet(
        device_name=device_name,
        constraints=valid_constraints,
        metadata={
            "source": "RAG Extraction",
            "chunks_processed": len(chunks),
            "raw_response_preview": raw_response[:200] if raw_response else ""
        }
    )


def _parse_and_validate(
    raw_response: str,
    chunks: List[EvidenceChunk],
    filename: str,
    llm_client: Any,
    max_repairs: int
) -> List[Constraint]:
    """
    Parse LLM response and validate constraints.
    Attempts repair if validation fails.
    """
    # Clean markdown code blocks
    clean_json = raw_response.strip()
    if clean_json.startswith("```"):
        # Remove ```json and ``` markers
        lines = clean_json.split("\n")
        clean_json = "\n".join(lines[1:-1])
    
    for attempt in range(max_repairs + 1):
        try:
            data = json.loads(clean_json)
            return _build_constraints(data, chunks, filename)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error (attempt {attempt + 1}): {e}")
            if attempt < max_repairs and llm_client:
                # Try to repair
                repair_prompt = get_validation_repair_prompt(clean_json, str(e))
                try:
                    resp = llm_client.generate_content(repair_prompt)
                    clean_json = resp.text if hasattr(resp, 'text') else str(resp)
                    clean_json = clean_json.strip()
                    if clean_json.startswith("```"):
                        lines = clean_json.split("\n")
                        clean_json = "\n".join(lines[1:-1])
                except Exception:
                    pass
    
    logger.error("Failed to parse LLM extraction output after repairs")
    return []


def _build_constraints(
    data: dict,
    chunks: List[EvidenceChunk],
    filename: str
) -> List[Constraint]:
    """
    Build validated Constraint objects from parsed JSON.
    """
    valid_constraints: List[Constraint] = []
    
    for item in data.get("constraints", []):
        try:
            # Find source page from quote
            quote = item.get("source_quote", "")
            best_page = 1
            best_section = ""
            
            if quote:
                for c in chunks:
                    # Fuzzy match: check if quote substring appears in chunk
                    if len(quote) > 15 and quote[:15] in c.text:
                        best_page = c.page
                        best_section = c.section or ""
                        break
            elif chunks:
                best_page = chunks[0].page
                best_section = chunks[0].section or ""
            
            # Build SourceRef
            source_ref = SourceRef(
                datasheet_name=filename,
                page=best_page,
                section=best_section or None,
                snippet=quote or "Extracted by LLM",
                confidence=0.9 if quote else 0.7
            )
            
            # Map kind string to enum
            kind_str = item.get("kind", "max").lower()
            kind_map = {
                "max": ConstraintKind.MAX,
                "min": ConstraintKind.MIN,
                "range": ConstraintKind.RANGE,
                "enum": ConstraintKind.ENUM,
                "regex": ConstraintKind.REGEX,
                "type": ConstraintKind.TYPE,
            }
            kind = kind_map.get(kind_str, ConstraintKind.MAX)
            applies_to = _infer_semantic_selector(item, item.get("applies_to", "*.value"))
            severity = _infer_severity(item, best_section)
            
            # Build Constraint
            constraint = Constraint(
                id=item.get("id", f"C_{len(valid_constraints)}"),
                name=item.get("name", "Unnamed Constraint"),
                kind=kind,
                applies_to=applies_to,
                min_val=item.get("min_val"),
                max_val=item.get("max_val"),
                allowed_values=item.get("allowed_values"),
                pattern=item.get("pattern"),
                unit=item.get("unit", "unitless"),
                severity=severity,
                source=source_ref
            )
            valid_constraints.append(constraint)
            
        except ValidationError as ve:
            logger.warning(f"Skipping invalid extraction: {ve}")
            continue
        except Exception as e:
            logger.warning(f"Unexpected error building constraint: {e}")
            continue
    
    return valid_constraints


def detect_conflicts(constraints: List[Constraint]) -> List[dict]:
    """
    Detect conflicts between constraints targeting the same parameter.
    
    Conflicts occur when:
    - Two constraints target the same applies_to path
    - They have incompatible limits (e.g., different max values)
    - They come from different sources
    
    Returns:
        List of conflict records for constraint_conflicts.json
    """
    conflicts = []
    seen_pairs = set()
    
    # Group constraints by applies_to path
    by_target: dict[str, List[Constraint]] = {}
    for c in constraints:
        if c.applies_to not in by_target:
            by_target[c.applies_to] = []
        by_target[c.applies_to].append(c)
    
    # Check for conflicts within each group
    for target, group in by_target.items():
        if len(group) < 2:
            continue
            
        # Compare each pair
        for i, c1 in enumerate(group):
            for c2 in group[i+1:]:
                pair_key = tuple(sorted([c1.id, c2.id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                conflict = _check_pair_conflict(c1, c2, target)
                if conflict:
                    conflicts.append(conflict)
    
    return conflicts


def _check_pair_conflict(c1: Constraint, c2: Constraint, target: str) -> Optional[dict]:
    """Check if two constraints conflict."""
    conflict_type = None
    
    # MAX conflict: different max values
    if c1.kind == ConstraintKind.MAX and c2.kind == ConstraintKind.MAX:
        if not _is_close(c1.max_val, c2.max_val):
            conflict_type = "MAX_MISMATCH"
    
    # MIN conflict: different min values  
    elif c1.kind == ConstraintKind.MIN and c2.kind == ConstraintKind.MIN:
        if not _is_close(c1.min_val, c2.min_val):
            conflict_type = "MIN_MISMATCH"
    
    # RANGE conflict: different ranges
    elif c1.kind == ConstraintKind.RANGE and c2.kind == ConstraintKind.RANGE:
        if (not _is_close(c1.max_val, c2.max_val)) or (not _is_close(c1.min_val, c2.min_val)):
            conflict_type = "RANGE_MISMATCH"
    
    # ENUM conflict: different allowed values
    elif c1.kind == ConstraintKind.ENUM and c2.kind == ConstraintKind.ENUM:
        s1 = set(_normalize_allowed_values(c1.allowed_values))
        s2 = set(_normalize_allowed_values(c2.allowed_values))
        if s1 != s2:
            conflict_type = "ENUM_MISMATCH"

    # Cross-kind numeric infeasibility:
    # e.g. MAX=3.5 and RANGE=[3.7, 6.4] on same target.
    if not conflict_type:
        c1_min, c1_max = _constraint_bounds(c1)
        c2_min, c2_max = _constraint_bounds(c2)
        lower_candidates = [v for v in [c1_min, c2_min] if v is not None]
        upper_candidates = [v for v in [c1_max, c2_max] if v is not None]
        if lower_candidates and upper_candidates:
            lower = max(lower_candidates)
            upper = min(upper_candidates)
            if lower > upper + _NUMERIC_EPSILON:
                conflict_type = "INFEASIBLE_BOUNDS"
            
    if conflict_type:
        sec1 = getattr(c1.source, "section", None) if c1.source else None
        sec2 = getattr(c2.source, "section", None) if c2.source else None
        return {
            "target": target,
            "conflict_type": conflict_type,
            "constraint_1": {
                "id": c1.id,
                "value": _constraint_value_repr(c1),
                "source": c1.source.datasheet_name if c1.source else "unknown",
                "section": sec1,
                "priority": _source_section_priority(c1),
            },
            "constraint_2": {
                "id": c2.id,
                "value": _constraint_value_repr(c2),
                "source": c2.source.datasheet_name if c2.source else "unknown",
                "section": sec2,
                "priority": _source_section_priority(c2),
            },
            "resolution": "priority_then_conservative"
        }
    
    return None


def resolve_conflicts(constraints: List[Constraint], conflicts: List[dict]) -> List[Constraint]:
    """
    Resolve conflicts by choosing the most conservative value.
    
    Conservative strategy:
    - For MAX: choose the LOWER max value (safer)
    - For MIN: choose the HIGHER min value (safer)
    - For RANGE: choose the narrower range
    - For ENUM: choose the intersection of allowed values
    
    Returns:
        List of constraints with conflicts resolved
    """
    if not conflicts:
        return constraints

    by_id: Dict[str, Constraint] = {c.id: c for c in constraints}
    ids_to_remove = set()

    def _pick_by_priority(a: Constraint, b: Constraint) -> Tuple[Constraint, Constraint]:
        pa = _rule_priority_score(a)
        pb = _rule_priority_score(b)
        if pa > pb:
            return a, b
        if pb > pa:
            return b, a
        # Tie-breaker: keep deterministic order
        return (a, b) if a.id <= b.id else (b, a)

    for conflict in conflicts:
        c1_id = conflict["constraint_1"]["id"]
        c2_id = conflict["constraint_2"]["id"]
        c1 = by_id.get(c1_id)
        c2 = by_id.get(c2_id)
        if not c1 or not c2:
            continue
        if c1.id in ids_to_remove or c2.id in ids_to_remove:
            continue

        conflict_type = conflict["conflict_type"]
        keep, drop = _pick_by_priority(c1, c2)

        if conflict_type == "MAX_MISMATCH":
            # Conservative tie-break: lower max
            if c1.max_val is not None and c2.max_val is not None and _source_section_priority(c1) == _source_section_priority(c2):
                if c2.max_val < c1.max_val:
                    keep, drop = c2, c1

        elif conflict_type == "MIN_MISMATCH":
            # Conservative tie-break: higher min
            if c1.min_val is not None and c2.min_val is not None and _source_section_priority(c1) == _source_section_priority(c2):
                if c2.min_val > c1.min_val:
                    keep, drop = c2, c1

        elif conflict_type == "RANGE_MISMATCH":
            # Prefer intersection when possible for a deterministic safe range.
            c1_min = c1.min_val if c1.min_val is not None else float("-inf")
            c1_max = c1.max_val if c1.max_val is not None else float("inf")
            c2_min = c2.min_val if c2.min_val is not None else float("-inf")
            c2_max = c2.max_val if c2.max_val is not None else float("inf")
            new_min = max(c1_min, c2_min)
            new_max = min(c1_max, c2_max)
            if new_min <= new_max:
                keep.min_val = None if new_min == float("-inf") else float(new_min)
                keep.max_val = None if new_max == float("inf") else float(new_max)

        elif conflict_type == "ENUM_MISMATCH":
            s_keep = set(_normalize_allowed_values(keep.allowed_values))
            s_drop = set(_normalize_allowed_values(drop.allowed_values))
            inter = sorted(s_keep & s_drop)
            if inter:
                keep.allowed_values = inter

        elif conflict_type == "INFEASIBLE_BOUNDS":
            # Preserve whichever rule is more reliable by priority score,
            # and drop the other to restore satisfiable search space.
            # If both are RANGE, keep the narrower legal one if possible.
            if c1.kind == ConstraintKind.RANGE and c2.kind == ConstraintKind.RANGE:
                w1 = (c1.max_val or float("inf")) - (c1.min_val or float("-inf"))
                w2 = (c2.max_val or float("inf")) - (c2.min_val or float("-inf"))
                if w2 < w1:
                    keep, drop = c2, c1

        ids_to_remove.add(drop.id)

    resolved = [c for c in constraints if c.id not in ids_to_remove]
    logger.info("Resolved %s conflicts, removed %s constraints", len(conflicts), len(ids_to_remove))
    return resolved


def extract_and_resolve_conflicts(
    chunks: List[EvidenceChunk],
    llm_client: Any,
    device_name: str,
    datasheet_filename: str,
    output_dir: Optional[str] = None
) -> Tuple[ConstraintSet, List[dict]]:
    """
    Full extraction pipeline with conflict detection and resolution.
    
    This is the recommended entry point for Phase 3 integration.
    
    Args:
        chunks: Evidence chunks from RAG
        llm_client: LLM for extraction
        device_name: Device identifier
        datasheet_filename: Source file name
        output_dir: Optional directory to save constraint_conflicts.json
        
    Returns:
        Tuple of (resolved ConstraintSet, conflicts list)
    """
    # 1. Hybrid extraction (LLM + heuristics + evidence-level enum fallback)
    constraint_set = extract_constraints(
        chunks, llm_client, device_name, datasheet_filename
    )
    llm_constraints = list(constraint_set.constraints)
    evidence_text = "\n".join((c.text or "") for c in chunks)
    heuristic_constraints = extract_constraints_heuristic(evidence_text, datasheet_filename)
    enum_fallback_constraints = extract_enum_constraints_from_chunks(chunks, datasheet_filename)

    merged_constraints = llm_constraints + heuristic_constraints + enum_fallback_constraints
    raw_count = len(merged_constraints)
    constraint_set.constraints = _dedupe_constraints(merged_constraints)
    constraint_set.constraints = normalize_constraint_semantics(constraint_set.constraints)
    deduped_count = len(constraint_set.constraints)
    
    # 2. Detect conflicts
    conflicts = detect_conflicts(constraint_set.constraints)
    
    # 3. Save conflicts to file if output_dir provided
    if output_dir and conflicts:
        import os
        os.makedirs(output_dir, exist_ok=True)
        conflict_path = os.path.join(output_dir, "constraint_conflicts.json")
        with open(conflict_path, 'w', encoding='utf-8') as f:
            json.dump({
                "device": device_name,
                "raw_constraints": raw_count,
                "deduped_constraints": deduped_count,
                "total_conflicts": len(conflicts),
                "conflict_targets": len({c.get("target") for c in conflicts}),
                "conflicts": conflicts,
                "resolution_strategy": "priority_then_conservative"
            }, f, indent=2)
        logger.info(f"Saved {len(conflicts)} conflicts to {conflict_path}")
    
    # 4. Resolve conflicts
    if conflicts:
        resolved_constraints = resolve_conflicts(constraint_set.constraints, conflicts)
        constraint_set.constraints = resolved_constraints
        constraint_set.metadata["conflicts_resolved"] = len(conflicts)
        constraint_set.metadata["conflict_targets"] = len({c.get("target") for c in conflicts})
    constraint_set.metadata["llm_constraints_count"] = len(llm_constraints)
    constraint_set.metadata["heuristic_constraints_count"] = len(heuristic_constraints)
    constraint_set.metadata["enum_fallback_constraints_count"] = len(enum_fallback_constraints)
    constraint_set.metadata["raw_constraints_count"] = raw_count
    constraint_set.metadata["deduped_constraints_count"] = deduped_count
    constraint_set.metadata["final_constraints_count"] = len(constraint_set.constraints)
    
    return constraint_set, conflicts
