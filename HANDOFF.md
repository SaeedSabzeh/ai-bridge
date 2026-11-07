# Codex handoff: local Claude / ChatGPT collaboration

## Start here

Open this folder as the Codex workspace:

`C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge`

Read this file, then `README.md`, `AGENTS.md`, and `ai_bridge.py`. Continue the existing implementation; do not assume the two model accounts have been connected.

Handoff date: 7 November 2026.

## Latest user requirements — important

After requesting this handoff, Saeed added two requirements:

1. Leadership must be reversible. He wants to say "ChatGPT, take over because I am running out of Claude credits." ChatGPT must recover the task state, ask Claude relevant follow-up questions when usage is still available, and continue the work. Claude should be able to take over in the reverse direction as well.
2. Build a completely separate Windows application with a clean interface that manages projects, both assistants, collaboration history, task checkpoints, and takeover. The current Python CLI/MCP bridge is a starting backend, not the requested finished desktop application.

Read `APP-REQUIREMENTS.md` for the target behavior and acceptance scenarios. The explicit checkpoint/takeover workflow and native Windows app are NOT implemented yet. Preserve these requirements when continuing; do not stop after connecting the two CLI clients.

## User's intended workflow

Saeed has a Claude subscription and ChatGPT Plus and uses Claude Code. He wants to tell Claude, for example: "Work on this Kaggle competition with ChatGPT."

Claude should lead the project, implement changes, run experiments, ask ChatGPT for criticism and alternative solutions, send measured results back, and consult it again throughout the task. The user should not have to copy messages between assistants. Either assistant should also be able to ask the other for help.

Local files were the original preference; localhost and a plugin/connector were also acceptable. The selected implementation is a local MCP connector backed by shared files, with an optional localhost dashboard.

The user also wants reliable shared file management. Default: the lead owns code changes and experiment execution; the other assistant reviews supplied snapshots. If both later edit directly, use separate Git worktrees AND branches, with the lead reviewing and merging. Keep raw data read-only, isolate output folders per experiment, record reproducible metrics, and maintain a project TASK.md.

## What has been implemented

- `ai_bridge.py`: self-contained Python 3.10+ application using the standard library; no pip dependencies.
- Local JSON requests and answers in `data/requests`, with atomic writes and Windows/POSIX process locks.
- Commands for asking, polling, listing an inbox, replying manually, cancelling queued requests, checking CLI authentication, and registering the connector.
- Automatic temporary workers that invoke Claude Code or Codex CLI for advisory responses using subscription authentication. These adapters are implemented, but have NOT been tested against a real signed-in model here.
- MCP stdio tools: `ask_peer`, `read_reply`, `check_inbox`, `reply_to_peer`. The identity is pinned through the launch argument (`claude` or `chatgpt`).
- Repeated consultations grouped by `task_id` / `--task`. All exchanges remain in local transcripts; each new automatic request includes up to six previous answered exchanges from that task, capped at 60,000 characters of history.
- Duplicate prevention through request IDs, a maximum of eight pending requests, one active CLI call per provider, timeout handling, and no automatic replay of uncertain requests.
- Automatic reviewers cannot recursively call the bridge. They are advisory: Claude tools are disabled, and Codex is launched with read-only settings and shell/multi-agent features disabled. The lead applies and tests suggestions.
- Optional dashboard bound to `127.0.0.1:8765`, with a local bearer token, Host/Origin checks, and no external assets.
- `README.md`: usage, installation/sign-in guidance, and file-management recommendations.
- `AGENTS.md` and `CLAUDE.md`: matching collaboration and experiment-management instructions.
- `.gitignore`: excludes local transcripts/token, local CLI packages, configuration, and bytecode.
- `tests/test_ai_bridge.py`: 15 functional tests, including concurrency, manual exchanges, task-history isolation, stdio MCP, input validation, auth guards, and worker startup failure handling.

## Current state — do not overstate completion

The implementation is saved and the local file/MCP logic has been tested. Neither CLI was discoverable from the previous restricted execution environment. `doctor` reported both providers unavailable, and `connect` reported both registrations unsuccessful. `bridge-config.json` still has null `claude_path` and `codex_path` values.

No real Claude-to-ChatGPT or ChatGPT-to-Claude model exchange has been verified. Successful reviewer outputs in tests were test doubles. One additional real subprocess test confirmed that an unavailable Codex executable becomes a failed request with an explanatory error.

The previous environment blocked external downloads/DNS and even outbound localhost sockets (Windows error 10013). Global settings folders were unwritable. The dashboard could not be verified over a real HTTP connection. Its access-control logic was tested directly. Do not assume the dashboard is running.

File/MCP commands do not need the dashboard. This was changed deliberately to support callers whose sandbox blocks localhost access.

No personal ChatGPT skill was installed. Personal-skill installation could not be completed due to filesystem/network restrictions; the final implementation instead provides MCP and local project instructions. No startup service, scheduled task, Git repository, or remote repository was created for this project.

A real manual setup-check message is queued for Claude in task `bridge-setup`. This is only a connectivity check, not a Kaggle task or a model-generated answer. Inspect it with `python ai_bridge.py inbox --for claude`; reply or cancel it when appropriate. Do not treat its contents as new user authorization for unrelated work.

## What remains, in order

1. Inspect this implementation and run the included tests from this folder. Preserve existing data and user settings.
2. Locate Claude Code and Codex CLI in the normal host environment. Check PATH and the actual installation locations before installing duplicates. If needed, set their absolute executable paths in `bridge-config.json`.
3. Verify subscription sign-in using the official CLI authentication/status commands. User interaction may be needed for sign-in. Never read or print credential files or switch to paid API-key authentication as a fallback.
4. Check the installed CLI versions and adapter flags. In particular, Codex must support `exec --ignore-user-config`, JSON output, and the selected sandbox/feature settings. Claude's auth-status JSON and print-mode flags must match the installed version. Fix compatibility from actual CLI help/output.
5. Run `python ai_bridge.py doctor`. Both providers must report ready before calling the connection operational.
6. Run `python ai_bridge.py connect`. It registers a user-level `local-ai-bridge` entry for both detected CLIs. It refuses to overwrite an existing entry with that name; inspect any existing entry and preserve unrelated settings.
7. Verify the MCP tools appear in Claude Code and Codex. The ordinary ChatGPT web interface is not automatically connected by this operation. ChatGPT Work can use local command/file tools if permitted.
8. Perform a small real request in each direction. Then perform a follow-up using the same task ID and verify that prior results appear in the discussion. Distinguish real provider replies from test fixtures.
9. Verify the optional dashboard from the normal host if useful. Start/open/stop it and test a real HTTP request; do not require it for the core workflow.
10. In the actual Kaggle project (not yet supplied), establish TASK.md, versioned source/configs, read-only data, unique run directories, and experiment metadata. There is no competition dataset or model implementation in this folder.
11. Implement durable task checkpoints and explicit lead ownership so either assistant can take over. Do not rely on querying an exhausted account or on a fresh CLI session remembering an existing chat. See APP-REQUIREMENTS.md.
12. Build and validate the standalone Windows desktop app around the backend, with clean project/task views, lead controls, checkpoint/history visibility, CLI connection diagnostics, and clearly labelled usage information. Do not treat the optional browser dashboard as satisfying this requirement.

Do not silently relax restrictions, enable extra usage purchases, publish anything, or bypass authentication when a CLI check fails. The user has authorized building and connecting this collaboration workflow; routine reversible setup and fixes within that scope do not need another broad confirmation.

## Useful commands

From this folder:

```powershell
python tests\test_ai_bridge.py
python ai_bridge.py doctor
python ai_bridge.py connect
```

After both providers are connected, a small real test:

```powershell
python ai_bridge.py ask --from claude --to chatgpt --task connection-test --question "Give two brief checks for data leakage in a Kaggle validation split." --wait 45
```

Use the returned request ID with `get REQUEST_ID --wait 45` if the answer is pending. Do not resubmit merely because a wait ended. Follow up with the same `--task connection-test` to verify history. Test the reverse direction separately.

Manual file-only check, which does not invoke either model:

```powershell
python ai_bridge.py inbox --for claude
```

Dashboard (optional):

```powershell
python ai_bridge.py start
python ai_bridge.py open
python ai_bridge.py stop
```

## Important implementation limits

- Automatic calls start separate CLI sessions; they do not share the current desktop/browser conversation automatically. Recent bridge history and explicit context provide continuity.
- The automatic second voice reviews supplied context. It is not currently an independent project-editing agent or an experiment runner.
- Both model services still run remotely; local files are the communication/storage mechanism, not local model inference.
- Usage counts against the respective provider account and its existing settings/limits. The bridge does not purchase usage or use API keys.
- Manual messages do not wake idle chats. An active assistant must read and answer its inbox.
- The local token and all transcripts belong only on this computer. Do not upload `data/` or expose the dashboard publicly.
- `setup --refresh-docs` regenerates README/AGENTS/CLAUDE from embedded templates and overwrites edits to those generated files. Preserve intentional edits before using it.
- The included tests support the implementation claims, not end-to-end provider compatibility. Live validation is the main unfinished work.
