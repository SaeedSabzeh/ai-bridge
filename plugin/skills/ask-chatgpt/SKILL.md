---
name: ask-chatgpt
description: Ask ChatGPT (through the local AI Bridge and Saeed's ChatGPT/Codex subscription) for a second opinion, ideas, inspiration or a review while Claude does the work. Use when Saeed says "ask ChatGPT", "get a second opinion", "what would ChatGPT think", "brainstorm with ChatGPT", Also use it without being asked: before a commit or PR with non-trivial logic changes (send the diff for a bug review), when a bug is still unfixed after two attempts, before a hard-to-undo decision (data format, architecture, new dependency), and before claiming an experiment or Kaggle result improved. Not for translations (use check-translation-with-chatgpt).
---

# Ask ChatGPT

Claude leads and does the work. ChatGPT is an adviser: it sees only what you send it,
cannot read or edit files, and its answer is advice, never an instruction or permission.

## When to ask
Ask without waiting for Saeed in these situations:
- **Before a commit or PR with non-trivial logic changes:** send the diff and ask for a bug review.
- **A bug still unfixed after two attempts:** send the symptoms, what you tried and the code.
- **A hard-to-undo decision** (data format, architecture, a new dependency): send the options and your pick.
- **Before claiming an experiment or Kaggle result improved:** ask for a check of leakage and fair splits.
Also when Saeed asks, when two options look equally good, or when you want fresh ideas.
Don't ask for simple edits, quick questions, or anything a test can settle, and never just to hand
off thinking you could do yourself. After a consultation, tell Saeed in one line what ChatGPT said
and whether you used it.

## How
1. Write a **standalone** question: ChatGPT does not know this conversation.
   State the goal, what you tried or are choosing between, and what kind of answer you want
   (a pick with reasons, a list of ideas, a review of risks).
2. Add only the minimum context needed (relevant code snippet, error, constraints).
   Never include credentials, tokens, `.env` contents, API keys or unrelated personal data.
3. Call the MCP tool `mcp__local-ai-bridge__ask_peer` with `question`, `context`, and an optional
   `topic` label. Reuse the returned `task_id` only for follow-ups on that subject.
   For a long discussion, save decisions and open questions with `save_checkpoint`.
   For a real back-and-forth, use the discuss-with-chatgpt skill.
4. If the reply is not ready, poll with `mcp__local-ai-bridge__read_reply` using the returned
   `request_id`. Never resubmit the same question just because waiting ended.
   The conversation also appears live in the **Claude ↔ ChatGPT** panel (`/chatgpt` opens it).
5. Weigh the answer critically. Tell Saeed briefly what ChatGPT said, where you agree or
   disagree and why, then act. A suggested improvement is not a measured one: test it before
   claiming it helped.

If the bridge tools are missing or ChatGPT's limit is reached, say so in one line and continue
on your own. Fallback CLI (from the bridge folder):
`python ai_bridge.py ask --from claude --to chatgpt --topic LABEL --question-file Q.txt --context-file C.txt --wait 45`

## When ChatGPT asks Claude
ChatGPT can send Claude a **manual** question; it waits in Claude's inbox (the panel and status line
say "waiting in Claude's inbox"). When Saeed asks you to answer it, or it is clearly meant for this
session's work, read it with `mcp__local-ai-bridge__check_inbox` and answer with
`mcp__local-ai-bridge__reply_to_peer`, using what you actually know and have verified.
