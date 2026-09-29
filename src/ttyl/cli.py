"""ttyl: a map of your agent terminals.

  ttyl                 live map (TUI)
  ttyl ls              print the map once
  ttyl show <which>    one session: recap + turn timeline
  ttyl jump <which>    focus the terminal tab that session runs in
  ttyl resume <which>  reopen a closed session in a new terminal window
  ttyl summarize [which]  write (or refresh) AI summaries now
  ttyl serve          JSON engine for the menu bar app (see gui/)
  ttyl --demo          try it on made-up sessions

<which> is an id prefix, a tty (ttys005 or 5), a pid, a project name or part of a title.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from . import render, terminal
from .model import Status
from .state import State
from .store import Store, find


def _store(args):
    if args.demo:
        from .demo import DemoStore

        return DemoStore()
    store = Store(days=args.days, state=State())
    store.show_all = args.all
    return store


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-a", "--all", action="store_true", help="include every session ever, not just recent + live")
    common.add_argument("--days", type=float, default=3.0, help="recent window for closed sessions (default 3)")
    common.add_argument("--demo", action="store_true", help="show made-up sessions instead of yours")
    common.add_argument("--no-summaries", action="store_true", help="don't send session excerpts to Claude for summaries")
    common.add_argument("--quiet", action="store_true", help="no ring sound or notifications (rows still flash)")

    ap = argparse.ArgumentParser(prog="ttyl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ls", parents=[common], help="print the map once")
    show = sub.add_parser("show", parents=[common], help="recap + timeline for one session")
    show.add_argument("which")
    show.add_argument("-n", type=int, default=40, help="how many turns to show")
    jump = sub.add_parser("jump", parents=[common], help="focus that session's terminal tab")
    jump.add_argument("which")
    resume = sub.add_parser("resume", parents=[common], help="reopen a closed session in a new window")
    resume.add_argument("which")
    summ = sub.add_parser("summarize", parents=[common], help="write AI summaries now (one session, or all that need one)")
    summ.add_argument("which", nargs="?")
    srv = sub.add_parser("serve", parents=[common], help="stream JSON snapshots for the menu bar app")
    srv.add_argument("--once", action="store_true", help="print one snapshot and exit")
    srv.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .serve import serve

        return serve(_store(args), interval=args.interval, once=args.once, ring=not args.quiet,
                     summaries=not args.no_summaries)

    if args.cmd is None:
        from .tui import run

        run(_store(args), summaries=not args.no_summaries, ring=not args.quiet)
        return 0

    console = Console(highlight=False)
    sessions = _store(args).refresh()

    if args.cmd == "summarize":
        return _summarize(console, sessions, args)

    if args.cmd == "ls":
        console.print(render.header(sessions))
        console.print()
        console.print(render.overview(sessions, console.width))
        console.print(render.LEGEND)
        return 0

    s = find(sessions, args.which)
    if s is None and not args.demo:
        # maybe it's older than the window
        store = _store(args)
        store.show_all = True
        s = find(store.refresh(), args.which)
    if s is None:
        console.print(f"[red]no session matches[/] {args.which!r}")
        return 1

    if args.cmd == "show":
        console.print(render.show(s, width=console.width, limit=args.n))
        return 0
    if args.demo and args.cmd in ("jump", "resume"):
        console.print(f"demo: would {'focus ' + s.tty if args.cmd == 'jump' and s.tty else 'run: ' + terminal.resume_command(s)}")
        return 0
    if args.cmd == "jump":
        if not s.tty:
            console.print(f"{s.project} isn't in a terminal ({s.status.value}); try: ttyl resume {s.id[:8]}")
            return 1
        return 0 if terminal.focus(s.tty) else 1
    if args.cmd == "resume":
        if s.status != Status.CLOSED:
            console.print(f"{s.project} is still open on {render.where(s) or '?'}; jumping there instead")
            return 0 if s.tty and terminal.focus(s.tty) else 1
        return 0 if terminal.reopen(s) else 1
    return 0


def _summarize(console: Console, sessions, args) -> int:
    from datetime import datetime, timedelta, timezone

    from .summarize import Summarizer

    if args.demo or args.no_summaries:
        console.print("summaries are off in this mode")
        return 1
    store_state = State()
    summarizer = Summarizer(store_state)
    if args.which:
        s = find(sessions, args.which)
        if s is None:
            console.print(f"[red]no session matches[/] {args.which!r}")
            return 1
        targets = [s]
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        targets = [s for s in render.ordered(sessions) if summarizer.needs(s)
                   and (s.status != Status.CLOSED or (s.last_active and s.last_active >= cutoff))]
        if not targets:
            console.print("every session's summary is up to date")
            return 0
    for s in targets:
        console.print(f"[bold]{s.title}[/]  [grey50]{render.place(s)}[/]")
        text = summarizer.summarize(s)
        if not summarizer.enabled:
            console.print(f"[red]summaries are off:[/] {summarizer.disabled_reason}")
            console.print("Set ANTHROPIC_API_KEY (from console.anthropic.com), then run this again.")
            return 1
        console.print(f"  {text}" if text else "  [grey50](skipped: rate limited or declined; try again later)[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
