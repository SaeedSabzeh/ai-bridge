# AI Bridge project

Build a local Windows workspace where Claude Code and Codex can lead a task in either direction, consult one another using their subscriptions, and resume after a checkpoint without repeating work. Preserve the existing mailbox and use one writer per workspace.

## Verified so far

- The original mailbox transcript was preserved byte for byte. The prototype's CLI and MCP remain available; the desktop app uses a separate state directory and can display the old mailbox.
- Both user-level MCP connectors are registered. Claude Code 2.1.319 was installed project-locally. Codex passed a real subscription reply and a follow-up that recovered the prior answer.
- A real Codex lead created a file, ran the approved verification command (exit code 0), and marked the task completed after recording the result.
- A real Codex turn took over from a fixture checkpoint owned by Claude, ran the pending check once, and left the completed file untouched. Offline tests cover both transfer directions, unavailable-provider handling, and Windows process recovery. The fixture did not call Claude.
- No Kaggle project or dataset has been supplied. There are no validation or leaderboard scores for a competition.

## Remaining live verification

Claude Code needs subscription sign-in. Saeed also reported hitting his five-hour limit. Once Claude is signed in and the limit has reset, run a small Claude advisory request and use its review of the takeover design. Then verify a real Claude lead turn and a live takeover with Claude as the incoming lead. Do not use API keys, enable extra usage, or repeatedly probe an exhausted provider.

See [README.md](README.md) for launch, recovery, and experiment instructions and [APP-REQUIREMENTS.md](APP-REQUIREMENTS.md) for acceptance scenarios.
