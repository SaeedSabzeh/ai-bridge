# AI Bridge project

Build a local Windows workspace where Claude Code and Codex can lead a task in either direction, consult one another using their subscriptions, and resume after a checkpoint without repeating work. Preserve the existing mailbox and use one writer per workspace.

## Verified so far

- The original mailbox transcript was preserved byte for byte. The prototype's CLI and MCP remain available; the desktop app uses a separate state directory and can display the old mailbox.
- Both user-level MCP connectors are registered. Claude Code 2.1.319 was installed project-locally. Both providers passed live subscription calls. Codex also recalled the prior answer in a follow-up.
- A real Codex lead created a file, ran the approved verification command (exit code 0), and marked the task completed after recording the result.
- A real Codex turn took over from a fixture checkpoint owned by Claude, ran the pending check once, and left the completed file untouched. Offline tests cover both transfer directions, unavailable-provider handling, and Windows process recovery. The fixture did not call Claude.
- The full live test used both assistants on one project. Codex acted on Claude's reply and passed the first local check. Claude took over from that checkpoint, preserved the original file's timestamp, acted on Codex's reply, and passed the final local check. Each stage ran its check once. Both adviser transcripts were answered.
- Claude reviewed the handoff design through the bridge. Its concern about interrupted publication led to staging the Markdown before committing ownership and publishing it afterward. Crash tests verify both sides of the JSON commit, rejection of stale writes, and idempotent recovery.
- No Kaggle project or dataset has been supplied. There are no validation or leaderboard scores for a competition.

## Next project

Use a small actual project before committing to a long competition run. Record the workspace, the objective and constraints, the validation split, and the approved commands. No installer or shared concurrent editing mode is needed for the current single-lead workflow.

The desktop app discovers the versioned Codex installation even outside the
Codex shell and has official CLI sign-in buttons. An optional [Google Cloud
host plan](cloud/README.md) runs the same app on a dedicated Windows VM. Claude's
review highlighted the need to test disconnected Windows sessions and restart
recovery. The plan uses no attached Google service account, and it does not
enable auto-logon or replay a model call after a restart.

See [README.md](README.md) for launch, recovery, and experiment instructions and [APP-REQUIREMENTS.md](APP-REQUIREMENTS.md) for acceptance scenarios.
