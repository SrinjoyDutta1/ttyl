"""`ttyl doctor`: check everything ttyl needs and say how to fix what's missing.
`ttyl update`: upgrade ttyl however it was installed, then the menu bar app."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, macapp, terminal
from .store import Paths, Store

OK, WARN, BAD = "✓", "!", "✗"


def _line(mark: str, label: str, detail: str = "", fix: str = "") -> None:
    color = {OK: "\033[32m", WARN: "\033[33m", BAD: "\033[31m"}[mark] if sys.stdout.isatty() else ""
    reset = "\033[0m" if color else ""
    print(f"  {color}{mark}{reset} {label}" + (f": {detail}" if detail else ""))
    if fix:
        print(f"      → {fix}")


def install_method() -> str:
    """'uv', 'pipx', 'checkout' (editable dev install) or 'pip'."""
    exe = os.path.realpath(sys.executable)
    if "/uv/tools/" in exe:
        return "uv"
    if "/pipx/venvs/" in exe:
        return "pipx"
    if (Path(__file__).resolve().parents[2] / ".git").exists():
        return "checkout"
    return "pip"


def doctor() -> int:
    print(f"ttyl {__version__} (Python {sys.version.split()[0]}, installed with {install_method()})\n")

    print("Agents")
    store = Store(Paths(), days=3)
    sessions = store.refresh()
    seen = {}
    for s in sessions:
        seen.setdefault(s.agent, []).append(s)
    p = Paths()
    homes = {"claude": p.claude_projects, "codex": p.codex_sessions, "gemini": p.gemini_home / "tmp",
             "qwen": p.qwen_home / "projects", "copilot": p.copilot_home / "session-state",
             "opencode": p.opencode_data, "goose": p.goose_data / "sessions"}
    for agent, home in homes.items():
        if agent in seen:
            live = sum(s.tty != "" for s in seen[agent])
            _line(OK, agent, f"{len(seen[agent])} recent session(s), {live} open in a terminal")
        elif home.exists():
            _line(OK, agent, "installed, no sessions in the last 3 days")
        else:
            _line(WARN, agent, "not found (fine if you don't use it)")
    if "aider" in seen:
        _line(OK, "aider", f"{len(seen['aider'])} repo(s) with recent history")
    if not sessions:
        print("      → nothing to show yet: start an agent (e.g. `claude`) in a terminal, or try `ttyl --demo`")

    print("\nmacOS")
    if sys.platform != "darwin":
        _line(WARN, "not macOS", "the terminal view works; jumping to tabs, ringing and the app are macOS only")
        return 0
    ok, detail = terminal.can_control_terminal()
    _line(OK if ok else BAD, "control Terminal (to bring tabs forward)", detail,
          "" if ok else "System Settings > Privacy & Security > Automation: allow ttyl (or your terminal) to control Terminal")
    clt = shutil.which("xcrun") is not None
    _line(OK if clt else BAD, "Command Line Tools (to build the menu bar app)", "installed" if clt else "missing",
          "" if clt else "xcode-select --install")
    if macapp.INSTALLED.exists():
        current = macapp.installed_hash() == macapp.source_hash()
        _line(OK if current else WARN, "menu bar app", f"{'running' if macapp.running() else 'not running'}, "
              f"{'up to date' if current else 'older than this ttyl'}",
              "" if current and macapp.running() else "ttyl app")
    else:
        _line(WARN, "menu bar app", "not installed", "ttyl app")
    log = macapp.app_log()
    if log.exists():
        lines = [ln for ln in log.read_text(errors="replace").splitlines() if "notifications allowed" in ln]
        if lines:
            allowed = lines[-1].strip().endswith("true")
            _line(OK if allowed else BAD, "notifications", "allowed" if allowed else "blocked",
                  "" if allowed else "System Settings > Notifications > ttyl: allow")

    print("\nSummaries")
    from .summarize import has_credentials, summaries_on

    if summaries_on():
        key = has_credentials()
        _line(OK if key else BAD, "AI summaries", "on" + ("" if key else ", but no ANTHROPIC_API_KEY"),
              "" if key else "export ANTHROPIC_API_KEY=sk-ant-... in ~/.zshrc")
    else:
        _line(OK, "AI summaries", "off (nothing is sent anywhere)", "optional: ttyl summaries on")
    return 0


def update() -> int:
    """Upgrade ttyl the way it was installed, then rebuild the menu bar app if it's installed."""
    method = install_method()
    repo = "git+https://github.com/SrinjoyDutta1/ttyl"
    cmds = {
        "uv": ["uv", "tool", "install", "--force", "--reinstall", "ttyl"],
        "pipx": ["pipx", "upgrade", "ttyl"],
    }
    if method == "checkout":
        print("You're running from a git checkout: `git pull` updates it (and `ttyl app` rebuilds the app).")
        return 0
    if method not in cmds:
        print(f"Installed with pip; upgrade with:  {sys.executable} -m pip install -U ttyl")
        return 0
    cmd = cmds[method]
    if not shutil.which(cmd[0]):
        print(f"couldn't find `{cmd[0]}` on your PATH; run the installer again:\n"
              "  curl -fsSL https://raw.githubusercontent.com/SrinjoyDutta1/ttyl/main/install.sh | bash")
        return 1
    before = __version__
    print("→ " + " ".join(cmd))
    res = subprocess.run(cmd)
    if res.returncode != 0 and method == "uv":  # installed from GitHub rather than PyPI
        res = subprocess.run(["uv", "tool", "install", "--force", "--reinstall", repo])
    if res.returncode != 0:
        return res.returncode
    if sys.platform == "darwin" and macapp.INSTALLED.exists():
        time.sleep(0.2)
        print("→ ttyl app (rebuilds the menu bar app if it changed)")
        subprocess.run([shutil.which("ttyl") or "ttyl", "app"])
    print(f"done (was {before})")
    return 0
