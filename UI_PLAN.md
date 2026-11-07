# AI Bridge desktop UI improvement plan

Goal: make `bridge_desktop.pyw` easy to use, clean and understandable for someone
who has never seen it. Plain words, one obvious next action, no raw JSON or internal
jargon on the main screen. Behaviour of the store/mailbox does not change.

All phases edit `bridge_desktop.pyw`, so they run **one at a time**. Each phase must keep
`python -m pytest tests -q` green and produce screenshots with
`verification/check_timeline_ui.py` (extend it to capture more states when needed).

## Phase 1: the task header and actions
- Replace the 9-button grid with **one context-aware primary button**:
  "Start" (new task), "Resume" (paused), "Pause" while running. Stop sits next to it
  only while running. Hide buttons that cannot apply instead of greying out a wall of them.
- Plain secondary buttons: "Hand over to Codex" / "Hand over to Claude" (was Switch lead),
  "Get a second opinion" (was Ask adviser).
- Move rare actions (Edit checkpoint, Open checkpoint file, Open project folder,
  Recover interrupted run) into a **"More ▾" menu**.
- Status line in plain language with a coloured pill, e.g. "Running · Claude Code is working",
  "Paused · Resume when ready", "Completed". Drop "Generation N" from the header
  (it stays in Diagnostics).
- Keep the reason text for why Start/Resume is unavailable, right under the button.

## Phase 2: sidebar, first run and connections
- Task rows: status symbol + objective (▶ running, ⏸ paused, ✓ completed, ○ new),
  no "Completed |" prefix text.
- Empty state: with no project, show a welcome panel with 3 numbered steps
  (Add a project folder → Add a task → Start) and one primary "Add your first project" button.
  With a project but no task, show a primary "Add a task" call to action.
- Rename "Approved commands" → "Commands…"; tooltip-free plain captions in forms.
- Footer: coloured dot per assistant (green ready, amber limit, red signed out);
  clicking it opens Connections.

## Phase 3: readable content tabs + robustness
- Experiments tab: list (status, command, time) on the left, readable detail on the right,
  same pattern as Timeline; metrics shown as "name: value" lines, not JSON.
- Checkpoint tab: render with the same note formatting as Timeline; collapse empty
  sections into one "Not recorded yet: …" line.
- Timeline: Markdown headings inside a note use a distinct "subsection" style, so a
  Summary that starts with "## Findings" no longer looks like two stacked empty labels.
- Connection check thread always reports a result (catch `Exception`), so a surprise error
  cannot freeze connection checks; recheck disconnected CLIs at most every 30 s when idle.
- `start_blocker` must not raise if the running task's project is missing.

## Phase 4: tests
- New `tests/test_desktop_helpers.py` for the pure helpers (`task_activity`,
  `start_blocker`, `insert_note`, any new helpers from phases 1-3), without opening windows.
