---
name: check-translation-with-chatgpt
description: 'Have ChatGPT (via the local AI Bridge and the ChatGPT/Codex subscription, no API key) review translated text - Arabic, Persian, English, Italian or any language - so it reads naturally to a native speaker, then apply the good suggestions. Use when the user asks to check, review or improve a translation "with ChatGPT" through the bridge or subscription, and on your own before finishing user-facing text you wrote or changed in Arabic, Persian, Italian or another language the user does not write in themselves. If the user asks to use their OpenAI API key instead, use the translation-review skill.'
---

# Check a translation with ChatGPT

1. Collect the strings: source text, current translation, target language, and where it
   appears (button, heading, error message, marketing copy). Keep keys/IDs so you can map back.
   Send batches of at most ~60 strings. ChatGPT can read the project files, so for strings that
   live in a locale file you may name the file and the keys instead of pasting them.
2. Ask with `mcp__local-ai-bridge__ask_peer` (use a `topic` label like `Persian checkout`, then reuse the returned `task_id` for the same translation task):
   - Ask for the most natural wording a native speaker would use in that UI context,
     never word-by-word; keep placeholders (`{name}`, `%s`, HTML tags) unchanged;
     keep length suitable for buttons; for Persian/Arabic mention RTL and formal vs. casual tone.
   - Ask for the answer as a table: key | current | suggested | short reason, and "OK" when
     the current text is already good.
3. `ask_peer` waits and returns the reply. If it says `queued` or `running`, call
   `mcp__local-ai-bridge__read_reply` with the request ID (don't ask again). The user can watch it
   in the **Claude ↔ ChatGPT** panel.
4. Check each suggestion yourself: meaning preserved, placeholders intact, consistent terms
   across the batch, fits the UI. Reject ones that change meaning and say why.
5. Apply the accepted ones, then verify the page/app still renders (RTL layout, overflow).
6. Report: how many strings checked, changed, kept, and any you disagreed with.
