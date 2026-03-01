#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.phase0_core import run_baseline_once
from src.agents.mock_llm import MockLLM


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase0 health_check: deps + minimal baseline pipeline + run_dir artifacts")
    ap.add_argument("--runs-dir", default="runs", help="runs directory (default: runs)")
    ap.add_argument("--mock-llm", action="store_true", help="run offline using deterministic MockLLM")
    ap.add_argument("--api-key", default="", help="Gemini API key (optional; can also use env GOOGLE_API_KEY)")
    ap.add_argument("--model", default="models/gemini-pro-latest", help="model id for non-mock mode")
    ap.add_argument("--style", default="Dark Mode", help="style name (baseline)")
    ap.add_argument("--prompt", default="Health check: generate a minimal dashboard.", help="user prompt")
    ap.add_argument("--no-stream", action="store_true", help="disable streaming in pipeline")
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir).resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)

    # ---- Dependency checks (minimal, do not fail mock mode) ----
    deps = {
        "pypdf": _try_import("pypdf"),
        "pandas": _try_import("pandas"),
        "google.generativeai": _try_import("google.generativeai"),
    }

    # ---- Choose LLM ----
    if args.mock_llm:
        llm: Any = MockLLM()
        selected_model = "mock-llm"
    else:
        api_key = args.api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            print("FAIL: missing API key (provide --api-key or set GOOGLE_API_KEY), or run with --mock-llm")
            return 1
        if not deps["google.generativeai"]:
            print("FAIL: google.generativeai not installed; install requirements or run with --mock-llm")
            return 1

        import google.generativeai as genai
        genai.configure(api_key=api_key)

        # Some SDKs need model id without 'models/' prefix; we try both.
        try:
            llm = genai.GenerativeModel(args.model)
            selected_model = args.model
        except Exception:
            if args.model.startswith("models/"):
                llm = genai.GenerativeModel(args.model.split("/", 1)[1])
                selected_model = args.model.split("/", 1)[1]
            else:
                raise

    # ---- Run minimal baseline pipeline (always creates run_dir) ----
    run_dir, metrics = run_baseline_once(
        runs_dir=runs_dir,
        user_prompt=args.prompt,
        selected_model=selected_model,
        selected_style=args.style,
        llm=llm,
        streaming=(not args.no_stream),
    )

    ok = bool(metrics.get("success"))
    status = "PASS" if ok else "FAIL"

    # Print a stable, parseable output line (tests rely on it)
    print(f"{status} run_dir={run_dir}")
    if args.mock_llm:
        print("mode=mock-llm (offline deterministic)")
    else:
        print("mode=real-llm (networked)")

    # Optional: show missing deps (non-fatal in mock mode)
    missing = [k for k, v in deps.items() if not v]
    if missing:
        print(f"deps_missing={missing}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
