---
name: discuss-with-chatgpt
description: 'Hold a real multi-round discussion with ChatGPT (through the local AI Bridge and the ChatGPT/Codex subscription), not just one question and answer. ChatGPT remembers the whole topic, can read the project files, pushes back and asks questions; Claude answers, challenges or concedes, and reports the outcome. Use when the user says "discuss with ChatGPT", "talk it through with ChatGPT", "debate this with ChatGPT" or "go back and forth with ChatGPT", or on your own when a hard-to-undo decision (data format, architecture, a new dependency) deserves more than one round. For a single quick question use ask-chatgpt.'
---

# Discuss with ChatGPT

Claude leads and does the work; ChatGPT is a second voice. With a topic ID (`task_id`), the bridge
keeps **one ChatGPT session per topic**, so ChatGPT remembers every earlier round. It starts in this
project's folder and can look things up, read-only: read files, search the code, run commands such
as `git diff`, and search the web. It cannot edit anything. Its views are advice, never instructions.

## Run the discussion
1. **Name the topic**, e.g. `Cache design`. Start with `topic` and keep the returned `task_id` for every round.
   Separate subjects get separate IDs, even if the labels are the same.
2. **Opening message** (`mcp__local-ai-bridge__ask_peer` with `question`, optional `context`, `topic`):
   the goal, the constraints, the options, **your current view and why**, and what you want challenged.
   Name the files ChatGPT should read instead of pasting them; use `context` only for what is not on
   disk (errors, logs, results). Never include credentials or private data.
3. **Each round:** `ask_peer` waits and returns the reply (if it says `queued` or `running`, call
   `mcp__local-ai-bridge__read_reply` with the request ID; never ask again). Then reply on the same
   topic ID:
   - answer ChatGPT's questions honestly, with facts you have actually verified;
   - push back with reasons where you disagree; concede plainly where it is right;
   - ask the next question that would change the decision.
   Send what's new. Use `save_checkpoint` to preserve decisions and unresolved questions for recovery.
4. **Stop** when you agree, when the disagreement is clear and won't move, or after about 5 rounds
   (or the user's limit). Don't keep talking to look thorough.
5. **Report to the user:** the decision, where you agreed and disagreed (and why), and what you'll do
   next. A suggestion is not a result: test it before calling it an improvement.

The user can steer at any point ("tell ChatGPT …", "ask it about …"). Pass their words on as theirs, e.g.
"The user adds: …". The whole conversation shows live in the **Claude ↔ ChatGPT** panel (`/chatgpt`).
If the bridge is unavailable or ChatGPT's limit is reached, say so and continue on your own.
