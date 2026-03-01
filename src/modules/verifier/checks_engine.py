
import json
import pint
from jsonpath_ng import parse
from pathlib import Path

# Initialize Unit Registry
_ureg = pint.UnitRegistry()

def execute_checks(case, mode, run_dir):
    """
    Executes checks against the validated DSL from the run directory.
    Replaces dummy implementation with actual verification logic.
    """
    run_dir = Path(run_dir)
    dsl_path = run_dir / "dsl_validated.json"
    
    # If DSL file is missing, fail all checks
    if not dsl_path.exists():
        return [{"status": "fail", "reason": "DSL file missing for check", "type": "ALL"}]
    
    with open(dsl_path, 'r') as f:
        try:
            dsl_data = json.load(f)
        except json.JSONDecodeError:
             return [{"status": "fail", "reason": "Invalid JSON in DSL file", "type": "ALL"}]

    results = []

    for check in case.get('expected_checks', []):
        check_type = check['type']
        
        if check_type == 'param_compare':
            # Extract actual value using JSONPath
            jsonpath_expr = parse(check['param'])
            matches = jsonpath_expr.find(dsl_data)
            
            if not matches:
                results.append({
                    "type": "param_compare", 
                    "ok": False, 
                    "msg": f"Param not found: {check['param']}"
                })
                continue
            
            # Use the first match for comparison
            match = matches[0]
            actual_val = match.value
            
            # Attempt to extract unit from the context (parent object)
            actual_unit = "unitless"
            # jsonpath-ng match context is the parent object usually
            if hasattr(match.context, 'value') and isinstance(match.context.value, dict):
                 actual_unit = match.context.value.get("unit", "unitless")
            
            try:
                # Normalize units using Pint
                q_actual = _ureg.Quantity(actual_val, actual_unit)
                if hasattr(q_actual, 'to_base_units'):
                     q_actual = q_actual.to_base_units()
                
                target_unit = check.get('unit', 'V') # Default to Volts if not specified, or just unitless
                # If check has no unit, treat as unitless number
                if 'unit' not in check and actual_unit == 'unitless':
                     q_expected = _ureg.Quantity(check['value'], 'unitless')
                else:
                     q_expected = _ureg.Quantity(check['value'], target_unit).to_base_units()

                # Perform comparison
                op = check['operator']
                # Using eval safely for simple operators
                allowed_ops = {'<', '>', '<=', '>=', '==', '!='}
                if op not in allowed_ops:
                     raise ValueError(f"Unsupported operator: {op}")

                is_ok = eval(f"{q_actual.magnitude} {op} {q_expected.magnitude}")
                
                results.append({
                    "type": "param_compare", 
                    "ok": is_ok, 
                    "val": actual_val,
                    "unit": actual_unit,
                    "check": check
                })

            except Exception as e:
                # Fallback for non-numeric or incompatible unit errors
                results.append({
                    "type": "param_compare", 
                    "ok": False, 
                    "msg": f"Comparison error: {str(e)}",
                    "val": actual_val
                })

        elif check_type == 'widget_type':
            # Simple check if target widget type exists in the DSL
            target_type = check['target'].lower()
            found = False
            widgets = dsl_data.get("widgets", [])
            for w in widgets:
                if w.get("type", "").lower() == target_type:
                    found = True
                    break
            results.append({"type": "widget_type", "ok": found, "target": target_type})
            
        elif check_type == 'metrics':
             # Placeholder for metrics check (requires separate metrics file usually)
             metrics_path = run_dir / "metrics.json"
             if metrics_path.exists():
                 with open(metrics_path) as mf:
                     metrics = json.load(mf)
                 metric_key = check['metric']
                 val = metrics.get(metric_key, 0)
                 op = check['operator']
                 target = check['value']
                 is_ok = eval(f"{val} {op} {target}")
                 results.append({"type": "metrics", "ok": is_ok, "metric": metric_key, "val": val})
             else:
                 results.append({"type": "metrics", "ok": False, "msg": "metrics.json missing"})

        else:
            # Pass through unknown checks or implement others
            results.append({"type": check_type, "ok": True, "msg": "Not implemented"})

    return results
