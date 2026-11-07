# Target application: Claude / ChatGPT workspace for Windows

## User goal

Provide a separate Windows desktop application with a clean interface that manages long-running collaboration between Claude Code and ChatGPT/Codex using the user's existing subscriptions.

Example: "Claude, work on this Kaggle competition with ChatGPT." Claude leads implementation and experiments, consults ChatGPT throughout, shares results, and tests suggestions. The direction is reversible: "ChatGPT, take over; I am running out of Claude credits." Either assistant can lead or advise.

The user is moving this work into Codex. This file records what to build next; it does not claim the desktop app exists.

## Current foundation

`ai_bridge.py` supplies file-backed messaging, MCP tools, CLI reviewer adapters, task-grouped recent history, process locking, and an optional localhost dashboard. See HANDOFF.md for tested behavior and blockers. Real provider authentication and calls still need verification.

Current automatic workers are advisers with limited tools. They cannot act as full project-executing lead agents. The desktop app needs an explicit execution/orchestration layer for a lead agent; do not confuse exchanging reviews with running an autonomous project.

## Interface

- A project list, with workspace path and active task status.
- A task view with the objective, current lead (Claude or ChatGPT), assistant activity, and next action.
- A readable collaboration timeline showing questions, answers, decisions, and experiment results. Distinguish measured results from suggestions.
- Controls to start a task, pause work, request a second opinion, switch the lead, and stop execution.
- A checkpoint panel showing current progress, changed files/commit, best result, open issues, and next steps.
- A connections/settings view with detected CLI paths, subscription login status, useful errors, and authentication guidance.
- An experiment view with per-run configuration and results when relevant to the project.
- Keep raw logs and technical diagnostics in a secondary view. Do not put tokens or credential contents in the UI.

Use an actual Windows desktop application, not just a link to a browser dashboard. Choose the UI framework after inspecting the next environment. Prefer a coherent, maintainable application over wrapping the current dashboard without addressing missing behavior. Provide a practical launch/install method and verify it on Windows; a packaged executable is a later deliverable, not an existing artifact.

## Leadership and takeover

Maintain a persisted task record and a concise human-readable checkpoint in the project workspace. At minimum record:

- Task/project ID, objective, workspace path, and user constraints.
- Current lead and a revision/ownership generation.
- Completed work, important decisions and their reasons.
- Code branch/worktree/commit and any uncommitted changes.
- Experiment IDs, configurations, seeds, validation splits, and actual results.
- Current best validated result and location of its artifacts.
- Work in progress, running process/job IDs, blockers, failures, and next actions.
- Questions the incoming lead should ask the outgoing lead if it remains available.

Checkpoint after meaningful milestones and before a requested handoff. Retain full messages and a compact summary so continuity is not limited to the bridge's six recent exchanges.

On takeover:

1. Pause new assignments to the outgoing lead.
2. Identify work already running. Resolve ownership explicitly: let a useful experiment finish, or stop it when the user asks. Never start a second writer on its live files.
3. Save and validate the latest checkpoint and relevant file state. Switch task ownership atomically, retaining a history of the transfer.
4. Have the incoming lead read the checkpoint, relevant code/files, experiment records, and recent discussion.
5. If the outgoing assistant has quota, optionally ask a focused handoff question. If it is exhausted/unavailable, continue from disk without waiting for its response or repeatedly calling it.
6. Resume execution under the new lead and show the new ownership clearly in the UI.

Do not assume a newly launched Claude or Codex session has access to an earlier browser/desktop conversation. Persisted project state is the continuity mechanism. If resuming a specific CLI session is useful, use supported CLI session IDs deliberately and verify their behavior.

## Usage limits

Use official CLI authentication and supported interfaces. Existing subscriptions were requested; do not silently fall back to API keys or enable paid extra usage.

Display quota/remaining-usage information only when a supported, reliable source provides it. Otherwise show unavailable/unknown or a user-set budget; do not invent remaining-credit percentages. Handle a provider's limit/error gracefully, preserve the task state, and offer takeover. Never repeatedly probe an exhausted provider.

Manual takeover is required. Automatic fallback can be added as an explicit user-controlled setting after its triggers and ownership behavior are reliable.

## Files and concurrent work

Default to one lead writer per working tree. An adviser reviews snapshots and proposes changes.

If both agents implement changes concurrently, use separate Git worktrees AND branches. The lead reviews and merges; shared branch names alone do not isolate filesystem edits. Handle uncommitted work explicitly during takeover.

Share read-only datasets. Keep large datasets, models, generated predictions, credentials, and environment secrets outside version control. Put each experiment in a unique run directory, with its configuration, seed, split, code version, environment metadata, metrics, and conclusion. Avoid concurrent writes to one notebook, model checkpoint, metrics CSV, or changing virtual environment. Coordinate GPU use.

Prefer local storage with atomic writes and recoverable state. Choose a clear data model; it is acceptable to replace the prototype's storage if needed for robust task ownership, while preserving existing transcripts and explaining migration.

## Acceptance scenarios

1. Claude leads a small coding/data task, asks ChatGPT for a review, acts on a useful suggestion, and records the actual outcome. No manual copying between apps.
2. ChatGPT leads and consults Claude in the same way.
3. The user switches from Claude to ChatGPT mid-task. ChatGPT correctly identifies completed work, current files/results, and next actions, then continues without rerunning completed work unnecessarily.
4. Simulate Claude being unavailable or out of usage. ChatGPT takes over using saved state without a successful Claude call.
5. Repeat the takeover in the reverse direction.
6. Restart the desktop app after a checkpoint and recover project/task status without duplicate model requests or concurrent file writers.
7. If both edit directly, demonstrate isolated worktrees and a reviewed merge. Never claim concurrent editing works merely because both can read the same folder.
8. The app visibly distinguishes disconnected, ready, working, paused, limit-reached, failed, and completed states without claiming unsupported quota information.

Use a small inexpensive task to validate these flows before attempting a full Kaggle competition. The specific Kaggle competition, dataset, compute budget, and target metric have not been supplied.
