"""Process table: which agents are alive, and which terminal each one lives in."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    tty: str  # "ttys005", or "" when detached
    command: str

    @property
    def exe(self) -> str:
        return os.path.basename(self.command.split(" ", 1)[0])


def snapshot() -> dict[int, Proc]:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,tty=,command="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    procs: dict[int, Proc] = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        tty = "" if parts[2] in ("??", "-") else parts[2]
        procs[int(parts[0])] = Proc(int(parts[0]), int(parts[1]), tty, parts[3])
    return procs


def cwd_of(pid: int) -> str | None:
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def is_codex_cli(p: Proc) -> bool:
    # the CLI runs in a terminal; the Desktop app's `codex app-server` has no tty
    if not p.tty:
        return False
    tokens = p.command.split()
    return any(os.path.basename(t) == "codex" for t in tokens[:2])
