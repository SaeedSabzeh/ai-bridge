"""Remembered peer sessions: one ongoing ChatGPT (Codex) or Claude session per bridge task.

The first question on a task starts a saved CLI session; follow-ups on the same task resume it,
so the peer remembers the whole discussion instead of a short summary of recent exchanges.
The mapping task -> session ID lives in data/sessions.json. Sessions stay read-only advisers:
they may read files and look things up, and can never edit, create or delete anything.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

TASK_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A CLI session ID: never starting with "-", so it can't be read as an option.
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$")

# Shared by both peers: a model name or effort level passed on a command line.
OPTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")

# Codex: no approvals, read-only sandbox, no sub-agents, apps or hooks; the user's own config
# (MCP servers, including this bridge) is ignored so a reply can't call back into the bridge.
CODEX_GLOBAL = ["--ask-for-approval", "never", "-c", 'sandbox_mode="read-only"',
                "-c", "features.multi_agent=false", "-c", "features.multi_agent_v2=false",
                "-c", "features.apps=false", "-c", "features.hooks=false"]
# Without tools the shell is switched off as well, so Codex answers from the prompt alone.
CODEX_NO_TOOLS = ["-c", "features.shell_tool=false"]
CODEX_EXEC = ["--ignore-user-config", "--skip-git-repo-check", "--json"]
# Claude: no MCP servers, no settings or hooks. Sessions are kept so they can resume.
CLAUDE_BASE = ["--print", "--output-format", "json", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--setting-sources", ""]
CLAUDE_NO_TOOLS = ["--tools", "", "--disallowedTools", "*", "--settings", '{"disableAllHooks":true}', "--max-turns", "1"]
# With tools Claude can only read: no shell, no edits, no network. Common secret files stay blocked.
CLAUDE_READ_TOOLS = "Read,Grep,Glob"
CLAUDE_DENY = ["Read(**/.env)", "Read(**/.env.*)", "Read(**/*.pem)", "Read(**/*.key)", "Read(**/id_rsa*)",
               "Read(**/id_ed25519*)", "Read(**/credentials*)", "Read(**/*token*.txt)", "Read(~/.ssh/**)",
               "Read(~/.aws/**)", "Read(~/.openai_key)", "Read(~/.codex/auth.json)", "Read(~/.claude/.credentials.json)"]
CLAUDE_TOOLS = ["--tools", CLAUDE_READ_TOOLS, "--allowedTools", CLAUDE_READ_TOOLS, "--settings",
                json.dumps({"disableAllHooks": True, "permissions": {"deny": CLAUDE_DENY}}), "--max-turns", "40"]


def command_args(peer, session=None, keep=True, *, tools=False, model=None, effort=None,
                 windows_sandbox=None, web_search=False, images=False):
    """CLI arguments (after the executable) for a new session, or for resuming `session`.

    keep=False makes a one-off session the CLI does not save (a question with no task).
    tools=True lets the peer look things up without changing anything: Codex gets its shell inside
    the read-only sandbox (plus web search when asked), Claude gets its file-reading tools.
    On Windows, Codex rejects every command unless told which sandbox to use (`windows_sandbox`).
    images=True (Codex only) turns its image tool on for an image request; otherwise it is turned off.
    """
    if session is not None and not SESSION_RE.match(session):
        raise ValueError("Invalid session ID.")
    for option in (model, effort):
        if option is not None and not OPTION_RE.match(option):
            raise ValueError("Invalid model or effort setting.")
    if peer == "chatgpt":
        settings = list(CODEX_GLOBAL)
        if tools:
            if windows_sandbox in ("elevated", "unelevated"):
                settings += ["-c", f'windows.sandbox="{windows_sandbox}"']
            if web_search:
                settings += ["-c", 'web_search="live"']
        else:
            settings += CODEX_NO_TOOLS
        # Explicit both ways: an ordinary question must not be able to make a picture even if asked to.
        settings += ["-c", f"features.image_generation={'true' if images else 'false'}"]
        if effort:
            settings += ["-c", f'model_reasoning_effort="{effort}"']
        chosen = ["--model", model] if model else []
        if session:
            return [*settings, "exec", "resume", *CODEX_EXEC, *chosen, session, "-"]
        once = [] if keep else ["--ephemeral"]
        return [*settings, "exec", *CODEX_EXEC, *chosen, "--sandbox", "read-only", *once, "-"]
    if peer == "claude":
        args = [*CLAUDE_BASE, *(CLAUDE_TOOLS if tools else CLAUDE_NO_TOOLS)]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        if session:
            return [*args, "--resume", session]
        return args if keep else [*args, "--no-session-persistence"]
    raise ValueError("Unknown peer.")


def codex_session(output):
    """The thread ID from `codex exec --json` output, or None."""
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            thread = event.get("thread_id")
            return thread if isinstance(thread, str) and SESSION_RE.match(thread) else None
    return None


def claude_session(result):
    """The session ID from Claude's `--output-format json` result, or None."""
    session = result.get("session_id") if isinstance(result, dict) else None
    return session if isinstance(session, str) and SESSION_RE.match(session) else None


class SessionStore:
    """task -> session ID per peer, in data/sessions.json, safe across processes."""

    def __init__(self, base):
        from ai_bridge import FileLock  # Imported here: ai_bridge imports this module.

        self.path = Path(base) / "data" / "sessions.json"
        self.lock = FileLock(Path(base) / "data" / "sessions.lock")

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data):
        from ai_bridge import atomic_json

        atomic_json(self.path, data)

    @staticmethod
    def _key(peer, task):
        return f"{peer}:{task}" if task and TASK_RE.match(task) else None

    def get(self, peer, task):
        key = self._key(peer, task)
        if not key:
            return None
        with self.lock:
            session = self._read().get(key)
        return session if isinstance(session, str) and SESSION_RE.match(session) else None

    def set(self, peer, task, session):
        key = self._key(peer, task)
        if not key or not session or not SESSION_RE.match(session):
            return
        with self.lock:
            data = self._read()
            data[key] = session
            self._write(data)

    def forget(self, peer, task):
        key = self._key(peer, task)
        if not key:
            return
        with self.lock:
            data = self._read()
            if data.pop(key, None) is not None:
                self._write(data)
