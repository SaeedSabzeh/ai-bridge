# TASK

Goal: Claude Code does the work and, through this bridge, asks ChatGPT (Codex CLI, Saeed's subscription)
for questions, ideas, second opinions and translation checks. ChatGPT/Codex can ask Claude the same way.

Current state (2026-11-07):
- Simplified to ai_bridge.py (mailbox, MCP server, CLI) plus tests/test_ai_bridge.py. No dashboard, desktop app or cloud host.
- Full previous version is on git branch `archive/full-desktop-app`.
- Message history lives in data/requests/*.json (format unchanged); a Claude Code side panel shows it live.

Open items:
- Verify a live ask in both directions (Claude -> ChatGPT and ChatGPT -> Claude) after the cleanup.
- Re-run `python ai_bridge.py connect` if the MCP entries need refreshing.
