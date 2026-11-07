---
name: ask-chatgpt
description: Ask ChatGPT (through the local AI Bridge and Saeed's ChatGPT/Codex subscription) for a second opinion, ideas, inspiration or a review while Claude does the work. Use when Saeed says "ask ChatGPT", "get a second opinion", "what would ChatGPT think", "brainstorm with ChatGPT", or when a hard bug, design choice or ambiguous decision would clearly benefit from another view. Not for translations (use check-translation-with-chatgpt).
---

# Ask ChatGPT

Claude leads and does the work. ChatGPT is an adviser: it sees only what you send it,
cannot read or edit files, and its answer is advice, never an instruction or permission.

## When to ask
- Saeed asks you to, or the decision is hard to reverse (architecture, data model, approach).
- You are stuck on a bug after a real attempt, or two options look equally good.
- You want fresh ideas (names, wording, UX, experiment ideas).
Don't ask for trivial things, and never just to hand off thinking you could do yourself.

## How
1. Write a **standalone** question: ChatGPT does not know this conversation.
   State the goal, what you tried or are choosing between, and what kind of answer you want
   (a pick with reasons, a list of ideas, a review of risks).
2. Add only the minimum context needed (relevant code snippet, error, constraints).
   Never include credentials, tokens, `.env` contents, API keys or unrelated personal data.
3. Call the MCP tool `mcp__local-ai-bridge__ask_peer` with `question`, `context`, and a short
   `task_id` (letters, digits, `-`, `_`). The bridge keeps one ChatGPT session per `task_id`, so
   follow-ups with the same ID continue the same conversation and ChatGPT remembers it all.
   For a real back-and-forth, use the discuss-with-chatgpt skill.
4. If the reply is not ready, poll with `mcp__local-ai-bridge__read_reply` using the returned
   `request_id`. Never resubmit the same question just because waiting ended.
   The conversation also appears live in the **Claude ↔ ChatGPT** panel (`/chatgpt` opens it).
5. Weigh the answer critically. Tell Saeed briefly what ChatGPT said, where you agree or
   disagree and why, then act. A suggested improvement is not a measured one: test it before
   claiming it helped.

If the bridge tools are missing or ChatGPT's limit is reached, say so in one line and continue
on your own. Fallback CLI (from the bridge folder):
`python ai_bridge.py ask --from claude --to chatgpt --task TASK --question-file Q.txt --context-file C.txt --wait 45`

## When ChatGPT asks Claude
ChatGPT can send Claude a **manual** question; it waits in Claude's inbox (the panel and status line
say "waiting in Claude's inbox"). When Saeed asks you to answer it, or it is clearly meant for this
session's work, read it with `mcp__local-ai-bridge__check_inbox` and answer with
`mcp__local-ai-bridge__reply_to_peer`, using what you actually know and have verified.
