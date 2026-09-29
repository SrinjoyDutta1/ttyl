"""Talk to the terminal app: focus the tab an agent lives in, or reopen a closed one.

Works with Terminal.app and iTerm2 via AppleScript. The first call may trigger
macOS's "allow ... to control Terminal" prompt.
"""

from __future__ import annotations

import os
import shlex
import subprocess

from .model import Session

_FOCUS_TERMINAL = """
on run argv
  set target to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is target then
          set selected tab of w to t
          set index of w to 1
          activate
          return "ok"
        end if
      end repeat
    end repeat
  end tell
  return "missing"
end run
"""

_FOCUS_ITERM = """
on run argv
  set target to item 1 of argv
  if application "iTerm2" is not running then return "missing"
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if tty of s is target then
            select w
            select t
            select s
            activate
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
"""

_OPEN_TERMINAL = """
on run argv
  tell application "Terminal"
    do script (item 1 of argv)
    activate
  end tell
end run
"""

_OPEN_ITERM = """
on run argv
  tell application "iTerm2"
    create window with default profile command (item 1 of argv)
    activate
  end tell
end run
"""


def _osascript(script: str, *args: str) -> str:
    try:
        res = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "error"
    return res.stdout.strip() or ("error: " + res.stderr.strip() if res.returncode else "")


def _apps() -> list[str]:
    first = "iterm" if os.environ.get("TERM_PROGRAM") == "iTerm.app" else "terminal"
    return [first, "terminal" if first == "iterm" else "iterm"]


def focus(tty: str) -> bool:
    """Bring the tab running on /dev/<tty> to the front."""
    if not tty:
        return False
    for app in _apps():
        script = _FOCUS_ITERM if app == "iterm" else _FOCUS_TERMINAL
        if _osascript(script, f"/dev/{tty}") == "ok":
            return True
    return False


def resume_command(s: Session) -> str:
    cd = f"cd {shlex.quote(s.cwd)} && " if s.cwd else ""
    if s.agent == "codex":
        return f"{cd}codex resume {s.id}"
    return f"{cd}claude --resume {s.id}"


def reopen(s: Session) -> bool:
    """Open a new terminal window that resumes this session."""
    cmd = resume_command(s)
    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return not _osascript(_OPEN_ITERM, cmd).startswith("error")
    return not _osascript(_OPEN_TERMINAL, cmd).startswith("error")
