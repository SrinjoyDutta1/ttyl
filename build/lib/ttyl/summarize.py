"""Model-written session summaries, saved in state.json so they survive restarts.

Off by default: summaries send excerpts of your sessions to Claude, billed to your
key, so nothing is sent until you run `ttyl summaries on` (or flip it in the menu
bar app). A summary is (re)written only when a session has turns it doesn't cover
yet and isn't mid-turn, one session at a time. Needs Anthropic credentials the SDK
can find (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile);
without them the recap falls back to the agent's own "while you were away" note.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

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


def digest(s: Session, max_turns: int = 15) -> str:
    """A compact, model-readable account of the session."""
    lines = [f"Title: {s.title}", f"Project: {s.project}" + (f" (branch {s.branch})" if s.branch else "")]
    status = {Status.WAITING: f"blocked, waiting for the developer ({s.waiting_for or 'input'})",
              Status.BUSY: "working", Status.IDLE: "finished its last turn, waiting for the next prompt",
              Status.CLOSED: "terminal closed"}[s.status]
    lines.append(f"Status: {status}")
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
    if s.status == Status.WAITING and last and last.last_action:
        lines.append(f"\nIt is waiting for approval to: {last.last_action}")
    return "\n".join(lines)


class Summarizer:
    def __init__(self, state: State | None, client=None, model: str = MODEL):
        self.state = state
        self.model = model
        self._client = client
        self.disabled_reason = ""
        self._backoff_until = 0.0
        self._failed: dict[str, int] = {}  # session id -> turn count that failed; don't retry until it changes

    @property
    def enabled(self) -> bool:
        return not self.disabled_reason

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
            return None
        except TypeError as e:  # the SDK found no credentials at all
            if "authentication" not in str(e).lower():
                raise
            self.disabled_reason = "no Anthropic credentials (set ANTHROPIC_API_KEY)"
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
        text = one_line(plain(text), 700)
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


def summaries_on() -> bool:
    """The user's choice (`ttyl summaries on|off`); TTYL_SUMMARIES=1/0 overrides it."""
    env = os.environ.get("TTYL_SUMMARIES")
    if env is not None:
        return env.lower() not in ("0", "false", "off", "no", "")
    return bool(settings.get("summaries", False))


def has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic")))
