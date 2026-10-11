"""Model-written session summaries, saved in state.json so they survive restarts.

Two models can write them (`ttyl summaries local|claude|off`):
- local (the default): Apple's on-device model, through the ttyl-summarize helper the
  menu bar app build ships (gui/Helpers). Free, and nothing leaves the Mac. Needs macOS 26
  on Apple silicon with Apple Intelligence on.
- claude (opt-in): sends excerpts of your sessions to Claude, billed to your key. Better
  summaries. Needs Anthropic credentials the SDK can find (ANTHROPIC_API_KEY,
  ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile); without them it falls back to local.
A summary is (re)written only when a session has turns it doesn't cover yet and isn't
mid-turn, one session at a time. With neither model, the recap shows the agent's own
"while you were away" note.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import settings
from .model import Session, Status, one_line, plain
from .state import State

MODEL = os.environ.get("TTYL_SUMMARY_MODEL", "claude-opus-5-5")

SYSTEM = """\
You write the note a developer reads when they switch back to one of the many terminal \
tabs they have open, each running a coding agent. They've forgotten what this one was \
doing. From the session log, write at most three short sentences of plain text (no \
markdown, no preamble, no lists):
1. what this session is for, in the developer's own terms;
2. what has actually been done (features, fixes, commits; name files, commands or \
branches when they matter);
3. where it stands right now: what the agent is waiting on, or the obvious next step.
Call the developer "you" and the agent "it". If the log is only a quick question and \
answer, say that in one sentence."""


def digest(s: Session, max_turns: int = 15, status: bool = True) -> str:
    """A compact, model-readable account of the session. `status=False` leaves out where it
    stands (for the on-device model, which writes only what it's for and what's done)."""
    lines = [f"Title: {s.title}", f"Project: {s.project}" + (f" (branch {s.branch})" if s.branch else "")]
    if status:
        now = {Status.WAITING: f"blocked, waiting for the developer ({s.waiting_for or 'input'})",
               Status.BUSY: "working", Status.IDLE: "finished its last turn, waiting for the next prompt",
               Status.CLOSED: "terminal closed"}[s.status]
        lines.append(f"Status: {now}")
    if s.away_summary:
        lines.append(f"The agent's own earlier recap: {one_line(plain(s.away_summary), 500)}")
    turns = s.visible_turns
    humans = [t for t in turns if t.origin == "human"]
    if humans and humans[0] not in turns[-max_turns:]:
        lines.append(f"First ask: {one_line(humans[0].prompt, 300)}")
        lines.append(f"({len(turns) - max_turns} earlier turns omitted)")
    for t in turns[-max_turns:]:
        who = "Developer" if t.origin == "human" else "Event"
        lines.append(f"\n{who}: {one_line(t.prompt, 300)}")
        did = []
        if t.files:
            did.append("edited " + ", ".join(os.path.basename(f) for f in t.files[:5]))
        if t.commands:
            did.append("ran " + "; ".join(one_line(c, 60) for c in t.commands[-3:]))
        for c in t.commits:
            did.append(f"committed {c.sha} \"{c.subject}\"")
        if t.interrupted:
            did.append("was interrupted")
        if did:
            lines.append("Agent " + ", ".join(did))
        if t.reply:
            lines.append(f"Agent said: {one_line(plain(t.reply), 400)}")
    last = s.last_turn
    if status and s.status == Status.WAITING and last and last.last_action:
        lines.append(f"\nIt is waiting for approval to: {last.last_action}")
    return "\n".join(lines)


ON_DEVICE = "apple-on-device"  # saved as the summary's model
ON_DEVICE_BUDGET = 7000  # characters of digest; the on-device model's whole context is 4096 tokens
ON_DEVICE_SYSTEM = """\
You read the log of a developer working with an AI coding agent in a terminal. Use only facts in \
the log. Never say something was pushed, merged, deployed or fixed unless the log shows it happened. \
Write exactly two plain sentences, no preamble, no lists, no markdown: first what the developer is \
trying to get done (start with "You're"), then what the agent found out and what it changed so far \
(start with "It"), naming files or commits that matter."""


def helper_path() -> Path | None:
    """The ttyl-summarize helper from the menu bar app build. TTYL_SUMMARIZE_HELPER overrides
    it (empty: none, which is what tests use)."""
    env = os.environ.get("TTYL_SUMMARIZE_HELPER")
    if env is not None:
        candidates = [Path(env)] if env else []
    else:
        from .macapp import BUILT, INSTALLED

        candidates = [app / "Contents" / "MacOS" / "ttyl-summarize" for app in (INSTALLED, BUILT)]
    return next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


class OnDevice:
    """Apple's on-device model, through the helper (one process per summary, a few seconds each)."""

    def __init__(self, path: Path):
        self.path = path

    def _run(self, args: list[str], request: dict | None = None, timeout: float = 90) -> dict:
        try:
            out = subprocess.run([str(self.path), *args], input=json.dumps(request) if request else "",
                                 capture_output=True, text=True, timeout=timeout).stdout
            reply = json.loads(out.strip().splitlines()[-1])
            return reply if isinstance(reply, dict) else {}
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            return {"error": "the on-device model didn't answer", "kind": "other"}

    def check(self) -> str:
        """'' when the model is ready, else why not."""
        reply = self._run(["--check"], timeout=20)
        return "" if reply.get("available") else reply.get("reason") or reply.get("error") or "unavailable"

    def write(self, s: Session) -> tuple[str, str]:
        """(summary, "") or ("", error kind: unavailable, context, declined, other)."""
        for turns in (12, 8, 5, 3, 1):
            text = digest(s, max_turns=turns, status=False)
            if len(text) > ON_DEVICE_BUDGET and turns > 1:
                continue
            reply = self._run([], {"instructions": ON_DEVICE_SYSTEM, "prompt": text[:ON_DEVICE_BUDGET]})
            if reply.get("kind") == "context":
                continue
            if "text" not in reply:
                return "", reply.get("kind", "other")
            return tidy(reply["text"]), ""
        return "", "context"


def tidy(text: str) -> str:
    """One line of plain prose, without a "Here's a summary:" opener a small model may add."""
    lines = [ln.strip().lstrip("-*•").strip() for ln in plain(text).splitlines()]
    lines = [ln for ln in lines if ln and not re.match(r"(?i)^(here('s| is)|sure\b|summary:?$)", ln)]
    return one_line(" ".join(lines), 700)


_on_device_cache: dict[Path | None, tuple[float, OnDevice | None, str]] = {}


def on_device(max_age: float = 600) -> tuple[OnDevice | None, str]:
    """Apple's on-device model if it's ready here, else (None, why not). Rechecked every 10 minutes."""
    path = helper_path()
    cached = _on_device_cache.get(path)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1], cached[2]
    if path is None:
        model, why = None, ("the menu bar app isn't built (`ttyl app`): it brings Apple's on-device model"
                            if os.uname().sysname == "Darwin" else "on-device summaries need a Mac")
    else:
        model = OnDevice(path)
        why = model.check()
        model = None if why else model
    _on_device_cache[path] = (time.monotonic(), model, why)
    return model, why


class Summarizer:
    def __init__(self, state: State | None, client=None, model: str = MODEL, local: OnDevice | None = None):
        self.state = state
        self.local = local  # write with Apple's on-device model instead of Claude
        self.model = ON_DEVICE if local else model
        self._client = client
        self.disabled_reason = ""
        self._backoff_until = 0.0
        self._failed: dict[str, int] = {}  # session id -> turn count that failed; don't retry until it changes

    @property
    def enabled(self) -> bool:
        return not self.disabled_reason

    @property
    def kind(self) -> str:
        return LOCAL if self.local else CLAUDE

    def needs(self, s: Session) -> bool:
        n = len(s.visible_turns)
        if n == 0 or s.status == Status.BUSY or self._failed.get(s.id) == n:
            return False
        return not s.summary or s.summary_turns != n

    def pick(self, sessions: list[Session], selected: str | None, cutoff: datetime) -> Session | None:
        """Next session to summarize: the one you're looking at, then live ones, then recent closed ones."""
        if not self.enabled or time.monotonic() < self._backoff_until:
            return None
        eligible = [s for s in sessions if self.needs(s) and (
            s.id == selected or s.status != Status.CLOSED or (s.last_active and s.last_active >= cutoff))]
        eligible.sort(key=lambda s: s.id != selected)  # stable: keeps display order otherwise
        return eligible[0] if eligible else None

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def summarize(self, s: Session) -> str | None:
        if self.local:
            return self._summarize_on_device(s)
        import anthropic

        turns = len(s.visible_turns)
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": digest(s)}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.CredentialsError) as e:
            self.disabled_reason = f"Anthropic credentials were rejected ({type(e).__name__})"
            _pause_claude(self.disabled_reason)
            return None
        except TypeError as e:  # the SDK found no credentials at all
            if "authentication" not in str(e).lower():
                raise
            self.disabled_reason = "no Anthropic credentials (set ANTHROPIC_API_KEY)"
            _pause_claude(self.disabled_reason)
            return None
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError):
            self._backoff_until = time.monotonic() + 60
            return None
        except anthropic.APIStatusError:
            self._failed[s.id] = turns
            return None

        if response.stop_reason == "refusal":
            self._failed[s.id] = turns
            return None
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            self._failed[s.id] = turns
            return None
        return self._save(s, one_line(plain(text), 700), turns)

    def _summarize_on_device(self, s: Session) -> str | None:
        turns = len(s.visible_turns)
        text, error = self.local.write(s)
        if error == "unavailable":
            self.disabled_reason = "Apple's on-device model is unavailable"
            _on_device_cache.clear()  # find out why on the next check
            return None
        if not text:
            self._failed[s.id] = turns  # too long, declined or broken: wait until the session changes
            return None
        return self._save(s, text, turns)

    def _save(self, s: Session, text: str, turns: int) -> str:
        s.summary, s.summary_turns, s.summary_at = text, turns, datetime.now(timezone.utc)
        if self.state is not None:
            self.state.set_summary(s.id, text, turns, self.model)
        return text


def load_key_from_shell(timeout: float = 5.0) -> bool:
    """Apps launched from the menu bar don't inherit your shell's environment. If no
    Anthropic credentials are set, ask your login shell for ANTHROPIC_API_KEY once."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    import subprocess

    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        out = subprocess.run([shell, "-lic", 'printf "\n__key=%s\n" "$ANTHROPIC_API_KEY"'], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=timeout, start_new_session=True).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    key = next((ln[6:] for ln in out.splitlines() if ln.startswith("__key=")), "").strip()
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
    return bool(key)


def has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic")))


CLAUDE, LOCAL, OFF = "claude", "local", "off"


def _env() -> bool | None:
    env = os.environ.get("TTYL_SUMMARIES")
    return None if env is None else env.lower() not in ("0", "false", "off", "no", "")


def summaries_on() -> bool:
    """Whether Claude writes summaries (`ttyl summaries claude`); TTYL_SUMMARIES=1/0 overrides it."""
    env = _env()
    return bool(settings.get("summaries", False)) if env is None else env


def mode() -> str:
    """claude, local (the default) or off: `ttyl summaries <mode>`. TTYL_SUMMARIES=0 forces off."""
    if summaries_on():
        return CLAUDE
    if _env() is False or not settings.get("local_summaries", True):
        return OFF
    return LOCAL


def set_mode(new: str) -> None:
    settings.set("summaries", new == CLAUDE)
    settings.set("local_summaries", new != OFF)  # claude falls back to local without a key
    _claude_paused[:] = [0.0, ""]


_claude_paused: list = [0.0, ""]  # [until (monotonic), why]: credentials were rejected


def _pause_claude(why: str, secs: float = 1800) -> None:
    """Don't try Claude again for a while (a fixed key is picked up after that, or on `set_mode`)."""
    _claude_paused[:] = [time.monotonic() + secs, why]


def _claude_ready() -> str:
    """'' if Claude can write summaries now, else why not."""
    if not has_credentials():
        return "no ANTHROPIC_API_KEY"
    if time.monotonic() < _claude_paused[0]:
        return _claude_paused[1]
    return ""


def choose(current: Summarizer | None, state: State | None) -> Summarizer | None:
    """The summarizer the settings call for now, reusing `current` when it's still the right one.
    Claude without working credentials falls back to the on-device model."""
    want = mode()
    if want == OFF:
        return None
    if want == CLAUDE and not _claude_ready():
        return current if current is not None and current.kind == CLAUDE else Summarizer(state)
    if current is not None and current.kind == LOCAL and current.enabled:
        return current
    local, _ = on_device()
    return Summarizer(state, local=local) if local else None


def describe(current: Summarizer | None) -> str:
    """Who writes summaries right now, in one line for the CLI, terminal view and app."""
    want = mode()
    if want == OFF:
        return "off: the recap shows each agent's own notes"
    using = current.kind if current is not None and current.enabled else ""
    if using == CLAUDE:
        return f"written by Claude ({MODEL}) from short excerpts, billed to your API key"
    if want == CLAUDE:
        why = _claude_ready() or (current.disabled_reason if current is not None else "") or "unavailable"
        if using == LOCAL:
            return f"Claude: {why}, so they're written on this Mac for now"
        return f"Claude: {why}. On this Mac: {on_device()[1] or 'unavailable'}"
    if using == LOCAL:
        return "written on this Mac by Apple Intelligence: nothing leaves it"
    return on_device()[1] or "starting…"
