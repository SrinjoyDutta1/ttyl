"""agt: a map of your agent terminals.

  agt                 live map (TUI)
  agt ls              print the map once
  agt show <which>    one session: recap + turn timeline
  agt jump <which>    focus the terminal tab that session runs in
  agt resume <which>  reopen a closed session in a new terminal window
  agt --demo          try it on made-up sessions

<which> is an id prefix, a tty (ttys005 or 5), a pid, a project name or part of a title.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from . import render, terminal
from .model import Status
from .store import Store, find


def _store(args):
    if args.demo:
        from .demo import DemoStore

        return DemoStore()
    store = Store(days=args.days)
    store.show_all = args.all
    return store


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-a", "--all", action="store_true", help="include every session ever, not just recent + live")
    common.add_argument("--days", type=float, default=3.0, help="recent window for closed sessions (default 3)")
    common.add_argument("--demo", action="store_true", help="show made-up sessions instead of yours")

    ap = argparse.ArgumentParser(prog="agt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ls", parents=[common], help="print the map once")
    show = sub.add_parser("show", parents=[common], help="recap + timeline for one session")
    show.add_argument("which")
    show.add_argument("-n", type=int, default=40, help="how many turns to show")
    jump = sub.add_parser("jump", parents=[common], help="focus that session's terminal tab")
    jump.add_argument("which")
    resume = sub.add_parser("resume", parents=[common], help="reopen a closed session in a new window")
    resume.add_argument("which")
    args = ap.parse_args(argv)

    if args.cmd is None:
        from .tui import run

        run(_store(args))
        return 0

    console = Console(highlight=False)
    sessions = _store(args).refresh()

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
            console.print(f"{s.project} isn't in a terminal ({s.status.value}); try: agt resume {s.id[:8]}")
            return 1
        return 0 if terminal.focus(s.tty) else 1
    if args.cmd == "resume":
        if s.status != Status.CLOSED:
            console.print(f"{s.project} is still open on {render.where(s) or '?'}; jumping there instead")
            return 0 if s.tty and terminal.focus(s.tty) else 1
        return 0 if terminal.reopen(s) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
