"""
Checks Engine for Benchmark Validation.

Executes validation checks against generated DSL outputs.
"""
import json
import os
from pathlib import Path
from typing import List, Dict, Any


def execute_checks(case: Dict[str, Any], mode: str, run_dir: str) -> List[Dict[str, Any]]:
    """
    Execute validation checks for a benchmark case.
    
    Args:
        case: Benchmark case definition with expected_checks
        mode: Run mode (baseline, dsl, verifier)
        run_dir: Directory containing run artifacts
        
    Returns:
        List of check results with ok/fail status
    """
    results = []
    run_path = Path(run_dir)
    
    # Try to load the generated DSL
    dsl_path = run_path / "dsl_validated.json"
    dsl_data = None
    
    if dsl_path.exists():
        try:
            with open(dsl_path, 'r') as f:
                dsl_data = json.load(f)
        except Exception as e:
            results.append({
                "type": "dsl_load",
                "ok": False,
                "message": f"Failed to load DSL: {e}"
            })
            return results
    else:
        # No DSL file - generation may have failed
        results.append({
            "type": "dsl_exists",
            "ok": False,
            "message": f"DSL file not found at {dsl_path}"
        })
        return results
    
    # Execute each expected check
    for check in case.get("expected_checks", []):
        check_type = check.get("type")
        result = {"type": check_type, "check": check}
        
        if check_type == "widget_type":
            # Check if widget type exists in DSL
            target_type = check.get("target")
            widgets = dsl_data.get("widgets", [])
            found = any(w.get("type") == target_type for w in widgets)
            result["ok"] = found
            result["message"] = f"Widget type '{target_type}' {'found' if found else 'not found'}"
            
        elif check_type == "param_compare":
            # Check parameter value against limit
            param = check.get("param")
            operator = check.get("operator")
            limit = check.get("value")
            severity = check.get("severity", "SOFT")
            
            # Search for parameter in widgets
            found_value = None
            for w in dsl_data.get("widgets", []):
                if param in ["max", "min", "value"]:
                    if param in w:
                        found_value = w[param]
                        break
                elif f"{param}_max" in str(w) or f"{param}_min" in str(w):
                    # Try to find parameter with prefix
                    found_value = w.get(param, w.get(f"{param}_max", w.get(f"{param}_min")))
                    break
            
            if found_value is not None:
                if operator == "<=":
                    result["ok"] = found_value <= limit
                elif operator == ">=":
                    result["ok"] = found_value >= limit
                elif operator == "<":
                    result["ok"] = found_value < limit
                elif operator == ">":
                    result["ok"] = found_value > limit
                elif operator == "==":
                    result["ok"] = found_value == limit
                else:
                    result["ok"] = False
                result["message"] = f"Param {param}={found_value} {operator} {limit}"
                result["severity"] = severity
            else:
                result["ok"] = True  # Param not found, assume compliant
                result["message"] = f"Param {param} not found in DSL"
                
        elif check_type == "binding_protocol":
            # Check if binding uses expected protocol
            target_protocol = check.get("target")
            widgets = dsl_data.get("widgets", [])
            found = False
            for w in widgets:
                binding = w.get("binding", {})
                if binding.get("protocol") == target_protocol:
                    found = True
                    break
            result["ok"] = found
            result["message"] = f"Protocol '{target_protocol}' {'found' if found else 'not found'}"
            
        elif check_type == "metrics":
            # Check metrics from verification report
            metrics_path = run_path / "metrics.json"
            if metrics_path.exists():
                with open(metrics_path) as f:
                    metrics = json.load(f)
                metric_name = check.get("metric")
                operator = check.get("operator")
                expected = check.get("value")
                actual = metrics.get(metric_name, 0)
                
                if operator == ">":
                    result["ok"] = actual > expected
                elif operator == ">=":
                    result["ok"] = actual >= expected
                elif operator == "<":
                    result["ok"] = actual < expected
                elif operator == "<=":
                    result["ok"] = actual <= expected
                elif operator == "==":
                    result["ok"] = actual == expected
                result["message"] = f"Metric {metric_name}={actual} {operator} {expected}"
            else:
                result["ok"] = False
                result["message"] = "Metrics file not found"
        else:
            result["ok"] = False
            result["message"] = f"Unknown check type: {check_type}"
            
        results.append(result)
    
    return results


def validate_dsl_schema(dsl_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Validate DSL against schema requirements.
    """
    errors = []
    
    # Required fields
    if "title" not in dsl_data:
        errors.append({"field": "title", "message": "Missing required field"})
    if "widgets" not in dsl_data:
        errors.append({"field": "widgets", "message": "Missing required field"})
    elif not isinstance(dsl_data["widgets"], list):
        errors.append({"field": "widgets", "message": "Must be a list"})
        
    return errors
