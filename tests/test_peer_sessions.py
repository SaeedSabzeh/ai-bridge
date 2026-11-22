"""Remembered peer sessions. No model calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import peer_sessions as ps


class PeerSessionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ps.SessionStore(self.temp.name)

    def test_store_round_trip_per_peer_and_task(self):
        self.assertIsNone(self.store.get("chatgpt", "design"))
        self.store.set("chatgpt", "design", "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf")
        self.store.set("claude", "design", "5d0c1f9e-1111-4222-8333-944455556666")
        self.assertEqual(self.store.get("chatgpt", "design"), "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf")
        self.assertEqual(self.store.get("claude", "design"), "5d0c1f9e-1111-4222-8333-944455556666")
        self.assertIsNone(self.store.get("chatgpt", "other-topic"))
        self.store.forget("chatgpt", "design")
        self.assertIsNone(self.store.get("chatgpt", "design"))
        self.assertIsNotNone(self.store.get("claude", "design"))

    def test_no_task_or_bad_values_are_ignored(self):
        for task in (None, "", "../x", "a" * 65):
            self.store.set("chatgpt", task, "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf")
            self.assertIsNone(self.store.get("chatgpt", task))
        self.store.set("chatgpt", "t", "--dangerous-flag")
        self.assertIsNone(self.store.get("chatgpt", "t"))
        Path(self.temp.name, "data", "sessions.json").write_text('{"chatgpt:t": "-x"}', encoding="utf-8")
        self.assertIsNone(self.store.get("chatgpt", "t"))

    def test_corrupt_file_reads_as_empty(self):
        path = Path(self.temp.name, "data", "sessions.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.store.get("chatgpt", "t"))
        self.store.set("chatgpt", "t", "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"chatgpt:t": "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf"})

    def test_codex_args_new_and_resume_stay_read_only(self):
        new = ps.command_args("chatgpt")
        self.assertNotIn("--ephemeral", new)
        self.assertEqual(new[new.index("exec") + 1], "--ignore-user-config")
        self.assertIn("read-only", new[new.index("--sandbox") + 1])
        resumed = ps.command_args("chatgpt", "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf")
        self.assertEqual(resumed[resumed.index("exec"):resumed.index("exec") + 2], ["exec", "resume"])
        self.assertIn('sandbox_mode="read-only"', resumed)
        self.assertIn("--ignore-user-config", resumed)
        self.assertEqual(resumed[-2:], ["01a1b73f-d5e4-7f82-91d4-699d4bfee1bf", "-"])
        self.assertIn("features.shell_tool=false", resumed)

    def test_claude_args_new_and_resume(self):
        new = ps.command_args("claude")
        self.assertNotIn("--no-session-persistence", new)
        self.assertNotIn("--resume", new)
        self.assertEqual(new[new.index("--disallowedTools") + 1], "*")
        resumed = ps.command_args("claude", "5d0c1f9e-1111-4222-8333-944455556666")
        self.assertEqual(resumed[-2:], ["--resume", "5d0c1f9e-1111-4222-8333-944455556666"])

    def test_tools_let_the_peer_read_but_never_write(self):
        session = "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf"
        for codex in (ps.command_args("chatgpt", tools=True, windows_sandbox="elevated", web_search=True),
                      ps.command_args("chatgpt", session, tools=True, windows_sandbox="elevated", web_search=True)):
            self.assertIn('sandbox_mode="read-only"', codex)
            self.assertIn('windows.sandbox="elevated"', codex)
            self.assertIn('web_search="live"', codex)
            self.assertNotIn("features.shell_tool=false", codex)
            self.assertIn("--ignore-user-config", codex)
            self.assertEqual(codex[codex.index("--ask-for-approval") + 1], "never")
            self.assertFalse([arg for arg in codex if "danger" in arg or "workspace-write" in arg])
        quiet = ps.command_args("chatgpt", tools=True, windows_sandbox="something-else")
        self.assertFalse([arg for arg in quiet if arg.startswith(("windows.sandbox", "web_search"))])
        claude = ps.command_args("claude", tools=True)
        self.assertEqual(claude[claude.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(claude[claude.index("--allowedTools") + 1], "Read,Grep,Glob")
        settings = json.loads(claude[claude.index("--settings") + 1])
        self.assertTrue(settings["disableAllHooks"])
        self.assertIn("Read(**/.env)", settings["permissions"]["deny"])
        self.assertIn("--strict-mcp-config", claude)
        for forbidden in ("Bash", "Edit", "Write", "WebFetch"):
            self.assertNotIn(forbidden, " ".join(claude))

    def test_image_tool_is_turned_on_only_for_image_requests(self):
        self.assertIn("features.image_generation=true", ps.command_args("chatgpt", keep=False, images=True))
        for args in (ps.command_args("chatgpt", keep=False), ps.command_args("chatgpt", "5d0c1f9e-1111-4222-8333-944455556666", tools=True)):
            self.assertIn("features.image_generation=false", args)
            self.assertNotIn("features.image_generation=true", args)

    def test_model_and_effort_are_passed_and_checked(self):
        codex = ps.command_args("chatgpt", model="gpt-example", effort="high")
        self.assertEqual(codex[codex.index("--model") + 1], "gpt-example")
        self.assertGreater(codex.index("--model"), codex.index("exec"))
        self.assertIn('model_reasoning_effort="high"', codex)
        self.assertEqual(codex[-1], "-")
        claude = ps.command_args("claude", "5d0c1f9e-1111-4222-8333-944455556666", model="opus", effort="high")
        self.assertEqual(claude[claude.index("--model") + 1], "opus")
        self.assertEqual(claude[claude.index("--effort") + 1], "high")
        self.assertEqual(claude[-2], "--resume")
        for bad in ("--oss", "", "a b", 'x"y', "m" * 81):
            with self.assertRaises(ValueError):
                ps.command_args("chatgpt", model=bad)
            with self.assertRaises(ValueError):
                ps.command_args("claude", effort=bad)

    def test_one_off_sessions_are_not_saved(self):
        self.assertIn("--ephemeral", ps.command_args("chatgpt", keep=False))
        self.assertIn("--no-session-persistence", ps.command_args("claude", keep=False))

    def test_bad_session_or_peer_rejected(self):
        with self.assertRaises(ValueError):
            ps.command_args("chatgpt", "--resume-evil")
        with self.assertRaises(ValueError):
            ps.command_args("someone")

    def test_parse_session_ids(self):
        out = "\n".join([
            "noise",
            json.dumps({"type": "thread.started", "thread_id": "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}),
        ])
        self.assertEqual(ps.codex_session(out), "01a1b73f-d5e4-7f82-91d4-699d4bfee1bf")
        self.assertIsNone(ps.codex_session('{"type":"thread.started","thread_id":"-bad"}'))
        self.assertIsNone(ps.codex_session("[]"))
        self.assertEqual(ps.claude_session({"session_id": "5d0c1f9e-1111-4222-8333-944455556666"}),
                         "5d0c1f9e-1111-4222-8333-944455556666")
        self.assertIsNone(ps.claude_session({"result": "x"}))
        self.assertIsNone(ps.claude_session("not a dict"))


if __name__ == "__main__":
    unittest.main()
