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


last_error = ""  # why the last AppleScript call failed, for logs and messages


def _osascript(script: str, *args: str) -> str:
    global last_error
    try:
        res = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        last_error = str(e)
        return "error"
    if res.returncode:
        last_error = res.stderr.strip()
        return "error: " + last_error
    return res.stdout.strip()


def can_control_terminal() -> tuple[bool, str]:
    """Ask Terminal something harmless. The first time, macOS asks you to allow it."""
    out = _osascript('tell application "Terminal" to count windows')
    if out.startswith("error"):
        denied = "-1743" in last_error or "not allowed" in last_error.lower()
        return False, ("not allowed to control Terminal (System Settings > Privacy & Security > Automation)"
                       if denied else last_error or out)
    return True, f"{out} Terminal windows"


def _apps() -> list[str]:
    first = "iterm" if os.environ.get("TERM_PROGRAM") == "iTerm.app" else "terminal"
    return [first, "terminal" if first == "iterm" else "iterm"]


def focus(tty: str) -> bool:
    """Bring the tab running on /dev/<tty> to the front."""
    global last_error
    if not tty:
        return False
    last_error = f"no Terminal or iTerm2 tab is on {tty}"
    for app in _apps():
        script = _FOCUS_ITERM if app == "iterm" else _FOCUS_TERMINAL
        if _osascript(script, f"/dev/{tty}") == "ok":
            return True
    return False


def codex_link(s: Session) -> str:
    return f"codex://threads/{s.id}"


def open_in_codex(s: Session) -> bool:
    """Open a Codex Desktop thread in the Codex app."""
    try:
        return subprocess.run(["open", codex_link(s)], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resume_command(s: Session) -> str:
    """The command that brings a closed session back, in its folder, with its original flags."""
    if s.agent == "codex" and s.entrypoint == "desktop":
        return f"open {codex_link(s)}"
    cd = f"cd {shlex.quote(s.cwd)} && " if s.cwd else ""
    if s.agent == "codex":
        return f"{cd}codex resume {s.id}"
    flags = "".join(" " + shlex.quote(a) for a in s.launch_args)
    return f"{cd}claude --resume {s.id}{flags}"


def run_in_new_window(cmd: str) -> bool:
    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return not _osascript(_OPEN_ITERM, cmd).startswith("error")
    return not _osascript(_OPEN_TERMINAL, cmd).startswith("error")


def reopen(s: Session) -> bool:
    """Open a new terminal window that resumes this session."""
    return run_in_new_window(resume_command(s))
