# Local AI Bridge

A small local bridge that lets Claude Code and ChatGPT (Codex CLI) ask each other for questions, ideas,
second opinions and translation checks. Messages are plain JSON files in `C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\data\requests`.
Each assistant answers through its own subscription CLI; no API keys are used.
The `plugin/` folder is a Claude Code plugin (`chatgpt-bridge`): a **Claude ↔ ChatGPT** side panel
that shows the conversation live (`/chatgpt` opens it), plus the skills `ask-chatgpt` and
`check-translation-with-chatgpt`. Install it in Claude Code with
`/plugin marketplace add <this repository or folder>` then `/plugin install chatgpt-bridge@ai-bridge`.
The panel reads `data/requests` at the path set in `plugin/hooks/register.tsx` (`REQUESTS`).

## Setup

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" doctor
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" connect

`doctor` checks that Claude Code (`claude auth login`) and Codex CLI (`codex login`) are installed and signed in.
`connect` registers the bridge as an MCP entry named `local-ai-bridge` in each detected client
(existing entries are never overwritten). Refresh the client's MCP list to see `ask_peer`, `read_reply`,
`check_inbox` and `reply_to_peer`.

If a client is not detected, set `claude_path` or `codex_path` in `C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\bridge-config.json`.
Install the clients if needed:

    winget install --id Anthropic.ClaudeCode -e
    npm install --prefix "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\tools" @openai/codex

## Claude asks ChatGPT

With MCP: call `ask_peer` (then `read_reply` with the returned request ID if the answer is not ready).
With the CLI:

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" ask --from claude --to chatgpt --question-file question.txt --context-file code.txt
    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" get REQUEST_ID --wait 45

`ask` waits up to 45 seconds. If it is still running, use `get` with the same ID; do not submit again.
Reuse `--task TASK_ID` (or `task_id` in MCP) for follow-ups so recent answers are included.
Only the question and the context you supply are sent to the other model.

## ChatGPT asks Claude

    python "C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\ai_bridge.py" ask --from chatgpt --to claude --question "Find weaknesses in this design"

Or use the MCP `ask_peer` tool in Codex. For manual replies add `--manual`, then the other side runs
`inbox --for claude` and `reply REQUEST_ID --as claude --answer-file answer.txt`.

## History

Every request is one file: `C:\Users\SAEED\Desktop\ChatGPT\2026-11-07\new-chat-2\ai-bridge\data\requests/REQUEST_ID.json`. Use the commands to change them.

## Troubleshooting

- `doctor` shows which provider is not ready and why.
- Calls count against each provider's subscription limits and can fail when a limit is reached.
- Requests are never replayed automatically. Use `cancel REQUEST_ID` for a queued one.
- At most eight pending requests, one active CLI call per provider, 180 seconds maximum runtime by default.
- Worker output is logged to `data/workers.log`.
- The providers still process submitted text on their servers; keep credentials out of questions.
