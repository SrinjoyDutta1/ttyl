# ttyl: notes for agents working on this repo

ttyl (talk to you later) watches every Claude Code / Codex session and rings when one needs you.

Dev setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`, then `.venv/bin/pytest -q`.
tests/conftest.py points XDG config/state at temp dirs: tests must never touch the real ones.
`scripts/test_install.sh [--github]` runs install.sh on a simulated brand-new Mac (empty HOME, OS tools only).
To see the TUI without a terminal, `scripts/screenshot.py` renders it on demo data with
`app.run_test()` + `save_screenshot()` and converts to PNG with headless Chrome. Use `--demo`
(or `DemoStore`) for anything that ends up public: real transcripts are personal.

## Layout
- `model.py`: agent-neutral `Session` (a lane) and `Turn` (a square). `Turn.kind` decides the square.
- `claude.py` / `codex.py` / `gemini.py` / `qwen.py`: incremental parsers, `feed(event)` one JSONL line
  at a time (`PARSERS` in store.py). Gemini re-appends whole messages (last copy of an id wins) and
  has `$set` / `$rewindTo` records, so `GeminiParser` rebuilds turns in `flush()` after each batch.
  Gemini/Qwen were built from their source (clones were in the scratchpad), not real sessions.
- `copilot.py` (JSONL, `PARSERS`), `opencode.py` + `goose.py` (SQLite, read-only, reloaded when the db or
  its -wal changes: `Store._database_sessions`), `aider.py` (per-repo markdown, latest run per repo,
  found via known folders + running aider: `Store._aider_sessions`). Terminal agents without a registry
  get live status from their process's cwd (`Store._cli_live`, `procs.is_agent_cli`).
- `store.py`: finds transcripts, tails them by byte offset, joins live state (registry, `ps`) and git commits.
- `render.py`: Rich renderables shared by the CLI and the TUI (sections, lanes, recap, timeline).
- `tui.py`: the Textual app. `cli.py`: entry point. `demo.py`: made-up sessions.
- `terminal.py`: AppleScript focus/reopen for Terminal.app and iTerm2.
- `state.py`: `~/.local/state/ttyl/state.json`, sessions seen alive (cwd, launch flags,
  last_live) and saved summaries. Keeps closed terminals on the map and reopenable.
- `settings.py`: ~/.config/ttyl/settings.json (kept apart from state.json so the CLI, TUI and menu bar
  engine can all change it). Summary mode (`ttyl summaries local|claude|off`): `local_summaries`
  (default on, Apple's on-device model, nothing leaves the Mac) and `summaries` (Claude, OFF by
  default: nothing is sent until the user chooses it). The TUI and engine re-check them every refresh.
- `summarize.py`: model-written summaries, never mid-turn, one at a time; `choose()` picks the writer.
  Claude via the `anthropic` SDK (`claude-opus-5-5`, effort low, `fallbacks="default"`), or Apple's
  on-device model via `gui/Helpers/Summarize.swift` (`ttyl-summarize`, built into the app bundle; 4096-token
  context, so the digest shrinks to fit; it writes only purpose + what's done, since a small model got
  "where it stands" wrong). Claude without a working key falls back to on-device. Tests use a fake client
  and a fake helper (conftest sets `TTYL_SUMMARIZE_HELPER=""`); never hit the API or the real model.
- `ring.py`: which sessions ring (blocked on you, or just finished) until acked; plays a
  generated double-ring WAV. Notifications are posted natively by the menu bar app from the
  snapshot's `rings` (never via osascript: macOS attributes those to Script Editor and clicking
  one opens it). Only the lock holder (`ringer.pid`,
  the menu bar engine when running) makes noise. `muted_until` in settings (`ttyl mute`, the app's 🔔)
  silences the sound only. Tests must use `Ringer(sound=False)`.
- `collide.py`: collision warnings, sessions (any agent, one still open) that edited the same absolute
  path within 60 min. `Session.collisions` feeds the lanes, recap, JSON and one-time alerts.
- `serve.py`: `ttyl serve`, JSON snapshot lines out, JSON commands in; the menu bar app's engine.
  `tests/test_serve.py` checks the JSON keys against `src/ttyl/gui/Sources/Model.swift`; change both together.
- `gui/` (inside the package, `src/ttyl/gui`, so pipx installs ship it): SwiftUI menu bar app,
  built by `ttyl app` / `gui/build.sh` into ~/Library/Caches/ttyl/build with plain swiftc, plus the
  `ttyl-summarize` helper from `gui/Helpers` (arm64, macOS 26 SDK; skipped otherwise) (SourceKit errors in
  single files are noise; the build compiles them together). It runs the engine directly, not via
  a shell: an interactive login shell hung when launched from the app.
  `ttyl-bar --snapshot out.png` renders the panel on demo data (menus can't be snapshotted);
  `--snapshot-live` lays out the real panel, scroll view included, in an offscreen window. build.sh
  runs it and fails if the list collapses (a ScrollView in a MenuBarExtra window has no natural height).
  Clicking a row closes the panel (`Panel.close()` clicks the status item; SwiftUI has no API for it);
  `ttyl-bar --demo --self-test-panel` opens/closes it programmatically and prints whether it worked.

## Releasing
Bump `__version__` (`src/ttyl/__init__.py`) and `CFBundleShortVersionString` (`gui/Info.plist`), push, wait
for CI, then push a `vX.Y.Z` tag: `release.yml` publishes to PyPI and makes the GitHub Release. The Homebrew
formula lives in SrinjoyDutta1/homebrew-tap (its daily `brew bump` opens the update PR). After an upgrade
the old menu bar app keeps running until `ttyl app` rebuilds it, so never drop a snapshot key an older
`Model.swift` requires (that's why `summaries.on` is still sent). `docs/` is also the GitHub Pages site
(`index.html` plays `demo.mp4`); the demo video is composed from demo data, with the panel a real render.

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
