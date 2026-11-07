---
name: check-translation-with-chatgpt
description: Have ChatGPT (via the local AI Bridge, Saeed's ChatGPT/Codex subscription, no API key) review translated text — Arabic, Persian, English, Italian or any language — so it reads naturally to a native speaker, then apply the good suggestions. Use when Saeed asks to check, review or improve a translation "with ChatGPT" through the bridge or subscription. If Saeed asks to use his OpenAI API key instead, use the translation-review skill.
---

# Check a translation with ChatGPT

1. Collect the strings: source text, current translation, target language, and where it
   appears (button, heading, error message, marketing copy). Keep keys/IDs so you can map back.
   Send batches of at most ~60 strings.
2. Ask with `mcp__local-ai-bridge__ask_peer` (task_id like `translate-fa-<area>`):
   - Ask for the most natural wording a native speaker would use in that UI context,
     never word-by-word; keep placeholders (`{name}`, `%s`, HTML tags) unchanged;
     keep length suitable for buttons; for Persian/Arabic mention RTL and formal vs. casual tone.
   - Ask for the answer as a table: key | current | suggested | short reason, and "OK" when
     the current text is already good.
3. Poll with `mcp__local-ai-bridge__read_reply` if needed (don't resubmit). Saeed can watch it
   in the **Claude ↔ ChatGPT** panel.
4. Check each suggestion yourself: meaning preserved, placeholders intact, consistent terms
   across the batch, fits the UI. Reject ones that change meaning and say why.
5. Apply the accepted ones, then verify the page/app still renders (RTL layout, overflow).
6. Report: how many strings checked, changed, kept, and any you disagreed with.
