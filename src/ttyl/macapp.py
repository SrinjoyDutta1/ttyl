"""`ttyl app`: build (first time, or after an update), install and open the menu bar app.

The Swift sources ship inside this package (ttyl/gui), so `pipx install` is all
anyone needs; the app is compiled locally with the Command Line Tools' swiftc,
which also means macOS doesn't quarantine it the way a downloaded app would be.
"""

from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

GUI = Path(__file__).resolve().parent / "gui"
INSTALLED = Path.home() / "Applications" / "ttyl.app"


def source_hash() -> str:
    """Same as build.sh: sha1 of the Swift sources then Info.plist, first 12 hex digits."""
    h = hashlib.sha1()
    for f in sorted((GUI / "Sources").glob("*.swift")) + [GUI / "Info.plist"]:
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def installed_hash() -> str:
    try:
        with open(INSTALLED / "Contents" / "Info.plist", "rb") as fh:
            return plistlib.load(fh).get("TTYLSourceHash", "")
    except (OSError, plistlib.InvalidFileException):
        return ""


def running() -> bool:
    return subprocess.run(["pgrep", "-x", "ttyl-bar"], capture_output=True).returncode == 0


def plan(rebuild: bool = False) -> str:
    """What `ttyl app` will do: "build" or "open"."""
    return "build" if rebuild or installed_hash() != source_hash() else "open"


def main(rebuild: bool = False, stop: bool = False, dry_run: bool = False) -> int:
    if sys.platform != "darwin":
        print("the menu bar app is macOS only; `ttyl` gives you the terminal version anywhere")
        return 1
    if stop:
        subprocess.run(["pkill", "-x", "ttyl-bar"])
        print("stopped the menu bar app")
        return 0
    todo = plan(rebuild)
    if dry_run:
        print(todo)
        return 0
    if todo == "open":
        subprocess.run(["open", str(INSTALLED)], check=False)
        print("ttyl is in your menu bar: look for ☎")
        return 0
    if not shutil.which("xcrun"):
        print("Building the app needs Apple's Command Line Tools. Install them with:\n"
              "  xcode-select --install\nthen run `ttyl app` again.")
        return 1
    print("building the ttyl menu bar app (about 20 seconds, only the first time)…", flush=True)
    ttyl = shutil.which("ttyl") or os.path.realpath(sys.argv[0])
    env = {**os.environ, "TTYL": ttyl, "ARCHS": platform.machine()}
    res = subprocess.run(["bash", str(GUI / "build.sh"), "--install"], env=env)
    return res.returncode
