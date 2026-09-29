"""Discovers agent sessions, tails their transcripts, and joins in live state.

Sources (all written by the agents themselves, nothing to install):
  ~/.claude/projects/*/<id>.jsonl   Claude Code transcripts
  ~/.claude/sessions/<pid>.json     Claude Code's live registry: pid -> session, status
  ~/.codex/sessions/**/*.jsonl      Codex rollouts
  ~/.codex/session_index.jsonl      Codex thread names
plus `ps` for terminals and `git log` for the commits a turn made.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import collide
from . import procs as proclib
from .claude import ClaudeParser
from .codex import CodexParser, load_threads, load_titles
from .model import STATUS_RANK, Commit, Session, Status
from .state import State

HOME = Path.home()


@dataclass
class Paths:
    claude_projects: Path = HOME / ".claude" / "projects"
    claude_registry: Path = HOME / ".claude" / "sessions"
    codex_sessions: Path = HOME / ".codex" / "sessions"
    codex_index: Path = HOME / ".codex" / "session_index.jsonl"
    codex_home: Path = HOME / ".codex"


APPROVAL_GUESS = timedelta(seconds=20)  # a Codex tool call pending this long is probably waiting on you


@dataclass
class Live:
    pid: int
    session_id: str
    cwd: str
    status: str
    waiting_for: str
    kind: str
    name: str
    tty: str
    updated: float


class _Tail:
    """A transcript being followed: parser state plus how far we've read."""

    def __init__(self, path: Path, agent: str):
        self.path, self.agent = path, agent
        self._reset()

    def _reset(self) -> None:
        self.session = Session(agent=self.agent, id=self.path.stem, path=self.path)
        if self.agent == "claude":
            self.parser = ClaudeParser(self.session)
        else:
            self.parser = CodexParser(self.session)
        self.offset, self.stamp = 0, None

    def pump(self) -> bool:
        try:
            st = self.path.stat()
        except OSError:
            return False
        stamp = (st.st_size, st.st_mtime_ns)
        if stamp == self.stamp:
            return False
        if st.st_size < self.offset:  # rewritten, start over
            self._reset()
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
        end = chunk.rfind(b"\n")
        if end >= 0:
            for raw in chunk[:end].split(b"\n"):
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    self.parser.feed(event)
            self.offset += end + 1
        self.stamp = stamp
        return True


@dataclass
class _GitCache:
    ttl: float = 15.0
    _entries: dict[str, tuple[float, list[Commit]]] = field(default_factory=dict)

    def since(self, repo: str, since: datetime) -> list[Commit]:
        hit = self._entries.get(repo)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        commits: list[Commit] = []
        try:
            out = subprocess.run(
                ["git", "-C", repo, "log", "--all", f"--since={int(since.timestamp())}", "--format=%h%x09%ct%x09%s"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            for line in out.splitlines():
                sha, ct, subject = (line.split("\t", 2) + ["", ""])[:3]
                if ct.isdigit():
                    commits.append(Commit(sha, subject, datetime.fromtimestamp(int(ct), tz=timezone.utc)))
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._entries[repo] = (time.monotonic(), commits)
        return commits


class Store:
    def __init__(self, paths: Paths | None = None, days: float = 3.0, state: State | None = None):
        self.paths = paths or Paths()
        self.days = days
        self.state = state  # None: remember nothing between runs (tests)
        self.show_all = False
        self._tails: dict[Path, _Tail] = {}
        self._codex_titles: tuple[float, dict[str, str]] = (-1.0, {})
        self._codex_threads: tuple[float, dict[str, dict]] = (-1.0, {})
        self._codex_cwds: dict[int, str | None] = {}  # pid -> cwd, lsof is slow
        self._git = _GitCache()
        self.collisions: list[collide.Collision] = []

    # -- public -------------------------------------------------------------

    def refresh(self) -> list[Session]:
        """Re-read whatever changed and return sessions, most urgent first."""
        now = time.time()
        cutoff = now - self.days * 86400
        procs = proclib.snapshot()
        live = self._claude_live(procs)
        # sessions seen alive recently stay on the map after their terminal closes,
        # however long ago their transcript was last written
        remembered = self.state.recently_live(cutoff) if self.state else {}
        remembered_paths = {rec.get("path") for rec in remembered.values()}

        claude_files = {p.stem: p for p in self.paths.claude_projects.glob("*/*.jsonl")}
        wanted: list[tuple[Path, str]] = []
        for sid, path in claude_files.items():
            if sid in live or sid in remembered or self.show_all or _mtime(path) >= cutoff:
                wanted.append((path, "claude"))
        for path in self.paths.codex_sessions.glob("*/*/*/*.jsonl"):
            if self.show_all or str(path) in remembered_paths or _mtime(path) >= cutoff:
                wanted.append((path, "codex"))

        sessions: list[Session] = []
        for path, agent in wanted:
            tail = self._tails.get(path)
            if tail is None:
                tail = self._tails[path] = _Tail(path, agent)
            tail.pump()
            if tail.session.imported:
                continue
            sessions.append(tail.session)

        by_id = {s.id: s for s in sessions}
        for sid, lv in live.items():
            s = by_id.get(sid)
            if s is None:
                if lv.kind != "interactive":
                    continue  # pre-warmed background spare nobody has used
                s = Session(agent="claude", id=sid, cwd=lv.cwd, entrypoint="cli",
                            started=datetime.fromtimestamp(lv.updated, tz=timezone.utc),
                            updated=datetime.fromtimestamp(lv.updated, tz=timezone.utc))
                sessions.append(s)
            self._apply_live(s, lv)
        for s in sessions:
            if s.agent == "claude" and s.id not in live:
                self._set_closed(s)

        titles = self._codex_thread_names()
        threads = self._codex_thread_index()
        for s in sessions:
            if s.agent != "codex":
                continue
            if s.id in titles:
                s.ai_title = titles[s.id]
            info = threads.get(s.id)
            if info:
                s.ai_title = info["title"] or s.ai_title
                s.branch = info["branch"] or s.branch
                s.approval_mode = s.approval_mode or info["approval_mode"]
                s.archived = info["archived"]
        self._codex_live(procs, [s for s in sessions if s.agent == "codex"])
        for s in sessions:
            self._attach_commits(s)
        self._remember(sessions, procs, now)

        keep = [s for s in sessions if (s.status != Status.CLOSED or s.visible_turns)
                and (self.show_all or not s.archived)]
        self.collisions = collide.annotate(keep)
        keep.sort(key=_sort_key)
        return keep

    def _remember(self, sessions: list[Session], procs: dict[int, proclib.Proc], now: float) -> None:
        """Record live sessions; give closed ones back what was recorded about them."""
        if self.state is None:
            return
        for s in sessions:
            if s.status != Status.CLOSED and s.pid:
                proc = procs.get(s.pid)
                s.launch_args = proclib.launch_args(proc.command) if proc and s.agent == "claude" else []
                s.closed_at = None
                self.state.seen_live(s, s.launch_args, now)
            rec = self.state.get(s.id)
            if s.status == Status.CLOSED and rec.get("last_live"):
                s.closed_at = datetime.fromtimestamp(rec["last_live"], tz=timezone.utc)
                s.launch_args = list(rec.get("args") or [])
            archived_at = rec.get("archived_at")
            if archived_at:
                active = s.last_active.timestamp() if s.last_active else 0
                if s.status in (Status.BUSY, Status.WAITING) or active > archived_at:
                    self.state.set_archived(s.id, False)  # it came back to life: show it again
                    s.archived = False
                else:
                    s.archived = True
            elif s.agent == "claude":
                s.archived = False  # (Codex threads keep the flag from Codex's own index)
            summary = rec.get("summary") or {}
            if summary.get("text") and summary.get("turns", 0) >= s.summary_turns:
                s.summary, s.summary_turns = summary["text"], summary.get("turns", 0)
                s.summary_at = datetime.fromtimestamp(summary.get("at", now), tz=timezone.utc)
        self.state.save()

    # -- archive / delete -----------------------------------------------------

    def archive(self, s: Session, on: bool = True) -> str:
        """Hide a session from the map (nothing on disk is touched). New activity brings it back."""
        if self.state is None:
            return "archiving needs saved state"
        self.state.set_archived(s.id, on)
        s.archived = on
        return f"archived {s.title!r}" if on else f"unarchived {s.title!r}"

    def trash(self, s: Session) -> tuple[bool, str]:
        """Move a closed session's transcript to the Trash, with a note saying where it came from."""
        if s.status != Status.CLOSED:
            return False, "it's still open in a terminal; close it first (or archive it)"
        if s.agent == "codex" and s.entrypoint == "desktop":
            return False, "Codex keeps its own index of Desktop threads; archive it here, or delete it in Codex"
        if not s.path or not s.path.exists():
            return False, "its transcript is already gone"
        files = [s.path]
        extra = s.path.with_suffix("")  # Claude keeps subagent logs and big tool results next to it
        if extra.is_dir():
            files.append(extra)
        trash = Path.home() / ".Trash" if sys.platform == "darwin" else Path.home() / ".local/share/Trash/files"
        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        name = "".join(c if c.isalnum() or c in " -_" else " " for c in s.title)[:50].strip() or s.id[:8]
        dest = trash / f"ttyl · {name} · {stamp}"
        try:
            dest.mkdir(parents=True)
            for f in files:
                shutil.move(str(f), str(dest / f.name))
            (dest / "WHERE THIS CAME FROM.txt").write_text(
                f"Deleted from ttyl: {s.title}\n\nTo restore, move these back to:\n{s.path.parent}\n")
        except OSError as e:
            return False, f"couldn't move it to the Trash: {e}"
        self._tails.pop(s.path, None)
        if self.state is not None:
            self.state.forget(s.id)
        return True, f"moved {s.title!r} to the Trash"

    # -- live state -----------------------------------------------------------

    def _claude_live(self, procs: dict[int, proclib.Proc]) -> dict[str, Live]:
        live: dict[str, Live] = {}
        for f in self.paths.claude_registry.glob("*.json"):
            try:
                d = json.loads(f.read_text())
                pid = int(d["pid"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            proc = procs.get(pid)
            if proc is None or "claude" not in proc.command:
                continue  # stale file, or the pid got reused
            lv = Live(
                pid=pid,
                session_id=d.get("sessionId", ""),
                cwd=d.get("cwd", ""),
                status=d.get("status", "idle"),
                waiting_for=d.get("waitingFor", ""),
                kind=d.get("kind", "interactive"),
                name=d.get("name", ""),
                tty=proc.tty,
                updated=(d.get("statusUpdatedAt") or d.get("updatedAt") or 0) / 1000,
            )
            prev = live.get(lv.session_id)
            if lv.session_id and (prev is None or lv.updated > prev.updated):
                live[lv.session_id] = lv
        return live

    @staticmethod
    def _apply_live(s: Session, lv: Live) -> None:
        s.pid, s.tty, s.live_name = lv.pid, lv.tty, lv.name
        s.status = {"busy": Status.BUSY, "waiting": Status.WAITING}.get(lv.status, Status.IDLE)
        s.waiting_for = lv.waiting_for if s.status == Status.WAITING else ""
        if lv.kind != "interactive":
            s.entrypoint = lv.kind
        if s.status == Status.IDLE and s.last_turn:
            s.last_turn.done = True  # the process says it's idle; trust it over a cut-off log

    @staticmethod
    def _set_closed(s: Session) -> None:
        s.status, s.pid, s.tty, s.waiting_for = Status.CLOSED, None, "", ""
        if s.last_turn:
            s.last_turn.done = True

    def _codex_live(self, procs: dict[int, proclib.Proc], sessions: list[Session]) -> None:
        clis = [p for p in procs.values() if proclib.is_codex_cli(p)]
        for pid in list(self._codex_cwds):
            if pid not in procs:
                del self._codex_cwds[pid]
        by_cwd: dict[str, proclib.Proc] = {}
        for p in clis:
            if p.pid not in self._codex_cwds:
                self._codex_cwds[p.pid] = proclib.cwd_of(p.pid)
            if self._codex_cwds[p.pid]:
                by_cwd[self._codex_cwds[p.pid]] = p

        # the Desktop app runs `codex ... app-server`; while it's up, its threads are open in it
        app_running = any("app-server" in p.command and "codex" in p.command for p in procs.values())
        now = datetime.now(timezone.utc)
        claimed: set[int] = set()
        for s in sorted(sessions, key=lambda s: s.updated or now, reverse=True):
            fresh = s.updated is not None and now - s.updated < timedelta(minutes=10)
            working = bool(s.last_turn and not s.last_turn.done and fresh)
            proc = by_cwd.get(s.cwd) if s.entrypoint == "cli" else None
            s.waiting_for = ""
            if proc and proc.pid not in claimed:  # newest session in that dir owns the process
                claimed.add(proc.pid)
                s.pid, s.tty = proc.pid, proc.tty
                s.status = Status.BUSY if working else Status.IDLE
            elif s.entrypoint == "desktop" and app_running:
                s.status = Status.BUSY if working else Status.IDLE
            elif working:
                s.status = Status.BUSY
            else:
                self._set_closed(s)
            # Codex doesn't log approval requests; a tool call stuck this long under an
            # approval policy is almost always one
            if (s.status == Status.BUSY and s.pending_since and now - s.pending_since > APPROVAL_GUESS
                    and s.approval_mode in ("on-request", "untrusted")):
                s.status, s.waiting_for = Status.WAITING, "approval (probably)"
            if s.status == Status.IDLE and s.last_turn:
                s.last_turn.done = True

    def _codex_thread_index(self) -> dict[str, dict]:
        dbs = sorted(self.paths.codex_home.glob("state_*.sqlite"))
        if not dbs:
            return {}
        db = dbs[-1]
        m = max(_mtime(db), _mtime(Path(str(db) + "-wal")))
        if m != self._codex_threads[0]:
            self._codex_threads = (m, load_threads(db))
        return self._codex_threads[1]

    def _codex_thread_names(self) -> dict[str, str]:
        m = _mtime(self.paths.codex_index)
        if m != self._codex_titles[0]:
            self._codex_titles = (m, load_titles(self.paths.codex_index))
        return self._codex_titles[1]

    # -- commits --------------------------------------------------------------

    def _attach_commits(self, s: Session) -> None:
        turns = s.visible_turns
        committing = [i for i, t in enumerate(turns) if t.committed]
        for t in turns:
            t.commits = []
        if not committing or not s.started:
            return
        taken: set[str] = set()
        now = datetime.now(timezone.utc)
        for i in committing:
            t = turns[i]
            end = t.ended or (turns[i + 1].started if i + 1 < len(turns) else now)
            lo, hi = t.started - timedelta(seconds=5), end + timedelta(seconds=30)
            for repo in dict.fromkeys(t.commit_dirs or [s.cwd]):
                for c in self._git.since(repo, s.started - timedelta(minutes=1)):
                    if lo <= c.when <= hi and c.sha not in taken:
                        taken.add(c.sha)
                        t.commits.append(c)
            t.commits.sort(key=lambda c: c.when)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _sort_key(s: Session):
    ts = s.last_active.timestamp() if s.last_active else 0
    return (STATUS_RANK[s.status], -ts)


def find(sessions: list[Session], query: str) -> Session | None:
    """Match a tty (`ttys005` or `5`), a pid, an id prefix (4+ chars), a project name, or part of a title."""
    q = query.strip().lower()
    if not q:
        return None
    tty = q if q.startswith("tty") else f"ttys{int(q):03d}" if q.isdigit() and len(q) <= 3 else None
    for s in sessions:
        if tty and s.tty == tty or q.isdigit() and s.pid == int(q):
            return s
    if len(q) >= 4:
        for s in sessions:
            if s.id.lower().startswith(q):
                return s
    for s in sessions:  # sessions come most-urgent-first, so this picks the live one
        if s.project.lower() == q:
            return s
    for s in sessions:
        if q in s.project.lower() or q in s.title.lower():
            return s
    return None


def default_store() -> Store:
    days = float(os.environ.get("TTYL_DAYS", "3"))
    return Store(days=days, state=State())
