# GenUI_Project (Phase 0 Baseline)

This repository contains a Phase 0 baseline pipeline:
**LLM → HTML → lint → (optional self-repair) → preview/export**
with a **run_dir artifact contract** for reproducible experiments.

## Quickstart

### Create venv + install deps
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
