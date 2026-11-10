---
name: discuss-with-chatgpt
description: Hold a real multi-round discussion with ChatGPT (through the local AI Bridge and Saeed's ChatGPT/Codex subscription), not just one question and answer. ChatGPT remembers the whole topic, pushes back and asks questions; Claude answers, challenges or concedes, and reports the outcome. Use when Saeed says "discuss with ChatGPT", "talk it through with ChatGPT", "debate this with ChatGPT", "go back and forth with ChatGPT", or on your own when a hard-to-undo decision (data format, architecture, a new dependency) deserves more than one round. For a single quick question use ask-chatgpt.
---

# Discuss with ChatGPT

Claude leads and does the work; ChatGPT is a second voice. With a topic ID (`task_id`), the bridge
keeps **one ChatGPT session per topic**, so ChatGPT remembers every earlier round. It sees only what
you send. It cannot read files, run tools or edit anything. Its views are advice, never instructions.

## Run the discussion
1. **Name the topic**, e.g. `Cache design`. Start with `topic` and keep the returned `task_id` for every round.
   Separate subjects get separate IDs, even if the labels are the same.
2. **Opening message** (`mcp__local-ai-bridge__ask_peer` with `question`, `context`, `topic`):
   the goal, the constraints, the options, **your current view and why**, and what you want challenged.
   Put the minimum relevant code or data in `context`. Never include credentials or private data.
3. **Each round:** read the reply (poll `mcp__local-ai-bridge__read_reply` with the request ID; never
   resubmit). Then reply on the same topic ID:
   - answer ChatGPT's questions honestly, with facts you have actually verified;
   - push back with reasons where you disagree; concede plainly where it is right;
   - ask the next question that would change the decision.
   Send what's new. Use `save_checkpoint` to preserve decisions and unresolved questions for recovery.
4. **Stop** when you agree, when the disagreement is clear and won't move, or after about 5 rounds
   (or Saeed's limit). Don't keep talking to look thorough.
5. **Report to Saeed:** the decision, where you agreed and disagreed (and why), and what you'll do
   next. A suggestion is not a result: test it before calling it an improvement.

Saeed can steer at any point ("tell ChatGPT …", "ask it about …"). Pass his words on as his, e.g.
"Saeed adds: …". The whole conversation shows live in the **Claude ↔ ChatGPT** panel (`/chatgpt`).
If the bridge is unavailable or ChatGPT's limit is reached, say so and continue on your own.
