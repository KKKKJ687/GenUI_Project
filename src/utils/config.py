import os
from pathlib import Path

# 获取项目根目录 (假设此文件在 src/utils/config.py)
# .parent = src/utils, .parent = src, .parent = GenerativeUI_Project
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESOURCES_DIR = PROJECT_ROOT / "resources"
SCHEMAS_DIR = RESOURCES_DIR / "schemas"

# ===========================================
# Ablation Switches (消融开关)
# 用于论文实验对比，支持4种模式
# ===========================================

class AblationConfig:
    """
    Ablation experiment configuration.
    
    论文要求的4个开关 (开题报告6.3):
    - baseline_html_mode: 直接生成HTML，无DSL
    - dsl_mode_no_verifier: DSL模式但跳过验证器
    - verifier_no_rag: 使用验证器但用手动约束（无RAG）
    - rag_no_extractor: RAG检索但不自动抽取约束（仅检索）
    """
    
    def __init__(
        self,
        baseline_html_mode: bool = False,
        dsl_mode_no_verifier: bool = False,
        verifier_no_rag: bool = False,
        rag_no_extractor: bool = False
    ):
        self.baseline_html_mode = baseline_html_mode
        self.dsl_mode_no_verifier = dsl_mode_no_verifier
        self.verifier_no_rag = verifier_no_rag
        self.rag_no_extractor = rag_no_extractor
    
    @property
    def mode_name(self) -> str:
        """Get human-readable mode name for reporting."""
        if self.baseline_html_mode:
            return "baseline_html"
        elif self.dsl_mode_no_verifier:
            return "dsl_no_verifier"
        elif self.verifier_no_rag:
            return "verifier_manual_constraints"
        elif self.rag_no_extractor:
            return "rag_retrieval_only"
        else:
            return "full_pipeline"
    
    @property
    def use_dsl(self) -> bool:
        """Whether to use DSL mode."""
        return not self.baseline_html_mode
    
    @property
    def use_verifier(self) -> bool:
        """Whether to run the verifier."""
        return self.use_dsl and not self.dsl_mode_no_verifier
    
    @property
    def use_rag(self) -> bool:
        """Whether to use RAG for constraint loading."""
        return self.use_verifier and not self.verifier_no_rag
    
    @property
    def use_extractor(self) -> bool:
        """Whether to use automatic constraint extraction."""
        return self.use_rag and not self.rag_no_extractor
    
    def to_dict(self) -> dict:
        """Serialize for logging/reporting."""
        return {
            "baseline_html_mode": self.baseline_html_mode,
            "dsl_mode_no_verifier": self.dsl_mode_no_verifier,
            "verifier_no_rag": self.verifier_no_rag,
            "rag_no_extractor": self.rag_no_extractor,
            "mode_name": self.mode_name,
            "effective": {
                "use_dsl": self.use_dsl,
                "use_verifier": self.use_verifier,
                "use_rag": self.use_rag,
                "use_extractor": self.use_extractor
            }
        }
    
    @classmethod
    def from_mode_name(cls, mode: str) -> "AblationConfig":
        """Create config from mode name string."""
        if mode == "baseline_html" or mode == "baseline":
            return cls(baseline_html_mode=True)
        elif mode == "dsl_no_verifier" or mode == "dsl":
            return cls(dsl_mode_no_verifier=True)
        elif mode == "verifier_manual_constraints" or mode == "verifier_no_rag":
            return cls(verifier_no_rag=True)
        elif mode == "rag_retrieval_only" or mode == "rag_no_extractor":
            return cls(rag_no_extractor=True)
        else:
            return cls()  # Full pipeline


# Default configuration
DEFAULT_ABLATION = AblationConfig()
