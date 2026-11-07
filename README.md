# Local AI Bridge

## Windows desktop app

Double-click **Launch AI Bridge.cmd** in this folder, or run
`python bridge_desktop.pyw` in PowerShell. The app uses Python 3.10+ with Tkinter
and runs in its own Windows window. It does not require the browser dashboard,
a listener, or an installer. The assistant CLIs are still required for live turns.

For a separate host that can run tasks while your PC is off, see the
[private Google Cloud setup](cloud/README.md).

1. Open **Connections**. If Claude and Codex both show **Ready**, there is no
   extra connection step in the app. If one is signed out, use its **Sign in**
   button and choose your subscription account. Use **Check connections** to
   refresh. The app does not estimate remaining quota.
2. Select **+ Project** to register an existing working folder, then **+ Task**
   to record the objective, constraints, and first lead. If work is already in
   progress, use **Edit checkpoint** to capture it before starting a turn.
3. Use **Start / Resume** for one lead turn. The lead can read and edit project
   text files, consult its adviser, and run any command preset you approved for
   that project. It saves useful progress before it ends. A task stays paused
   between turns until you resume it or the lead marks it complete.
   When **Start / Resume** is unavailable, the app shows the reason below the
   controls. The project list marks a project with an active turn with ●.
   If a lead reaches a usage limit, **Connections** shows **Limit reached**.
   Use **Switch lead**, or wait for the provider's reset and clear its limit.
   In **Timeline**, select a turn, checkpoint, question, or reply to read it.
   **Mailbox history** lists exchanges from every task.
4. Use **Switch lead** to finish the current turn first or stop it immediately.
   The app saves file state and increments the ownership generation before
   the new lead starts. By default the new lead continues from disk without
   contacting the outgoing assistant; you can allow follow-up questions.
   **Pause** lets the current turn finish without starting new checks or reviews;
   **Stop** also terminates its local process tree.
5. If you close and reopen the window, the worker and history are still there.
   If the worker itself was interrupted, use **Recover interrupted run**.
   Recovery checks the process tree and releases the reservation without
   resubmitting a request. Review uncommitted files before starting another turn.

**Checkpoint**, **Timeline**, and **Experiments** show task progress, advice, and
observed results. **Mailbox history** includes existing CLI/MCP transcripts.
**Diagnostics** shows raw logs. Desktop task state is stored separately in
`data/desktop/workspace.json`; `data/requests/` remains the original mailbox.
The app writes an `AI-BRIDGE-CHECKPOINT-<task-id>.md` snapshot in each project.
Edit the checkpoint through the app (or CLI), because the Markdown is a view of
the saved state. **Open checkpoint** repairs the Markdown from the saved state
if a process quit before the file could be updated. Back up the bridge's
`data` directory locally along with your project files to retain the full history.

### Commands and experiments

In **Approved commands**, configure a JSON object mapping a short name to an
argument list. The first argument is the absolute path to an executable. For
example, adjust the executable to match your installation:

```json
{"verify":["C:/Python311/python.exe","-m","unittest","discover","-s","tests"]}
```

There are no presets by default. Approve commands only for trusted local
projects: they run with your Windows account, and a test runner may execute
code the lead edited. The app contains child processes for stop and takeover;
it does not turn arbitrary scripts into sandboxed code. Avoid commands that
submit jobs to services outside the local process tree. Pause any external
writers separately before using the desktop lead.
For longer local commands, raise the **Lead turn limit** under
**Connections → CLI settings** before starting the turn.

Each command gets its own `runs/bridge-<id>/` directory through the
`AI_BRIDGE_RUN_DIR` environment variable. Write artifacts there and optionally
write a small `metrics.json`. The app saves `run.json`, `output.txt`, and a
`result.json` on confirmed completion. A failed or interrupted run is not
reported as a measured improvement. Ask the lead to record the configuration,
seed, validation split, and environment in the run metadata, and update the
best result only after comparing on the same split. The built-in file tools
restrict writes under `data`, `datasets`, `raw`, and `runs`.

### Taking over from a terminal

The same task controls are available to an assistant with local command access:

```powershell
python lead_run.py list
python lead_run.py status TASK_ID
python lead_run.py switch TASK_ID --to chatgpt --resume
python lead_run.py switch TASK_ID --to claude --resume --stop-current
```

Use `--stop-current` only when you want to interrupt the current turn. Add
`--allow-outgoing-review` to let the new lead ask the outgoing assistant for advice. Without
`--resume` the transfer leaves the new lead paused. CLI sessions start fresh and
use the stored checkpoint, recent discussion, and workspace files for continuity.
They do not recover unsaved browser chat history. The app permits one lead at a
time; advisers do not edit the project. It does not change account billing or
switch providers automatically on a quota error.

### Verification

```powershell
python -m unittest discover -s tests -v
python bridge_desktop.pyw --smoke
python ai_bridge.py doctor
```

`python tests/live_codex_smoke.py` is an opt-in subscription test that uses a
disposable project under `verification/`. It does not run in the offline suite.
Add `--takeover` to resume from a fixture checkpoint. With both subscriptions
signed in, `python tests/live_collaboration_smoke.py` exercises a live handoff
and one review in each direction. These checks make real subscription calls.

## Original bridge CLI and optional dashboard

The shared folder is `C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge`. Start the service with:

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" start

Open the authenticated local dashboard with:

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" open

## Connect the two assistants

Register this bridge as an MCP connector in Claude Code and Codex CLI:

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" connect

This adds a user-level MCP entry called `local-ai-bridge` to each detected client, preserving their other entries.
If an entry with that name already exists, inspect it with the client's `mcp get local-ai-bridge` before changing it.
Refresh the client's MCP connection list to see `ask_peer`, `read_reply`, `check_inbox`, and `reply_to_peer`.
The connector uses local stdio and shared files. The localhost dashboard is optional.
It does not need a tunnel or a public endpoint and works when a caller's sandbox blocks localhost sockets.
If the CLIs are not installed or their settings cannot be written, connect reports which registration failed.
This does not register a custom connector in the ordinary ChatGPT web interface.
ChatGPT Work in this folder can use the same bridge through its local command tools:

For ChatGPT Work/Codex, say:
"Read C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\AGENTS.md. Use this bridge to ask Claude for help when a second opinion would materially improve our work."

For Claude Code, say:
"Read C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\CLAUDE.md. Use this bridge to ask ChatGPT for help when a second opinion would materially improve our work."

Run Claude Code or Codex in this folder to load their project instructions automatically.
Other projects and new ChatGPT conversations need the instruction above or a link in their own project instructions.
Ordinary browser chats cannot call localhost or read this folder without a file/command-capable integration.

## Questions and answers

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" ask --from chatgpt --to claude --question "Find weaknesses in this design"
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" ask --from claude --to chatgpt --question-file question.txt --context-file code.txt
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" inbox
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" get REQUEST_ID --wait 45

`ask` waits up to 45 seconds by default. If still running, use `get` with the same ID; do not submit again.
Only the question and the context you supply are sent to the other model.
Automatic replies launch separate advisory CLI sessions, not the open desktop/browser conversations.
Reviews do not carry previous conversations unless you include their relevant context.

For an iterative task, pass `--task kaggle-example` on each question, or reuse `task_id` in the MCP tool.
The worker includes up to six previous answered exchanges from that task, capped at 60,000 characters.
Use distinct task IDs for unrelated projects. Claude can lead the experiments and ask ChatGPT for ideas,
then return with measured scores and errors. Suggestions are hypotheses until the lead assistant tests them.

## File management for a Kaggle project

Default roles: Claude owns implementation and experiment execution; ChatGPT reviews snapshots and proposes changes.
The current automatic reviewer does not edit your project. Claude applies the changes it judges useful and measures them.

Keep code, configs, small notebooks, and a TASK.md brief under Git in the competition project.
Keep raw datasets outside Git and treat them as read-only. Use the same recorded validation split when comparing runs.
Give every experiment a fresh output directory, for example `runs/claude/20261107-001/`.
Record the config, seed, dataset/split version, Git commit, environment versions, metrics, and short conclusion in that directory.
Separate local validation scores from leaderboard scores. Record failed experiments too.
Never let a new run overwrite a previous model, prediction file, or score record.
Prefer one JSON result file per run; let Claude rebuild a summary table from those files.

Maintain TASK.md with the objective, current best validated result, open questions, and next experiments.
Before a review, send ChatGPT the relevant code, the task brief, and actual experiment outputs.
Large notebooks are poor shared editing surfaces: keep reusable model/training code in Python modules.

If both assistants will edit code directly in a future configuration, give them separate Git branches AND worktrees
(separate working directories). A branch alone does not protect simultaneous edits to the same working directory.
Use one lead assistant to review and merge changes. Do not share a writable notebook, checkpoint path,
virtual environment being modified, or append-only metrics file between concurrent writers.
Run GPU-heavy experiments sequentially unless you intentionally assign separate resources.
Keep credentials, tokens, `.env` files, datasets, checkpoints, and generated predictions out of Git and peer prompts.

## Check the connections

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" doctor

Claude Code must be installed and signed in using `claude auth login` with your Claude subscription.
Codex CLI must be installed and signed in using `codex login` with your ChatGPT account.
The bridge refuses API-key authentication and does not read any API-key file.
Calls count against each provider's normal subscription usage limits and may fail when those limits are reached.
No purchase or extra-usage setting is enabled by this program.

Install the official clients if needed (from a normal terminal with internet access):

    winget install --id Anthropic.ClaudeCode -e
    npm install --prefix "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\tools" @openai/codex

If an installed client is not detected, set `claude_path` or `codex_path` in `C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\bridge-config.json`
to its full executable path. A Codex npm script path can end in `node_modules/@openai/codex/bin/codex.js`.
Run doctor again, then restart the bridge after changing configuration.
For the locally installed Codex, sign in with:

    node "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\tools\node_modules\@openai\codex\bin\codex.js" login

## Shared files without automatic CLI calls

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" ask --from chatgpt --to claude --manual --question "Please review this"
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" inbox --for claude
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" reply REQUEST_ID --as claude --answer-file answer.txt

Manual mode waits until the other active assistant checks its inbox and replies.
It never wakes a closed or idle chat. Each question has one answer; another round needs another explicit request.
`data/requests/REQUEST_ID.json` is the durable transcript. Use commands for updates to preserve atomic writes.
Keep `data/access-token.txt` private; clients read it locally. Do not upload it or include it in prompts.

## Stop

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" stop

This shuts down the dashboard after any reviews it launched finish or time out. Independent file/MCP reviews continue.
No startup task or Windows service is installed. The connector launches a temporary worker only when a question is submitted.
Use `cancel REQUEST_ID` for a queued request. Running requests have a maximum runtime of 180 seconds by default.
The queue holds at most eight pending requests, with one active CLI call per provider. Requests are never replayed automatically.
The service binds only to 127.0.0.1, requires a random token for message access, and rejects cross-origin browser requests.
Messages are local plain-text JSON files. The providers still process submitted text on their servers.

## Official documentation

- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/setup
- https://developers.openai.com/codex/cli
- https://developers.openai.com/codex/noninteractive
- https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan
- https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
