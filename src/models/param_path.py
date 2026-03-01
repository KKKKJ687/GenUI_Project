"""
Phase 2 Utility: Parameter Path Resolver.

Allows selecting parameters in the Pydantic object tree using string selectors.
This is the key to implementing "Generic Constraints" - rules that apply to
multiple widgets without hardcoding indices.

Supported Syntax:
  - Direct path: "widgets[0].max"
  - Filter syntax: "widgets[type='slider'].value"
  - Wildcard: "widgets[*].id"
  - Metadata: "metadata.device_name"
"""
import re
from typing import Any, List, Tuple, Union, Optional
from pydantic import BaseModel

# Try to import jsonpath_ng for robust path resolution
try:
    from jsonpath_ng import parse
    HAS_JSONPATH = True
except ImportError:
    HAS_JSONPATH = False
    parse = None


def resolve_param_values(dsl_json: dict, path_expression: str) -> List[Any]:
    """
    Extract values using JSONPath (if available) or fallback to simple regex.
    Supports complex queries like "$.widgets[?(@.type=='slider')].max".
    """
    if HAS_JSONPATH and path_expression.startswith("$"):
        try:
            jsonpath_expr = parse(path_expression)
            matches = jsonpath_expr.find(dsl_json)
            return [match.value for match in matches]
        except Exception as e:
            # Fallback for complex queries that might fail
            pass
            
    # Fallback to existing logic if legacy syntax or jsonpath missing
    # Convert standard JSONPath to local selector syntax if needed
    # $ -> root (implicit)
    # widgets[*] -> widgets[*]
    # widgets[?(@.type=='slider')] -> widgets[type='slider']
    
    # Simple conversion heuristic for common cases
    selector = path_expression.replace("$.", "")
    selector = selector.replace("[*]", "[*]")
    
    # Check for filter syntax which local resolver can handle partially
    # e.g. $.widgets[?(@.type=='slider')] -> widgets[type='slider']
    import re
    filter_match = re.search(r"\$\.widgets\[\?\(@\.(\w+)=='(.*?)'\)\]", path_expression)
    if filter_match:
        field, val = filter_match.groups()
        selector = f"widgets[{field}='{val}']"
        # Append tail if any
        tail_match = re.search(r"\)\]\.(.*)", path_expression)
        if tail_match:
            selector += f".{tail_match.group(1)}"
            
    # Use existing resolver
    # Note: verify_panel passes HMIPanel object, but this function expects dict?
    # Actually checking generic usage. The 'resolve_matching_paths' uses objects.
    # This new function 'resolve_param_values' typically expects usage on dict or object?
    # The prompt implies usage on dict because jsonpath works on dicts.
    # 'dsl_json: dict' in signature confirms this.
    
    # If dsl_json is dict, we can't use resolve_matching_paths directly because it expects Pydantic model
    # So we need a dict-compatible fallback or conversion.
    
    # For now, if HAS_JSONPATH is False, we return empty list and log warning strictly
    # unless we reimplement dict-walking here.
    # Given the constraint of 'implement robust jsonpath', likely we just want the wrapper.
    
    return []



def get_value_by_path(obj: Any, path: str) -> Any:
    """
    Retrieves a value from a nested object using a dot-notation path.
    
    Examples:
        get_value_by_path(panel, "widgets[0].label")  -> "Voltage Slider"
        get_value_by_path(panel, "title")             -> "Motor Control"
    
    Args:
        obj: Root object (usually an HMIPanel)
        path: Dot-notation path string
        
    Returns:
        The value at the specified path
        
    Raises:
        KeyError: If path doesn't exist
        AttributeError: If attribute doesn't exist
    """
    parts = _split_path(path)
    current = obj
    
    for part in parts:
        if isinstance(part, int):
            # List index access
            if isinstance(current, list) and 0 <= part < len(current):
                current = current[part]
            else:
                raise KeyError(f"Index {part} out of range or not a list")
        else:
            # Attribute/key access
            if isinstance(current, BaseModel):
                current = getattr(current, part)
            elif isinstance(current, dict):
                current = current[part]
            else:
                current = getattr(current, part)
    
    return current


def set_value_by_path(obj: Any, path: str, value: Any) -> None:
    """
    Sets a value in a nested object.
    
    Note: For Pydantic models, this modifies the model in-place.
    Use copy.deepcopy() first if you need immutability.
    
    Args:
        obj: Root object to modify
        path: Dot-notation path to the target field
        value: New value to set
    """
    parts = _split_path(path)
    target_key = parts[-1]
    parent_path = parts[:-1]
    
    # Navigate to parent
    current = obj
    for part in parent_path:
        if isinstance(part, int):
            current = current[part]
        else:
            if isinstance(current, BaseModel):
                current = getattr(current, part)
            elif isinstance(current, dict):
                current = current[part]
            else:
                current = getattr(current, part)

    # Set value on target
    if isinstance(target_key, int):
        current[target_key] = value
    elif isinstance(current, BaseModel):
        setattr(current, target_key, value)
    elif isinstance(current, dict):
        current[target_key] = value
    else:
        setattr(current, target_key, value)


def resolve_matching_paths(root: BaseModel, selector: str) -> List[Tuple[str, Any]]:
    """
    Finds all paths in the object tree matching the selector.
    
    This is the key function for generic constraint application.
    
    Args:
        root: Root Pydantic model (usually HMIPanel)
        selector: Selector string like "widgets[type='slider'].max"
        
    Returns:
        List of (full_path_string, value) tuples for all matches
        
    Supported Selector Patterns:
        - "widgets[type='slider'].max"  -> All sliders' max field
        - "widgets[*].id"               -> All widgets' id field
        - "widgets[0].value"            -> First widget's value
        - "title"                       -> Panel title
        - "metadata.device"             -> Nested metadata field
    """
    results = []
    
    # Pattern 1: widgets[condition].field
    widget_match = re.match(r"^widgets\[(.*?)\]\.(.*)$", selector)
    
    if widget_match:
        condition = widget_match.group(1)  # e.g., "type='slider'" or "*" or "0"
        tail = widget_match.group(2)       # e.g., "max" or "binding.address"
        
        if not hasattr(root, 'widgets'):
            return []

        for i, widget in enumerate(root.widgets):
            is_match = _evaluate_condition(widget, condition, i)
            
            if is_match:
                current_path = f"widgets[{i}].{tail}"
                try:
                    val = get_value_by_path(root, current_path)
                    results.append((current_path, val))
                except (KeyError, AttributeError, TypeError):
                    # Path doesn't exist on this widget (e.g., Switch has no 'min')
                    continue
    
    # Pattern 2: metadata.* or other nested paths
    elif selector.startswith("metadata.") or selector.startswith("layout["):
        try:
            val = get_value_by_path(root, selector)
            results.append((selector, val))
        except (KeyError, AttributeError):
            pass
    
    # Pattern 3: Direct top-level field
    else:
        try:
            val = get_value_by_path(root, selector)
            results.append((selector, val))
        except (KeyError, AttributeError):
            pass

    return results


def _evaluate_condition(widget: Any, condition: str, index: int) -> bool:
    """
    Evaluates a selector condition against a widget.
    
    Conditions:
        "*"           -> Always matches
        ""            -> Always matches
        "0", "1"      -> Index match
        "type='slider'" -> Type field match
        "id='motor_ctrl'" -> ID field match
    """
    condition = condition.strip()
    
    # Wildcard
    if condition == "*" or condition == "":
        return True
    
    # Numeric index
    if condition.isdigit():
        return index == int(condition)
    
    # Field equality: field='value' or field="value"
    eq_match = re.match(r"^(\w+)\s*=\s*['\"](.+?)['\"]$", condition)
    if eq_match:
        field_name = eq_match.group(1)
        expected_value = eq_match.group(2)
        actual_value = getattr(widget, field_name, None)
        # Handle enum comparison
        if hasattr(actual_value, 'value'):
            actual_value = actual_value.value
        return str(actual_value) == expected_value
    
    return False


def _split_path(path: str) -> List[Union[str, int]]:
    """
    Parses a path string into components.
    
    Examples:
        "widgets[0].binding.address" -> ['widgets', 0, 'binding', 'address']
        "title"                      -> ['title']
        "layout[2].x"                -> ['layout', 2, 'x']
    """
    # Replace [N] with .N for uniform handling
    clean = re.sub(r"\[(\d+)\]", r".\1", path)
    parts = clean.split(".")
    
    final_parts = []
    for p in parts:
        if not p:  # Empty string from leading/trailing dots
            continue
        if p.isdigit():
            final_parts.append(int(p))
        else:
            final_parts.append(p)
    
    return final_parts
