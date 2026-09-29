"""The live map. Glance (what needs me?), pick (↑↓ or a number), go (enter)."""

from __future__ import annotations

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
from .store import Store, default_store


class Lanes(OptionList):
    BINDINGS = [Binding("enter", "select", "go to terminal")]


def _signature(s: Session) -> tuple:
    last = s.last_turn
    return (render.section_of(s).key, s.id, s.status, s.title, s.branch, s.tty, len(s.visible_turns),
            last.kind if last else None, last.last_action if last else "", render.when(s), s.waiting_for)


class AgentTermApp(App):
    TITLE = "agentterm"
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
        Binding("o", "reopen", "reopen closed"),
        Binding("a", "toggle_all", "show all"),
        Binding("r", "refresh", "refresh", show=False),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, store: Store | None = None, interval: float = 2.0):
        super().__init__()
        self.store = store or default_store()
        self.interval = interval
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
        self._announce(sessions)
        self.sessions = sessions
        self._draw()

    def _announce(self, sessions: list[Session]) -> None:
        """Toast when a lane starts needing you or finishes its turn."""
        first = not self._last_status
        for s in sessions:
            before = self._last_status.get(s.id)
            self._last_status[s.id] = s.status
            if first or before == s.status:
                continue
            if s.status == Status.WAITING:
                self.notify(f"{s.project}: {s.waiting_for or 'waiting for you'}", title="needs you", severity="warning")
                self.bell()
            elif before == Status.BUSY and s.status == Status.IDLE:
                self.notify(f"{s.project}: {s.title}", title="finished")

    def _draw(self) -> None:
        bar = render.header(self.sessions)
        scope = "all history" if self.store.show_all else f"last {self.store.days:g}d + everything open"
        bar.append(f"      {scope}", "grey42")
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
                options.append(Option(render.lane(s, number if number <= 9 else None, render.squares_for(self.size.width)), id=s.id))
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
        body.update(render.recap(s))
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
        if not s.tty:
            if s.status == Status.CLOSED:
                hint = "closed. Press o to reopen it in a new window"
            else:
                hint = f"runs in {render.where(s) or 'no terminal'}, nothing to jump to"
            self.notify(hint, title=s.project)
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
        if getattr(self.store, "demo", False):
            self.notify(terminal.resume_command(s), title="demo: would reopen with")
            return
        self._reopen(s)

    @work(thread=True, group="osa")
    def _reopen(self, s: Session) -> None:
        ok = terminal.reopen(s)
        self.call_from_thread(self.notify, terminal.resume_command(s), title="reopened" if ok else "couldn't open a terminal",
                              severity="information" if ok else "error")

    def action_toggle_all(self) -> None:
        self.store.show_all = not self.store.show_all
        self.action_refresh()


def run(store: Store | None = None) -> None:
    AgentTermApp(store).run()
