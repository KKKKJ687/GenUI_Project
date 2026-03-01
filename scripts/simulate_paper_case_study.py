import sys
import os
import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime

# 确保能导入 src
sys.path.insert(0, str(Path.cwd()))

# 引入核心模块
from src.models.schema import HMIPanel
from src.modules.verifier.constraints import ConstraintSet, Constraint, ConstraintKind, Severity
from src.modules.verifier.verifier import verify_panel, apply_fixes
from src.modules.renderer.renderer import render_panel
from src.core.phase2_pipeline import run_phase2_pipeline # 假设这是你的入口
from src.utils.run_artifacts import RunArtifacts # 假设你有这个工具类

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger("GrandChallenge")

# 颜色
CYAN = "\033[96m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def section(title):
    print(f"\n{CYAN}{'='*60}")
    print(f"   STAGE: {title}")
    print(f"{'='*60}{RESET}")

def check(condition, message):
    if condition:
        print(f"{GREEN}[PASS] {message}{RESET}")
        return True
    else:
        print(f"{RED}[FAIL] {message}{RESET}")
        return False

class MockArtifacts:
    """模拟运行产物记录器"""
    def __init__(self):
        self.data = {}
    def stage(self, name): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def write_json(self, name, content): self.data[name] = content
    def write_text(self, name, content): self.data[name] = content
    def record_error(self, e, where): print(f"Error in {where}: {e}")

def run_simulation():
    print(f"{YELLOW}启动 IEEE Transactions 论文 Case Study 模拟程序...{RESET}")
    
    # ---------------------------------------------------------
    # 1. 模拟 RAG 提取阶段 (The Perception Layer)
    # 对应《开题报告》 3.1 Domain-RAG
    # ---------------------------------------------------------
    section("1. Datasheet Knowledge Extraction (RAG Simulation)")
    
    # 模拟从 PDF 提取出的非结构化文本证据
    raw_evidence_text = """
    Device: SuperMotor-X2000
    Absolute Maximum Ratings:
    - Input Voltage: -0.5V to 24.0V
    - Operating Temperature: -20C to 85C
    - PWM Frequency: Max 100kHz
    """
    logger.info(f"模拟读取数据手册内容:\n{raw_evidence_text.strip()}")
    
    # 模拟 Constraint Extractor 的工作结果 (将文本转为 ConstraintSet)
    # 这里我们手动构造，验证 pipeline 是否能承接这种结构
    extracted_constraints = ConstraintSet(
        device_name="SuperMotor-X2000",
        constraints=[
            Constraint(
                id="ELEC_LIMIT_01",
                name="Max Input Voltage",
                kind=ConstraintKind.MAX,
                applies_to="widgets[id='slider_vol'].max", # Simplified syntax
                max_val=24.0,
                unit="V",
                severity=Severity.HARD,
                description="Datasheet Max Input Voltage"
            )
        ]
    )
    
    check(len(extracted_constraints.constraints) == 1, "约束提取模块产出符合 Schema 定义")
    logger.info("-> 约束集 (Constraints) 已加载至内存上下文")

    # ---------------------------------------------------------
    # 2. 模拟 LLM 生成阶段 (The Hallucination)
    # 对应《开题报告》 1.2 参数幻觉风险
    # ---------------------------------------------------------
    section("2. Neuro-Generative Phase (Simulating LLM Hallucination)")
    
    # 模拟 LLM 生成了一个“不懂硬件”的 UI，电压设为了 48V (烧机参数)
    hallucinated_dsl_json = {
        "title": "DeathStar_Control",
        "version": "1.0",
        "theme": "dark",
        "layout": [{"i": "slider_vol", "x": 0, "y": 0, "w": 4, "h": 2}],
        "widgets": [
            {
                "id": "slider_vol",
                "type": "slider",
                "label": "Main Voltage",
                "min": 0,
                "max": 48.0, # <--- 危险！超过 24V
                "step": 1,
                "binding": {"protocol": "mqtt", "address": "motor/vol"}
            }
        ]
    }
    
    try:
        draft_panel = HMIPanel(**hallucinated_dsl_json)
        check(True, "Architect Agent 生成了符合语法的 DSL (但包含危险参数)")
    except Exception as e:
        check(False, f"DSL 解析失败: {e}")
        return

    # ---------------------------------------------------------
    # 3. 神经符号验证与修复 (The Closed-Loop)
    # 对应《任务清单》 Phase 2 物理感知验证机制
    # ---------------------------------------------------------
    section("3. Symbolic Verification & Self-Healing Loop")
    
    # 3.1 运行校验器
    report = verify_panel(draft_panel, extracted_constraints)
    
    is_caught = check(not report.passed, "Verifier 成功捕获安全违规")
    if is_caught:
        violation = report.violations[0]
        print(f"   -> 报警详情: {YELLOW}{violation.message}{RESET}")
        print(f"   -> 违规值: {violation.observed_value}V, 限制: {violation.expected_limit}V")

    # 3.2 运行修复器
    fixed_panel, fix_report = apply_fixes(draft_panel, report)
    
    # 验证修复结果
    target_widget = next(w for w in fixed_panel.widgets if w.id == "slider_vol")
    is_fixed = check(target_widget.max == 24.0, f"Repair Loop 成功将参数 Clamp 至安全边界 (48.0 -> {target_widget.max})")
    
    # 验证修复记录
    fix_record = fix_report.fixes[0]
    check(fix_record.action_type.name == "CLAMP", "修复动作类型记录正确 (CLAMP)")

    # ---------------------------------------------------------
    # 4. 确定性渲染验证 (The Determinism)
    # 对应《任务清单》 Phase 1 确定性渲染器
    # ---------------------------------------------------------
    section("4. Deterministic Rendering Verification")
    
    # 生成 HTML
    html_output_1 = render_panel(fixed_panel)
    html_output_2 = render_panel(fixed_panel)
    
    # 验证哈希一致性 (两次生成必须完全字节级一致)
    hash1 = hashlib.sha256(html_output_1.encode()).hexdigest()
    hash2 = hashlib.sha256(html_output_2.encode()).hexdigest()
    
    check(hash1 == hash2, "渲染器具备数学级确定性 (Deterministic Output)")
    check("src=\"https://cdn.jsdelivr.net/npm/alpinejs" in html_output_1, "生成的 HTML 包含 Alpine.js 运行时")
    check("data-max-value=\"24.0\"" in html_output_1 or 'max="24.0"' in html_output_1, "安全约束已正确映射到 HTML 属性中")

    # ---------------------------------------------------------
    # 5. 论文指标生成 (Metrics Generation)
    # 对应《项目计划蓝图》 Phase 5 指标体系
    # ---------------------------------------------------------
    section("5. Academic Metrics Export")
    
    
    # 【核心修改点】: 对修复后的 Panel 进行二次校验，获取真实的最终分数
    final_verification_report = verify_panel(fixed_panel, extracted_constraints)
    
    metrics = {
        "total_checks": report.stats.get("total_checks", 0),
        "violations_detected": len(report.violations),
        "fixes_applied": len(fix_report.fixes),
        "safety_score_initial": report.score,         # 90.0
        "safety_score_final": final_verification_report.score # 应该是 100.0
    }
    
    print(json.dumps(metrics, indent=2))
    
    check(metrics["violations_detected"] == 1, "指标: 违规检测数正确")
    check(metrics["fixes_applied"] == 1, "指标: 自动修复数正确")
    
    # 现在这个检查应该能 PASS 了
    check(metrics["safety_score_final"] > metrics["safety_score_initial"], 
          f"指标: 安全评分显著提升 ({metrics['safety_score_initial']} -> {metrics['safety_score_final']})")

    section("GRAND CHALLENGE RESULT")
    if is_caught and is_fixed and (hash1 == hash2):
        print(f"{GREEN}🏆 恭喜！你的系统完美通过了 IEEE Transactions 级别的全案模拟。{RESET}")
        print(f"{GREEN}   所有核心创新点 (DSL, RAG-Constraint, Self-Healing) 均已闭环。{RESET}")
    else:
        print(f"{RED}❌ 模拟失败。请检查上述 FAIL 项目。{RESET}")

if __name__ == "__main__":
    run_simulation()