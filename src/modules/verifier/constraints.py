"""
Phase 2: Physical Constraint Models
Defines the structure of hardware constraints extracted from datasheets.
Acts as the 'Ground Truth' for the Verifier.

Key Concepts:
  - Constraint: A single physical rule (e.g., "Input Voltage must not exceed 3.3V")
  - ConstraintSet: Collection of constraints for a hardware device
  - SourceRef: Traceability back to original datasheet PDF
"""
import json
from enum import Enum
from typing import List, Optional, Union, Any, Dict
from pydantic import BaseModel, Field, model_validator, ConfigDict


# ==========================================
# Enums
# ==========================================

class ConstraintKind(str, Enum):
    """Type of constraint logic."""
    RANGE = "range"       # min <= x <= max
    MAX = "max"           # x <= max
    MIN = "min"           # x >= min
    ENUM = "enum"         # x in [a, b, c]
    REGEX = "regex"       # re.match(pattern, x)
    TYPE = "type"         # isinstance(x, type)


class Severity(str, Enum):
    """Constraint violation severity level."""
    HARD = "HARD"         # Must fix (e.g., hardware damage risk)
    SOFT = "SOFT"         # Warning (e.g., recommended operating condition)


# ==========================================
# Evidence Tracking (Vital for "Hallucination Mitigation")
# ==========================================

class SourceRef(BaseModel):
    """
    Traceability back to the original Datasheet PDF.
    Used for the 'Explanation' column in paper tables.
    
    This is critical for academic credibility - every constraint
    must be traceable to its source document.
    """
    datasheet_name: str = Field(..., description="Filename of the source PDF")
    page: Optional[int] = Field(None, description="Page number")
    section: Optional[str] = Field(None, description="Section header or Table name")
    snippet: Optional[str] = Field(None, description="Raw text excerpt from the PDF")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Extraction confidence score")


# ==========================================
# Core Constraint Definition
# ==========================================
class Constraint(BaseModel):
    """
    A single physical rule derived from a datasheet.
    
    Examples:
      - "Input Voltage must not exceed 3.3V"
      - "PWM frequency must be between 1kHz and 100kHz"
      - "Protocol must be one of: SPI, I2C, UART"
    """
    id: str = Field(..., description="Unique rule ID (e.g., 'C001_VCC_MAX')")
    name: str = Field(..., description="Human readable name")
    description: Optional[str] = None
    
    kind: ConstraintKind = Field(..., description="Logic type of the constraint")
    
    # Target: Where does this apply? (JSON Pointer-like or simple logic)
    # Example: "widgets[type='slider'].value" or "global.protocol.port"
    applies_to: str = Field(..., description="Selector string identifying target parameters")
    
    # Thresholds / Rules (Polymorphic based on kind)
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    allowed_values: Optional[List[Any]] = None
    pattern: Optional[str] = None
    
    unit: str = Field("unitless", description="Physical unit for error messages")
    severity: Severity = Field(Severity.HARD)
    
    source: Optional[SourceRef] = Field(None, description="Where did this rule come from?")
    
    model_config = ConfigDict(extra="ignore")  # Robustness for RAG noise

    @model_validator(mode='before')
    @classmethod
    def normalize_legacy_enum_field(cls, data):
        """
        Backward compatibility:
        Some extraction outputs use `enum_values` instead of `allowed_values`.
        """
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if (
            normalized.get("allowed_values") is None
            and isinstance(normalized.get("enum_values"), list)
        ):
            normalized["allowed_values"] = normalized.get("enum_values")
        return normalized

    @model_validator(mode='after')
    def validate_logic(self):
        """Self-consistency check based on constraint kind."""
        if self.kind == ConstraintKind.RANGE:
            if self.min_val is None or self.max_val is None:
                raise ValueError("Range constraint requires both min_val and max_val")
            if self.min_val > self.max_val:
                raise ValueError(f"min_val ({self.min_val}) cannot be greater than max_val ({self.max_val})")
        if self.kind == ConstraintKind.MAX and self.max_val is None:
            raise ValueError("Max constraint requires max_val")
        if self.kind == ConstraintKind.MIN and self.min_val is None:
            raise ValueError("Min constraint requires min_val")
        if self.kind == ConstraintKind.ENUM and not self.allowed_values:
            raise ValueError("Enum constraint requires allowed_values list")
        if self.kind == ConstraintKind.REGEX and not self.pattern:
            raise ValueError("Regex constraint requires pattern string")
        return self


class ConstraintSet(BaseModel):
    """
    A collection of constraints for a specific hardware device.
    
    This replaces ad-hoc 'system prompt' logic with structured,
    verifiable rules that can be:
      - Extracted from PDFs via RAG
      - Validated by the Verifier engine
      - Audited for correctness
    """
    device_name: str = Field(..., description="Name of the hardware device")
    version: str = Field("1.0", description="Constraint set version")
    constraints: List[Constraint] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def save_to_json(self, path: str) -> None:
        """Persist constraint set to JSON file."""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_from_json(cls, path: str) -> 'ConstraintSet':
        """Load constraint set from JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.model_validate(data)
    
    def get_constraint_by_id(self, rule_id: str) -> Optional[Constraint]:
        """Lookup a constraint by its ID."""
        return next((c for c in self.constraints if c.id == rule_id), None)
    
    def get_constraints_for_target(self, target_path: str) -> List[Constraint]:
        """Find all constraints that apply to a given parameter path."""
        # Simple substring match for now; can be enhanced with JSONPath logic
        return [c for c in self.constraints if target_path in c.applies_to or c.applies_to in target_path]
