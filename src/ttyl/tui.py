"""The live map. Glance (what needs me?), pick (↑↓ or a number), go (enter)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from rich.console import Group
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from . import render, terminal
from .model import Session, Status
from .ring import Ringer
from .store import Store, default_store
from .summarize import Summarizer, enabled_by_env

REOPEN_GRACE = 45.0  # seconds a reopened session gets to show up before ⏎ may reopen it again


class Lanes(OptionList):
    BINDINGS = [Binding("enter", "select", "go / reopen")]


def _signature(s: Session) -> tuple:
    last = s.last_turn
    return (render.section_of(s).key, s.id, s.status, s.title, s.branch, s.tty, len(s.visible_turns),
            last.kind if last else None, last.last_action if last else "", render.when(s), s.waiting_for,
            s.summary_turns, s.ringing)


class TtylApp(App):
    TITLE = "ttyl"
    CSS = """
    #bar { height: 1; padding: 0 1; background: $panel; }
    #lanes { height: auto; max-height: 60%; border: none; padding: 0; background: $background; }
    #lanes:focus { border: none; background-tint: $foreground 0%; }
    #lanes > .option-list--option { padding: 0 1; }
    #lanes > .option-list--option-highlighted,
    #lanes:focus > .option-list--option-highlighted {
        background: $primary 22%; color: $foreground; text-style: none;
    }
    #detail { height: 1fr; border-top: hkey $panel-lighten-2; }
    #recap { width: 46%; padding: 1 2 0 2; }
    #timeline { width: 1fr; padding: 1 1 0 2; border-left: vkey $panel-lighten-2; }
    #recap, #timeline, #lanes {
        scrollbar-size-vertical: 1; scrollbar-background: $background; scrollbar-color: $panel-lighten-2;
    }
    """
    BINDINGS = [
        Binding("1", "go(1)", "go to #", key_display="1-9"),
        *[Binding(str(n), f"go({n})", show=False) for n in range(2, 10)],
        Binding("o", "reopen", "reopen", show=False),
        Binding("a", "toggle_all", "show all"),
        Binding("r", "refresh", "refresh", show=False),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, store: Store | None = None, interval: float = 2.0, summarizer: Summarizer | None = None,
                 summaries: bool = True, ring: bool = True):
        super().__init__()
        demo = getattr(store, "demo", False)
        self.ringer = Ringer(sound=ring and not demo)
        self._phase = True
        self.store = store or default_store()
        self.interval = interval
        if summarizer is None and summaries and enabled_by_env() and not getattr(self.store, "demo", False):
            summarizer = Summarizer(getattr(self.store, "state", None))
        self.summarizer = summarizer
        self._summarizing = False
        self._reopened: dict[str, float] = {}  # session id -> when we asked the terminal to reopen it
        self.sessions: list[Session] = []
        self.order: list[Session] = []  # display order; index + 1 is the lane's number
        self.selected: str | None = None
        self._sig: list[tuple] = []
        self._last_status: dict[str, Status] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="bar")
        yield Lanes(id="lanes")
        with Horizontal(id="detail"):
            with VerticalScroll(id="recap"):
                yield Static(id="recap-body")
            with VerticalScroll(id="timeline"):
                yield Static(id="turns")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Lanes).focus()
        self.action_refresh()
        self.set_interval(self.interval, self.action_refresh)
        self.set_interval(0.5, self._blink)

    def on_resize(self) -> None:
        self._sig = []  # lane layout depends on width
        if self.sessions:
            self._draw()

    # -- data -----------------------------------------------------------------

    @work(thread=True, exclusive=True, group="refresh")
    def action_refresh(self) -> None:
        sessions = self.store.refresh()
        self.call_from_thread(self._update, sessions)

    def _update(self, sessions: list[Session]) -> None:
        for s in self.ringer.update(sessions):
            self.notify(f"{s.project}: {s.title}", title=f"☎ ring ring · {s.ringing}",
                        severity="warning" if s.ringing == "needs you" else "information")
        self._announce(sessions)
        self.sessions = sessions
        self._draw()
        self._summarize_next()

    # -- summaries ------------------------------------------------------------

    def _summarize_next(self) -> None:
        if self.summarizer is None or self._summarizing:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.store.days)
        s = self.summarizer.pick(self.order, self.selected, cutoff)
        if s is not None:
            self._summarizing = True
            self._summarize(s)

    @work(thread=True, group="summarize")
    def _summarize(self, s: Session) -> None:
        try:
            self.summarizer.summarize(s)
        finally:
            self.call_from_thread(self._summarized)

    def _summarized(self) -> None:
        self._summarizing = False
        self._show_detail()
        self._summarize_next()

    def _summary_hint(self) -> str:
        if self.summarizer is None or getattr(self.store, "demo", False):
            return ""
        if not self.summarizer.enabled:
            return f"AI summaries are off: {self.summarizer.disabled_reason}"
        return ""

    def _announce(self, sessions: list[Session]) -> None:
        """Toast when a terminal closes (rings cover needing you and finishing)."""
        first = not self._last_status
        for s in sessions:
            before = self._last_status.get(s.id)
            self._last_status[s.id] = s.status
            if first or before == s.status:
                continue
            if before is not None and s.status == Status.CLOSED:
                self.notify(f"{s.title}\nselect it and press ⏎ to reopen", title=f"{s.project}: terminal closed",
                            severity="warning")
            if s.status != Status.CLOSED:
                self._reopened.pop(s.id, None)  # it's back

    def _blink(self) -> None:
        ringing = [s for s in self.order if s.ringing]
        if not ringing:
            return
        self._phase = not self._phase
        lanes = self.query_one(Lanes)
        count = render.squares_for(self.size.width)
        for s in ringing:
            number = self.order.index(s) + 1
            try:
                lanes.replace_option_prompt(s.id, render.lane(s, number if number <= 9 else None, count, self._phase))
            except Exception:  # the option list is mid-rebuild
                return

    def _stop_ringing(self, s: Session) -> None:
        if s.ringing:
            self.ringer.ack(s.id)
            s.ringing = ""
            self._sig = []
            self._draw()

    def _draw(self) -> None:
        bar = render.header(self.sessions)
        if self.store.show_all:
            bar.append("   all history", "grey42")
        if self.size.width >= 150:
            bar.append("      squares = turns, newest on the right:  ", "grey42")
            bar.append_text(render.LEGEND)
        self.query_one("#bar", Static).update(bar)

        groups = render.grouped(self.sessions)
        self.order = [s for _, members in groups for s in members]
        sig = [_signature(s) for s in self.order]
        if sig != self._sig:
            self._sig = sig
            self._rebuild(groups)
        self._show_detail()

    def _rebuild(self, groups) -> None:
        lanes = self.query_one(Lanes)
        options: list[Option] = []
        number = 0
        for sec, members in groups:
            rule = render.section_rule(sec, len(members))
            options.append(Option(Group(Text(), rule) if options else rule, id=f"§{sec.key}", disabled=True))
            for s in members:
                number += 1
                options.append(Option(render.lane(s, number if number <= 9 else None, render.squares_for(self.size.width),
                                                  self._phase), id=s.id))
        keep = self.selected if any(s.id == self.selected for s in self.order) else None
        lanes.clear_options()
        lanes.add_options(options)
        if keep is None and self.order:
            keep = self.order[0].id
        if keep is not None:
            self.selected = keep
            lanes.highlighted = lanes.get_option_index(keep)

    def _current(self) -> Session | None:
        for s in self.order:
            if s.id == self.selected:
                return s
        return self.order[0] if self.order else None

    def _show_detail(self) -> None:
        body, turns = self.query_one("#recap-body", Static), self.query_one("#turns", Static)
        s = self._current()
        if s is None:
            body.update(Text("No agent sessions yet. Start one in another terminal, or press a to show all history.", "grey50"))
            turns.update("")
            return
        pane = self.query_one("#timeline", VerticalScroll)
        width = max(30, pane.size.width - 4)
        body.update(render.recap(s, hint=self._summary_hint()))
        turns.update(Group(render.timeline(s, limit=200, width=width), Text(), render.LEGEND))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option_id
        if oid and not oid.startswith("§") and oid != self.selected:
            self.selected = oid
            self._show_detail()
            self.query_one("#recap", VerticalScroll).scroll_home(animate=False)
        if oid and not oid.startswith("§"):
            pane = self.query_one("#timeline", VerticalScroll)
            self.call_after_refresh(pane.scroll_end, animate=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_jump()

    # -- actions --------------------------------------------------------------

    def action_go(self, n: int) -> None:
        if n > len(self.order):
            return
        s = self.order[n - 1]
        lanes = self.query_one(Lanes)
        lanes.highlighted = lanes.get_option_index(s.id)
        self.selected = s.id
        self._show_detail()
        self.action_jump()

    def action_jump(self) -> None:
        s = self._current()
        if s is None:
            return
        self._stop_ringing(s)
        if s.status == Status.CLOSED:
            self.action_reopen()
            return
        if not s.tty:
            self.notify(f"runs in {render.where(s) or 'no terminal'}, nothing to jump to", title=s.project)
            return
        if getattr(self.store, "demo", False):
            self.notify(f"would bring {s.tty} to the front", title=f"demo: {s.project}")
            return
        self._focus(s.tty, s.project)

    @work(thread=True, group="osa")
    def _focus(self, tty: str, name: str) -> None:
        if not terminal.focus(tty):
            self.call_from_thread(self.notify, f"couldn't find the tab on {tty}", title=name, severity="error")

    def action_reopen(self) -> None:
        s = self._current()
        if s is None:
            return
        if s.status != Status.CLOSED:
            self.action_jump()
            return
        asked = self._reopened.get(s.id)
        if asked and time.monotonic() - asked < REOPEN_GRACE:
            self.notify("already reopening in another window", title=s.project)
            return
        if getattr(self.store, "demo", False):
            self.notify(terminal.resume_command(s), title="demo: would reopen with")
            return
        self._reopened[s.id] = time.monotonic()
        self._reopen(s)

    @work(thread=True, group="osa")
    def _reopen(self, s: Session) -> None:
        ok = terminal.reopen(s)
        self.call_from_thread(self.notify, terminal.resume_command(s), title="reopened" if ok else "couldn't open a terminal",
                              severity="information" if ok else "error")

    def action_toggle_all(self) -> None:
        self.store.show_all = not self.store.show_all
        self.action_refresh()


def run(store: Store | None = None, summaries: bool = True, ring: bool = True) -> None:
    TtylApp(store, summaries=summaries, ring=ring).run()
