# Local AI Bridge

A small local bridge that lets Claude Code and ChatGPT (Codex CLI) ask each other for questions, ideas,
second opinions and translation checks. Messages are plain JSON files under `data/requests` in the bridge folder.
Each assistant answers through its own subscription CLI; no API keys are used.
The `plugin/` folder is a Claude Code plugin (`chatgpt-bridge`): a **Claude ↔ ChatGPT** side panel
that shows the conversation live (`/chatgpt` opens it), plus the skills `ask-chatgpt` and
`check-translation-with-chatgpt`. Install it in Claude Code with
`/plugin marketplace add <this repository or folder>` then `/plugin install chatgpt-bridge@ai-bridge`.
The panel finds the bridge from the current folder or the registered user-scope
`local-ai-bridge` MCP entry. Set `AI_BRIDGE_HOME` to point it to a different bridge folder.

For Codex, run `.\codex-plugin\Install.ps1` from the bridge folder in PowerShell.
Open a new local Codex chat, select **Claude Bridge** from the plugin picker, and
ask **“Show the Claude–Codex conversation.”** The plugin reads the same mailbox
and offers a live panel in clients that render MCP Apps. The conversation is
also available as text. To prepare the local marketplace without installing
it, run `.\codex-plugin\Install.ps1 -BuildOnly`.

For a live view beside the chat, ask Codex to open the Claude–Codex side panel.
The panel can also be started from the bridge folder with
`python codex_conversation.py`. Open the printed local URL to follow the
conversation. The viewer closes after five minutes without a refresh.

## Setup

Run the following commands from the bridge folder.

    python ai_bridge.py doctor
    python ai_bridge.py connect

`doctor` checks that Claude Code (`claude auth login`) and Codex CLI (`codex login`) are installed and signed in.
`connect` registers the bridge as an MCP entry named `local-ai-bridge` in each detected client
(existing entries are never overwritten). Refresh the client's MCP list to see `ask_peer`, `read_reply`,
`check_inbox`, `reply_to_peer`, and `save_checkpoint`.

If a client is not detected, set `claude_path` or `codex_path` in `bridge-config.json`.
Relative override paths are resolved from the bridge data folder. After moving
the bridge, remove any MCP entry that points to the old folder and rerun `connect`.
Install the clients if needed:

    winget install --id Anthropic.ClaudeCode -e
    npm install --prefix tools @openai/codex

## Claude asks ChatGPT

With MCP: call `ask_peer` (then `read_reply` with the returned request ID if the answer is not ready).
With the CLI:

    python ai_bridge.py ask --from claude --to chatgpt --question-file question.txt --context-file code.txt
    python ai_bridge.py get REQUEST_ID --wait 45

`ask` waits up to 45 seconds. If it is still running, use `get` with the same ID; do not submit again.
Start a new subject with `--topic "Experiment review"` (or `topic` in MCP).
The label is for display. New requests without `--task` get a unique task ID,
even if another topic has the same label. Reuse the returned `--task TASK_ID`
(or `task_id` in MCP) to continue that topic explicitly. Existing named task IDs
still work. Use `save_checkpoint` in MCP or the following CLI command to save
decisions and unresolved questions for recovery.

    python ai_bridge.py checkpoint TASK_ID --summary-file decisions.txt

A confirmed lost provider session can restart using the checkpoint and up to
six recent answered exchanges from that topic. The local browser view shows
session continuity and lets you open the full context and diagnostics.

## ChatGPT asks Claude

    python ai_bridge.py ask --from chatgpt --to claude --question "Find weaknesses in this design"

Or use the MCP `ask_peer` tool in Codex. For manual replies add `--manual`, then the other side runs
`inbox --for claude` and `reply REQUEST_ID --as claude --answer-file answer.txt`.

## Coach each other

Tell either assistant, **“Use peer coaching on this task. Ask the other assistant to challenge your plan, then test its feedback.”**

1. **Lead:** Share the goal, constraints, proposed approach and checks already run. Ask for the biggest thing you may have missed. Use one bridge topic ID for the exchange.
2. **Coach:** Pick one concrete concern. Explain why it matters and what principle the lead can reuse. Suggest a small check and predict what it will show if the concern is real. If you find no material issue, say what you reviewed.
3. **Lead:** Run the check and report the evidence. Say whether you confirmed the concern and what you changed, ruled it out, or need more evidence. Use the same topic ID if a follow-up could resolve uncertainty, then bring any remaining open decision to Saeed.

Either Claude or Codex can lead. The lead owns edits and verification, and the coach replies through the bridge. Both panels show the exchange.

## History

Every request is one file under `data/requests/REQUEST_ID.json`. Use the commands to change them.

## Troubleshooting

- `doctor` shows which provider is not ready and why.
- Calls count against each provider's subscription limits and can fail when a limit is reached.
- A worker that stops after claiming a request is marked failed when checked. The bridge does not replay a request with an uncertain outcome. Use `cancel REQUEST_ID` for a queued one.
- At most eight pending requests, one active CLI call per provider, 180 seconds maximum runtime by default.
- Worker output is logged to `data/workers.log`.
- The providers still process submitted text on their servers; keep credentials out of questions.
