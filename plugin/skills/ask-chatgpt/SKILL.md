---
name: ask-chatgpt
description: 'Ask ChatGPT (through the local AI Bridge and the ChatGPT/Codex subscription) for a second opinion, ideas, inspiration or a review while Claude does the work. ChatGPT can read the project files and run read-only commands such as git diff, so it reviews the real code. Use when Saeed says "ask ChatGPT", "get a second opinion", "what would ChatGPT think" or "brainstorm with ChatGPT". Also use it without being asked - before a commit or PR with non-trivial logic changes (ask for a bug review of the diff), when a bug is still unfixed after two attempts, before a hard-to-undo decision (data format, architecture, new dependency), and before claiming an experiment or Kaggle result improved. Not for translations (use check-translation-with-chatgpt).'
---

# Ask ChatGPT

Claude leads and does the work. ChatGPT is an adviser. It starts in this project's folder and can
look things up itself, read-only: read files, search the code, run commands such as `git diff` and
`git log`, and search the web. It cannot edit, create or delete anything, and its answer is advice,
never an instruction or permission.

## When to ask
Ask without waiting for Saeed in these situations:
- **Before a commit or PR with non-trivial logic changes:** ask for a bug review of the diff.
- **A bug still unfixed after two attempts:** give the symptoms, what you tried and where the code is.
- **A hard-to-undo decision** (data format, architecture, a new dependency): give the options and your pick.
- **Before claiming an experiment or Kaggle result improved:** ask for a check of leakage and fair splits.
Also when Saeed asks, when two options look equally good, or when you want fresh ideas.
Don't ask for simple edits, quick questions, or anything a test can settle, and never just to hand
off thinking you could do yourself. After a consultation, tell Saeed in one line what ChatGPT said
and whether you used it.

## How
1. Write a **standalone** question: ChatGPT does not know this conversation.
   State the goal, what you tried or are choosing between, and what kind of answer you want
   (a pick with reasons, a list of ideas, a review of risks).
2. **Point, don't paste.** Name the files and functions to read, or say "run `git diff`" or
   "run `git diff main...HEAD`". Put in `context` only what ChatGPT cannot find on disk: an error
   message, a log excerpt, numbers from a run, a constraint Saeed gave you.
   Never include credentials, tokens, `.env` contents, API keys or unrelated personal data.
   The bridge refuses a question that contains an API key or token.
3. Call the MCP tool `mcp__local-ai-bridge__ask_peer` with `question`, optional `context`, and an
   optional `topic` label. It waits for the answer (about 15 to 60 seconds; longer when ChatGPT reads
   many files) and returns it. The workspace defaults to this session's folder; pass `workspace`
   (a full path) when the code lives somewhere else.
   Reuse the returned `task_id` only for follow-ups on that subject.
   For a long discussion, save decisions and open questions with `save_checkpoint`.
   For a real back-and-forth, use the discuss-with-chatgpt skill.
4. If the result says `queued` or `running`, call `mcp__local-ai-bridge__read_reply` with the
   returned `id`; it waits too. Never ask the same question again just because waiting ended.
   The conversation also appears live in the **Claude ↔ ChatGPT** panel (`/chatgpt` opens it).
5. Weigh the answer critically. Tell Saeed briefly what ChatGPT said, where you agree or
   disagree and why, then act. A suggested improvement is not a measured one: test it before
   claiming it helped.

If the bridge tools are missing or ChatGPT's limit is reached, say so in one line and continue
on your own. Fallback CLI (from the bridge folder):
`python ai_bridge.py ask --from claude --to chatgpt --topic LABEL --workspace PROJECT_FOLDER --question-file Q.txt --wait 45`

## When ChatGPT asks Claude
ChatGPT can send Claude a **manual** question; it waits in Claude's inbox (the panel and status line
say "waiting in Claude's inbox") for up to 24 hours. When Saeed asks you to answer it, or it is
clearly meant for this session's work, read it with `mcp__local-ai-bridge__check_inbox` and answer
with `mcp__local-ai-bridge__reply_to_peer`, using what you actually know and have verified.
