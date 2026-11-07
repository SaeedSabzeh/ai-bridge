"""Durable state and fencing for the Windows desktop bridge."""
from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from ai_bridge import BASE, ID_RE, PEERS, BridgeError, FileLock, now, read_json
from ai_bridge import atomic_json

FIELDS = {
    "completed": "Completed work", "decisions": "Decisions and reasons",
    "best": "Best validated result and artifacts", "in_progress": "Work in progress",
    "blockers": "Blockers and failures", "next": "Next actions", "questions": "Handoff questions",
}


def require_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise BridgeError("Invalid project, task, or run ID.")
    return value


def event(task, kind, **detail):
    task["events"].append({"at": now(), "kind": kind, **detail})


def git_state(root):
    git = shutil.which("git")
    if not git:
        return {"status": "Git is unavailable."}

    def run(*args):
        result = subprocess.run(
            [git, "--no-optional-locks", "-c", "core.fsmonitor=false",
             "-c", "core.quotePath=false", "-C", str(root), *args],
            capture_output=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.decode("utf-8", "replace").strip() if not result.returncode else None

    try:
        worktree = run("rev-parse", "--show-toplevel")
        if not worktree:
            return {"status": "Workspace is outside a Git working tree."}
        output = run("status", "--porcelain=v2", "--branch", "--untracked-files=normal", "--", ".",
                     ":(exclude)AI-BRIDGE-CHECKPOINT-*.md", ":(exclude).AI-BRIDGE-CHECKPOINT-*.tmp")
        if output is None:
            return {"worktree": worktree, "status": "Git status unavailable."}
        result, lines = {"worktree": worktree}, []
        for line in output.splitlines():
            if line.startswith("# branch.oid "):
                result["commit"] = line.removeprefix("# branch.oid ")
            elif line.startswith("# branch.head "):
                result["branch"] = line.removeprefix("# branch.head ")
            elif line and not line.startswith("#"):
                prefix = {"1": 8, "2": 9, "u": 10}.get(line[0])
                parts = line.split(" ", prefix) if prefix else []
                lines.append(parts[1] + " " + parts[-1] if len(parts) > 2 else line)
        changes = "\n".join(lines)
        result["changes"] = changes[:8000] + ("\n[truncated]" if len(changes) > 8000 else "")
        return result
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "Git inspection failed or timed out."}


class Store:
    def __init__(self, base=BASE):
        self.base = Path(base).resolve()
        self.home = self.base / "data" / "desktop"
        self.path = self.home / "workspace.json"
        self.lock = FileLock(self.home / "state.lock")
        # One desktop lead at a time. A lead holding provider A can ask B without
        # creating a cycle with another lead holding B and waiting for A.
        self.execution = FileLock(self.home / "execution.lock", timeout=0)

    def read(self):
        with self.lock:
            return self.read_unlocked()

    def read_unlocked(self):
        return read_json(self.path) if self.path.exists() else {
            "version": 1, "projects": {}, "tasks": {}, "limited": {}}

    def save(self, state):
        atomic_json(self.path, state)

    def task(self, state, task_id):
        try:
            return state["tasks"][require_id(task_id)]
        except KeyError:
            raise BridgeError("Task not found.", 404) from None

    def project(self, state, project_id):
        try:
            return state["projects"][require_id(project_id)]
        except KeyError:
            raise BridgeError("Project not found.", 404) from None

    def add_project(self, name, path):
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir() or not name.strip():
            raise BridgeError("Choose an existing folder and a project name.")
        key = uuid.uuid4().hex
        with self.lock:
            state = self.read()
            for project in state["projects"].values():
                other = Path(project["path"])
                if root.is_relative_to(other) or other.is_relative_to(root):
                    raise BridgeError("This folder overlaps an existing project.")
            state["projects"][key] = {"id": key, "name": name.strip()[:150],
                                      "path": str(root), "commands": {}}
            self.save(state)
        return key

    def set_commands(self, project_id, commands):
        if not isinstance(commands, dict) or len(commands) > 20:
            raise BridgeError("Use a JSON object with up to 20 named command presets.")
        for key, args in commands.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", key) or not isinstance(args, list) or not args or any(
                not isinstance(x, str) or not x or "\0" in x or len(x) > 2000 for x in args
            ) or len(args) > 40:
                raise BridgeError("Each command needs a short name and a list of arguments.")
            executable = Path(args[0])
            if not executable.is_absolute() or not executable.is_file() or (
                os.name == "nt" and executable.suffix.lower() != ".exe"
            ):
                raise BridgeError("The first argument must be an absolute path to an executable (.exe on Windows).")
        with self.lock:
            state = self.read()
            if any(t["project"] == project_id and t["active"] for t in state["tasks"].values()):
                raise BridgeError("Pause the project before changing its approved commands.")
            self.project(state, project_id)["commands"] = commands
            self.save(state)

    def add_task(self, project_id, objective, lead, constraints=""):
        if lead not in PEERS or not 1 <= len(objective.strip()) <= 10000 or len(constraints) > 10000:
            raise BridgeError("Enter an objective (up to 10,000 characters) and select a lead.")
        key = uuid.uuid4().hex
        with self.lock:
            state = self.read()
            project = self.project(state, project_id)
            task = {"id": key, "project": project_id, "objective": objective.strip(),
                    "constraints": constraints.strip(), "lead": lead, "generation": 1,
                    "revision": 0, "status": "ready", "active": None, "transfer": None,
                    "skip_adviser": None,
                    "checkpoint": {field: "" for field in FIELDS}, "files": {},
                    "experiments": [], "events": []}
            event(task, "created", lead=lead)
            state["tasks"][key] = task
            self.checkpoint(state, task, project)
        return key

    def mirror_path(self, project, task):
        return Path(project["path"]) / ("AI-BRIDGE-CHECKPOINT-" + task["id"] + ".md")

    def markdown(self, project, task):
        lines = ["# AI Bridge checkpoint", "", f"Project: {project['name']} ({project['id']})",
                 f"Task: {task['id']}", f"Workspace: {project['path']}",
                 f"Lead: {task['lead']} · generation {task['generation']} · revision {task['revision']}",
                 f"Status: {task['status']}", "", "## Objective", task["objective"],
                 "", "## User constraints", task["constraints"] or "None recorded."]
        for field, title in FIELDS.items():
            lines.extend(["", "## " + title, task["checkpoint"][field] or "None recorded."])
        lines.extend(["", "## File state", *[f"{k}: {v}" for k, v in task["files"].items()],
                      "", "## Active work", str(task["active"] or "None"),
                      "", "## Pending takeover", str(task["transfer"] or "None"),
                      "", "## Experiments", *[
                          f"- {row['id']}: {row['metrics']} ({row['split']}); {row['artifact']}"
                          for row in task["experiments"]], ""])
        return "\n".join(lines)

    def checkpoint(self, state, task, project=None):
        project = project or self.project(state, task["project"])
        task["revision"] += 1
        task["files"] = git_state(project["path"])
        edits = {entry["path"]: entry["sha256"] for entry in task["events"] if entry["kind"] == "file written"}
        if edits:
            task["files"]["lead edits"] = "\n" + "\n".join(
                f"{path} [{sha[:12]}]" for path, sha in list(edits.items())[-30:])
        path = self.mirror_path(project, task)
        # Stage and fsync the snapshot before committing ownership. A crash can
        # leave an older projection, never a projection that claims a generation
        # that hasn't committed. The caller only launches after both steps return.
        staged = stage_text(path, self.markdown(project, task))
        try:
            self.save(state)
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)

    def repair_checkpoint(self, task_id):
        """Regenerate the human-readable projection without changing ownership."""
        with self.lock:
            state = self.read()
            task = self.task(state, task_id)
            project = self.project(state, task["project"])
            path = self.mirror_path(project, task)
            content = self.markdown(project, task)
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                atomic_text(path, content)
            return path

    def update(self, task_id, fields, *, scope=None, revision=None):
        if not isinstance(fields, dict) or not fields or set(fields) - FIELDS.keys() or any(
            not isinstance(v, str) or len(v) > 16000 for v in fields.values()
        ):
            raise BridgeError("Invalid checkpoint fields or more than 16,000 characters per field.")
        with self.lock:
            state = self.read()
            task = self.task(state, task_id)
            if scope:
                self.check_scope(task, *scope)
            elif task["active"] or revision != task["revision"]:
                raise BridgeError("Checkpoint changed or a lead is active. Refresh and try again.", 409)
            task["checkpoint"].update(fields)
            event(task, "checkpoint", lead=task["lead"], fields=fields)
            self.checkpoint(state, task)
            return copy.deepcopy(task)

    def reserve(self, state, task):
        if state["limited"].get(task["lead"]):
            raise BridgeError("This lead reported a usage limit. Switch leads or clear the limit after it resets.")
        if any(other["active"] for other in state["tasks"].values()):
            raise BridgeError("A desktop lead is already assigned. Wait, stop it, or recover an interrupted run.", 409)
        task["active"] = {"id": uuid.uuid4().hex, "generation": task["generation"], "lead": task["lead"],
                          "phase": "queued", "at": now(), "control": None, "pid": None}
        task["status"] = "queued"
        event(task, "run queued", run=task["active"]["id"], lead=task["lead"])
        return task["active"]["id"]

    def start(self, task_id):
        with self.lock:
            state = self.read()
            task = self.task(state, task_id)
            if task["status"] == "completed" or task["transfer"]:
                raise BridgeError("Completed tasks and pending transfers cannot be started.")
            run_id = self.reserve(state, task)
            self.checkpoint(state, task)
            return run_id

    def check_scope(self, task, run_id, generation):
        active = task["active"]
        if not active or active["id"] != run_id or active["generation"] != generation or (
            task["generation"] != generation or active["phase"] != "working" or active["control"] == "stop"
        ):
            raise BridgeError("This lead no longer owns an active write lease.", 409)
        return active

    def claim(self, task_id, run_id):
        with self.lock:
            state = self.read()
            task = self.task(state, task_id)
            active = task["active"]
            if not active or active["id"] != run_id or active["phase"] != "queued" or (
                active["generation"] != task["generation"]
            ):
                return None
            if active["control"]:
                return False
            active.update(phase="working", pid=os.getpid())
            task["status"] = "working"
            event(task, "run started", run=run_id, lead=task["lead"])
            self.save(state)
            return state, task

    def control(self, task_id, action, *, incoming=None, resume=False, interrupt=False,
                allow_outgoing_review=False):
        with self.lock:
            state = self.read()
            task = self.task(state, task_id)
            active = task["active"]
            if task["status"] == "completed":
                raise BridgeError("This task is completed.")
            if action == "switch":
                if incoming not in PEERS or incoming == task["lead"] or task["transfer"]:
                    raise BridgeError("Choose the other lead; only one transfer can be pending.")
                if resume and state["limited"].get(incoming):
                    raise BridgeError("The incoming lead reported a usage limit. Switch without resuming.")
                task["transfer"] = {"to": incoming, "resume": resume,
                                    "allow_outgoing_review": allow_outgoing_review}
                event(task, "takeover requested", outgoing=task["lead"], incoming=incoming)
                if active:
                    active["control"] = "stop" if interrupt else active["control"] or "drain"
                    self.save(state)
                    return None
                new_run = self.transfer(state, task)
                self.checkpoint(state, task)
                return new_run
            if action not in {"pause", "stop"}:
                raise BridgeError("Unknown run control.")
            if task["transfer"]:
                event(task, "takeover cancelled")
                task["transfer"] = None
            if active:
                active["control"] = "stop" if action == "stop" or active["control"] == "stop" else "drain"
                event(task, action + " requested", run=active["id"])
                self.save(state)
            else:
                task["status"] = "paused" if action == "pause" else "stopped"
                event(task, task["status"])
                self.checkpoint(state, task)
            return None

    def transfer(self, state, task):
        transfer = task["transfer"]
        old = task["lead"]
        task.update(lead=transfer["to"], generation=task["generation"] + 1,
                    status="paused", transfer=None,
                    skip_adviser=None if transfer.get("allow_outgoing_review") else old)
        event(task, "lead transferred", outgoing=old, incoming=task["lead"],
              generation=task["generation"])
        if transfer["resume"] and not state["limited"].get(task["lead"]):
            return self.reserve(state, task)
        return None

    def finish(self, task_id, run_id, outcome, detail=""):
        """Call only with the execution lock and after the contained process tree is empty."""
        with self.lock:
            state = self.read()
            task = self.task(state, task_id)
            active = task["active"]
            if not active or active["id"] != run_id or active["generation"] != task["generation"]:
                return None
            if outcome == "limit reached":
                state["limited"][task["lead"]] = now()
            if active["control"] == "stop":
                status = "stopped"
            elif active["control"] == "drain" and outcome in {"completed", "paused"}:
                status = "paused"
            else:
                status = outcome
            task.update(active=None, status=status)
            for row in task["experiments"]:
                if row["status"] == "running":
                    row.update(status="interrupted", metrics={"status": "No confirmed result"},
                               finished_at=now())
                    event(task, "experiment interrupted", experiment=row["id"])
            if outcome in {"failed", "limit reached", "interrupted"}:
                checkpoint = task["checkpoint"]
                checkpoint["blockers"] = (checkpoint["blockers"] + "\n" + detail).strip()[-16000:]
            event(task, "run " + status, run=run_id, lead=task["lead"], detail=detail)
            following = self.transfer(state, task) if task["transfer"] else None
            self.checkpoint(state, task)
            return following

    def clear_limit(self, peer):
        if peer not in PEERS:
            raise BridgeError("Unknown provider.")
        with self.lock:
            state = self.read()
            state["limited"].pop(peer, None)
            self.save(state)

    def run_dir(self, run_id):
        return self.home / "runs" / require_id(run_id)


def stage_text(path, value):
    path = Path(path)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        return temp
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def atomic_text(path, value):
    temp = stage_text(path, value)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
