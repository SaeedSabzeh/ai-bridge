"""Opt-in: live Codex → Claude takeover, with one review in each direction."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_bridge import BASE, BridgeError, Mailbox, atomic_json, config_for, executable_for
from lead_run import launch
from workspace_state import Store


def await_turn(store, task_id):
    for _ in range(210):
        try:
            task = store.task(store.read(), task_id)
        except BridgeError as exc:
            if exc.status != 409:
                raise
            time.sleep(1)
            continue
        if not task["active"]:
            print(json.dumps({"status": task["status"], "checkpoint": task["checkpoint"],
                              "generation": task["generation"],
                              "last_event": task["events"][-1]}, indent=2), flush=True)
            return task
        time.sleep(1)
    raise RuntimeError("Lead did not finish within the smoke test wait. Check the existing run; do not replay it.")


def main():
    home = BASE / "verification" / ("live-collaboration-" + uuid.uuid4().hex[:8])
    root = home / "project"
    root.mkdir(parents=True)
    config = config_for(BASE)
    config = {**config, "claude_path": executable_for("claude", BASE, config)[-1],
              "codex_path": executable_for("chatgpt", BASE, config)[-1]}
    atomic_json(home / "bridge-config.json", config)
    store = Store(home)
    project = store.add_project("Live collaboration smoke test", root)
    (root / "verify.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "stage = sys.argv[1]\n"
        "assert Path('first.txt').read_text().strip() == 'ALPHA'\n"
        "if stage == '2':\n    assert Path('second.txt').read_text().strip() == 'BRAVO'\n"
        "print('Verified stage ' + stage)\n", encoding="utf-8")
    store.set_commands(project, {"verify-first": [sys.executable, "verify.py", "1"],
                                 "verify-final": [sys.executable, "verify.py", "2"]})
    task_id = store.add_task(
        project,
        "Complete a two-stage collaboration smoke test. Generation 1 (Codex) owns stage 1: "
        "ask Claude to suggest the exercise's exact token ALPHA. Poll until answered, "
        "use that answer to create first.txt, "
        "run verify-first once, record the observed result and checkpoint next actions for stage 2. "
        "Do not mark complete yet; end the turn so the operator can switch lead. "
        "Generation 2 (Claude) owns stage 2: read the checkpoint and first.txt, preserve first.txt "
        "unchanged. Ask Codex to suggest the exercise's exact token BRAVO. Poll until "
        "answered, put that answer in second.txt, run verify-final once, save its actual result, "
        "then mark the overall task complete.",
        "chatgpt", "Do not edit verify.py. One adviser request per lead; poll the existing ID, "
                   "never resubmit. If the adviser is unavailable, record the error and stop this test.")
    print(f"Test workspace: {home}\nTask: {task_id}", flush=True)
    launch(store, task_id, store.start(task_id))
    return continue_smoke(store, task_id)


def continue_smoke(store, task_id):
    task = await_turn(store, task_id)
    if (task["status"] != "paused" or task["generation"] != 1 or task["lead"] != "chatgpt"
            or len(task["experiments"]) != 1 or task["experiments"][0]["status"] != "finished"):
        return 1
    root = Path(store.project(store.read(), task["project"])["path"])
    original = (root / "first.txt").stat().st_mtime_ns
    launch(store, task_id, store.control(task_id, "switch", incoming="claude", resume=True,
                                        allow_outgoing_review=True))
    task = await_turn(store, task_id)
    records = Mailbox(store.base, config_for(store.base), external_workers=True).listing(limit=None)
    reviews = {(r["sender"], r["recipient"]) for r in records if r["status"] == "answered"}
    print("Reviews:", [(r["id"], r["sender"], r["recipient"], r["status"]) for r in records], flush=True)
    return 0 if (task["status"] == "completed" and task["lead"] == "claude" and task["generation"] == 2
                 and len(task["experiments"]) == 2 and all(e["status"] == "finished" for e in task["experiments"])
                 and (root / "first.txt").stat().st_mtime_ns == original
                 and reviews == {("chatgpt", "claude"), ("claude", "chatgpt")}) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--continue", dest="continuation", nargs=2, metavar=("TEST_FOLDER", "TASK_ID"))
    args = parser.parse_args()
    if args.continuation:
        raise SystemExit(continue_smoke(Store(args.continuation[0]), args.continuation[1]))
    raise SystemExit(main())
