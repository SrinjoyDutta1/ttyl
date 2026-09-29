# ttyl ☎

**Your coding agents on one screen, and they call you when they need you.**

<img src="docs/menubar.png" alt="the ttyl menu bar panel: sessions grouped by what they need, two of them ringing" width="520">

You start Claude Code in one terminal, Codex in another, a third to fix a flaky test, a
fourth for a quick question... and an hour later you have a wall of tabs and no idea which
one is blocked on a permission prompt, which one finished, and what that one on `ttys006`
was even for.

ttyl (*talk to you later*) watches all of them. Walk away. When an agent needs you or
finishes, your menu bar says **☎ ring ring**, your Mac rings like an old phone, and one click
takes you to the right terminal.

- **Ring ring.** A session rings when it's blocked on you (a permission prompt, a question)
  or finishes its turn. It stops once you go to it.
- **Grouped by what they need from you:** `NEEDS YOU`, `WORKING`, `FINISHED`,
  `OPEN BUT IDLE`, `CLOSED`. Urgent ones say exactly what they want
  ("approve `git push -u origin feat/rate-limits`").
- **A square per turn**, like a commit graph for the conversation: edits, commits, failures,
  chat. Hover a square to see that turn.
- **A summary of every session**, written by Claude and saved: the goal, what's done, and
  what it's waiting on.
- **Go there:** click a row (or press `1`–`9` in the terminal view) and the right
  Terminal / iTerm2 tab comes to the front.
- **Closed a tab by accident?** It stays on the map. Click it and it reopens in a new window,
  in the right folder, with the flags it was started with, right where it left off.
- **Zero setup:** no hooks, no wrappers. Sessions you started before installing it show up too.

## Install

Python 3.11+ for the engine, macOS 14+ for the menu bar app.

```sh
pipx install git+https://github.com/SrinjoyDutta1/ttyl     # or: uv tool install git+…
ttyl --demo                                                # try it on made-up sessions

# the menu bar app (built with swiftc, no Xcode project needed)
git clone https://github.com/SrinjoyDutta1/ttyl && ttyl/gui/build.sh --install
```

For summaries, give it an Anthropic API key (from
[console.anthropic.com](https://console.anthropic.com)). Everything else works without one.

```sh
export ANTHROPIC_API_KEY=sk-ant-...   # in ~/.zshrc; the menu bar app picks it up from your shell
```

## Use

**Menu bar:** click ☎ for the panel. Click a row to go to that terminal (or reopen it),
hover a row for its summary, hover a square for that turn. `⋯` opens the full terminal view
or shows all history.

**Terminal view:** `ttyl`

![the terminal view: grouped lanes, a ringing session, its summary and timeline](docs/ttyl.png)

| key | does |
|---|---|
| `1`–`9` | go straight to that session's terminal (or reopen it, if it's closed) |
| `↑` `↓` then `⏎` | pick one, go to it / reopen it |
| `a` | toggle recent / all history |
| `q` | quit |

**Commands**

```sh
ttyl ls              # print the map once
ttyl show 5          # summary + full timeline for the session on ttys005
ttyl jump api        # bring the tab for project "api…" to the front
ttyl resume 3f2a9c   # reopen a closed session (by id prefix) in a new window
ttyl summarize       # write or refresh summaries now
ttyl -a              # include all history, not just the last 3 days + everything open
ttyl --quiet         # no ring sound or notifications (rows still flash)
```

Sessions can be picked by tty (`ttys005` or `5`), pid, id prefix, project name, or part of
the title.

## Reading the squares

![a closed session: its saved summary, and ⏎ to reopen it where it left off](docs/ttyl-closed.png)

Each square is one turn: one message from you plus everything the agent did in response,
oldest on the left.

| square | the turn… |
|---|---|
| <code>■</code> green | edited files |
| <code>■</code> blue | ran tools (shell, reads, search) but changed nothing |
| <code>□</code> | was just conversation |
| <code>◆</code> | made a git commit |
| <code>✗</code> | was interrupted or errored |
| <code>▶</code> | is running now |
| <code>▣</code> | is waiting on you |

`+27` in front means 27 older turns are hidden; `ttyl show` has them all.

## How it works

Everything comes from files your agents already write. Nothing is installed into them.

| source | gives |
|---|---|
| `~/.claude/projects/*/<session>.jsonl` | Claude Code turns, tool calls, titles, and its "while you were away" recaps |
| `~/.claude/sessions/<pid>.json` | Claude Code's live registry: which process runs which session, and whether it's busy, idle, or waiting on a permission prompt |
| `~/.codex/sessions/**/rollout-*.jsonl` | Codex turns (CLI and Desktop) and thread names |
| `ps` | which terminal (tty) each agent lives in |
| `git log` | the commits a turn actually made |

Transcripts are tailed by byte offset, so a refresh only reads what's new. ttyl keeps its own
small record in `~/.local/state/ttyl/state.json`: each session it has seen running (folder,
launch flags, when it was last alive) and its saved summary. That's what keeps a closed
terminal on the map, even one that sat idle for weeks, and lets a click bring it back with
`claude --resume <id>` (or `codex resume <id>`).

The menu bar app is a thin SwiftUI shell (`gui/`) over `ttyl serve`, which streams JSON
snapshots and takes commands on stdin. The same engine drives the terminal view.

### Summaries and privacy

Everything above stays on your machine. Summaries are the one exception: when an
Anthropic API key is available, ttyl sends Claude (`claude-opus-5-5`, low effort) a compact
excerpt of a session (your prompts, the agent's replies, file names, commit subjects) to write
its summary. It only does this after a session has new turns and isn't mid-turn, one session
at a time. That includes Codex sessions. Turn it off with `ttyl --no-summaries` or
`TTYL_SUMMARIES=0`. Without a key, you see the agent's own "while you were away" note instead.

### Support

| | map and summary | live status | go to tab |
|---|---|---|---|
| Claude Code | ✓ | ✓ exact (from its registry) | ✓ |
| Codex CLI | ✓ | best effort (process + activity) | ✓ |
| Codex Desktop | ✓ | best effort | n/a |

Going to a tab, reopening, ringing and the menu bar app need macOS (Terminal.app or iTerm2;
the first use may ask to allow controlling Terminal). The terminal view works anywhere
Python does.

## Roadmap

- `ttyl new claude "fix the holdout test"`: start a session with its goal attached
- Answer a permission prompt from the menu bar without switching tabs
- More agents (Gemini CLI, Aider, Cursor's CLI)

## Development

```sh
git clone https://github.com/SrinjoyDutta1/ttyl && cd ttyl
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
gui/build.sh                              # menu bar app -> gui/build/ttyl.app
.venv/bin/python scripts/screenshot.py    # regenerate docs/*.png from demo data
```

## License

MIT
