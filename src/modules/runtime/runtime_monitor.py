from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union


class ProtocolBaseLike(Protocol):
    """Minimal protocol surface for monitoring (duck-typed)."""
    def get_status(self) -> Dict[str, Any]: ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any) -> Any:
    """Best-effort JSON-safe conversion for status payloads."""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


@dataclass(frozen=True)
class RuntimeEvent:
    ts_utc: str
    event_type: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["payload"] = {k: _safe_json(v) for k, v in (d.get("payload") or {}).items()}
        return d


def monitor_protocol(protocol: ProtocolBaseLike) -> Dict[str, Any]:
    """Return a JSON-safe status snapshot for a protocol instance."""
    base: Dict[str, Any] = {
        "kind": protocol.__class__.__name__,
        "ts_utc": _utc_now_iso(),
    }
    try:
        status = protocol.get_status()
        if isinstance(status, dict):
            base.update({"status": {k: _safe_json(v) for k, v in status.items()}})
            return base
    except Exception as e:
        base["status_error"] = f"get_status_failed: {e}"
        
    for attr in ["name", "connected", "last_error", "last_rx_utc", "last_tx_utc", "config"]:
        if hasattr(protocol, attr):
            base[attr] = _safe_json(getattr(protocol, attr))
    return base


def monitor_command(command: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy command monitor."""
    out: Dict[str, Any] = {
        "kind": "command",
        "ts_utc": _utc_now_iso(),
    }
    for k, v in (command or {}).items():
        out[k] = _safe_json(v)
    return out

def monitor_command_with_context(command: Dict[str, Any], guard_result: Dict[str, Any], run_dir: Union[str, Path]):
    """
    Log command execution with comprehensive guard context for traceability.
    """
    event_payload = {
        "command": command,
        "allowed": guard_result.get("allowed"),
        "reason": guard_result.get("reason"),
        "rule_source": guard_result.get("source_ref"),  # Traceability to Datasheet
        "target": guard_result.get("target"),
        "action": guard_result.get("action"),
        "constraint": guard_result.get("constraint"),
        "timestamp_ms": int(time.time() * 1000),
    }
    append_event(run_dir, "command_guard", event_payload)


def append_event(run_dir: Union[str, Path], event_type: str, payload: Dict[str, Any]) -> Path:
    """Append a runtime monitoring event into run_dir/runtime_events.jsonl."""
    run_dir_p = Path(run_dir)
    run_dir_p.mkdir(parents=True, exist_ok=True)
    path = run_dir_p / "runtime_events.jsonl"
    ev = RuntimeEvent(ts_utc=_utc_now_iso(), event_type=event_type, payload=payload).to_dict()
    line = json.dumps(ev, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
    return path


def read_events(run_dir: Union[str, Path], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read runtime events."""
    path = Path(run_dir) / "runtime_events.jsonl"
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass
            if limit is not None and len(events) >= limit:
                break
    return events


def replay_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Replay runtime events into a deterministic state snapshot.
    """
    state: Dict[str, Any] = {
        "values": {},
        "acks": [],
        "errors": [],
        "telemetry": [],
        "commands_total": 0,
        "commands_denied": 0,
    }

    for ev in events:
        et = (ev or {}).get("event_type", "")
        payload = (ev or {}).get("payload") or {}

        if et in {"command", "command_guard"}:
            state["commands_total"] += 1
            if et == "command_guard":
                allowed = bool(payload.get("allowed"))
                if not allowed:
                    state["commands_denied"] += 1

                cmd = payload.get("command") or {}
                widget_id = cmd.get("widget_id") or cmd.get("target")
                cmd_value = cmd.get("value")
                if widget_id is not None and cmd_value is not None and allowed:
                    state["values"][str(widget_id)] = cmd_value
            else:
                widget_id = payload.get("widget_id") or payload.get("target")
                cmd_value = payload.get("value")
                guard = payload.get("guard")
                if isinstance(guard, dict) and (guard.get("allowed") is False):
                    state["commands_denied"] += 1
                elif widget_id is not None and cmd_value is not None:
                    state["values"][str(widget_id)] = cmd_value

        elif et == "ack":
            state["acks"].append(payload)

        elif et == "error":
            state["errors"].append(payload)

        elif et == "telemetry":
            state["telemetry"].append(payload)

    return state


def replay_run_events(run_dir: Union[str, Path]) -> Dict[str, Any]:
    """
    Convenience helper for replaying run_dir/runtime_events.jsonl.
    """
    return replay_events(read_events(run_dir))


def export_session_log(run_dir: Union[str, Path], filename: str = "session_log.json") -> Path:
    """
    Export replayable runtime session log for paper/case-study artifacts.
    """
    run_dir_p = Path(run_dir)
    run_dir_p.mkdir(parents=True, exist_ok=True)
    events = read_events(run_dir_p)
    replay = replay_events(events)
    out = run_dir_p / filename
    out.write_text(
        json.dumps(
            {
                "generated_utc": _utc_now_iso(),
                "events": events,
                "replay_state": replay,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out
