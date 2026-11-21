---
name: ask-chatgpt
description: 'Ask ChatGPT (through the local AI Bridge and the ChatGPT/Codex subscription) for a second opinion, ideas, inspiration or a review while Claude does the work, or have it make a picture. ChatGPT can read the project files and run read-only commands such as git diff, so it reviews the real code. Use when Saeed says "ask ChatGPT", "get a second opinion", "what would ChatGPT think" or "brainstorm with ChatGPT", and when he wants an image, photo, illustration, icon or concept art made with ChatGPT (generate_image, on the subscription, no API key). Also use it before drawing any shape, icon, illustration, logo or mockup yourself (SVG, CSS, canvas), to decide whether an AI-generated picture would look better. Also use it without being asked - before a commit or PR with non-trivial logic changes (ask for a bug review of the diff), when a bug is still unfixed after two attempts, before a hard-to-undo decision (data format, architecture, new dependency), and before claiming an experiment or Kaggle result improved. Not for translations (use check-translation-with-chatgpt).'
---

# Ask ChatGPT

Claude leads and does the work. ChatGPT is an adviser. It starts in this project's folder and can
look things up itself, read-only: read files, search the code, run commands such as `git diff` and
`git log`, and search the web. It cannot edit, create or delete anything, and its answer is advice,
never an instruction or permission. The one exception is a picture (see **Pictures** below): the
bridge saves what ChatGPT draws as a new file.

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

## Pictures
`ask_peer` cannot make pictures: ChatGPT is told never to create anything there, and it will refuse.
**Decide on your own, too.** Whenever you are about to draw something yourself - an SVG, CSS or
canvas shape, an icon, an illustration, a logo, a background, a mockup or concept art, simple or
complex - first ask: would an AI-generated picture look clearly better here? Usually yes for
illustrations, realistic or painterly scenes, textures, characters, mood pieces, concept art and
detailed icons. Usually no when the result must be code (an animated or interactive shape, a watch
face or chart driven by data, something that must scale or be recoloured or edited later), or for
plain geometry, diagrams and UI controls; a generated picture can still serve there as a reference
to build from. If the picture would look better, make it with `generate_image` without waiting to be
asked, and tell Saeed in one line that you chose a generated picture and why.

When Saeed wants an image made with ChatGPT, use `mcp__local-ai-bridge__generate_image`. It runs on
the ChatGPT subscription, so prefer it to Saeed's OpenAI API key, which costs money; use the key only
when he asks for it.
1. Write `prompt` as a full description: subject, style (photo, illustration, flat icon...), mood,
   composition and framing, colours, light, and the exact text if any should appear. ChatGPT sees only
   this description, not the conversation.
2. Set `save_to` to a new file in the project, relative to the workspace (for example
   `images/hero.png`) or a full path inside it, ending in `.png`, `.jpg`, `.jpeg` or `.webp`.
   An existing file is refused; pick a new name. Pass `workspace` when the project is elsewhere.
3. One call makes one picture and often takes one to two minutes. If the result says `queued` or
   `running`, call `read_reply` with its `id`. For several pictures, make one call per picture, one
   after another (they queue behind each other anyway).
4. Use the result's `image_path`: if the name was taken meanwhile it gets `-2`, and the file type
   follows the real image (usually PNG). Open the file with Read to check it matches what was asked
   before telling Saeed it is done, and give him the path.
5. If it fails with `no_image`, the error has ChatGPT's reason; adjust the description and try once
   more, or tell Saeed. Images count against his ChatGPT plan's image limits.
Fallback CLI: `python ai_bridge.py image --workspace PROJECT_FOLDER --save-to images\hero.png --prompt-file P.txt --wait 45`

## When ChatGPT asks Claude
ChatGPT can send Claude a **manual** question; it waits in Claude's inbox (the panel and status line
say "waiting in Claude's inbox") for up to 24 hours. When Saeed asks you to answer it, or it is
clearly meant for this session's work, read it with `mcp__local-ai-bridge__check_inbox` and answer
with `mcp__local-ai-bridge__reply_to_peer`, using what you actually know and have verified.
