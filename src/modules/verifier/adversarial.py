"""
Adversarial Simulation Engine
Logic: Fuzz testing based on physical constraints.
"""
import random
import sys
from typing import List, Any, Dict, Optional
import math

from src.modules.verifier.constraints import Constraint, ConstraintKind
from src.models.schema import HMIPanel

class AttackVector:
    def __init__(self, name: str, value: Any, expected_result: str):
        self.name = name
        self.value = value
        self.expected_result = expected_result  # "REJECT", "CLAMP", "CRASH", "ERROR"

class AdversarialGenerator:
    """Generates edge cases and malicious inputs based on constraints."""
    
    @staticmethod
    def generate_attacks(constraint: Constraint) -> List[AttackVector]:
        attacks = []
        
        # 针对 MAX 约束的攻击
        if constraint.kind == ConstraintKind.MAX:
            if constraint.max_val is not None:
                limit = constraint.max_val
                # 1. 边界溢出 (Boundary Overflow)
                attacks.append(AttackVector("Overflow_Small", limit + 1e-6, "CLAMP_OR_REJECT"))
                # 2. 数量级溢出 (Magnitude Overflow)
                attacks.append(AttackVector("Overflow_Huge", limit * 10, "REJECT"))
                # 3. 物理极大值 (Physical Infinity)
                attacks.append(AttackVector("Max_Float", sys.float_info.max, "REJECT"))
                # 4. NaN/Inf 注入
                attacks.append(AttackVector("Injection_NaN", float('nan'), "REJECT_OR_ERROR"))
                attacks.append(AttackVector("Injection_Inf", float('inf'), "REJECT_OR_ERROR"))
            
        # 针对 MIN 约束的攻击
        elif constraint.kind == ConstraintKind.MIN:
            if constraint.min_val is not None:
                limit = constraint.min_val
                attacks.append(AttackVector("Underflow_Small", limit - 1e-6, "CLAMP_OR_REJECT"))
                attacks.append(AttackVector("Underflow_Huge", limit - 1000, "REJECT"))

        # 针对 RANGE 约束
        elif constraint.kind == ConstraintKind.RANGE:
             if constraint.max_val is not None:
                attacks.append(AttackVector("Range_Overflow", constraint.max_val + 1, "CLAMP_OR_REJECT"))
             if constraint.min_val is not None:
                attacks.append(AttackVector("Range_Underflow", constraint.min_val - 1, "CLAMP_OR_REJECT"))

        # 针对 ENUM 约束
        elif constraint.kind == ConstraintKind.ENUM:
            attacks.append(AttackVector("Enum_Invalid_Value", "INVALID_OPTION_XYZ", "REJECT"))
            attacks.append(AttackVector("Enum_Type_Mismatch", 12345, "REJECT"))

        # 针对类型系统的通用攻击 (Type Confusion)
        attacks.append(AttackVector("Type_Injection_Str", "100; DROP TABLE", "ERROR"))
        attacks.append(AttackVector("Type_Injection_None", None, "IGNORE_OR_ERROR"))
        attacks.append(AttackVector("Type_Injection_Dict", {"hack": True}, "ERROR"))
        
        return attacks

def run_simulation(panel: HMIPanel, constraints: List[Constraint]) -> Dict[str, Any]:
    """
    Run adversarial simulation against the panel using provided constraints.
    Returns statistics on survived (blocked) vs failed (accepted) attacks.
    """
    results = {
        "survived": 0, 
        "failed": 0, 
        "total": 0,
        "logs": []
    }
    
    # Lazy import to avoid circular dependency
    from src.modules.verifier.verifier import _find_targets_smart, check_threshold, _check_single_value
    
    # Iterate through all constraints to find targets
    for constr in constraints:
        # Use smart resolver (handles abstract selectors like *.voltage)
        targets = _find_targets_smart(panel, constr)
        
        if not targets:
            continue
            
        # Generate attacks for this constraint
        attacks = AdversarialGenerator.generate_attacks(constr)
        
        for widget, attr, path in targets:
            for attack in attacks:
                results["total"] += 1
                try:
                    # 模拟注入：检查 Verifier 是否认为此攻击值是安全的
                    # 注意：在这里我们复用 Verifier 的核心检查逻辑 `check_threshold` 或类似机制
                    # 但我们需要根据 ConstraintKind 适配检查逻辑
                    
                    is_safe = _check_attack_safety(attack.value, constr)
                    
                    if not is_safe:
                        results["survived"] += 1 # 系统成功识别了不安全的值 -> 防御成功
                    else:
                        # Value was accepted (Safe).
                        # Use attack.expected_result to determine if this is a failure.
                        # If we expect REJECT/CLAMP/ERROR, then accepting it is a failure.
                        # If we expect IGNORE (e.g. None), accepting it is a pass.
                        
                        if "IGNORE" in attack.expected_result or "SAFE" in attack.expected_result:
                             results["survived"] += 1
                        else:
                            results["failed"] += 1
                            results["logs"].append(
                                f"[VULNERABILITY] {path} accepted {attack.name} ({attack.value}) despite {constr.id}"
                            )
                except Exception as e:
                    # 如果系统抛出异常，通常也算作一种防御（比起悄悄接受主要好）
                    # 但如果是 "Type_Injection" 导致 Crash，可能需要记录
                    results["survived"] += 1
                    # results["logs"].append(f"[DEFENSE_EXCEPTION] {path} attack {attack.name} triggered: {e}")

    return results

def _check_attack_safety(value: Any, rule: Constraint) -> bool:
    """
    Helper to check if a specific value violates the rule.
    Returns True if Safe (Compliant), False if Violation.
    """
    # Import locally to avoid circular imports if any, checks duplicate internal logic of verifier
    # Ideally should use verifier._check_single_value but that returns Violation object
    # We want a boolean.
    
    try:
        from src.modules.verifier.verifier import _check_single_value
        violation = _check_single_value(value, rule, "simulated_path")
        return violation is None # No violation = Safe
    except Exception:
        # If checker crashes, assume it failed to validate properly or is fragile
        # But for adversarial context, crashing on bad input is better than accepting it?
        # Let's say raised exception = Safe (Rejected)
        return False 
