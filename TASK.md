# TASK

Goal: Claude Code does the work and, through this bridge, asks ChatGPT (Codex CLI, Saeed's subscription)
for questions, ideas, second opinions and translation checks. ChatGPT/Codex can ask Claude the same way.

Current state (2026-11-11):
- ai_bridge.py (mailbox, MCP server, CLI), peer_sessions.py (CLI arguments and saved sessions), tests in tests/.
  No dashboard, desktop app or cloud host; the previous version is on git branch `archive/full-desktop-app`.
- The answering peer can look things up, read-only: Codex gets a read-only shell and web search, Claude gets
  Read, Grep and Glob. It starts in the asker's project folder. Neither can edit anything.
- `ask_peer` and `read_reply` wait for the answer and return a short record instead of the whole stored file.
- Manual questions expire after 24 hours; damaged request files are skipped; questions containing a
  recognisable credential are refused.
- Message history lives in data/requests/*.json; the Claude Code side panel and the Codex view show it live.
- README.md is the only copy of the guide. AGENTS.md and CLAUDE.md are generated from ai_bridge.py with
  `python ai_bridge.py setup --refresh-docs`.

Open items:
- Delegated tasks (a peer that may also edit files in an assigned scope) are designed in bridge-findings.txt
  and not built.
- Read access is not confined to the workspace folder (see "What the peer can see and do" in the README).
