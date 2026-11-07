"""Opt-in: one live Codex lead turn against a disposable local project."""
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_bridge import BASE
from lead_run import launch
from workspace_state import Store


def main(takeover=False):
    home = BASE / "verification" / ("codex-" + uuid.uuid4().hex[:8])
    root = home / "project"
    root.mkdir(parents=True)
    store = Store(home)
    project = store.add_project("Codex live smoke test", root)
    (root / "verify.py").write_text(
        "from pathlib import Path\n"
        "assert Path('proof.txt').read_text().strip() == 'LEAD-CHECKPOINT-OK'\n"
        "print('Verified proof.txt')\n", encoding="utf-8")
    store.set_commands(project, {"verify": [sys.executable, "verify.py"]})
    task_id = store.add_task(
        project, "Use bridge tools to create proof.txt containing LEAD-CHECKPOINT-OK. "
        "Run the approved 'verify' command, record its actual outcome in completed, then call finish_task.",
        "claude" if takeover else "chatgpt",
        "Small connector smoke test; no peer call, and do not change verify.py.")
    initial_mtime = None
    if takeover:
        proof = root / "proof.txt"
        proof.write_text("LEAD-CHECKPOINT-OK\n", encoding="utf-8")
        initial_mtime = proof.stat().st_mtime_ns
        state = store.read()
        store.update(task_id, {"completed": "proof.txt was already created and must not be changed.",
                               "next": "Read the existing proof.txt, run verify once, record actual output, finish."},
                     revision=store.task(state, task_id)["revision"])
        run = store.control(task_id, "switch", incoming="chatgpt", resume=True)
    else:
        run = store.start(task_id)
    print(f"Test workspace: {home}\nTask: {task_id}\nRun: {run}", flush=True)
    launch(store, task_id, run)
    for _ in range(130):
        task = store.task(store.read(), task_id)
        if task["active"] is None:
            print(json.dumps(task, indent=2))
            return 0 if (task["status"] == "completed" and
                         len(task["experiments"]) == 1 and task["experiments"][0]["status"] == "finished" and
                         (not takeover or (task["generation"] == 2 and
                                           (root / "proof.txt").stat().st_mtime_ns == initial_mtime)) and
                         (root / "proof.txt").read_text(encoding="utf-8").strip() == "LEAD-CHECKPOINT-OK") else 1
        time.sleep(1)
    return 2


if __name__ == "__main__":
    raise SystemExit(main("--takeover" in sys.argv))
