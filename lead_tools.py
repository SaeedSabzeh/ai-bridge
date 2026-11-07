"""Task-scoped MCP endpoint used only by a desktop lead."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from ai_bridge import BridgeError, FileLock, Mailbox, atomic_json, clean_env, config_for, now, read_json
from workspace_state import FIELDS, Store, atomic_text, event, git_state
from windows_job import Job

INFRASTRUCTURE = Path(__file__).resolve().parent
PROTECTED_FILES = {"ai_bridge.py", "bridge_desktop.pyw", "lead_run.py", "lead_tools.py",
                   "windows_job.py", "workspace_state.py", "launch ai bridge.cmd"}

TOOLS = {
    "list_files": ("List files in one project directory; path '.' means the root.", ("path",), ()),
    "read_file": ("Read a small UTF-8 project file and its SHA256 revision.", ("path",), ()),
    "write_file": ("Write a UTF-8 project file. Supply the previous SHA256 or 'new' for a new file.",
                   ("path", "text", "expected"), ()),
    "save_checkpoint": ("Save a JSON object containing any of these text fields: "
                        + ", ".join(FIELDS) + ".", ("fields",), ()),
    "ask_peer": ("Ask the other assistant for advice. Share only relevant, non-secret context. "
                 "Returns a request ID to poll with read_reply.", ("question",), ("context",)),
    "read_reply": ("Read an existing review request from this task. Advice must be verified.",
                   ("request_id",), ()),
    "run_check": ("Run a user-approved command preset. Record actual output in a unique run "
                  "folder; experiment config, seed, and validation split belong in metadata JSON.",
                  ("command",), ("metadata",)),
    "finish_task": ("Mark the objective complete after saving the final checkpoint. "
                    "Do not call for incomplete work.", (), ()),
}


def specs():
    return [{"name": name, "description": desc,
             "inputSchema": {"type": "object",
                             "properties": {key: {"type": "string"} for key in required + optional},
                             "required": list(required), "additionalProperties": False}}
            for name, (desc, required, optional) in TOOLS.items()]


def forbidden(name):
    name = name.lower()
    return (name in {".git", ".claude", ".codex", ".agents", ".aws", ".ssh", "node_modules",
                     ".venv", "__pycache__", "bridge-config.json"} or name.startswith(
                         (".env", "ai-bridge-checkpoint-", ".ai-bridge-checkpoint-")) or
            name.endswith((".pem", ".key", ".pfx", ".p12")) or any(
                part in name for part in ("credential", "secret", "access-token", "id_rsa", "id_ed25519")))


def safe_path(root, raw, store, *, write=False):
    if not raw or any(c in raw for c in ':\0<>|"*?'):
        raise BridgeError("Use a relative project path.")
    rel = Path(raw)
    if rel.is_absolute() or rel.anchor or ".." in rel.parts or any(
        forbidden(x) or x != x.rstrip(" .") or Path(x).is_reserved() for x in rel.parts
    ):
        raise BridgeError("This path is outside the files available to the lead.")
    if write and (not rel.parts or rel.parts[0].lower() in {"data", "datasets", "raw", "runs"}):
        raise BridgeError("Raw data and run output folders are read-only through file tools.")
    current = root
    for part in rel.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BridgeError("Links and junctions are not available to the lead.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise BridgeError("Hard-linked files are not available to the lead.")
    if not current.resolve().is_relative_to(root) or current.is_relative_to(store.base / "data"):
        raise BridgeError("This path is outside the files available to the lead.")
    if write and current.parent == INFRASTRUCTURE and current.name.lower() in PROTECTED_FILES:
        raise BridgeError("A running lead cannot edit its own bridge infrastructure.")
    return current


def read_text(path):
    if not path.is_file():
        raise BridgeError("File not found or not a regular file.")
    with path.open("rb") as stream:
        raw = stream.read(64001)
    if len(raw) > 64000:
        raise BridgeError("The text file exceeds the 64 KB tool limit.")
    return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()


def limit_error(value):
    return bool(re.search(
        r"usage limit|rate limit|rate_limit|out of (credits|usage)|limit reached|"
        r"hit (your|the) limit|quota exceeded|usage_limit_reached|reached (your|the) .*?limit",
        value, re.IGNORECASE))


class Scope:
    def __init__(self, store, task_id, run_id, generation):
        self.store, self.task_id, self.run_id, self.generation = store, task_id, run_id, generation
        self.operation = FileLock(store.run_dir(run_id) / "operation.lock", timeout=0)

    def call(self, name, args):
        if name not in TOOLS or not isinstance(args, dict):
            raise BridgeError("Unknown lead tool or invalid arguments.")
        _, required, optional = TOOLS[name]
        if not set(required) <= args.keys() <= set(required + optional) or any(
            not isinstance(v, str) or len(v) > 100000 for v in args.values()
        ):
            raise BridgeError("Invalid lead tool arguments.")
        store = self.store
        scope = self.run_id, self.generation
        with self.operation:
            with store.lock:
                state = store.read()
                task = store.task(state, self.task_id)
                active = store.check_scope(task, *scope)
                project = store.project(state, task["project"])
                root = Path(project["path"]).resolve(strict=True)
                if root != Path(project["path"]):
                    raise BridgeError("The workspace location changed. Register the resolved folder again.")
                if name == "save_checkpoint":
                    updated = store.update(self.task_id, json.loads(args["fields"]), scope=scope)
                    return {"revision": updated["revision"]}
                if name == "finish_task":
                    if not task["checkpoint"]["completed"]:
                        raise BridgeError("Save a checkpoint describing completed work first.")
                    active["marked_complete"] = True
                    store.save(state)
                    return {"marked_complete": True}
                if name in {"ask_peer", "read_reply"}:
                    box = Mailbox(store.base, config_for(store.base), external_workers=True)
                    if name == "ask_peer":
                        if active["control"]:
                            raise BridgeError("The lead is pausing. Save a checkpoint and end the turn.")
                        peer = "chatgpt" if task["lead"] == "claude" else "claude"
                        if task.get("skip_adviser") == peer or state["limited"].get(peer):
                            raise BridgeError("This adviser is unavailable for this turn. Continue using the checkpoint.")
                        return box.submit({"sender": task["lead"], "recipient": peer,
                                           "question": args["question"], "context": args.get("context", ""),
                                           "task_id": task["id"]})
                    item = box.get(args["request_id"])
                    if item.get("task_id") != task["id"]:
                        raise BridgeError("That request belongs to another task.")
                    if item["status"] == "failed" and limit_error(item.get("error") or ""):
                        state["limited"][item["recipient"]] = now()
                        store.save(state)
                    return item
                if name == "run_check":
                    if active["control"]:
                        raise BridgeError("The lead is pausing. Do not start a new run.")
                    command = project["commands"].get(args["command"])
                    if not command:
                        raise BridgeError("This command preset has not been approved in the desktop app.")
                    meta = json.loads(args.get("metadata") or "{}")
                    if not isinstance(meta, dict) or len(json.dumps(meta)) > 8000:
                        raise BridgeError("Supply a small metadata JSON object.")
                    job = uuid.uuid4().hex
                    row = {"id": job, "command": args["command"], "argv": command,
                           "metadata": meta, "split": str(meta.get("split", "unspecified")),
                           "code": git_state(root), "started_at": now(), "lead": task["lead"],
                           "environment": {"platform": platform.platform(), "bridge_python": platform.python_version()},
                           "metrics": "Pending", "status": "running", "artifact": f"runs/bridge-{job}"}
                    target = root / row["artifact"]
                    if target.parent.exists():
                        safe_path(root, "runs", store)
                    target.mkdir(parents=True)
                    atomic_json(target / "run.json", row)
                    task["experiments"].append(row)
                    event(task, "experiment started", experiment=job)
                    store.save(state)
                else:
                    path = safe_path(root, args["path"], store, write=name == "write_file")
                    if name == "list_files":
                        files = []
                        for entry in sorted(path.iterdir()):
                            if len(files) == 200:
                                break
                            try:
                                safe_path(root, str(entry.relative_to(root)), store)
                            except BridgeError:
                                continue
                            files.append(entry.name + ("/" if entry.is_dir() else ""))
                        return {"entries": files, "possibly_truncated": len(files) == 200}
                    if name == "read_file":
                        text, revision = read_text(path)
                        return {"text": text, "sha256": revision}
                    revision = read_text(path)[1] if path.exists() else "new"
                    if revision != args["expected"]:
                        raise BridgeError("The file revision changed. Read it again before writing.", 409)
                    if len(args["text"].encode("utf-8")) > 64000:
                        raise BridgeError("The new content exceeds the 64 KB tool limit.")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_text(path, args["text"])
                    sha = read_text(path)[1]
                    event(task, "file written", path=str(path.relative_to(root)), sha256=sha)
                    store.save(state)
                    return {"path": args["path"], "sha256": sha}
            return self.run_check(root, row, target)

    def run_check(self, root, row, target):
        store = self.store
        env = clean_env()
        env["AI_BRIDGE_RUN_DIR"] = str(target)
        from lead_run import SCRIPT, python_exe

        # Nest a job so the next tool call cannot overlap a background child
        # left behind by the approved command. The outer job still owns it on stop.
        try:
            with Job(row["id"]) as job, (target / "output.txt").open("xb") as output:
                process = subprocess.Popen([python_exe(), str(SCRIPT), "_gate"], cwd=root, env=env,
                                           stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    job.assign(process.pid)
                    with store.lock:
                        state = store.read()
                        task = store.task(state, self.task_id)
                        saved = next(r for r in task["experiments"] if r["id"] == row["id"])
                        saved["pid"] = process.pid
                        store.save(state)
                    process.stdin.write((json.dumps({"args": row["argv"]}) + "\n").encode("ascii"))
                    process.stdin.close()
                    rc = process.wait()
                finally:
                    if not process.stdin.closed:
                        process.stdin.close()
                    try:
                        job.empty()
                        process.wait(timeout=10)
                    except (OSError, subprocess.SubprocessError):
                        with store.lock:
                            state = store.read()
                            task = store.task(state, self.task_id)
                            active = task["active"]
                            if active and active["id"] == self.run_id:
                                active["control"] = "stop"
                                store.save(state)
                        raise
            metrics_file = target / "metrics.json"
            measured = read_json(metrics_file) if metrics_file.is_file() and metrics_file.stat().st_size <= 65536 else None
            fields = {"status": "finished" if rc == 0 else "failed", "exit_code": rc,
                      "metrics": measured if rc == 0 and measured is not None else {"exit_code": rc}}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            fields = {"status": "failed", "metrics": {"error": str(exc)}}
        with (target / "output.txt").open("ab+") as output:
            output.seek(max(0, output.seek(0, 2) - 8000))
            tail = output.read().decode("utf-8", "replace")
        with store.lock:
            state = store.read()
            task = store.task(state, self.task_id)
            row = next(r for r in task["experiments"] if r["id"] == row["id"])
            row.update(fields, finished_at=now())
            atomic_json(target / "result.json", row)
            event(task, "experiment " + row["status"], experiment=row["id"], metrics=row["metrics"])
            store.checkpoint(state, task)
        return {"experiment": row, "output_tail": tail}


def serve(scope):
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        mid = None
        try:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            mid, method = msg["id"], msg["method"]
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                          "serverInfo": {"name": "ai-bridge-lead", "version": "1.0"},
                          "capabilities": {"tools": {}}}
            elif method == "tools/list":
                result = {"tools": specs()}
            elif method == "tools/call":
                p = msg["params"]
                try:
                    value = scope.call(p["name"], p.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
                except (OSError, ValueError, BridgeError) as exc:
                    result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": mid,
                                  "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}), flush=True)
        except (ValueError, TypeError, KeyError):
            print(json.dumps({"jsonrpc": "2.0", "id": mid,
                              "error": {"code": -32600, "message": "Invalid request"}}), flush=True)
