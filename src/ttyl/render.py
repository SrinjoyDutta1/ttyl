"""Rich renderables shared by `ttyl ls`/`ttyl show` and the TUI.

Design rule: glance, pick, go. Sessions are grouped by what they need from you,
the urgent groups get an extra line saying exactly what, and everything else
stays on one line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .model import Kind, Session, Status, Turn, one_line, plain
from .terminal import resume_command

# solid = changed something, hollow = talk; color says what kind of change
GLYPHS = {
    Kind.ACTIVE: ("▶", "bold cyan"),
    Kind.COMMIT: ("◆", "bold yellow"),
    Kind.FAIL: ("✗", "bold red"),
    Kind.EDIT: ("■", "green3"),
    Kind.RUN: ("■", "steel_blue"),
    Kind.CHAT: ("□", "grey50"),
}
WAITING_GLYPH = ("▣", "bold magenta")

LEGEND = Text.assemble(
    ("■", "green3"), " edit  ", ("■", "steel_blue"), " tools  ", ("□", "grey50"), " chat  ",
    ("◆", "bold yellow"), " commit  ", ("✗", "bold red"), " fail  ",
    ("▶", "bold cyan"), " running  ", ("▣", "bold magenta"), " you",
    style="grey62",
)

SQUARES = 12  # turns shown per lane
FRESH = timedelta(hours=24)  # "finished" vs "idle"


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    hint: str
    style: str
    icon: str


SECTIONS = [
    Section("needs", "NEEDS YOU", "blocked until you answer", "bold magenta", "▣"),
    Section("working", "WORKING", "", "bold cyan", "▶"),
    Section("finished", "FINISHED", "done in the last day, your move", "bold green3", "✓"),
    Section("idle", "OPEN BUT IDLE", "quiet for over a day", "bold grey70", "●"),
    Section("closed", "CLOSED", "⏎ reopens it where it left off", "bold grey50", "○"),
    Section("archived", "ARCHIVED", "hidden from the map; new activity brings one back", "bold grey42", "▫"),
]
_BY_KEY = {s.key: s for s in SECTIONS}


def section_of(s: Session, now: datetime | None = None) -> Section:
    if s.archived:
        return _BY_KEY["archived"]
    if s.status == Status.WAITING:
        return _BY_KEY["needs"]
    if s.status == Status.BUSY:
        return _BY_KEY["working"]
    if s.status == Status.CLOSED:
        return _BY_KEY["closed"]
    now = now or datetime.now(timezone.utc)
    recent = s.updated is not None and now - s.updated < FRESH
    return _BY_KEY["finished" if recent else "idle"]


def grouped(sessions: list[Session]) -> list[tuple[Section, list[Session]]]:
    now = datetime.now(timezone.utc)
    buckets: dict[str, list[Session]] = {sec.key: [] for sec in SECTIONS}
    for s in sessions:
        buckets[section_of(s, now).key].append(s)
    return [(sec, buckets[sec.key]) for sec in SECTIONS if buckets[sec.key]]


# -- small pieces -------------------------------------------------------------

def ago(dt: datetime | None, now: datetime | None = None) -> str:
    if not dt:
        return ""
    secs = ((now or datetime.now(timezone.utc)) - dt).total_seconds()
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit}"
    return "now"


def clock(dt: datetime | None) -> str:
    if not dt:
        return "     "
    local = dt.astimezone()
    if local.date() == datetime.now().astimezone().date():
        return local.strftime("%H:%M")
    return local.strftime("%b %d").replace(" 0", "  ")


def glyph(s: Session, turn: Turn) -> tuple[str, str]:
    if s.status == Status.WAITING and turn is s.last_turn:
        return WAITING_GLYPH
    return GLYPHS[turn.kind]


def squares(s: Session, count: int = SQUARES) -> Text:
    """The lane's recent history, oldest to newest, one spaced square per turn."""
    turns = s.visible_turns
    out = Text(no_wrap=True, overflow="crop")
    hidden = max(0, len(turns) - count)
    out.append(f"{f'+{hidden}' if hidden else '':<4} ", "grey42")
    for t in turns[-count:]:
        out.append(glyph(s, t)[0], glyph(s, t)[1])
        out.append(" ")
    return out


def squares_width(count: int = SQUARES) -> int:
    return 5 + 2 * count


def squares_for(width: int) -> int:
    """How many turns a lane can show at this terminal width."""
    return SQUARES if width >= 120 else 8 if width >= 95 else 5


def action(s: Session) -> Text | None:
    """The one line that says what an urgent lane is doing or wants."""
    last = s.last_turn
    what = last.last_action if last else ""
    if s.status == Status.WAITING:
        ask = s.waiting_for or "input"
        if "permission" in ask:
            return Text.assemble(("approve  ", "bold magenta"), (what or "a tool call", "magenta"))
        return Text.assemble((f"{ask}  ", "bold magenta"), (what, "magenta"))
    if s.status == Status.BUSY:
        return Text(what or "thinking…", "cyan")
    return None


def when(s: Session) -> str:
    age = ago(s.last_active if s.status == Status.CLOSED else s.updated)
    if s.status == Status.WAITING:
        return f"waiting {age}" if age != "now" else "waiting"
    if s.status == Status.BUSY:
        return age
    if s.status == Status.CLOSED and s.closed_at:
        return f"closed {age} ago" if age != "now" else "just closed"
    return f"{age} ago" if age != "now" else "just now"


def where(s: Session) -> str:
    if s.tty:
        return s.tty
    if s.agent == "codex" and s.entrypoint == "desktop":
        return "codex app"
    if s.entrypoint in ("bg", "claude-desktop"):
        return s.entrypoint
    return ""


def place(s: Session) -> str:
    out = s.project
    if s.branch and s.branch != "HEAD":
        out += f" · {s.branch}"
    return out


# -- lanes ----------------------------------------------------------------------

def _right(s: Session) -> Text:
    if s.status in (Status.WAITING, Status.BUSY):
        return Text(where(s), "grey62")
    age = ago(s.last_active if s.status == Status.CLOSED else s.updated)
    return Text.assemble((age, "grey50"), ("  " + where(s) if where(s) else "", "grey62"))


RING_STYLE = "bold magenta"
COLLIDE_STYLE = "bold dark_orange"


def collision_text(s: Session, width: int = 200) -> Text | None:
    """`⚠ src/limits.py is also being edited by codex · Add request logging (ttys008)`"""
    if not s.collisions:
        return None
    path, others = s.collisions[0]
    rel = s.rel(path)
    who = ", ".join(f"{o.agent} · {o.title}" + (f" ({where(o)})" if where(o) else "") for o in others)
    out = Text.assemble(("⚠ ", COLLIDE_STYLE), (rel, COLLIDE_STYLE), (" is also being edited by ", "dark_orange"),
                        (who, "bold"))
    if len(s.collisions) > 1:
        out.append(f"  +{len(s.collisions) - 1} more file{'s' * (len(s.collisions) > 2)}", "grey50")
    return out


def ring_badge(phase: bool = True) -> Text:
    return Text("☎ ring ring" if phase else "☏ ring ring", RING_STYLE if phase else "magenta")


def lane(s: Session, number: int | None = None, count: int = SQUARES, phase: bool = True) -> RenderableType:
    """One session: `n  title   project · branch   ■ ■ □ ◆   ttys005`, plus an action line if urgent.

    A ringing session shows a phone instead of its number and a `ring ring` line;
    `phase` flips twice a second so it flashes."""
    closed = s.status == Status.CLOSED
    row = Table.grid(expand=True, padding=(0, 1))
    row.add_column(width=2, no_wrap=True)
    row.add_column(ratio=5, no_wrap=True, overflow="ellipsis")
    row.add_column(ratio=3, no_wrap=True, overflow="ellipsis")
    row.add_column(width=squares_width(count), no_wrap=True)
    row.add_column(width=13, no_wrap=True, justify="right")
    if s.ringing:
        num = Text("☎" if phase else "☏", RING_STYLE if phase else "magenta")
    else:
        num = Text(str(number) if number else "", "bold" if not closed else "grey50")
    row.add_row(
        num,
        Text(s.title, "grey62" if closed or not s.visible_turns else "bold"),
        Text(place(s), "grey50"),
        squares(s, count),
        _right(s),
    )
    act = action(s)
    if s.ringing == "finished" and act is None:
        reply = next((t.reply for t in reversed(s.visible_turns) if t.reply), "")
        act = Text.assemble(("finished  ", "bold green3"), (one_line(plain(reply), 120), "grey70"))
    clash = collision_text(s)
    if act is None and clash is None:
        return row
    second = Table.grid(expand=True, padding=(0, 1))
    second.add_column(width=2)
    second.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    second.add_column(width=13, no_wrap=True, justify="right")
    if act is not None:
        line = Text("↳ ", "grey50")
        if s.ringing:
            line.append_text(ring_badge(phase))
            line.append("  ")
        line.append_text(act)
        second.add_row("", line, Text(when(s), "grey62"))
    if clash is not None:
        second.add_row("", clash, "")
    return Group(row, second)


def section_rule(sec: Section, n: int) -> RenderableType:
    title = Text.assemble((f"{sec.icon} {sec.title}", sec.style), (f"  {n}", "grey62"))
    if sec.hint:
        title.append(f"  · {sec.hint}", "grey50")
    return Rule(title, align="left", style="grey23", characters="─")


def overview(sessions: list[Session], width: int = 120) -> RenderableType:
    parts: list[RenderableType] = []
    number = 0
    count = squares_for(width)
    for sec, members in grouped(sessions):
        parts.append(section_rule(sec, len(members)))
        for s in members:
            number += 1
            parts.append(lane(s, number if number <= 9 else None, count))
        parts.append(Text())
    return Group(*parts)


def ordered(sessions: list[Session]) -> list[Session]:
    """Sessions in the order they're displayed (and numbered)."""
    return [s for _, members in grouped(sessions) for s in members]


def header(sessions: list[Session]) -> Text:
    counts = {sec.key: 0 for sec in SECTIONS}
    for s in sessions:
        counts[section_of(s).key] += 1
    out = Text.assemble(("ttyl", "bold"), "   ")
    ringing = sum(bool(s.ringing) for s in sessions)
    if ringing:
        out.append(f" ☎ ring ring ×{ringing} ", "bold white on magenta")
        out.append("   ")
    clashes = len({path for s in sessions for path, _ in s.collisions})
    if clashes:
        out.append(f" ⚠ {clashes} collision{'s' * (clashes != 1)} ", "bold white on dark_orange3")
        out.append("   ")
    if counts["needs"]:
        out.append(f"▣ {counts['needs']} need{'s' * (counts['needs'] == 1)} you", "bold magenta")
    else:
        out.append("✓ nothing needs you", "green3")
    for key, label, style in (("working", "working", "cyan"), ("finished", "finished", "green3"),
                              ("idle", "idle", "grey70"), ("closed", "closed", "grey50")):
        out.append("   ")
        out.append(f"{counts[key]} {label}", style if counts[key] else "grey35")
    return out


# -- one session --------------------------------------------------------------

def timeline(s: Session, limit: int = 40, width: int = 100) -> RenderableType:
    """One row per turn, oldest first; commits hang off the turn that made them."""
    turns = s.visible_turns
    rows: list[RenderableType] = []
    if len(turns) > limit:
        rows.append(Text(f"  ⋮  {len(turns) - limit} earlier", "grey42"))
        turns = turns[-limit:]
    for t in turns:
        g, style = glyph(s, t)
        line = Text.assemble((g, style), "  ", (clock(t.started), "grey50"), "  ", no_wrap=True, overflow="ellipsis")
        extra = Text(f"  ✎ {len(t.files)}", "green3") if t.files else Text()
        room = max(20, width - line.cell_len - extra.cell_len)
        line.append(one_line(t.prompt, room), "italic grey62" if t.origin != "human" else "")
        line.append_text(extra)
        rows.append(line)
        for c in t.commits:
            rows.append(Text.assemble("   └ ", (c.sha, "yellow"), " ", (c.subject, "grey70"), no_wrap=True, overflow="ellipsis"))
        if t is s.last_turn and s.status in (Status.WAITING, Status.BUSY):
            act = action(s)
            if act is not None:
                rows.append(Text.assemble("   └ ", act, no_wrap=True, overflow="ellipsis"))
    if not rows:
        rows.append(Text("  nothing asked yet", "grey50"))
    return Group(*rows)


def _own_files(s: Session) -> list[str]:
    # the agent's own bookkeeping (memory files, plans) isn't what you worked on
    return [f for f in s.files if "/.claude/" not in f and "/.codex/" not in f]


def _heading(text: str) -> Text:
    return Text(text, "bold grey62")


def _first_lines(text: str, n: int, width: int = 400) -> str:
    return "\n".join([one_line(plain(x), width) for x in text.splitlines() if x.strip()][:n])


def recap(s: Session, short_id: bool = True, hint: str = "") -> RenderableType:
    """Everything needed to pick this session back up, and nothing else."""
    parts: list[RenderableType] = [Text(s.title, "bold")]
    meta = [s.agent, place(s)]
    if s.tty:
        meta.append(s.tty)
    meta.append(f"{len(s.visible_turns)} turns")
    parts.append(Text(" · ".join(meta), "grey50"))
    parts.append(Text())

    sec = section_of(s)
    status = Text.assemble((f"{sec.icon} {sec.title}", sec.style), "  ")
    act = action(s)
    status.append_text(act if act is not None else Text(when(s), "grey62"))
    if s.status == Status.CLOSED:
        status.append_text(Text.assemble("   ", ("⏎", "bold"), (" reopen it", "grey62")))
    elif s.tty:
        status.append_text(Text.assemble("   ", ("⏎", "bold"), (f" go to {s.tty}", "grey62")))
    parts.append(status)
    parts.append(Text())
    if s.collisions:
        parts.append(Text("⚠ COLLISION", COLLIDE_STYLE))
        for path, others in s.collisions:
            who = "; ".join(f"{o.agent} · {o.title}" + (f" ({where(o)})" if where(o) else "") for o in others)
            parts.append(Text.assemble((s.rel(path), "dark_orange"), " is also being edited by ", (who, "bold")))
        parts.append(Text())

    last = s.last_turn
    replied = [t for t in s.visible_turns if t.reply]
    behind = len(s.visible_turns) - s.summary_turns
    if s.summary:
        parts.append(_heading("SUMMARY"))
        note = f"  written {ago(s.summary_at)} ago" if s.summary_at else ""
        if behind > 0:
            note += f", {behind} newer turn{'s' * (behind != 1)} since"
        parts.append(Text.assemble((s.summary, ""), (note, "grey42")))
    else:
        parts.append(_heading("WHERE IT LEFT OFF"))
        if s.away_summary:
            parts.append(Text.assemble((plain(s.away_summary).strip(), ""), (f"  recap, {ago(s.away_at)} ago", "grey42")))
            stale = last is not None and s.away_at is not None and s.away_at < last.started
            if stale and replied:
                parts.append(Text.assemble(("then: ", "grey50"), (_first_lines(replied[-1].reply, 1, 200), "grey70")))
        elif replied:
            parts.append(Text(_first_lines(replied[-1].reply, 5)))
        else:
            parts.append(Text("no reply yet", "grey50"))
        if hint:
            parts.append(Text(hint, "grey42"))

    humans = [t for t in s.visible_turns if t.origin == "human"]
    if humans:
        parts.append(Text())
        parts.append(_heading("YOU LAST ASKED"))
        parts.append(Text(one_line(humans[-1].prompt, 200), "grey85"))

    tail: list[Text] = []
    if s.commits:
        c = s.commits[-1]
        more = f"  +{len(s.commits) - 1} more" if len(s.commits) > 1 else ""
        tail.append(Text.assemble(("◆ ", "bold yellow"), (c.sha, "yellow"), " ", one_line(c.subject, 70), (more, "grey50")))
    files = _own_files(s)
    if files:
        shown = ", ".join(os.path.basename(f) for f in files[-3:][::-1])
        more = f"  +{len(files) - 3} more" if len(files) > 3 else ""
        tail.append(Text.assemble(("✎ ", "green3"), (shown, "green3"), (more, "grey50")))
    if tail:
        parts.append(Text())
        parts.extend(tail)

    if s.status == Status.CLOSED and not short_id:  # `ttyl show`: the command to copy
        parts.append(Text())
        parts.append(Text.assemble(("reopen with  ", "grey50"), (resume_command(s), "grey70")))
    return Group(*parts)


def show(s: Session, width: int, limit: int = 40) -> RenderableType:
    """`ttyl show`: recap, then the timeline."""
    return Group(recap(s, short_id=False), Text(), _heading("TIMELINE"), timeline(s, limit, width), Text(), LEGEND)
