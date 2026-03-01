"""
Phase 2: Verification Reporting
Defines the output of the Verifier engine.

This module is crucial for generating metrics for the IEEE paper.
All statistics (修复率, 违规分布, etc.) are derived from these models.

Key Concepts:
  - Violation: A specific instance of a constraint being broken
  - FixAction: Record of a self-correction applied by the system
  - VerificationReport: Complete audit log for a generation run
"""
import json
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# Import from constraints module
# Import from constraints module
# Removed try-except to prevent silent fallback to incompatible classes
from src.modules.verifier.constraints import SourceRef, Severity


# ==========================================
# Fix Actions
# ==========================================

class FixActionType(str, Enum):
    """Types of automatic corrections the system can apply."""
    CLAMP = "CLAMP"         # Value coerced to limit (e.g., 5.0 -> 3.3)
    REJECT = "REJECT"       # Widget removed or generation failed
    EDIT = "EDIT"           # Non-numeric fix (e.g., changing protocol string)
    WARN = "WARN"           # No fix applied, warning added
    NONE = "NONE"           # No fix possible / User confirmation needed


class Violation(BaseModel):
    """
    A specific instance of a rule being broken.
    
    Example:
      A slider widget with max=5.0V when the datasheet specifies max=3.3V
    """
    rule_id: str = Field(..., description="ID of the violated Constraint")
    param_path: str = Field(..., description="Path to the offending parameter (e.g., 'slider_01.max')")
    
    observed_value: Any = Field(..., description="What the LLM generated")
    expected_limit: Any = Field(..., description="What the datasheet allowed")
    unit: str = Field("unitless", description="Physical unit")
    
    severity: Severity = Field(Severity.HARD)
    message: str = Field(..., description="Human readable error message")
    source_ref: Optional[SourceRef] = Field(None, description="Link back to datasheet")


class FixAction(BaseModel):
    """
    Record of a self-correction applied by the system.
    
    This is the audit trail that proves the system is "self-healing".
    """
    fix_id: str = Field(..., description="Unique ID for this fix event")
    violation_rule_id: str = Field(..., description="Which rule violation triggered this fix")
    action_type: FixActionType = Field(..., description="Type of fix applied")
    
    param_path: str = Field(..., description="Path to the corrected parameter")
    value_before: Any = Field(..., description="Original (unsafe) value")
    value_after: Any = Field(..., description="Corrected (safe) value")
    
    reason: str = Field(..., description="Why this fix was applied")
    diff_note: Optional[str] = Field(None, description="Additional context for the change")


# ==========================================
# The Final Report (Paper Artifact)
# ==========================================

class VerificationReport(BaseModel):
    """
    The complete audit log for a single generation run.
    Contains all violations found and all fixes applied.
    
    This is the primary data source for IEEE paper metrics:
      - Violation rate per generation
      - Fix success rate
      - Distribution of violation types
      - Residual risk analysis
    """
    passed: bool = Field(..., description="Is the final DSL safe to render?")
    score: float = Field(100.0, ge=0.0, le=100.0, description="Safety score (0-100)")
    
    violations: List[Violation] = Field(default_factory=list)
    fixes: List[FixAction] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list, description="Unfixable warnings")
    
    # Statistical Summary for Paper
    stats: Dict[str, int] = Field(default_factory=lambda: {
        "total_checks": 0,
        "violations_found": 0,
        "fixes_applied": 0,
        "critical_errors": 0,
        "warnings": 0
    })

    def save_to_json(self, path: str) -> None:
        """Persist report to JSON file."""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.model_dump_json(indent=2))
    
    @classmethod
    def load_from_json(cls, path: str) -> 'VerificationReport':
        """Load report from JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.model_validate(data)

    def summary_table(self) -> str:
        """
        Returns a markdown table string for logging.
        Useful for terminal output and documentation.
        """
        if not self.violations:
            return "✅ No Violations Found."
        
        rows = [
            "| Component | Rule | Observed | Limit | Unit | Action |",
            "|-----------|------|----------|-------|------|--------|"
        ]
        
        for v in self.violations:
            # Find corresponding fix if any
            fix = next((f for f in self.fixes if f.violation_rule_id == v.rule_id), None)
            if fix:
                action = f"{fix.action_type.value} → {fix.value_after}"
            else:
                action = "⚠️ PENDING"
            
            rows.append(
                f"| {v.param_path} | {v.rule_id} | {v.observed_value} | {v.expected_limit} | {v.unit} | {action} |"
            )
        
        return "\n".join(rows)
    
    def compute_stats(self) -> Dict[str, int]:
        """Recompute statistics from violations and fixes."""
        hard_violations = [v for v in self.violations if v.severity == Severity.HARD]
        soft_violations = [v for v in self.violations if v.severity == Severity.SOFT]
        
        self.stats = {
            "total_checks": self.stats.get("total_checks", 0),
            "violations_found": len(self.violations),
            "fixes_applied": len(self.fixes),
            "critical_errors": len(hard_violations) - len([f for f in self.fixes if f.violation_rule_id in {v.rule_id for v in hard_violations}]),
            "warnings": len(soft_violations)
        }
        return self.stats
    
    def is_safe(self) -> bool:
        """Check if the output is safe to render (no unresolved HARD violations)."""
        hard_violation_ids = {v.rule_id for v in self.violations if v.severity == Severity.HARD}
        fixed_violation_ids = {f.violation_rule_id for f in self.fixes}
        unresolved = hard_violation_ids - fixed_violation_ids
        return len(unresolved) == 0
