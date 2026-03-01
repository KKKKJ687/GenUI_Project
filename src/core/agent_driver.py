
import json
import logging
import time
import re
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

# 引入项目现有模块
from src.models.schema import HMIPanel
from src.modules.verifier.verifier import verify_panel, VerificationReport, apply_fixes
from src.modules.verifier.constraints import Severity, ConstraintSet
from src.utils.run_artifacts import RunArtifacts
from src.agents.prompts_phase2 import get_dsl_correction_prompt

logger = logging.getLogger(__name__)

# --- 1. 状态定义 (State Graph) ---
class AgentState(Enum):
    INIT = "INIT"           # 初始状态
    DRAFTING = "DRAFTING"   # 生成初稿
    VERIFYING = "VERIFYING" # 规则校验
    REFLECTING = "REFLECTING" # 错误反思 (计算 Reward)
    REPAIRING = "REPAIRING" # 修正生成
    FINISHED = "FINISHED"   # 成功结束
    FAILED = "FAILED"       # 失败终止

# --- 2. 审计日志结构 (Audit Chain) ---
@dataclass
class AuditEvent:
    timestamp: float
    round_idx: int
    state: str
    action: str
    payload: Dict[str, Any]
    score: float = 0.0

class EngineeringAgent:
    def __init__(self, ra: RunArtifacts, llm_client: Any, constraints: ConstraintSet, config: Dict[str, Any]):
        self.ra = ra
        self.llm = llm_client
        self.constraints = constraints
        self.config = config
        
        # 状态机初始化
        self.state = AgentState.INIT
        self.round = 0
        self.max_rounds = config.get("max_rounds", 3)
        self.history: List[AuditEvent] = []
        self.current_panel: Optional[HMIPanel] = None
        self.current_json_text: str = ""
        self.best_panel: Optional[HMIPanel] = None
        self.max_score = -float('inf')
        self.last_parse_error: str = ""
        self.last_parse_clean_text: str = ""

    # --- 3. 奖惩编码 (Reward/Penalty) ---
    def _calculate_score(self, report: VerificationReport) -> float:
        """
        工程核心：将布尔型的“通过/失败”转化为连续的数值评分。
        0分 = 完美。负分越低，违规越严重。
        """
        score = 0.0
        for v in report.violations:
            if v.severity == Severity.HARD:
                score -= 100.0  # 重罚：触碰高压线
            else:
                score -= 10.0   # 轻罚：软性建议
        
        # 还可以加入"结构完整性"奖励，或者"JSON字符长度"惩罚（防止啰嗦）
        return score

    # --- 4. 审计记录 (Logging) ---
    def _log_event(self, action: str, payload: Dict[str, Any], score: float = 0.0):
        # Convert payload to string safe representation
        try:
            payload_str = json.dumps(payload, default=str)
        except:
            payload_str = str(payload)

        event = AuditEvent(
            timestamp=time.time(),
            round_idx=self.round,
            state=self.state.name,
            action=action,
            payload=json.loads(payload_str) if isinstance(payload, dict) else {"raw": payload_str},
            score=score
        )
        self.history.append(event)
        # 实时写入磁盘，形成不可篡改的证据链
        # Note: write_jsonl doesn't exist on RunArtifacts yet, using append_text with json line
        self.ra.safe_append_text_atomic("agent_audit_trail.jsonl", json.dumps(event.__dict__, default=str) + "\n")

    # --- 5. 失败恢复路径 (Failure Recovery) ---
    def _build_parse_violation(self) -> Dict[str, Any]:
        message = self.last_parse_error or "JSON parse/validation failed"
        return {
            "rule_id": "DSL_SCHEMA_VALIDATION",
            "severity": "HARD",
            "param_path": "$",
            "message": (
                f"{message}. Remove unsupported fields and output strict HMIPanel JSON. "
                "Do not include top-level fields outside schema (e.g. validation_report)."
            ),
            "expected_limit": "Valid HMIPanel JSON only",
        }

    def _safe_parse(self, text: str) -> Optional[HMIPanel]:
        """
        多级恢复策略解析器 - 增强版 (Stack Balancing)
        """
        self.last_parse_error = ""
        clean_text = text.strip()
        
        # 策略 A: 栈平衡提取 (比正则更可靠)
        try:
            # 寻找第一个 {
            start_idx = clean_text.find('{')
            if start_idx != -1:
                balance = 0
                end_idx = -1
                in_string = False
                escape = False
                
                for i in range(start_idx, len(clean_text)):
                    char = clean_text[i]
                    
                    if in_string:
                        if escape:
                            escape = False
                        elif char == '\\':
                            escape = True
                        elif char == '"':
                            in_string = False
                    else:
                        if char == '"':
                            in_string = True
                        elif char == '{':
                            balance += 1
                        elif char == '}':
                            balance -= 1
                            if balance == 0:
                                end_idx = i
                                break
                
                if end_idx != -1:
                    clean_text = clean_text[start_idx : end_idx+1]
        except Exception:
            pass # Fallback to original or regex if simple balancing fails

        # 策略 B: 尝试清洗常见 JSON 错误
        def robust_loads(s):
            # 1. 移除 Markdown 代码块标记 (如果栈平衡没处理掉)
            s = re.sub(r"^```[a-zA-Z]*\n", "", s)
            s = re.sub(r"\n```$", "", s)
            
            # 2. 移除注释
            s = re.sub(r"//.*$", "", s, flags=re.MULTILINE)
            s = re.sub(r"/\*[\s\S]*?\*/", "", s)
            
            # 3. 移除尾随逗号 (简单处理)
            s = re.sub(r",\s*\}", "}", s)
            s = re.sub(r",\s*\]", "]", s)
            
            return json.loads(s)

        import streamlit as st # Inject debug info directly to UI
        self.last_parse_clean_text = clean_text
        
        try:
            data = robust_loads(clean_text)
            if isinstance(data, dict):
                # Keep compatibility with common LLM-added audit blocks.
                if "validation_report" in data:
                    metadata = data.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata["validation_report"] = data.pop("validation_report")
                    data["metadata"] = metadata
            return HMIPanel.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e_json:
            self.last_parse_error = str(e_json)
            self._log_event("PARSE_ERROR", {"error": str(e_json), "strategy": "Standard"}, -50.0)
            
            # CRITICAL DEBUG: Show user what failed
            try:
                with st.expander("🔴 JSON Parse Failed (Debug)", expanded=False):
                    st.text(f"Error: {e_json}")
                    st.code(clean_text, language="json")
            except: pass
            
            return None
        except Exception as e_pydantic:
             self.last_parse_error = str(e_pydantic)
             self._log_event("VALIDATION_ERROR", {"error": str(e_pydantic)}, -50.0)
             
             try:
                 with st.expander("🔴 Validation Failed (Debug)", expanded=False):
                     st.text(f"Error: {e_pydantic}")
                     st.code(clean_text, language="json")
             except: pass
             
             return None

    # --- 核心驱动循环 (State Machine Loop) ---
    def run(self, initial_prompt: str, initial_text: str = "") -> Tuple[Optional[HMIPanel], Dict]:
        
        self.state = AgentState.DRAFTING
        self.current_json_text = initial_text
        
        while self.state not in [AgentState.FINISHED, AgentState.FAILED]:
            
            # self._log_event("STATE_TRANSITION", {"new_state": self.state.name})

            # --- DRAFTING: 初次生成 (或接收外部输入) ---
            if self.state == AgentState.DRAFTING:
                # 假设外部已经传入了 initial_text，或者在这里调用 generate
                if not self.current_json_text:
                    # 如果是空的，说明需要生成 (Not implemented in this refactor step, assumes passed in)
                    pass 
                self.state = AgentState.VERIFYING

            # --- VERIFYING: 规则校验与评分 ---
            elif self.state == AgentState.VERIFYING:
                panel = self._safe_parse(self.current_json_text)
                
                if not panel:
                    # 解析失败，直接进入 REPAIRING (或者 FAILED)
                    self._log_event("VERIFY_FAIL", {"reason": self.last_parse_error or "JSON Parse Error"}, -999.0)
                    if self.round >= self.max_rounds:
                        self.state = AgentState.FAILED
                    else:
                        self.state = AgentState.REFLECTING # Goto Reflecting to increment round
                    
                    # LOGGING: Backward Compatibility
                    self.ra.write_text(
                        f"intermediate/round_{self.round}_syntax_error.txt",
                        self.last_parse_error or "JSON Parse Error"
                    )
                    continue

                self.current_panel = panel
                
                # LOGGING: Backward Compatibility - Intermediate DSL
                self.ra.write_json(f"intermediate/round_{self.round}_dsl.json", panel.model_dump())

                report = verify_panel(panel, self.constraints)
                score = self._calculate_score(report)
                
                # [核心修复] Proactively apply symbolic CLAMP to get a safe panel
                if not report.passed:
                    clamped_panel, clamped_report = apply_fixes(panel, report)
                    self.ra.write_json(f"intermediate/round_{self.round}_dsl_clamped.json", clamped_panel.model_dump())
                    candidate_panel = clamped_panel
                    # Re-verify the clamped panel
                    post_clamp_report = verify_panel(clamped_panel, self.constraints)
                    post_clamp_score = self._calculate_score(post_clamp_report)
                    if post_clamp_score > score:
                        score = post_clamp_score
                        report = post_clamp_report
                else:
                    candidate_panel = panel
                
                # LOGGING: Backward Compatibility - Verification Report
                self.ra.write_json(f"intermediate/round_{self.round}_report.json", report.model_dump())
                
                self._log_event("VERIFICATION", {
                    "passed": report.passed, 
                    "violations": len(report.violations)
                }, score)

                # 记录“历史最佳” (Checkpointing)
                if score > self.max_score:
                    self.max_score = score
                    self.best_panel = candidate_panel
                
                if report.passed:
                    self.state = AgentState.FINISHED
                else:
                    self.state = AgentState.REFLECTING

            # --- REFLECTING: 反思与决策 ---
            elif self.state == AgentState.REFLECTING:
                self.round += 1
                if self.round >= self.max_rounds:
                    # 超过轮次，回退到历史最佳 (Graceful Degradation)
                    self._log_event("TIMEOUT", {"fallback_to_best": True}, self.max_score)
                    
                    if self.best_panel:
                        # Attempt to apply symbolic clamps to the best panel if it had errors
                        # Or just return it. Let's try to clamp it if we have violations.
                        # For simplicity, we just return best_panel, but a symbolic clamp would be better.
                        # Let's reproduce the logic from repair_loop: apply_fixes
                        best_report = verify_panel(self.best_panel, self.constraints)
                        if not best_report.passed:
                             clamped_panel, _ = apply_fixes(self.best_panel, best_report)
                             self.best_panel = clamped_panel
                             self._log_event("SYMBOLIC_CLAMP", {"applied": True})
                        
                        self.current_panel = self.best_panel
                        self.state = AgentState.FINISHED 
                    else:
                        self.state = AgentState.FAILED
                else:
                    self.state = AgentState.REPAIRING

            # --- REPAIRING: 执行修复 ---
            elif self.state == AgentState.REPAIRING:
                # 获取校验报告
                last_report = verify_panel(self.current_panel, self.constraints) if self.current_panel else None
                violations = last_report.violations if last_report else [self._build_parse_violation()]
                
                # 构建修复 Prompt (Chain of Thought)
                prompt = get_dsl_correction_prompt(self.current_json_text, violations)
                prompt += """

CRITICAL: If you change a widget's `max` or `min` attribute, you MUST also update that widget's `value` so it remains within the new range.
Example: if max is clamped from 100 to 3.3, value must become 3.3 or lower.
CRITICAL: Output ONLY a single JSON object. No markdown fences.
CRITICAL: Do NOT output top-level keys outside HMIPanel schema; put audit notes under `metadata` instead.
"""
                
                # 调用 LLM
                try:
                    resp = self.llm.generate_content(prompt)
                    self.current_json_text = resp.text
                    self._log_event("LLM_REPAIR", {"response_len": len(resp.text)})
                    self.state = AgentState.VERIFYING # 闭环：修完再去验
                except Exception as e:
                    self._log_event("LLM_ERROR", {"error": str(e)})
                    # Network/high-load fallback: try symbolic path instead of immediate hard fail.
                    fallback_panel = self._safe_parse(self.current_json_text)
                    if fallback_panel:
                        fallback_report = verify_panel(fallback_panel, self.constraints)
                        if not fallback_report.passed:
                            fallback_panel, _ = apply_fixes(fallback_panel, fallback_report)
                            fallback_report = verify_panel(fallback_panel, self.constraints)
                        self.best_panel = fallback_panel
                        self.max_score = max(self.max_score, self._calculate_score(fallback_report))
                        self.current_panel = fallback_panel
                        self._log_event(
                            "LLM_ERROR_FALLBACK",
                            {"recovered": True, "passed": bool(fallback_report.passed)}
                        )
                        self.state = AgentState.FINISHED if fallback_report.passed else AgentState.FAILED
                    elif self.best_panel:
                        self._log_event("LLM_ERROR_FALLBACK", {"recovered": True, "source": "best_panel"})
                        self.current_panel = self.best_panel
                        self.state = AgentState.FINISHED
                    else:
                        self.state = AgentState.FAILED

        # 最终输出
        metrics = {
            "final_state": self.state.name,
            "rounds_used": self.round,
            "final_score": (self.max_score if self.max_score != -float("inf") else -999.0),
            "audit_trail_file": "agent_audit_trail.jsonl",
            "final_pass": self.state == AgentState.FINISHED,
            # Maintain backward compatibility with old metrics for UI
            "closed_loop_rounds": self.round,
            "closed_loop_success": self.state == AgentState.FINISHED
        }
        
        return self.best_panel, metrics
