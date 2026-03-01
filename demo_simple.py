
import json
import asyncio
from src.models.schema import HMIPanel
# Mock verify_panel import for demo simplicity if modules not fully set up, 
# but here we use the real one since we fixed imports.
from src.modules.verifier.verifier import verify_panel, apply_fixes
from src.modules.verifier.constraints import ConstraintSet, Constraint, ConstraintKind, Severity
from src.modules.renderer.renderer import render_panel

def run_demo():
    print("🚀 GenUI Project - Simple Demo")
    print("--------------------------------")

    # 1. 定义安全约束 (模拟从Datasheet提取)
    # 假设我们有一个LED设备，最大亮度不能超过100%
    constraints = ConstraintSet(
        device_name="Generic LED",
        constraints=[
            Constraint(
                id="MAX_BRIGHTNESS",
                name="Max Brightness Limit",
                kind=ConstraintKind.MAX,
                applies_to="widgets[type='slider'].value",
                max_val=100.0,
                unit="%",
                severity=Severity.HARD
            ),
            Constraint(
                id="MAX_SETTING",
                name="Max Setting Limit",
                kind=ConstraintKind.MAX,
                applies_to="widgets[type='slider'].max",
                max_val=100.0,
                unit="%",
                severity=Severity.HARD
            )
        ]
    )
    print("✅ 1. 加载安全约束: Max Brightness <= 100%")

    # 2. 模拟 LLM 生成的 DSL (包含危险值)
    # 这里的 max=150 和 value=120 违反了上述约束
    dsl_json = """
    {
        "title": "LED Control Panel",
        "version": "1.0.0",
        "widgets": [
            {
                "id": "brightness_slider",
                "type": "slider",
                "label": "Brightness Control",
                "min": 0, 
                "max": 150, 
                "value": 120,
                "unit": "%"
            }
        ],
        "layout": [{"i": "brightness_slider", "x": 0, "y": 0, "w": 4, "h": 2}]
    }
    """
    print("\n🔵 2. 模拟 LLM 生成结果 (危险!):")
    print(f"   Slider Max: 150%, Value: 120% (Exceeds 100% limit)")

    # 3. 验证
    print("\n🔵 3. 运行验证器 (Verifier)...")
    try:
        panel = HMIPanel.model_validate_json(dsl_json)
        report = verify_panel(panel, constraints)
    except Exception as e:
        print(f"DTO Parse Error: {e}")
        return

    if report.passed:
        print("   验证通过 (Unexpected for this demo)")
    else:
        print(f"   ❌ 验证失败! 发现 {len(report.violations)} 个违规项:")
        for v in report.violations:
            print(f"      - [HIGH RISK] {v.message}")

    # 4. 修复
    print("\n🔵 4. 执行自动修复 (Auto-Repair)...")
    if not report.passed:
        fixed_panel, fix_report = apply_fixes(panel, report)
        print(f"   ✅ 修复完成! 应用了 {len(fix_report.fixes)} 个修复动作:")
        for f in fix_report.fixes:
            print(f"      - {f.action_type.value}: {f.reason} (Old: {f.value_before} -> New: {f.value_after})")
        
        # 验证修复后的面板
        final_report = verify_panel(fixed_panel, constraints)
        if final_report.passed:
            print("   ✨ 再次验证通过!")
            panel = fixed_panel

    # 5. 渲染
    print("\n🔵 5. 确定性渲染 (Renderer)...")
    html_output = render_panel(panel)
    print(f"   ✅ HTML 生成成功 ({len(html_output)} bytes)")
    print("   (此处可将HTML保存文件或通过Streamlit显示)")

    print("\n🎉 Demo 运行结束! 系统成功拦截并修正了潜在的硬件风险。")

if __name__ == "__main__":
    run_demo()
