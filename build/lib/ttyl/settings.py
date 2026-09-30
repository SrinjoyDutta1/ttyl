"""User settings, in ~/.config/ttyl/settings.json (separate from the busy state.json,
so the CLI, the terminal view and the menu bar engine can all change them safely).

    summaries: bool   send session excerpts to Claude to write summaries (default off)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_cache: tuple[float, dict] = (-1.0, {})


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "ttyl" / "settings.json"


def load() -> dict:
    """Current settings; rereads the file only when it changed."""
    global _cache
    p = path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cache[0]:
        try:
            data = json.loads(p.read_text())
            _cache = (mtime, data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            _cache = (mtime, {})
    return _cache[1]


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> None:
    global _cache
    p = path()
    data = dict(load())
    data[key] = value
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".settings-")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, p)
    _cache = (-1.0, {})
