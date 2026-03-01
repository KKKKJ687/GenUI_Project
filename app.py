import streamlit as st
import google.generativeai as genai
import pandas as pd
from pypdf import PdfReader
import json
import re
import base64
from io import BytesIO
import sys
from pathlib import Path

# ==========================================
# Phase 0B bootstrap: stable paths + run_dir
# ==========================================
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ==========================================
# 1. 导入工具层和新模块 (路径修正版)
# ==========================================
# Agents & Tools
from src.agents.prompts import (
    get_planner_prompt,
    get_architect_prompt,
    get_review_prompt_v1,
    get_simple_review_prompt
)

# Renderer Modules
from src.modules.renderer.styles import get_style_names
from src.modules.renderer.preview_utils import build_data_uri_link

# RAG Modules
from src.modules.rag.context_splitter import split_text_recursive
from src.modules.rag.local_rag import (
    extract_keywords_fallback,
    retrieve_top_k_chunks,
    format_retrieved_chunks_for_prompt,
)
from src.modules.rag import html_extractor

# Verifier Modules
from src.modules.verifier.html_lint import lint_html

# Runtime & Core Modules
from src.modules.runtime.runtime_monitor import append_event, read_events
from src.core.status_reporter import generate_report, export_report
from src.utils.dataframe_summary import summarize_dataframe
from src.utils.run_artifacts import RunArtifacts  # 提到顶层，防止 Lazy Import 报错
from src.utils.streaming_utils import chunk_to_text
from src.utils.config import AblationConfig

# Pipeline Cores
from src.core.phase1_core import run_dsl_pipeline
from src.core.phase2_pipeline import run_phase2_pipeline, load_constraints, get_sample_constraints


# 条件导入 - 某些库可能未安装
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


def _rt_log(run_dir, event_type: str, payload: dict) -> None:
    """Best-effort runtime event logging (never breaks UI)."""
    try:
        append_event(run_dir, event_type=event_type, payload=payload or {})
    except Exception:
        # monitoring must not affect primary pipeline
        pass


def _configure_genai_client(api_key: str) -> None:
    """
    Prefer REST transport to reduce flaky gRPC connectivity failures.
    """
    try:
        genai.configure(api_key=api_key, transport="rest")
    except TypeError:
        # Backward compatibility for older SDK builds.
        genai.configure(api_key=api_key)


# ==========================================
# 2. 配置与界面样式
# ==========================================
st.set_page_config(page_title="GenUI Agent Pro", layout="wide")

st.markdown("""<style>
    :root {
        --gp-bg-0: #f5f7fb;
        --gp-bg-1: #eef2f8;
        --gp-bg-2: #f8fbff;
        --gp-surface: rgba(255, 255, 255, 0.82);
        --gp-surface-solid: #ffffff;
        --gp-line: rgba(15, 23, 42, 0.07);
        --gp-line-strong: rgba(15, 23, 42, 0.14);
        --gp-text-main: #1d1d1f;
        --gp-text-sub: #636369;
        --gp-accent: #0071e3;
        --gp-accent-soft: #d7e8ff;
        --gp-success-bg: rgba(48, 209, 88, 0.14);
        --gp-success-line: rgba(48, 209, 88, 0.34);
    }

    .stApp {
        font-family: "SF Pro Display", "SF Pro Text", "PingFang SC", "Helvetica Neue", "Noto Sans SC", sans-serif;
        color: var(--gp-text-main);
        background:
            radial-gradient(circle at 8% -10%, #ffffff 0%, rgba(255, 255, 255, 0) 36%),
            radial-gradient(circle at 94% 14%, #e2edff 0%, rgba(226, 237, 255, 0) 34%),
            linear-gradient(150deg, var(--gp-bg-0) 0%, var(--gp-bg-1) 48%, var(--gp-bg-2) 100%);
    }

    [data-testid="stAppViewContainer"] > .main {
        background: transparent;
    }

    .main .block-container {
        max-width: 1500px;
        padding-top: 0.8rem;
        padding-bottom: 1.2rem;
    }

    h1, h2, h3, h4, h5, h6 {
        color: var(--gp-text-main);
        letter-spacing: -0.012em;
    }

    .gp-topbar {
        border: 1px solid var(--gp-line);
        border-radius: 24px;
        background: var(--gp-surface);
        backdrop-filter: blur(16px) saturate(130%);
        -webkit-backdrop-filter: blur(16px) saturate(130%);
        box-shadow: 0 10px 26px rgba(15, 23, 42, 0.07);
        padding: 14px 18px;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
    }

    .gp-brand {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
    }

    .gp-brand-mark {
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: linear-gradient(145deg, var(--gp-accent), #58a7ff);
        box-shadow: 0 0 0 6px var(--gp-accent-soft);
        flex: none;
    }

    .gp-title {
        margin: 2px 0 0;
        font-size: 17px;
        font-weight: 670;
        color: #1d1d1f;
    }

    .gp-eye {
        margin: 0;
        font-size: 11px;
        letter-spacing: 0.09em;
        text-transform: uppercase;
        color: var(--gp-text-sub);
        font-weight: 620;
    }

    [data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid var(--gp-line-strong);
        border-radius: 18px;
        background: var(--gp-surface);
        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
    }

    .gp-section-title {
        margin: 0 0 8px;
        font-size: 14px;
        font-weight: 670;
        letter-spacing: 0.01em;
        color: #303035;
    }

    .gp-metric-head {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        margin-bottom: 8px;
    }

    .gp-pill-row {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
    }

    .gp-pill {
        border: 1px solid var(--gp-line);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.82);
        color: #374151;
        font-size: 12px;
        font-weight: 620;
        line-height: 1;
        padding: 7px 10px;
    }

    .gp-progress-label {
        margin: 0 0 6px;
        font-size: 12px;
        color: #4b5563;
        font-weight: 620;
    }

    [data-testid="stProgressBar"] > div > div > div > div {
        background: linear-gradient(90deg, #0071e3, #4a9cff) !important;
    }

    .stTextInput input,
    .stTextArea textarea,
    .stFileUploader section,
    div[data-baseweb="select"] > div,
    .stNumberInput input {
        border-radius: 12px;
        border: 1px solid var(--gp-line-strong);
        background: var(--gp-surface-solid);
        font-size: 13px;
        color: #111827;
    }

    .stTextArea textarea {
        min-height: 140px;
        line-height: 1.48;
    }

    .stButton > button {
        border-radius: 12px;
        border: 0;
        background: linear-gradient(160deg, #0071e3, #2d8fff);
        color: #ffffff;
        box-shadow: 0 8px 18px rgba(0, 113, 227, 0.28);
        font-weight: 640;
        height: 2.4rem;
    }

    .stButton > button:hover {
        filter: brightness(1.02);
    }

    .stDownloadButton > button {
        border-radius: 12px;
        border: 1px solid var(--gp-line-strong);
        background: #ffffff;
        color: #374151;
        font-weight: 600;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: linear-gradient(180deg, #ffffff, #f7f9fd);
        border-bottom: 1px solid var(--gp-line);
        padding: 0.4rem;
        border-radius: 12px 12px 0 0;
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 10px;
        height: 2rem;
        font-size: 12px;
        font-weight: 640;
        color: var(--gp-text-sub);
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(180deg, #f5faff, #e9f3ff) !important;
        border: 1px solid #bed9ff !important;
        color: #005bb9 !important;
    }

    .stAlert {
        border-radius: 12px;
        border: 1px solid var(--gp-line);
    }

    @media (max-width: 920px) {
        .gp-topbar {
            padding: 12px;
        }
    }
</style>""", unsafe_allow_html=True)

st.markdown(
    """
    <section class="gp-topbar">
      <div class="gp-brand">
        <span class="gp-brand-mark" aria-hidden="true"></span>
        <div>
          <p class="gp-eye">GENUI AGENT PRO</p>
          <h1 class="gp-title">GenUI Agent Pro</h1>
        </div>
      </div>
    </section>
    """,
    unsafe_allow_html=True,
)

ui_left, ui_right = st.columns([0.95, 1.85], gap="medium")

# ==========================================
# 3. 辅助函数：稳健的 HTML 提取器 & 缓存引擎
# ==========================================
import tempfile
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import threading
import signal
from src.modules.rag.datasheet_rag import ingest_pdf, retrieve_evidence
from src.modules.rag.constraint_extractor import extract_and_resolve_conflicts, extract_constraints_heuristic
from src.modules.verifier.constraints import ConstraintSet


class TimedModelWrapper:
    """
    Inject per-request timeout into Gemini client calls to avoid 600s UI hangs.
    """
    def __init__(self, model, timeout_seconds: int = 45, hard_timeout_seconds: int | None = None):
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._hard_timeout_seconds = hard_timeout_seconds or max(timeout_seconds + 20, 60)
        self._max_transient_retries = 1

    @staticmethod
    def _is_transient_network_error(exc: Exception) -> bool:
        name = type(exc).__name__
        msg = str(exc).lower()
        transient_types = {"ServiceUnavailable", "RetryError", "DeadlineExceeded", "TimeoutError"}
        if name in transient_types:
            return True
        return (
            "failed to connect" in msg
            or "unavailable" in msg
            or "timed out" in msg
            or "tcp handshaker shutdown" in msg
        )

    def _invoke_with_request_timeout(self, *args, **kwargs):
        """Single model invocation with best-effort SDK timeout injection."""
        call_kwargs = dict(kwargs)
        retry_count = int(call_kwargs.pop("_retry_count", 0))
        request_options = call_kwargs.get("request_options")
        if request_options is None:
            call_kwargs["request_options"] = {"timeout": self._timeout_seconds, "retry": None}
        elif isinstance(request_options, dict):
            merged = dict(request_options)
            if "timeout" not in merged:
                merged["timeout"] = self._timeout_seconds
            if "retry" not in merged:
                merged["retry"] = None
            call_kwargs["request_options"] = merged

        try:
            return self._model.generate_content(*args, **call_kwargs)
        except TypeError as e:
            # Some SDK versions may reject `retry` in request_options.
            if isinstance(call_kwargs.get("request_options"), dict):
                timeout_only = dict(call_kwargs)
                ro = dict(timeout_only["request_options"])
                ro.pop("retry", None)
                timeout_only["request_options"] = ro
                try:
                    return self._model.generate_content(*args, **timeout_only)
                except TypeError as timeout_type_error:
                    if "request_options" in str(timeout_type_error):
                        raise TimeoutError(
                            "Gemini SDK in this environment does not support request_options; aborting to avoid long retry hangs."
                        ) from timeout_type_error
                    raise
            if "request_options" in str(e):
                raise TimeoutError(
                    "Gemini SDK in this environment does not support request_options; aborting to avoid long retry hangs."
                ) from e
            raise
        except Exception as e:
            if self._is_transient_network_error(e) and retry_count < self._max_transient_retries:
                # One short retry for flaky network bursts.
                time.sleep(1.0)
                retry_kwargs = dict(kwargs)
                retry_kwargs["_retry_count"] = retry_count + 1
                return self._invoke_with_request_timeout(*args, **retry_kwargs)
            raise

    def _generate_with_thread_deadline(self, *args, **kwargs):
        """
        Fallback deadline when SIGALRM is unavailable (non-main thread / non-POSIX).
        """
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(self._invoke_with_request_timeout, *args, **kwargs)
        try:
            return fut.result(timeout=self._hard_timeout_seconds)
        except FuturesTimeoutError:
            fut.cancel()
            raise TimeoutError(f"Gemini generate_content exceeded hard timeout ({self._hard_timeout_seconds}s)")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def generate_content(self, *args, **kwargs):
        # Streaming responses are consumed incrementally; return stream directly.
        if kwargs.get("stream"):
            return self._invoke_with_request_timeout(*args, **kwargs)

        # Prefer SIGALRM in main thread to avoid orphan worker threads.
        if hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread():
            def _timeout_handler(_signum, _frame):
                raise TimeoutError(f"Gemini generate_content exceeded hard timeout ({self._hard_timeout_seconds}s)")

            previous_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, float(self._hard_timeout_seconds))
            try:
                return self._invoke_with_request_timeout(*args, **kwargs)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                signal.signal(signal.SIGALRM, previous_handler)

        return self._generate_with_thread_deadline(*args, **kwargs)

def _extract_constraints_with_fallback(evidence, filename: str, llm_client, timeout_seconds: int = 120):
    """
    Prefer LLM extraction; fallback to heuristic extraction on timeout/network failure.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(
        extract_and_resolve_conflicts,
        evidence,
        llm_client,
        filename,
        filename,
    )
    try:
        constraints, conflicts = fut.result(timeout=timeout_seconds)
        if constraints and constraints.constraints:
            constraints.metadata["extract_mode"] = "llm_rag"
            return constraints, conflicts
        raise RuntimeError("LLM extraction returned empty constraints")
    except FuturesTimeoutError:
        fut.cancel()
        reason = f"llm_timeout_after_{timeout_seconds}s"
    except Exception as e:
        reason = f"llm_extraction_failed:{type(e).__name__}:{str(e)[:200]}"
    finally:
        # Critical: do not block waiting on a timed-out network task.
        pool.shutdown(wait=False, cancel_futures=True)

    text_blob = "\n".join((c.text or "") for c in evidence)
    heuristic_constraints = extract_constraints_heuristic(text_blob, filename)
    fallback_set = ConstraintSet(
        device_name=filename,
        constraints=heuristic_constraints,
        metadata={
            "source": "heuristic_fallback",
            "manual": True,
            "fallback_reason": reason,
            "chunks_processed": len(evidence),
        },
    )
    return fallback_set, []


def _is_usable_constraints_cache(obj) -> bool:
    return bool(obj is not None and hasattr(obj, "constraints") and len(getattr(obj, "constraints", [])) > 0)


@st.cache_resource(show_spinner="Running Structure-Aware PDF Extraction...")
def cache_pdf_constraints(file_content: bytes, filename: str, _llm_client):
    """
    Core Optimization: Parses PDF, extracts tables, and retrieves constraints.
    Cached until file changes or server restart.
    """
    # Create temp file for pdfplumber
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        # A. Structure-Aware Ingestion (pdfplumber)
        index = ingest_pdf(tmp_path)
        
        # B. Retrieval
        queries = [
            "Absolute Maximum Ratings", 
            "Recommended Operating Conditions", 
            "Electrical Characteristics",
            "Full Scale Range",
            "Selectable options",
            "Configuration register",
            "Pin Configuration",
        ]
        evidence = retrieve_evidence(index, queries, top_k=12) 
        
        # C. Extraction (LLM first, heuristic fallback on failure/timeout)
        constraints, conflicts = _extract_constraints_with_fallback(
            evidence,
            filename=filename,
            llm_client=_llm_client,
            timeout_seconds=120,
        )

        return constraints, conflicts, evidence
        
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def infer_template_tags_from_query(query: str) -> list[str]:
    """Fallback: Infer template tags from user query if Planner fails."""
    query = query.lower()
    tags = []

    keywords = {
        "chart": "chart", "graph": "chart", "plot": "chart",
        "dashboard": "dashboard",
        "quiz": "quiz", "test": "quiz", "exam": "quiz",
        "timeline": "timeline", "history": "timeline",
        "card": "card", "flip": "card",
        "tab": "tabs", "nav": "tabs",
        "modal": "modal", "popup": "modal", "dialog": "modal",
        "carousel": "carousel", "slider": "carousel", "gallery": "carousel",
        "form": "form", "input": "form", "login": "form", "sign": "form"
    }

    for kw, tag in keywords.items():
        if kw in query:
            tags.append(tag)

    return list(set(tags))


def _repair_html_with_model(model, raw_text: str) -> str:
    """Uses the model to repair truncated HTML."""
    try:
        repair_prompt = (
            "The following HTML code is incomplete or truncated."
            "Please continue generating it to make it a valid, complete HTML document."
            "Ensure all tags are closed (especially </html>)."
            "Output ONLY the HTML code.\n\n"
            "TRUNCATED CODE:\n"
            + raw_text[-2000:]
        )
        response = model.generate_content(repair_prompt)
        return response.text
    except Exception as e:
        print(f"Repair failed: {e}")
        return ""


def extract_html_wrapper(text, model=None):
    """Wrapper for html_extractor with repairs."""
    def repair_callback(broken_text):
        if not model:
            return ""
        if st.session_state.get("_repair_triggered"):
            return ""

        st.session_state["_repair_triggered"] = True
        with st.spinner("Repairing incomplete HTML..."):
            repaired = _repair_html_with_model(model, broken_text)
            return repaired

    if "_repair_triggered" not in st.session_state:
        st.session_state["_repair_triggered"] = False

    st.session_state["_repair_triggered"] = False
    html_code, meta = html_extractor.extract_html(text, repair_fn=repair_callback)

    if meta.get("repaired"):
        st.info(meta.get("reason"))

    if not meta.get("valid"):
        reason = meta.get("reason", "Unknown error")
        safe_preview = text.replace("<", "&lt;")[:500]
        fallback_html = f"""<!DOCTYPE html>
<html>
<head><title>Generation Error</title></head>
<body>
    <div style="padding: 2rem; color: #ef4444; font-family: sans-serif;">
        <h2>HTML Parsing Failed</h2>
        <p>Could not extract valid HTML from the response.</p>
        <p><strong>Reason:</strong> {reason}</p>
        <h3>Raw Output Preview:</h3>
        <pre style="background: #1e293b; color: #e2e8f0; padding: 1rem; overflow: auto;">{safe_preview}...</pre>
    </div>
</body>
</html>"""
        return fallback_html

    return html_code


def _safe_read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _format_ms(ms) -> str:
    try:
        ms_val = float(ms)
    except Exception:
        return "--"
    if ms_val < 1000:
        return f"{int(ms_val)}ms"
    return f"{ms_val / 1000:.2f}s"


def _short_ts(ts_utc: str) -> str:
    if not ts_utc:
        return "--:--:--"
    s = str(ts_utc)
    if "T" in s:
        s = s.split("T", 1)[1]
    s = s.replace("Z", "")
    if "+" in s:
        s = s.split("+", 1)[0]
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def _event_to_text(ev: dict) -> str:
    et = (ev or {}).get("event_type", "")
    payload = (ev or {}).get("payload") or {}
    mapping = {
        "ui_run_start": "任务已启动",
        "phase2_architect_start": "开始生成 DSL 结构",
        "phase2_architect_done": "DSL 结构生成完成",
        "phase2_render_start": "开始渲染 HTML",
        "phase2_render_done": "HTML 渲染完成",
        "phase2_symbolic_repair_done": "符号修复完成",
        "phase2_degraded_fallback": "触发降级回退策略",
        "command_guard": "约束守卫执行",
        "ack": "应用自动修复",
        "telemetry": "指标写入完成",
        "error": "流程结束，存在错误",
    }
    base = mapping.get(et, et or "event")
    if et == "phase2_architect_done":
        dsl_chars = payload.get("dsl_chars")
        if dsl_chars is not None:
            base = f"{base} ({dsl_chars} chars)"
    elif et == "phase2_render_done":
        out_chars = payload.get("output_chars")
        if out_chars is not None:
            base = f"{base} ({out_chars} chars)"
    elif et == "phase2_symbolic_repair_done":
        fixes = payload.get("fixes_count", 0)
        base = f"{base} (fixes={fixes})"
    elif et == "error":
        err_type = payload.get("type", "")
        if err_type:
            base = f"{base} ({err_type})"
    elif et == "telemetry":
        success = payload.get("success")
        if success is not None:
            base = f"{base} (success={str(bool(success)).lower()})"
    return base


def _build_stage_lines(run_dir: Path) -> list[str]:
    timing_path = run_dir / "timing.json"
    timing = _safe_read_json(timing_path)
    if not isinstance(timing, dict):
        return []
    label_map = {
        "plan": "Plan",
        "architect": "Architect",
        "architect_dsl": "Architect DSL",
        "acquire_constraints": "Acquire Constraints",
        "architect_gen": "Architect Gen",
        "verification_loop": "Verification Loop",
        "lint_pre": "Lint Pre",
        "review": "Review",
        "lint_post": "Lint Post",
        "render": "Render",
        "export": "Export",
        "total": "Total",
    }
    order = [
        "acquire_constraints",
        "plan",
        "architect",
        "architect_dsl",
        "architect_gen",
        "verification_loop",
        "lint_pre",
        "review",
        "lint_post",
        "render",
        "export",
        "total",
    ]
    lines: list[str] = []
    for key in order:
        if key in timing:
            lines.append(f"- `{label_map.get(key, key)}`: {_format_ms(timing.get(key))}")
    for key, value in timing.items():
        if key not in order:
            lines.append(f"- `{key}`: {_format_ms(value)}")
    return lines


def _build_event_lines(run_dir: Path, limit: int = 20) -> list[str]:
    events = read_events(run_dir)
    if not events:
        return []
    lines: list[str] = []
    for ev in events[-limit:]:
        ts = _short_ts(ev.get("ts_utc", ""))
        lines.append(f"- `{ts}` {_event_to_text(ev)}")
    return lines


def _build_audit_lines(run_dir: Path, limit: int = 15) -> list[str]:
    trail_path = run_dir / "agent_audit_trail.jsonl"
    if not trail_path.exists():
        return []
    rows: list[str] = []
    try:
        raw_lines = trail_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in raw_lines[-limit:]:
            obj = json.loads(line)
            round_idx = obj.get("round_idx", "-")
            state = obj.get("state", "")
            action = obj.get("action", "")
            score = obj.get("score", "")
            rows.append(f"- `Round {round_idx}` `{state}` `{action}` score={score}")
    except Exception:
        return []
    return rows


def _build_process_markdown(run_dir: str, metrics: dict) -> str:
    if not run_dir:
        return ""
    run_path = Path(run_dir)
    sections: list[str] = []
    stage_lines = _build_stage_lines(run_path)
    if stage_lines:
        sections.append("**Stages**")
        sections.extend(stage_lines)
    event_lines = _build_event_lines(run_path)
    if event_lines:
        sections.append("**Runtime Events**")
        sections.extend(event_lines)
    audit_lines = _build_audit_lines(run_path)
    if audit_lines:
        sections.append("**Closed-Loop Audit**")
        sections.extend(audit_lines)
    if metrics:
        sections.append("**Result Metrics**")
        success = metrics.get("success")
        total_ms = metrics.get("total_ms")
        fixes = metrics.get("fixes_count", metrics.get("proactive_fixes_count", 0))
        rounds = metrics.get("closed_loop_rounds", metrics.get("dsl_parse_rounds", metrics.get("repair_rounds", 0)))
        sections.append(f"- `success`: {success}")
        sections.append(f"- `latency`: {_format_ms(total_ms)}")
        sections.append(f"- `fixes`: {fixes}")
        sections.append(f"- `rounds`: {rounds}")
    return "\n".join(sections)


def _estimate_live_progress(run_dir: Path, events: list[dict], elapsed_sec: float) -> tuple[int, str]:
    progress = 5
    label = "初始化"
    if (run_dir / "input.json").exists():
        progress, label = max(progress, 15), "任务初始化完成"
    if (run_dir / "constraints.json").exists():
        progress, label = max(progress, 30), "约束加载完成"
    if (run_dir / "dsl_raw_r0.txt").exists() or (run_dir / "model_raw.txt").exists():
        progress, label = max(progress, 48), "结构生成中"
    if (run_dir / "dsl_validated.json").exists():
        progress, label = max(progress, 68), "结构校验中"
    if (run_dir / "verifier_report.json").exists() or (run_dir / "lint_report.json").exists():
        progress, label = max(progress, 82), "安全检查中"
    if (run_dir / "final.html").exists():
        progress, label = max(progress, 95), "渲染输出中"

    event_types = {ev.get("event_type") for ev in events}
    if "phase2_architect_start" in event_types:
        progress, label = max(progress, 46), "生成 DSL 中"
    if "phase2_architect_done" in event_types:
        progress, label = max(progress, 62), "DSL 已生成"
    if "phase2_symbolic_repair_done" in event_types:
        progress, label = max(progress, 83), "自动修复中"
    if "phase2_render_start" in event_types:
        progress, label = max(progress, 90), "渲染 HTML 中"
    if "phase2_render_done" in event_types:
        progress, label = max(progress, 97), "渲染完成"
    if "telemetry" in event_types:
        progress, label = 100, "完成"
    if "error" in event_types:
        progress, label = max(progress, 96), "完成（含错误）"

    synthetic = min(88, 8 + int(elapsed_sec * 3.5))
    progress = max(progress, synthetic)
    return min(progress, 100), label


# ==========================================
# 4. 左侧配置面板
# ==========================================
with ui_left:
    with st.container(border=True):
        st.markdown('<p class="gp-section-title">运行环境</p>', unsafe_allow_html=True)
        api_key = st.text_input("Gemini API Key", type="password")

    model_options = [
        "models/gemini-3-flash-preview",
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash",
        "models/gemini-2.5-flash-lite",
        "models/gemma-3-27b-it",
        "models/gemma-3-12b-it",
    ]

    with st.container(border=True):
        st.markdown('<p class="gp-section-title">模型与模式</p>', unsafe_allow_html=True)
        selected_model = st.selectbox(
            "Model",
            model_options,
            index=0,
        )
        pipeline_mode = st.selectbox(
            "Pipeline",
            ["Legacy (Direct HTML)", "Phase 1 (DSL)", "Phase 2 (Neuro-Symbolic)"],
            index=2,
        )
        ablation_label = st.selectbox(
            "Ablation",
            [
                "Full Pipeline",
                "Baseline HTML",
                "DSL without Verifier",
                "Verifier without RAG",
                "RAG without Extractor",
            ],
            index=0,
        )

    ablation_map = {
        "Full Pipeline": "full_pipeline",
        "Baseline HTML": "baseline_html",
        "DSL without Verifier": "dsl_no_verifier",
        "Verifier without RAG": "verifier_no_rag",
        "RAG without Extractor": "rag_no_extractor",
    }
    ablation_mode_name = ablation_map[ablation_label]
    ablation_cfg = AblationConfig.from_mode_name(ablation_mode_name)

    use_dsl_mode = pipeline_mode in ["Phase 1 (DSL)", "Phase 2 (Neuro-Symbolic)"]
    use_phase2 = pipeline_mode == "Phase 2 (Neuro-Symbolic)"

    if ablation_cfg.baseline_html_mode:
        use_dsl_mode = False
        use_phase2 = False
        pipeline_mode = "Legacy (Direct HTML)"
    elif ablation_cfg.dsl_mode_no_verifier and use_phase2:
        use_dsl_mode = True
        use_phase2 = False
        pipeline_mode = "Phase 1 (DSL)"

    with st.container(border=True):
        st.markdown('<p class="gp-section-title">安全开关</p>', unsafe_allow_html=True)
        enable_verifier = st.checkbox(
            "Enable Verifier",
            value=not ablation_cfg.dsl_mode_no_verifier,
            disabled=not use_phase2,
        )
        enable_closed_loop = st.checkbox(
            "Enable Self-Correction",
            value=True,
            disabled=(not use_phase2 or not enable_verifier),
        )

    with st.container(border=True):
        st.markdown('<p class="gp-section-title">约束输入</p>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader(
            "Upload File",
            type=["pdf", "txt", "json"],
            key="single_uploader",
        )
        upload_progress_slot = st.empty()
        upload_status_slot = st.empty()
        upload_trace_slot = st.empty()
        chunk_size = st.slider("Chunk Size", 500, 3000, 1200)
        chunk_overlap = st.slider("Chunk Overlap", 0, 500, 150)
        rag_top_k = st.number_input("Top-K", 1, 10, 5)
        rag_max_chunk_chars = 1200

style_names = get_style_names()
selected_style = "Dark Mode" if "Dark Mode" in style_names else style_names[0]

phase2_config = {
    "enable_verifier": bool(use_phase2 and enable_verifier),
    "enable_closed_loop": bool(use_phase2 and enable_verifier and enable_closed_loop),
    "constraints_source": "sample",
    "constraints_file_path": None,
    "max_rounds": 2,
    "style": selected_style,
    "constraints_override": None,
    "ablation_mode": ablation_cfg.mode_name,
    "verifier_no_rag": ablation_cfg.verifier_no_rag,
    "rag_no_extractor": ablation_cfg.rag_no_extractor,
}

file_context = ""
cached_constraints_obj = None
constraints_file_path = None

upload_lines: list[str] = []

def _upload_step(progress: int, text: str):
    p = max(0, min(100, int(progress)))
    upload_progress_slot.progress(p / 100, text=f"{p}%")
    upload_status_slot.caption(text)
    upload_lines.append(f"- {text}")
    upload_trace_slot.markdown("\n".join(upload_lines))

if uploaded_file:
    try:
        file_ext = uploaded_file.name.split(".")[-1].lower()
        _upload_step(8, f"检测到文件：{uploaded_file.name}")

        if file_ext == "pdf":
            pdf_bytes = uploaded_file.getvalue()
            pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
            st.session_state["pdf_filename"] = uploaded_file.name
            _upload_step(18, "读取 PDF 字节并计算哈希")

            if (
                st.session_state.get("cached_constraints") is not None
                and st.session_state.get("cached_constraints_hash") == pdf_hash
                and _is_usable_constraints_cache(st.session_state.get("cached_constraints"))
            ):
                cached_constraints_obj = st.session_state.get("cached_constraints")
                _upload_step(100, f"文件上传处理完成（命中缓存，{len(cached_constraints_obj.constraints)} rules）")
                st.success("文件上传完成")
            elif (
                st.session_state.get("cached_constraints_hash") == pdf_hash
                and st.session_state.get("cached_constraints") is not None
                and not _is_usable_constraints_cache(st.session_state.get("cached_constraints"))
            ):
                st.warning("Cached constraints are invalid. Re-extracting.")
                try:
                    cache_pdf_constraints.clear()
                except Exception:
                    pass

            need_extract = (
                st.session_state.get("cached_constraints_hash") != pdf_hash
                or not _is_usable_constraints_cache(st.session_state.get("cached_constraints"))
            )
            if need_extract:
                if api_key:
                    _upload_step(38, "准备调用模型进行约束抽取")
                    _configure_genai_client(api_key)
                    clean_model_name = selected_model
                    if "/" in clean_model_name:
                        clean_model_name = clean_model_name.split("/")[-1]
                    model_for_cache = TimedModelWrapper(
                        genai.GenerativeModel(clean_model_name),
                        timeout_seconds=45,
                    )
                    _upload_step(62, "抽取与解析约束中")
                    with st.spinner("Processing PDF..."):
                        constraints_obj, conflicts, evidence = cache_pdf_constraints(
                            pdf_bytes,
                            uploaded_file.name,
                            model_for_cache,
                        )
                        cached_constraints_obj = constraints_obj
                        st.session_state["cached_constraints"] = constraints_obj
                        st.session_state["cached_constraints_hash"] = pdf_hash
                        _upload_step(88, f"约束抽取完成：{len(constraints_obj.constraints)} rules")
                        if constraints_obj.metadata.get("source") == "heuristic_fallback":
                            st.warning("LLM extraction failed. Heuristic extraction was used.")
                        if conflicts:
                            target_count = len({c.get("target") for c in conflicts if c.get("target")})
                            st.warning(f"Resolved {len(conflicts)} conflicts across {target_count} targets")
                    st.session_state["file_chunks"] = [c.text for c in evidence]
                    file_context = "[DATASHEET_PROCESSED]"
                    _upload_step(100, "文件上传处理完成")
                    st.success("文件上传完成")
                else:
                    _upload_step(100, "文件上传完成，等待 API Key 进行约束抽取")
                    st.success("文件上传完成")
                    st.warning("Missing API key. PDF constraints were not extracted.")

            if st.session_state.get("file_chunks"):
                file_context = "[DATASHEET_PROCESSED]"

        elif file_ext == "json":
            _upload_step(30, "解析 JSON 约束文件")
            os.makedirs("temp", exist_ok=True)
            constraints_file_path = f"temp/{uploaded_file.name}"
            with open(constraints_file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            _upload_step(100, "文件上传处理完成")
            st.success("文件上传完成")

        elif file_ext == "txt":
            _upload_step(30, "解析文本并切分上下文")
            raw_text = uploaded_file.read().decode("utf-8", errors="ignore")
            st.session_state["file_chunks"] = split_text_recursive(raw_text, chunk_size, chunk_overlap)
            file_context = "[UPLOADED_TEXT_CHUNKS_AVAILABLE]"
            _upload_step(100, "文件上传处理完成")
            st.success("文件上传完成")

    except Exception as e:
        _upload_step(100, "文件处理失败")
        st.error(f"File parse error: {e}")

if use_phase2 and cached_constraints_obj:
    phase2_config["constraints_override"] = cached_constraints_obj
    phase2_config["constraints_source"] = "datasheet_pdf"
elif use_phase2 and constraints_file_path:
    phase2_config["constraints_file_path"] = constraints_file_path
    phase2_config["constraints_source"] = "manual_json"

if "ui_last_html" not in st.session_state:
    st.session_state["ui_last_html"] = ""
if "ui_last_metrics" not in st.session_state:
    st.session_state["ui_last_metrics"] = {}
if "ui_last_success" not in st.session_state:
    st.session_state["ui_last_success"] = None
if "ui_last_run_id" not in st.session_state:
    st.session_state["ui_last_run_id"] = ""
if "ui_last_run_dir" not in st.session_state:
    st.session_state["ui_last_run_dir"] = ""
if "ui_last_process_md" not in st.session_state:
    st.session_state["ui_last_process_md"] = ""

# ==========================================
# 5. 主逻辑：ReAct Agent 流程
# ==========================================
with ui_right:
    with st.container(border=True):
        st.markdown('<p class="gp-section-title">Prompt</p>', unsafe_allow_html=True)
        user_prompt = st.text_area(
            "User Request",
            height=150,
            placeholder="Design an industrial dashboard with sliders, gauges, and constraints.",
        )
        run_clicked = st.button("Run", use_container_width=True)

    with st.container(border=True):
        workspace_container = st.container()

with workspace_container:
    with st.container(border=True):
        st.markdown('<p class="gp-section-title">生成过程</p>', unsafe_allow_html=True)
        live_progress_slot = st.empty()
        live_trace_slot = st.empty()

    if run_clicked:
        if not api_key:
            live_progress_slot.progress(0.0, text="0%")
            live_trace_slot.markdown("- 缺少 API Key")
            st.warning("Please provide Gemini API Key.")
        elif not user_prompt:
            live_progress_slot.progress(0.0, text="0%")
            live_trace_slot.markdown("- 缺少用户请求")
            st.warning("Please enter a user request.")
        else:
            runs_base = Path(__file__).resolve().parent / "runs"
            ra = RunArtifacts.create(base_dir=runs_base)
            _rt_log(
                ra.run_dir,
                "ui_run_start",
                {"model": selected_model, "mode": pipeline_mode, "ablation_mode": ablation_mode_name},
            )
            cached_constraints_for_run = st.session_state.get("cached_constraints")

            live_progress_slot.progress(0.08, text="8% · 任务已启动")
            live_trace_slot.markdown("- 任务已启动")

            run_result = {
                "success": False,
                "final_html_code": "",
                "metrics": {},
                "run_dir_for_view": str(ra.run_dir),
                "alerts": [],
                "error_text": "",
            }

            def _run_worker():
                success = False
                final_html_code = ""
                metrics = {}
                run_dir_for_view = ra.run_dir
                alerts = []
                try:
                    _configure_genai_client(api_key)
                    clean_model_name = selected_model
                    if "/" in clean_model_name:
                        clean_model_name = clean_model_name.split("/")[-1]

                    model = TimedModelWrapper(
                        genai.GenerativeModel(clean_model_name),
                        timeout_seconds=45,
                    )

                    if use_dsl_mode:
                        if use_phase2:
                            if cached_constraints_for_run:
                                if (
                                    phase2_config.get("constraints_source") != "sample"
                                    and not phase2_config.get("verifier_no_rag", False)
                                ):
                                    phase2_config["constraints_override"] = cached_constraints_for_run
                                    phase2_config["constraints_source"] = "datasheet_pdf"

                            final_html_code, metrics = run_phase2_pipeline(
                                ra=ra,
                                user_prompt=user_prompt,
                                model=model,
                                config=phase2_config,
                            )

                            is_error_page = "Generation Interrupted" in final_html_code
                            is_degraded = bool(metrics.get("degraded_fallback", False))
                            if phase2_config.get("enable_closed_loop", False):
                                phase2_pass = bool(metrics.get("closed_loop_success", False))
                            else:
                                phase2_pass = bool(metrics.get("verifier_passed", False))
                            success = bool(final_html_code) and (((phase2_pass and (not is_error_page))) or is_degraded)
                        else:
                            final_html_code, metrics = run_dsl_pipeline(
                                ra=ra,
                                user_prompt=user_prompt,
                                model=model,
                                selected_style=selected_style,
                                file_context=file_context,
                                max_repair_rounds=2,
                            )
                        if not use_phase2:
                            success = bool(final_html_code) and bool(metrics.get("validation_success", False))
                    else:
                        from src.core.phase0_core import run_baseline_once

                        legacy_run_dir, metrics = run_baseline_once(
                            runs_dir=runs_base,
                            user_prompt=user_prompt,
                            selected_model=selected_model,
                            selected_style=selected_style,
                            llm=model,
                            streaming=True,
                            on_chunk=lambda text: None,
                        )
                        run_dir_for_view = legacy_run_dir
                        final_html_path = legacy_run_dir / "final.html"
                        if final_html_path.exists():
                            final_html_code = final_html_path.read_text(encoding="utf-8")
                            success = True
                        else:
                            success = False

                    if use_phase2 and metrics.get("prompt_conflicts_detected", 0) > 0:
                        alerts.append((
                            "warning",
                            f"Detected {metrics['prompt_conflicts_detected']} prompt-vs-datasheet contradiction(s). "
                            "Output was constrained to datasheet limits.",
                        ))
                    if use_phase2 and metrics.get("fixes_count", 0) > 0:
                        alerts.append(("warning", f"Safety intervention: {metrics['fixes_count']} parameter(s) were auto-corrected."))
                    if metrics.get("quota_exceeded"):
                        alerts.append(("error", "Gemini API quota exceeded."))

                    run_result["success"] = bool(success)
                    run_result["final_html_code"] = final_html_code
                    run_result["metrics"] = metrics
                    run_result["run_dir_for_view"] = str(run_dir_for_view)
                    run_result["alerts"] = alerts
                except Exception as e:
                    err = ra.record_error(e, where="main_loop")
                    ra.write_json("errors.json", err)
                    run_result["success"] = False
                    run_result["final_html_code"] = final_html_code
                    run_result["metrics"] = {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                    run_result["run_dir_for_view"] = str(ra.run_dir)
                    run_result["alerts"] = [("error", f"Execution error: {e}")]
                    run_result["error_text"] = str(e)

            worker_thread = threading.Thread(target=_run_worker, daemon=True)
            worker_thread.start()
            poll_t0 = time.time()

            while worker_thread.is_alive():
                monitor_run_dir = Path(run_result.get("run_dir_for_view") or str(ra.run_dir))
                events = read_events(monitor_run_dir) if monitor_run_dir.exists() else []
                pct, status_label = _estimate_live_progress(monitor_run_dir, events, time.time() - poll_t0)
                live_progress_slot.progress(pct / 100, text=f"{pct}% · {status_label}")
                event_lines = _build_event_lines(monitor_run_dir, limit=8)
                if event_lines:
                    live_trace_slot.markdown("\n".join(event_lines))
                else:
                    live_trace_slot.markdown(f"- 运行中，已耗时 {time.time() - poll_t0:.1f}s")
                time.sleep(0.35)

            worker_thread.join()
            final_run_dir = str(run_result.get("run_dir_for_view") or str(ra.run_dir))
            process_md = _build_process_markdown(final_run_dir, run_result.get("metrics", {}))
            live_progress_slot.progress(1.0, text="100% · 完成")
            if process_md:
                live_trace_slot.markdown(process_md)
            else:
                live_trace_slot.markdown("- 运行完成")

            for level, msg in run_result.get("alerts", []):
                if level == "warning":
                    st.warning(msg)
                elif level == "error":
                    st.error(msg)
                else:
                    st.info(msg)

            st.session_state["ui_last_html"] = run_result.get("final_html_code", "")
            st.session_state["ui_last_metrics"] = run_result.get("metrics", {})
            st.session_state["ui_last_success"] = bool(run_result.get("success", False))
            st.session_state["ui_last_run_id"] = Path(final_run_dir).name if final_run_dir else ""
            st.session_state["ui_last_run_dir"] = final_run_dir
            st.session_state["ui_last_process_md"] = process_md
    else:
        last_process_md = st.session_state.get("ui_last_process_md", "")
        if last_process_md:
            live_progress_slot.progress(1.0, text="100% · 上次运行结果")
            live_trace_slot.markdown(last_process_md)
        else:
            live_progress_slot.progress(0.0, text="0%")
            live_trace_slot.markdown("- 等待运行")

    last_html = st.session_state.get("ui_last_html", "")
    last_metrics = st.session_state.get("ui_last_metrics", {}) or {}
    last_success = st.session_state.get("ui_last_success", None)
    last_run_id = st.session_state.get("ui_last_run_id", "")
    last_run_dir = st.session_state.get("ui_last_run_dir", "")

    fixes_count = int(last_metrics.get("fixes_count", last_metrics.get("proactive_fixes_count", 0)) or 0)
    rounds_count = int(
        last_metrics.get(
            "closed_loop_rounds",
            last_metrics.get("dsl_parse_rounds", last_metrics.get("repair_rounds", 0)),
        )
        or 0
    )
    total_ms = last_metrics.get("total_ms")
    if isinstance(total_ms, (int, float)) and total_ms > 0:
        latency_text = f"{float(total_ms) / 1000:.2f}s"
    else:
        latency_text = "--"
    success_text = "--" if last_success is None else ("true" if bool(last_success) else "false")
    progress_percent = 100 if (last_run_id or last_html or last_metrics) else 0

    st.markdown(
        f"""
        <div class="gp-metric-head">
          <div class="gp-section-title" style="margin:0;">生成结果</div>
          <div class="gp-pill-row">
            <span class="gp-pill">Success: {success_text}</span>
            <span class="gp-pill">Fixes: {fixes_count}</span>
            <span class="gp-pill">Rounds: {rounds_count}</span>
            <span class="gp-pill">Latency: {latency_text}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<p class="gp-progress-label">Pipeline Progress</p>', unsafe_allow_html=True)
    st.progress(progress_percent / 100, text=f"{progress_percent}%")

    tab_preview, tab_html, tab_process, tab_inspector = st.tabs(["Preview", "HTML Code", "Process", "Inspector"])

    with tab_preview:
        if last_html:
            st.components.v1.html(last_html, height=820, scrolling=True)
        else:
            st.info("No output yet.")

    with tab_html:
        if last_html:
            st.download_button("Download HTML", last_html, "gen_ui.html", "text/html")
            st.code(last_html, language="html")
        else:
            st.info("No output yet.")

    with tab_process:
        last_process_md = st.session_state.get("ui_last_process_md", "")
        if last_process_md:
            st.markdown(last_process_md)
        else:
            st.info("No process log yet.")

    with tab_inspector:
        st.json(last_metrics)
        if last_run_id:
            st.caption(f"Run ID: {last_run_id}")
        if last_run_dir:
            st.caption(f"Path: {last_run_dir}")
            session_log = Path(last_run_dir) / "session_log.json"
            if session_log.exists():
                st.download_button(
                    "Download Session Log",
                    session_log.read_text(encoding="utf-8"),
                    "session_log.json",
                    "application/json",
                )
            cons_path = Path(last_run_dir) / "constraints.json"
            if cons_path.exists():
                with open(cons_path) as f:
                    st.json(json.load(f))
