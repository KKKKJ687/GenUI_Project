from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pint

logger = logging.getLogger(__name__)
_ureg = pint.UnitRegistry()


def _extract_constraints_from_panel(panel: Any) -> Dict[str, Dict[str, Any]]:
    """Best-effort extraction of runtime limits from an HMIPanel-like object."""
    out: Dict[str, Dict[str, Any]] = {}
    widgets = getattr(panel, "widgets", None)
    if not widgets:
        return out

    for w in widgets:
        wid = getattr(w, "id", None)
        if not wid:
            continue
        safety = getattr(w, "safety", None)
        if safety:
            out[wid] = {
                "min": getattr(safety, "min_value", None),
                "max": getattr(safety, "max_value", None),
                "unit": getattr(safety, "unit", "unitless"),
                "source_ref": getattr(safety, "source_ref", None),
            }
            continue

        # Fallback to widget's own numeric range
        w_min = getattr(w, "min", None)
        w_max = getattr(w, "max", None)
        if w_min is not None or w_max is not None:
            out[wid] = {"min": w_min, "max": w_max, "unit": "unitless", "source_ref": None}
    return out


def _extract_constraints(context: Any) -> Dict[str, Dict[str, Any]]:
    # 1) object method
    if hasattr(context, "get_safety_constraints"):
        try:
            c = context.get_safety_constraints()
            if isinstance(c, dict):
                return c
        except Exception:
            pass

    # 2) dict style object used in tests
    if isinstance(context, dict):
        fn = context.get("get_safety_constraints")
        if callable(fn):
            try:
                c = fn()
                if isinstance(c, dict):
                    return c
            except Exception:
                pass
        return context

    # 3) HMIPanel fallback extraction
    return _extract_constraints_from_panel(context)


class RuntimeGuard:
    """Stateful safety guard that enforces constraints on runtime commands."""

    def __init__(self, context: Any):
        self.context = context
        self.constraints = _extract_constraints(context)

    def validate_command(self, command: Dict[str, Any]) -> bool:
        return bool(self.inspect_command(command).get("allowed"))

    def inspect_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        return evaluate_command(command, self.constraints)


def check_command_safe(command_val: float, command_unit: str, constraint: Dict[str, Any]) -> bool:
    """Check value against min/max with unit conversion when possible."""
    limit_max = constraint.get("max")
    limit_min = constraint.get("min")
    constraint_unit = constraint.get("unit", "unitless")

    # Pure numeric path
    if command_unit in (None, "", "unitless") and constraint_unit in (None, "", "unitless"):
        if limit_max is not None and command_val > float(limit_max):
            return False
        if limit_min is not None and command_val < float(limit_min):
            return False
        return True

    try:
        cmd_q = _ureg.Quantity(command_val, command_unit).to_base_units()
        if limit_max is not None:
            q_max = _ureg.Quantity(limit_max, constraint_unit).to_base_units()
            if cmd_q > q_max:
                return False
        if limit_min is not None:
            q_min = _ureg.Quantity(limit_min, constraint_unit).to_base_units()
            if cmd_q < q_min:
                return False
        return True
    except Exception as e:
        logger.warning("Unit conversion check failed: %s", e)
        return False


def _evaluate_against_constraint(
    *,
    command: Dict[str, Any],
    target_id: str,
    value: float,
    unit: str,
    constraint: Dict[str, Any],
) -> Dict[str, Any]:
    safe = check_command_safe(value, unit, constraint)
    if safe:
        return {
            "allowed": True,
            "action": "ALLOW",
            "target": target_id,
            "value": value,
            "unit": unit,
            "reason": "within_range",
            "source_ref": constraint.get("source_ref"),
            "constraint": {
                "min": constraint.get("min"),
                "max": constraint.get("max"),
                "unit": constraint.get("unit", "unitless"),
            },
        }
    return {
        "allowed": False,
        "action": "REJECT",
        "target": target_id,
        "value": value,
        "unit": unit,
        "reason": "out_of_range",
        "source_ref": constraint.get("source_ref"),
        "constraint": {
            "min": constraint.get("min"),
            "max": constraint.get("max"),
            "unit": constraint.get("unit", "unitless"),
        },
    }


def _extract_value_and_unit(command: Dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    val = command.get("value")
    unit = command.get("unit")

    payload = command.get("payload")
    if isinstance(payload, dict):
        if val is None:
            val = payload.get("value")
        if unit is None:
            unit = payload.get("unit")

    if val is None:
        return None, unit
    try:
        return float(val), unit
    except Exception:
        return None, unit


def evaluate_command(command: Dict[str, Any], constraints: Dict[str, Any]) -> Dict[str, Any]:
    """
    Structured runtime decision with explanation.
    """
    action = command.get("action")
    if action is not None and action not in {"click", "update", "set_value"}:
        return {
            "allowed": False,
            "action": "REJECT",
            "target": command.get("widget_id") or command.get("target"),
            "reason": f"unsupported_action:{action}",
            "source_ref": None,
        }

    target_id = command.get("widget_id") or command.get("target")
    value, unit = _extract_value_and_unit(command)
    if target_id and target_id in constraints and value is not None:
        return _evaluate_against_constraint(
            command=command,
            target_id=target_id,
            value=value,
            unit=unit or "unitless",
            constraint=constraints[target_id],
        )

    # Key/value command style, e.g. {"voltage": 6}
    for key, cons in constraints.items():
        if key not in command or not isinstance(cons, dict):
            continue
        try:
            v = float(command[key])
        except Exception:
            continue
        return _evaluate_against_constraint(
            command=command,
            target_id=key,
            value=v,
            unit="unitless",
            constraint=cons,
        )

    # No relevant constraints found: allow by policy, but keep explicit reason.
    return {
        "allowed": True,
        "action": "ALLOW",
        "target": target_id,
        "reason": "no_matching_constraint",
        "source_ref": None,
    }


def check_command(command: Dict[str, Any], constraints: Dict[str, Any]) -> bool:
    return bool(evaluate_command(command, constraints).get("allowed"))


def apply_guard(panel: Any, command: Dict[str, Any]):
    guard = RuntimeGuard(panel)
    if guard.validate_command(command):
        return command
    reject_invalid_command(command)
    return None


def reject_invalid_command(command: Dict[str, Any]) -> bool:
    logger.warning("Command rejected by runtime guard: %s", command)
    return False
