"""Desktop integration tests. No model calls or credential files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_bridge import BridgeError, Mailbox, config_for, now
from lead_run import build_prompt, controls, recover, worker
from lead_tools import Scope
from windows_job import Job
from workspace_state import Store


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.store = Store(Path(self.temp.name) / "bridge")
        self.project = self.store.add_project("Test project", self.root)
        self.task = self.store.add_task(self.project, "Fix the scorer", "claude", "Use the same validation split.")

    def snapshot(self):
        return self.store.task(self.store.read(), self.task)

    def claim(self, run):
        state, task = self.store.claim(self.task, run)
        return state, task, Scope(self.store, self.task, run, task["generation"])

    def test_draining_handoff_fences_old_lead_and_restores_context_in_both_directions(self):
        run = self.store.start(self.task)
        _, _, old = self.claim(run)
        old.call("write_file", {"path": "scorer.py", "text": "score = 42\n", "expected": "new"})
        old.call("save_checkpoint", {"fields": json.dumps({
            "completed": "Scorer implementation saved.", "best": "42 on fold A", "next": "Check fold A edge case",
            "questions": "Was the original scorer insensitive to class imbalance?"})})
        box = Mailbox(self.store.base, config_for(self.store.base), external_workers=True)
        request = box.submit({"sender": "claude", "recipient": "chatgpt", "mode": "manual",
                              "task_id": self.task, "question": "Could fold A leak its labels?"})
        box.reply(request["id"], {"sender": "chatgpt", "answer": "Check timestamp ordering."})
        self.assertIsNone(self.store.control(self.task, "switch", incoming="chatgpt", resume=True))
        self.assertEqual(self.snapshot()["lead"], "claude")
        with self.assertRaises(BridgeError):
            old.call("ask_peer", {"question": "Start a new review?"})
        with self.assertRaises(BridgeError):
            self.store.start(self.task)
        old.call("save_checkpoint", {"fields": '{"decisions":"Keep fold A for comparison."}'})
        # No process was launched in this test; production finalizes after checking the named Job.
        incoming = self.store.finish(self.task, run, "paused")
        state, task, new = self.claim(incoming)
        self.assertEqual((task["lead"], task["generation"]), ("chatgpt", 2))
        self.assertIn("score = 42", new.call("read_file", {"path": "scorer.py"})["text"])
        with self.assertRaisesRegex(BridgeError, "adviser is unavailable"):
            new.call("ask_peer", {"question": "The outgoing lead is skipped by default."})
        prompt = build_prompt(self.store, state, task)
        for clue in ("42 on fold A", "Keep fold A", "Check timestamp ordering", "same validation split"):
            self.assertIn(clue, prompt)
        with self.assertRaisesRegex(BridgeError, "no longer owns"):
            old.call("write_file", {"path": "wrong.txt", "text": "stale", "expected": "new"})
        self.store.control(self.task, "switch", incoming="claude", interrupt=True)
        with self.assertRaises(BridgeError):
            new.call("save_checkpoint", {"fields": '{"next":"stale"}'})
        self.store.finish(self.task, incoming, "stopped")
        self.assertEqual((self.snapshot()["lead"], self.snapshot()["generation"]), ("claude", 3))
        self.assertEqual(self.snapshot()["checkpoint"]["next"], "Check fold A edge case")
        checkpoint = next(self.root.glob("AI-BRIDGE-CHECKPOINT-*.md")).read_text(encoding="utf-8")
        self.assertIn("Lead: claude", checkpoint)

    def test_quota_failure_persists_and_other_lead_runs_without_outgoing_call(self):
        run = self.store.start(self.task)
        with patch("lead_run.run_lead", return_value=("limit reached", "Usage limit reached.")) as denied:
            worker(self.store, self.task, run)
        self.assertEqual(denied.call_count, 1)
        self.assertIn("claude", self.store.read()["limited"])
        self.assertEqual(self.snapshot()["status"], "limit reached")
        with self.assertRaisesRegex(BridgeError, "reported a usage limit"):
            self.store.start(self.task)
        incoming = self.store.control(self.task, "switch", incoming="chatgpt", resume=True)
        def finish_from_checkpoint(store, _, task):
            self.assertEqual(task["lead"], "chatgpt")
            self.assertIn("Usage limit reached", task["checkpoint"]["blockers"])
            Scope(store, self.task, incoming, 2).call("save_checkpoint", {"fields": '{"completed":"Recovered"}'})
            return "completed", "Used saved state"
        with patch("lead_run.run_lead", side_effect=finish_from_checkpoint) as next_lead:
            worker(Store(self.store.base), self.task, incoming)
        self.assertEqual(next_lead.call_count, 1)
        self.assertEqual(self.snapshot()["status"], "completed")

    @unittest.skipUnless(os.name == "nt", "requires native Windows jobs")
    def test_recovery_does_not_replay_and_kills_leftover_job(self):
        run = self.store.start(self.task)
        _, _, scope = self.claim(run)
        with patch("ai_bridge.launch_worker"):
            request = scope.call("ask_peer", {"question": "This queued review belongs to the interrupted run."})
        self.store.control(self.task, "switch", incoming="chatgpt", resume=True)
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        self.addCleanup(child.stdin.close)
        with Job(run) as job:
            job.assign(child.pid)
            self.assertEqual(job.active(), 1)
            self.addCleanup(child.wait, timeout=10)
            recover(Store(self.store.base), self.task)
            self.assertEqual(job.active(), 0)
        self.assertIsNone(self.snapshot()["active"])
        self.assertEqual(self.snapshot()["lead"], "chatgpt")
        self.assertIsNone(self.store.claim(self.task, run))
        self.assertIn("no request was replayed", self.snapshot()["checkpoint"]["blockers"])
        box = Mailbox(self.store.base, config_for(self.store.base), external_workers=True)
        self.assertEqual(box.get(request["id"])["status"], "failed")
        self.assertIn("not replayed", box.get(request["id"])["error"])

    def test_recovery_refuses_while_another_process_holds_writer_lease(self):
        run = self.store.start(self.task)
        code = ("import sys; from contextlib import ExitStack; from ai_bridge import FileLock; "
                "s=ExitStack(); [s.enter_context(FileLock(p)) for p in sys.argv[1:]]; "
                "print('locked',flush=True); sys.stdin.read(); s.close()")
        with subprocess.Popen([sys.executable, "-c", code, str(self.store.home / "execution.lock"),
                               str(self.store.lock.path)],
                              stdout=subprocess.PIPE, stdin=subprocess.PIPE) as p:
            try:
                self.assertEqual(p.stdout.readline().strip(), b"locked")
                with self.assertRaises(BridgeError):
                    recover(Store(self.store.base), self.task)
                self.assertIsNone(controls(self.store, self.task, run, wait=False))
            finally:
                p.stdin.close()
                p.wait(timeout=10)
        self.assertEqual(self.snapshot()["active"]["id"], run)

    def test_file_revision_workspace_boundaries_and_failed_checkpoint(self):
        first = self.snapshot()
        self.store.update(self.task, {"next": "Review"}, revision=first["revision"])
        with self.assertRaises(BridgeError):
            self.store.update(self.task, {"next": "Overwrite"}, revision=first["revision"])
        _, _, scope = self.claim(self.store.start(self.task))
        for bad in ("../unrelated", "raw/data.txt", "data/raw.txt", ".git/config", ".env.local", "foo:stream", "NUL"):
            with self.subTest(path=bad), self.assertRaises(BridgeError):
                scope.call("write_file", {"path": bad, "text": "invalid", "expected": "new"})
        scope.call("write_file", {"path": "notes.txt", "text": "keep", "expected": "new"})
        with self.assertRaises(BridgeError):
            scope.call("write_file", {"path": "notes.txt", "text": "stale", "expected": "new"})
        with patch("workspace_state.stage_text", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.finish(self.task, self.snapshot()["active"]["id"], "paused")
        self.assertIsNotNone(self.snapshot()["active"])
        self.assertEqual((self.root / "notes.txt").read_text(), "keep")

    @unittest.skipUnless(os.name == "nt", "requires native Windows jobs")
    def test_abrupt_exit_at_both_checkpoint_publication_boundaries(self):
        code = """
import os, sys
from pathlib import Path
from workspace_state import Store
store = Store(sys.argv[1])
task, run, boundary = sys.argv[2:]
real_replace = os.replace
def stop_at_boundary(src, dst):
    if ((boundary == 'before-json' and Path(dst) == store.path)
            or (boundary == 'after-json' and Path(dst).suffix == '.md')):
        os._exit(73)
    return real_replace(src, dst)
with store.execution:
    os.replace = stop_at_boundary
    store.finish(task, run, 'paused')
"""
        for boundary in ("before-json", "after-json"):
            with self.subTest(boundary=boundary):
                task_id = self.store.add_task(self.project, boundary, "claude")
                run = self.store.start(task_id)
                self.assertTrue(self.store.claim(task_id, run))
                self.store.control(task_id, "switch", incoming="chatgpt", resume=True)
                child = subprocess.run([sys.executable, "-c", code, str(self.store.base), task_id, run, boundary],
                                       capture_output=True, timeout=30)
                self.assertEqual(child.returncode, 73, child.stderr.decode(errors="replace"))
                state = self.store.read()
                task = self.store.task(state, task_id)
                mirror = self.store.mirror_path(self.store.project(state, self.project), task)
                self.assertIn("Lead: claude", mirror.read_text(encoding="utf-8"))
                self.assertEqual(task["lead"], "claude" if boundary == "before-json" else "chatgpt")
                recovered = Store(self.store.base)
                recover(recovered, task_id)
                task = recovered.task(recovered.read(), task_id)
                self.assertEqual((task["lead"], task["generation"], task["active"]), ("chatgpt", 2, None))
                self.assertIn("Lead: chatgpt", mirror.read_text(encoding="utf-8"))
                self.assertIsNone(recovered.claim(task_id, run))
                with self.assertRaises(BridgeError):
                    Scope(recovered, task_id, run, 1).call("write_file", {
                        "path": "stale.txt", "text": "must not be written", "expected": "new"})
                recover(recovered, task_id)
                self.assertEqual(recovered.task(recovered.read(), task_id), task)

    @unittest.skipUnless(os.name == "nt", "requires native Windows jobs")
    def test_approved_experiment_records_measured_result_and_kills_detached_child(self):
        script = self.root / "score.py"
        script.write_text(
            "from pathlib import Path\nimport subprocess, sys, os\n"
            "out = Path(os.environ['AI_BRIDGE_RUN_DIR'])\n"
            "(out / 'metrics.json').write_text('{\"f1\": 0.82}')\n"
            "subprocess.Popen([sys.executable, '-c', "
            "'import time; from pathlib import Path; time.sleep(2); Path(\"escaped.txt\").touch()'])\n"
            "print('measured fold-a')\n",
            encoding="utf-8")
        self.store.set_commands(self.project, {"score": [sys.executable, str(script)]})
        _, _, scope = self.claim(self.store.start(self.task))
        with self.assertRaises(BridgeError):
            scope.call("run_check", {"command": "unknown"})
        results = [scope.call("run_check", {"command": "score",
                   "metadata": '{"seed":42,"split":"fold-a","environment":"test"}'}) for _ in range(2)]
        self.assertNotEqual(results[0]["experiment"]["artifact"], results[1]["experiment"]["artifact"])
        self.assertEqual(len(self.snapshot()["experiments"]), 2)
        for result in results:
            row = result["experiment"]
            self.assertEqual(row["metrics"], {"f1": .82})
            self.assertEqual(row["metadata"]["seed"], 42)
            self.assertIn("measured fold-a", result["output_tail"])
            persisted = json.loads((self.root / row["artifact"] / "result.json").read_text())
            self.assertEqual(persisted["metrics"], {"f1": .82})
        time.sleep(2.1)
        self.assertFalse((self.root / "escaped.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
