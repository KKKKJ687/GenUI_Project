"""Lightweight, deterministic dataframe summarization for prompt injection.

This module intentionally avoids heavy dependencies beyond pandas/numpy.
It produces a compact, JSON-serializable dict that captures:
- schema (columns, dtypes, missing)
- a small sample (first 5 rows)
- basic stats (numeric + categorical)

Used by app.py (Phase 2.3: structured data preprocessing).
"""

from __future__ import annotations

from typing import Any, Dict, List

import math

import pandas as pd


def _to_jsonable(value: Any) -> Any:
    """Convert pandas/numpy scalars to plain JSON-serializable Python types."""
    # pandas uses NaN/NaT
    if value is None:
        return None
    try:
        # NaN
        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    # pandas Timestamp
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        try:
            return value.isoformat()
        except Exception:
            pass
    # numpy scalar -> python scalar
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def summarize_dataframe(
    df: pd.DataFrame,
    *,
    sample_rows: int = 5,
    top_values: int = 5,
    round_digits: int = 6,
) -> Dict[str, Any]:
    """Summarize a dataframe into a compact dict for LLM consumption.

    Required keys:
      - shape
      - columns: list[{name,dtype,missing_count,missing_rate}]
      - sample_rows: list[records]
      - stats: dict
    """

    if df is None:
        df = pd.DataFrame()

    rows, cols = int(df.shape[0]), int(df.shape[1])

    # Column schema
    col_summaries: List[Dict[str, Any]] = []
    if cols > 0:
        missing_by_col = df.isna().sum()
        for name in df.columns:
            miss = int(missing_by_col.get(name, 0))
            miss_rate = (miss / rows) if rows > 0 else 0.0
            col_summaries.append(
                {
                    "name": str(name),
                    "dtype": str(df[name].dtype),
                    "missing_count": miss,
                    "missing_rate": round(float(miss_rate), round_digits),
                }
            )

    # Sample rows (first N) as JSONable records
    n = min(int(sample_rows), rows) if rows > 0 else 0
    sample: List[Dict[str, Any]] = []
    if n > 0 and cols > 0:
        recs = df.head(n).to_dict(orient="records")
        for r in recs:
            sample.append({str(k): _to_jsonable(v) for k, v in r.items()})

    # Stats
    numeric_cols = list(df.select_dtypes(include=["number"]).columns)
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]

    numeric_stats: Dict[str, Any] = {}
    for c in numeric_cols:
        s = df[c]
        miss = int(s.isna().sum())
        # Convert to python scalars; allow empty all-NaN columns
        try:
            c_min = s.min(skipna=True)
            c_max = s.max(skipna=True)
            c_mean = s.mean(skipna=True)
        except Exception:
            c_min = c_max = c_mean = None
        numeric_stats[str(c)] = {
            "missing_count": miss,
            "min": _to_jsonable(round(float(c_min), round_digits) if c_min is not None and pd.notna(c_min) else None),
            "max": _to_jsonable(round(float(c_max), round_digits) if c_max is not None and pd.notna(c_max) else None),
            "mean": _to_jsonable(round(float(c_mean), round_digits) if c_mean is not None and pd.notna(c_mean) else None),
        }

    categorical_stats: Dict[str, Any] = {}
    for c in non_numeric_cols:
        s = df[c]
        miss = int(s.isna().sum())
        try:
            uniq = int(s.nunique(dropna=True))
        except Exception:
            uniq = 0
        top_list: List[Dict[str, Any]] = []
        try:
            vc = s.dropna().astype(str).value_counts().head(int(top_values))
            for val, cnt in vc.items():
                top_list.append({"value": str(val), "count": int(cnt)})
        except Exception:
            top_list = []
        categorical_stats[str(c)] = {
            "missing_count": miss,
            "unique_count": uniq,
            "top_values": top_list,
        }

    return {
        "shape": {"rows": rows, "columns": cols},
        "columns": col_summaries,
        "sample_rows": sample,
        "stats": {
            "numeric": numeric_stats,
            "categorical": categorical_stats,
            "row_count": rows,
            "column_count": cols,
            "numeric_columns": [str(c) for c in numeric_cols],
            "categorical_columns": [str(c) for c in non_numeric_cols],
        },
    }
