"""Render, CLI and TUI smoke tests against fake sessions."""

from datetime import datetime, timedelta, timezone

from rich.console import Console

from ttyl import render
from ttyl.claude import ClaudeParser
from ttyl.cli import main
from ttyl.demo import DemoStore
from ttyl.model import Session, Status
from ttyl.tui import TtylApp

from helpers import Claude


def session(sid, builder, status=Status.IDLE, tty="ttys001", **kw) -> Session:
    s = Session(agent="claude", id=sid, cwd="/work/repo", status=status, tty=tty, **kw)
    p = ClaudeParser(s)
    for e in builder.events:
        p.feed(e)
    return s


def many_turns(n):
    b = Claude()
    for i in range(n):
        b.prompt(f"turn {i}").say("ok")
    return b


def text_of(renderable, width=120) -> str:
    console = Console(record=True, width=width)
    console.print(renderable)
    return console.export_text()


def test_squares_truncate_and_mark_waiting():
    s = session("a", many_turns(30).prompt("go").pending_tool("Bash", {"command": "rm -rf build"}),
                status=Status.WAITING, waiting_for="permission prompt")
    sq = render.squares(s, count=10).plain
    assert sq.startswith("+21 ") and sq.split()[-1] == "▣"
    assert len(sq.split()) == 11  # "+21" and ten squares
    assert render.action(s).plain == "approve  $ rm -rf build"


def test_sections_order_by_what_needs_you():
    now = datetime.now(timezone.utc)
    mk = lambda sid, status, age: Session(agent="claude", id=sid, status=status, updated=now - age)
    sessions = [mk("closed", Status.CLOSED, timedelta(hours=1)), mk("stale", Status.IDLE, timedelta(days=3)),
                mk("done", Status.IDLE, timedelta(minutes=5)), mk("busy", Status.BUSY, timedelta(0)),
                mk("ask", Status.WAITING, timedelta(0))]
    groups = render.grouped(sessions)
    assert [(sec.key, [s.id for s in m]) for sec, m in groups] == [
        ("needs", ["ask"]), ("working", ["busy"]), ("finished", ["done"]), ("idle", ["stale"]), ("closed", ["closed"])]
    assert "1 needs you" in render.header(sessions).plain


def test_recap_and_timeline_render():
    s = session("a", many_turns(3).raw(type="system", subtype="away_summary", content="**Goal** was X"))
    out = text_of(render.show(s, width=100))
    assert "WHERE IT LEFT OFF" in out and "Goal was X" in out and "turn 2" in out
    assert "claude --resume a" not in out  # live: offer the jump, not the resume command
    s.status, s.tty = Status.CLOSED, ""
    assert "claude --resume a" in text_of(render.show(s, width=100))


def test_cli_demo(capsys):
    assert main(["ls", "--demo"]) == 0
    out = capsys.readouterr().out
    assert "NEEDS YOU" in out and "Per-tenant rate limiting" in out
    assert main(["show", "gateway", "--demo"]) == 0
    assert "git push" in capsys.readouterr().out
    assert main(["jump", "gateway", "--demo"]) == 0
    assert "would focus ttys004" in capsys.readouterr().out


class FakeStore:
    demo = True  # keeps the TUI from driving the real Terminal via AppleScript
    days = 3
    show_all = False

    def __init__(self, sessions):
        self.sessions = sessions

    def refresh(self):
        return self.sessions


async def test_tui_navigates_numbers_and_announces():
    a = session("a", many_turns(2), status=Status.BUSY, tty="ttys001")
    b = session("b", many_turns(5), status=Status.IDLE, tty="ttys002")
    store = FakeStore([a, b])
    app = TtylApp(store, interval=60)
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        lanes = app.query_one("#lanes")
        assert lanes.option_count == 4  # two section headers + two lanes
        assert app.selected == "a"
        await pilot.press("down")
        assert app.selected == "b"
        await pilot.press("up")  # the section header between them is skipped
        assert app.selected == "a"

        await pilot.press("2")
        assert app.selected == "b"
        assert any("would bring ttys002" in n.message for n in app._notifications)

        a.status, a.waiting_for = Status.WAITING, "permission prompt"
        app.action_refresh()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert a.ringing == "needs you"
        assert any("ring ring" in (n.title or "") and "needs you" in n.title for n in app._notifications)
        assert app.order[0] is a and app.selected == "b"  # selection survives the reshuffle
        await pilot.press("1")  # go to it: the ringing stops
        assert a.ringing == "" and not app.ringer.ringing

        await pilot.press("a")
        await app.workers.wait_for_complete()
        assert store.show_all


async def test_tui_runs_on_demo_data():
    app = TtylApp(DemoStore(), interval=60)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(app.order) == 8 and app.order[0].status == Status.WAITING


async def test_enter_on_a_closed_session_reopens_it_once(monkeypatch):
    from ttyl import terminal

    calls = []
    monkeypatch.setattr(terminal, "reopen", lambda s: calls.append(s.id) or True)
    closed = session("c", many_turns(2), status=Status.CLOSED, tty="")
    live = session("l", many_turns(2), status=Status.IDLE, tty="ttys001")

    class Store(FakeStore):
        demo = False

    store = Store([live, closed])
    app = TtylApp(store, interval=60, summaries=False, ring=False)
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("2")  # lane 2 is the closed one
        await app.workers.wait_for_complete()
        await pilot.press("enter")  # impatient second press
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == ["c"]
        assert any("already reopening" in n.message for n in app._notifications)


async def test_toast_when_a_terminal_closes():
    s = session("a", many_turns(2), status=Status.IDLE, tty="ttys001")
    store = FakeStore([s])
    app = TtylApp(store, interval=60)
    async with app.run_test(size=(140, 40)) as pilot:
        await app.workers.wait_for_complete()
        s.status, s.tty = Status.CLOSED, ""
        app.action_refresh()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any("terminal closed" in (n.title or "") for n in app._notifications)
