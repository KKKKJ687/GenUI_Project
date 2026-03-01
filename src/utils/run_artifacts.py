from __future__ import annotations

import json
import os
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterator, Optional


def _utc_timestamp_compact() -> str:
    # e.g. 20260129_153012_123 (ms)
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S_%f")[:-3]


def _short_id(n: int = 6) -> str:
    import secrets
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """
    Atomic file write: write to a temp file in the same directory then replace.
    """
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text if text is not None else "")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any, *, encoding: str = "utf-8") -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2), encoding=encoding)


@dataclass
class RunArtifacts:
    run_dir: Path
    created_utc: str
    run_id: str
    _t0: float = field(default_factory=perf_counter)
    _stage_t0: Dict[str, float] = field(default_factory=dict)
    timing_ms: Dict[str, int] = field(default_factory=dict)
    notes: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, base_dir: Path) -> "RunArtifacts":
        ensure_dir(base_dir)
        ts = _utc_timestamp_compact()
        sid = _short_id()
        run_id = f"{ts}_{sid}"
        run_dir = base_dir / run_id
        ensure_dir(run_dir)
        return cls(run_dir=run_dir, created_utc=ts, run_id=run_id)

    def path(self, filename: str) -> Path:
        return self.run_dir / filename

    def write_text(self, filename: str, text: str) -> None:
        atomic_write_text(self.path(filename), text)

    def write_json(self, filename: str, obj: Any) -> None:
        atomic_write_json(self.path(filename), obj)

    def safe_append_text_atomic(self, filename: str, extra: str) -> None:
        """
        Atomic append by read+rewrite. Safe for small artifacts (like model_raw.txt).
        """
        p = self.path(filename)
        old = ""
        try:
            if p.exists():
                old = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            old = ""
        atomic_write_text(p, old + (extra or ""))

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = perf_counter()
        self._stage_t0[name] = t0
        try:
            yield
        finally:
            dt_ms = int((perf_counter() - t0) * 1000)
            self.timing_ms[name] = dt_ms

    def finish_total(self) -> None:
        self.timing_ms["total"] = int((perf_counter() - self._t0) * 1000)

    def record_error(self, exc: BaseException, *, where: Optional[str] = None) -> Dict[str, Any]:
        return {
            "where": where or "",
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
