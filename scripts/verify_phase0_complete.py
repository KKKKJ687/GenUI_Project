#!/usr/bin/env python3
"""
Phase 0 Acceptance Test Suite (Comprehensive)
运行此脚本以验证 Phase 0 是否达到验收标准。
"""
import sys
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# 确保能导入模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.mock_llm import MockLLM
from src.core import phase0_core


class TestPhase0Acceptance(unittest.TestCase):

    def setUp(self):
        # 每个测试都在临时目录跑，保证环境干净
        self.temp_dir = TemporaryDirectory()
        self.runs_dir = Path(self.temp_dir.name) / "runs"
        self.runs_dir.mkdir()

        # 使用 MockLLM 保证确定性 (Determinism)
        self.mock_llm = MockLLM(mode="baseline_html")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_end_to_end_pipeline_artifacts(self):
        """验收标准 1: 完整的 Artifacts 留痕"""
        print("\nTesting End-to-End Pipeline Artifacts...")

        user_prompt = "Test Prompt for Phase0"

        # 运行 Pipeline
        run_dir, metrics = phase0_core.run_baseline_once(
            runs_dir=self.runs_dir,
            user_prompt=user_prompt,
            selected_model="mock-model",
            selected_style="Default",
            llm=self.mock_llm,
            streaming=True,
        )

        # 1. 验证 run_dir 是否创建
        self.assertTrue(run_dir.exists(), "Run directory not created")
        print(f"[PASS] Run directory created: {run_dir.name}")

        # 2. 验证关键文件是否存在 (The Contract)
        required_files = [
            "input.json",
            "model_raw.txt",
            "lint_report.json",  # Defect 4 fix check
            "final.html",
            "timing.json",
            "metrics.json",
        ]
        for fname in required_files:
            fpath = run_dir / fname
            self.assertTrue(fpath.exists(), f"Missing artifact: {fname}")
        print("[PASS] All required artifacts present")

        # 3. 验证 Metrics 结构 (Defect 5 fix check)
        # app.py 和 core 必须使用同一套 Metrics 逻辑
        self.assertTrue(metrics["success"], "Metrics report failure")
        self.assertIn("lint_passed", metrics)
        self.assertIn("repair_rounds", metrics)
        print("[PASS] Metrics structure valid")

        # 4. 验证 HTML 内容
        html_content = (run_dir / "final.html").read_text(encoding="utf-8")
        self.assertTrue(
            html_content.strip().startswith("<!DOCTYPE html>"), "Final HTML invalid"
        )
        print("[PASS] Final HTML is valid")

    def test_lint_schema_standardization(self):
        """验收标准 2: Lint 报告 Schema 统一 (针对 Defect 4)"""
        print("\nTesting Lint Schema Standardization...")

        run_dir, _ = phase0_core.run_baseline_once(
            runs_dir=self.runs_dir,
            user_prompt="Lint Check",
            selected_model="mock-model",
            selected_style="Default",
            llm=self.mock_llm,
        )

        lint_report = json.loads((run_dir / "lint_report.json").read_text())

        # 检查 initial lint 字段
        self.assertIn("initial", lint_report, "Missing 'initial' in lint_report")
        initial = lint_report["initial"]
        self.assertIn("ok", initial)
        self.assertIn("errors", initial)
        self.assertIn("warnings", initial)
        self.assertIn(
            "schema_version", initial, "Missing schema_version in initial lint report"
        )
        self.assertEqual(initial["schema_version"], "v1.0")
        print("[PASS] Lint report schema conforms to v1.0")

    def test_streaming_callback(self):
        """验收标准 3: UI Streaming 回调机制 (针对 Defect 5)"""
        print("\nTesting Streaming Callback Mechanism...")

        received_chunks = []

        def my_callback(text):
            received_chunks.append(text)

        phase0_core.run_baseline_once(
            runs_dir=self.runs_dir,
            user_prompt="Stream Check",
            selected_model="mock-model",
            selected_style="Default",
            llm=self.mock_llm,
            streaming=True,
            on_chunk=my_callback,  # 注入回调
        )

        self.assertTrue(len(received_chunks) > 0, "No chunks received via callback")
        full_text = received_chunks[-1]  # Last accumulated text
        self.assertIn("<!DOCTYPE html>", full_text, "Streamed content incomplete")
        print(
            f"[PASS] Successfully streamed {len(received_chunks)} chunks via callback"
        )


if __name__ == "__main__":
    print("=== Phase 0 Final Acceptance Test ===")
    unittest.main(verbosity=2)
