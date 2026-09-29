"""Builders for fake Claude/Codex transcript events."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_ids = itertools.count(1)


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class Claude:
    """Chainable builder for a Claude Code transcript."""

    def __init__(self, cwd="/work/repo", branch="main"):
        self.cwd, self.branch = cwd, branch
        self.events: list[dict] = []
        self.t = 0.0

    def _base(self, kind: str, **extra) -> dict:
        self.t += 1
        d = {"type": kind, "timestamp": ts(self.t), "cwd": self.cwd, "gitBranch": self.branch, "sessionId": "s1"}
        d.update(extra)
        self.events.append(d)
        return d

    def prompt(self, text, **extra):
        self._base("user", message={"role": "user", "content": text}, origin={"kind": "human"}, **extra)
        return self

    def say(self, text, stop="end_turn"):
        self._base("assistant", message={"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop})
        return self

    def tool(self, name, inp, result="ok", is_error=False, stdout=None):
        tid = f"toolu_{next(_ids)}"
        self._base("assistant", message={"role": "assistant", "stop_reason": "tool_use",
                                         "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]})
        extra = {"toolUseResult": {"stdout": stdout if stdout is not None else result, "stderr": ""}}
        self._base("user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}, **extra)
        return self

    def pending_tool(self, name, inp):
        self._base("assistant", message={"role": "assistant", "stop_reason": "tool_use",
                                         "content": [{"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": inp}]})
        return self

    def raw(self, **d):
        self._base(d.pop("type"), **d)
        return self

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in self.events))
        return path


def codex_line(kind: str, payload: dict, minute: float) -> dict:
    return {"timestamp": ts(minute), "type": kind, "payload": payload}
