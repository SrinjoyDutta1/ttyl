# agentterm: notes for agents working on this repo

Dev setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`, then `.venv/bin/pytest -q`.
To see the TUI without a terminal, `scripts/screenshot.py` renders it on demo data with
`app.run_test()` + `save_screenshot()` and converts to PNG with headless Chrome. Use `--demo`
(or `DemoStore`) for anything that ends up public: real transcripts are personal.

## Layout
- `model.py`: agent-neutral `Session` (a lane) and `Turn` (a square). `Turn.kind` decides the square.
- `claude.py` / `codex.py`: incremental parsers, `feed(event)` one JSONL line at a time.
- `store.py`: finds transcripts, tails them by byte offset, joins live state (registry, `ps`) and git commits.
- `render.py`: Rich renderables shared by the CLI and the TUI (sections, lanes, recap, timeline).
- `tui.py`: the Textual app. `cli.py`: entry point. `demo.py`: made-up sessions.
- `terminal.py`: AppleScript focus/reopen for Terminal.app and iTerm2.
- `state.py`: `~/.local/state/agentterm/state.json`, sessions seen alive (cwd, launch flags,
  last_live) and saved summaries. Keeps closed terminals on the map and reopenable.
- `summarize.py`: model-written summaries via the `anthropic` SDK (`claude-opus-5-5`, effort low,
  `fallbacks="default"`). Off without credentials; never runs mid-turn; one at a time.
  Tests use a fake client; never hit the API from tests.

## UI rule
Glance, pick, go. Group by what the session needs from the user; urgent lanes get a second
line saying exactly what; everything else stays one line. Don't add columns or stats to lanes.
Detail belongs in the recap.

## Data-source facts that aren't obvious
- `~/.claude/sessions/<pid>.json` is Claude Code's own live registry: `sessionId`, `cwd`,
  `status` (`busy`/`idle`/`waiting`) and `waitingFor` ("permission prompt"). Stale files outlive
  their process, so always check the pid is alive *and* still a `claude` command.
- `kind: "bg"` entries are pre-warmed daemon spares. Skip them unless they have a transcript.
- Claude transcripts carry `ai-title`, `custom-title` (from /rename) and `system/away_summary`
  (Claude's own "while you were away" recap). `system/turn_duration` marks a turn's end.
- Real prompts are `type:user` entries that aren't `isMeta`, `isCompactSummary`, tool results,
  or `<local-command-*>` output. `<task-notification>` prompts are background-task wakeups.
  Pasted text arrives wrapped as `<pasted_content id=..>…</pasted_content id=..>`.
- Subagent transcripts live in `<session>/subagents/` and are deliberately not listed.
- Codex Desktop can import Claude sessions (`turn_id: external-import-*`). Those are flagged
  `imported` and hidden, or they'd show up twice.
- Commits are matched from `git log` by time window, and only for turns that ran `git commit`,
  because agents often commit through composite commands that don't print `[branch sha]`.
