"""Aider, from the `.aider.chat.history.md` it keeps in each repo.

Markers (Aider-AI/aider aider/io.py, coders/base_coder.py, repo.py): every run
starts with `# aider chat started at YYYY-MM-DD HH:MM:SS`; your input lines are
prefixed `#### `; tool/status lines are prefixed `> ` (`> Applied edit to
<path>`, `> Commit <hash> <message>`); the assistant's reply is the plain text in
between. Aider has no session ids, so ttyl shows each repo's latest run as one
session. Replies are written only after they finish, so a prompt with nothing
after it means Aider is still working. Only per-run times are logged.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from .model import Commit, Session, Turn

HISTORY = ".aider.chat.history.md"
_RUN = re.compile(r"^# aider chat started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$", re.M)
_COMMIT = re.compile(r"^Commit ([0-9a-f]{7,40}) (.+)$")


def session_id(path: Path) -> str:
    return "aider-" + hashlib.sha1(str(path).encode()).hexdigest()[:12]


def parse(path: Path, text: str, mtime: float) -> Session | None:
    runs = list(_RUN.finditer(text))
    if not runs:
        return None
    last = runs[-1]
    started = datetime.strptime(last.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
    s = Session(agent="aider", id=session_id(path), path=path, cwd=str(path.parent), entrypoint="cli",
                started=started, updated=datetime.fromtimestamp(mtime).astimezone())
    turn: Turn | None = None
    reply: list[str] = []
    prompt: list[str] = []

    def close_prompt():
        nonlocal turn, prompt
        if prompt:
            if turn:
                turn.done = True
            text = " ".join(prompt).strip()
            turn = Turn(started=started, prompt=text, origin="command" if text.startswith("/") else "human")
            s.turns.append(turn)
            prompt = []

    for line in text[last.end():].splitlines():
        if line.startswith("#### "):
            if turn and reply:
                turn.reply = "\n".join(reply).strip()
                reply = []
            prompt.append(line[5:])
            continue
        close_prompt()
        if turn is None:
            continue
        if line.startswith("> "):
            status = line[2:].strip()
            if status.startswith("Applied edit to "):
                turn.add_file(str(path.parent / status[len("Applied edit to "):]))
                turn.tools["edit"] += 1
            elif m := _COMMIT.match(status):
                turn.commits.append(Commit(m.group(1), m.group(2), s.updated))
                turn.committed = True
            continue
        if line.strip():
            turn.had_agent = True
            reply.append(line)
    close_prompt()
    if turn and reply:
        turn.reply = "\n".join(reply).strip()
    if turn:
        turn.done = turn.had_agent  # nothing after the prompt yet: Aider is still answering
    return s
