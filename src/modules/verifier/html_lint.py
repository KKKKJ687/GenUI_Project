"""
Static HTML Lint for Error Observability (Task 3.4).
Checks if the generated HTML contains mandatory error handling patterns.
"""

import re
from typing import Dict, Any, List

def lint_error_observability(html: str) -> Dict[str, Any]:
    """
    Checks the HTML for mandatory error handling components:
    1. reportError definition
    2. Try/Catch usage in scripts
    3. Error UI / Store usage
    """
    
    findings = []
    
    # Check 1: reportError definition
    if not re.search(r"window\.reportError\s*=", html):
        findings.append("Missing window.reportError definition")
        
    # Check 2: Try/Catch patterns
    # Heuristic: simple scan strings in script tags or inline alpine
    # We look for "try {" or "try{" to allow simple whitespace
    if not re.search(r"try\s*\{", html, re.IGNORECASE):
        findings.append("No try/catch blocks detected (expected for robust logic)")
        
    # Check 3: Alpine Error Store or Global Error Array
    # Look for Alpine.store('err'...) or $store.err usage or window.__errors
    has_store_def = re.search(r"Alpine\.store\(\s*['\"]err['\"]", html)
    has_store_usage = re.search(r"\$store\.err", html)
    has_global_errs = "window.__errors" in html
    
    if not (has_store_def or has_store_usage or has_global_errs):
        findings.append("Missing global error store/UI mechanism (Alpine.store('err') or window.__errors)")
        
    # Check 4: Error UI visibility (Toast/Container)
    # Heuristic: check for elements binding to the error store
    has_error_ui = False
    if has_store_usage or "x-show=\"$store.err" in html:
        has_error_ui = True
    elif "window.__errors" in html and "x-data" in html:
         # Lower confidence but maybe manual render?
         # Check for some "error" keyword in a visible container
         if re.search(r"class=.*fixed.*error", html, re.IGNORECASE):
             has_error_ui = True
             
    # Strict check: must have explicit UI binding loop or show
    if not has_error_ui:
        # Relaxed check: Look for "toast" or "error-container" ID/class + alpine loop
        if re.search(r"(id|class)=['\"].*(toast|error-container|alert).*(x-for|x-show)", html, re.IGNORECASE):
            pass 
        else:
             findings.append("No visible Error UI detected (Toast/Alert binding to error data)")

    return {
        "ok": len(findings) == 0,
        "errors": findings
    }

def lint_html(html: str) -> Dict[str, Any]:
    """
    Rule-based static lint for generated HTML.
    
    Checks for:
    - Placeholders (src="#", path/to/, etc.)
    - Missing Core Dependencies (Alpine, Tailwind)
    - Structural Integrity (<html>, <body>)
    - Broken Attributes (empty img src)
    
    Returns:
      {"ok": bool, "errors": [..], "warnings": [..]}
    """
    errors = []
    warnings = []
    
    if not html:
        return {"ok": False, "errors": ["Empty HTML content"], "warnings": []}
        
    lower_html = html.lower()
    
    # --- 1. Placeholders & Invalid Values ---
    # Common placeholder patterns
    placeholders = [
        (r'src=["\']#["\']', "Img/Script src cannot be '#'"),
        (r'href=["\']#["\']', "Link href cannot be '#' (unless pure anchor, check logic)"),
        (r'src=["\'].*/path/to/.*["\']', "Placeholder path detected: 'path/to/'"),
        (r'src=["\'].*example\.com.*["\']', "Placeholder domain detected: 'example.com'"),
        (r'src=["\'].*/your-image.*["\']', "Placeholder path detected: 'your-image'"),
    ]
    
    for pat, msg in placeholders:
        if re.search(pat, html, re.IGNORECASE):
            # Special case: href="#" is often used for JS actions, so maybe warn instead of error?
            # Requirement says "Placeholder src='#'...". Let's treat src='#' as error, href='#' as warning.
            if "href" in pat:
                warnings.append(f"Potential placeholder: {msg}")
            else:
                errors.append(f"Placeholder detected: {msg}")

    # --- 2. Structural Integrity ---
    if "<html" not in lower_html:
        errors.append("Missing <html> tag")
    if "<body" not in lower_html:
        errors.append("Missing <body> tag")
    if "<!doctype html" not in lower_html:
        warnings.append("Missing <!DOCTYPE html>")

    # --- 3. Missing Dependencies ---
    # Check for Tailwind
    if "tailwindcss" not in lower_html:
        errors.append("Missing Tailwind CSS dependency")
        
    # Check for Alpine.js
    if "alpinejs" not in lower_html:
        errors.append("Missing Alpine.js dependency")
        
    # --- 4. Tag Specific Checks ---
    # Images with empty src
    # re search for <img ... src="" ... >
    if re.search(r'<img\b[^>]*\bsrc=["\']["\']', html, re.IGNORECASE):
        errors.append("Image tag found with empty src attribute")

    # Audio/Video simple checks
    if "<video" in lower_html and "src=" not in lower_html:
         # Could be source tags inside, simplistic check
         pass

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }

