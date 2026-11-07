import concurrent.futures
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'ai_bridge.py'
spec = importlib.util.spec_from_file_location('ai_bridge', SCRIPT)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
REAL_LAUNCH_WORKER = b.launch_worker


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.config = b.init(self.base)
        self.calls = []
        self.release = threading.Event()
        self.release.set()
        def runner(peer, question, context, base, config):
            self.calls.append((peer, question, context))
            self.release.wait(5)
            if question == 'simulate error':
                raise RuntimeError('Simulated unavailable model')
            return 'TEST DOUBLE: ' + question + ' / ' + context
        config = dict(self.config, port=0)
        self.server = b.BridgeServer(self.base, config, runner)
        self.config['port'] = self.server.server_port
        b.atomic_json(self.base / 'bridge-config.json', self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.worker_patch = patch.object(b, 'launch_worker', side_effect=lambda base, job_id: self.server.mailbox.pools[b.read_json(base / 'data/requests' / (job_id + '.json'))['recipient']].submit(self.server.mailbox.work, job_id))
        self.worker_patch.start()

    def tearDown(self):
        self.worker_patch.stop()
        self.release.set()
        self.server.shutdown()
        self.thread.join(5)
        self.server.mailbox.close()
        self.server.server_close()
        self.temp.cleanup()

    def submit(self, **changes):
        data = dict(sender='chatgpt', recipient='claude', question='Review this', context='متن فارسی')
        data.update(changes)
        return b.api(self.base, '/api/requests', data)

    def wait(self, item):
        for _ in range(200):
            item = b.api(self.base, '/api/requests/' + item['id'])
            if item['status'] in b.TERMINAL:
                return item
            time.sleep(.01)
        self.fail('Job never finished')

    def test_auto_roundtrip_and_local_transcript(self):
        item = self.wait(self.submit())
        self.assertEqual(item['status'], 'answered')
        self.assertIn('متن فارسی', item['answer'])
        self.assertEqual(item, b.read_json(self.base / 'data/requests' / (item['id'] + '.json')))
        self.assertEqual(len(self.calls), 1)

    def test_simultaneous_idempotent_submission_invokes_model_once(self):
        job_id = 'a' * 32
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(lambda _: self.submit(id=job_id), range(10)))
        self.wait(items[0])
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(b.BridgeError) as caught:
            self.submit(id=job_id, question='Different question')
        self.assertEqual(caught.exception.status, 409)

    def test_manual_mcp_exchange_in_both_directions(self):
        item = b.mcp_call(self.base, 'claude', 'ask_peer', {'question': 'What is 2+2?', 'mode': 'manual'})
        self.assertEqual(item['recipient'], 'chatgpt')
        inbox = b.mcp_call(self.base, 'chatgpt', 'check_inbox', {})
        self.assertEqual(inbox['requests'][0]['id'], item['id'])
        with self.assertRaises(b.BridgeError):
            b.mcp_call(self.base, 'claude', 'reply_to_peer', {'request_id': item['id'], 'answer': '4'})
        b.mcp_call(self.base, 'chatgpt', 'reply_to_peer', {'request_id': item['id'], 'answer': '4'})
        result = b.mcp_call(self.base, 'claude', 'read_reply', {'request_id': item['id']})
        self.assertEqual(result['answer'], '4')
        self.assertEqual(result['answered_by'], 'manual:chatgpt')
        self.assertEqual(self.calls, [])

    def test_auth_origin_and_host_protection(self):
        token = self.server.token
        for headers, expected in [({}, 401), ({'Authorization': 'Bearer wrong'}, 401),
                ({'Authorization': 'Bearer ' + token, 'Origin': 'https://evil.example'}, 403),
                ({'Authorization': 'Bearer ' + token, 'Host': 'evil.example'}, 403)]:
            handler = b.BridgeHandler.__new__(b.BridgeHandler)
            handler.server, handler.path = self.server, '/api/requests'
            handler.headers = {'Host': f'127.0.0.1:{self.config["port"]}', **headers}
            with self.assertRaises(b.BridgeError) as caught:
                handler.validate()
            self.assertEqual(caught.exception.status, expected)
        handler.headers = {'Host': f'127.0.0.1:{self.config["port"]}', 'Authorization': 'Bearer ' + token}
        handler.validate()

    def test_inputs_and_traversal_rejected(self):
        for changes in ({'question': ''}, {'question': 123}, {'context': []}, {'context': 'x' * 80001},
                        {'sender': 'claude'}, {'mode': 'weird'}, {'id': '../escape'}):
            with self.assertRaises(b.BridgeError):
                self.submit(**changes)
        with self.assertRaises(b.BridgeError):
            b.mcp_call(self.base, 'claude', 'read_reply', {'request_id': '../access-token.txt'})
        with self.assertRaises(b.BridgeError):
            b.mcp_call(self.base, 'claude', 'ask_peer', {'question': 'hey', 'sender': 'user'})
        self.assertEqual(self.calls, [])

    def test_limit_works_with_large_history(self):
        for _ in range(105):
            item = self.submit(mode='manual')
            b.api(self.base, '/api/requests/' + item['id'] + '/reply', {'sender': 'claude', 'answer': 'old'})
        for _ in range(8):
            self.submit(mode='manual')
        with self.assertRaises(b.BridgeError) as caught:
            self.submit(mode='manual')
        self.assertEqual(caught.exception.status, 429)

    def test_cancel_queued_request_and_error_visibility(self):
        item = self.submit(mode='manual')
        self.assertEqual(b.api(self.base, '/api/requests/' + item['id'] + '/cancel', {})['status'], 'cancelled')
        item = self.wait(self.submit(question='simulate error'))
        self.assertEqual(item['status'], 'failed')
        self.assertIn('Simulated unavailable', item['error'])

    def test_recursive_review_blocked(self):
        with patch.dict(os.environ, {'AI_BRIDGE_WORKER': '1'}):
            with self.assertRaises(b.BridgeError):
                b.mcp_call(self.base, 'claude', 'ask_peer', {'question': 'ask again'})

    def test_stdio_handshake_and_tools_call(self):
        requests = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'ask_peer','arguments':{'question':'hello','mode':'manual'}}},
        ]
        run = subprocess.run([sys.executable, str(SCRIPT), '--data-dir', str(self.base), 'mcp', '--identity', 'claude'],
                             input='\n'.join(map(json.dumps, requests)) + '\n', capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        output = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(output), 3)
        self.assertEqual(output[0]['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(len(output[1]['result']['tools']), 4)
        item = json.loads(output[2]['result']['content'][0]['text'])
        self.assertEqual(item['sender'], 'claude')
        self.assertEqual(item['recipient'], 'chatgpt')

    def test_worker_auth_refuses_api_billing(self):
        cases = [('claude', (0, '{"authMethod":"api_key"}', '')),
                 ('chatgpt', (0, 'Logged in using an API key', ''))]
        for peer, result in cases:
            with patch.object(b, 'run_process', return_value=result):
                with self.assertRaises(b.BridgeError):
                    b.auth_status(peer, ['mock'], self.base)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY':'secret', 'OPENAI_API_KEY':'secret', 'CLAUDECODE':'1'}):
            env = b.clean_env()
            self.assertNotIn('ANTHROPIC_API_KEY', env)
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('CLAUDECODE', env)

    def test_cli_adapters_parse_success_and_errors(self):
        for peer in b.PEERS:
            captured = []
            def process(cmd, cwd, timeout, input_text=None, env=None):
                captured.append((cmd, input_text))
                if '--help' in cmd:
                    return (0, '--ignore-user-config', '')
                if peer == 'claude':
                    return (0, '{"is_error":false,"result":"an answer"}', '')
                return (0, '{"type":"item.completed","item":{"type":"agent_message","text":"an answer"}}\n', '')
            with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'auth_status'), patch.object(b, 'run_process', side_effect=process):
                answer = b.cli_reply(peer, 'literal $(bad) & `test`', 'context', self.base, self.config)
            self.assertEqual(answer, 'an answer')
            cmd, stdin = captured[-1]
            self.assertNotIn('literal $(bad) & `test`', cmd)
            self.assertIn('literal $(bad) & `test`', stdin)
            self.assertNotIn('--dangerously-skip-permissions', cmd)
            if peer == 'chatgpt':
                self.assertIn('read-only', cmd)
                self.assertGreater(cmd.index('--ignore-user-config'), cmd.index('exec'))

    def test_restart_does_not_replay_uncertain_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = b.init(base)
            item = dict(id='f'*32, mode='auto', status='running', sender='claude', recipient='chatgpt', created_at=b.now())
            b.atomic_json(base / 'data/requests' / ('f'*32 + '.json'), item)
            mailbox = b.Mailbox(base, config, runner=lambda *args: self.fail('Replayed uncertain request'), recover=True)
            self.assertEqual(mailbox.get('f'*32)['status'], 'failed')
            mailbox.close()

    def test_followup_task_includes_prior_experiment(self):
        first = self.wait(self.submit(question='Baseline score: 0.71', task_id='kaggle-test'))
        self.wait(self.submit(question='New score: 0.73. What next?', task_id='kaggle-test'))
        self.assertIn('Baseline score: 0.71', self.calls[-1][2])
        self.assertIn(first['answer'], self.calls[-1][2])
        self.wait(self.submit(question='Unrelated task', task_id='separate-task'))
        self.assertNotIn('Baseline score: 0.71', self.calls[-1][2])

    def test_cross_process_manual_messages(self):
        commands = [[sys.executable, str(SCRIPT), '--data-dir', str(self.base), 'ask', '--from', 'claude', '--to', 'chatgpt', '--manual', '--question', 'process ' + str(i)] for i in range(6)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=15), commands))
        self.assertTrue(all(result.returncode == 0 for result in results), [result.stderr for result in results])
        self.assertEqual(len(b.api(self.base, '/api/requests')['requests']), 6)

    @unittest.skipUnless(os.name == 'nt', 'Windows desktop install layout')
    def test_codex_desktop_discovery_without_path(self):
        home = self.base / 'user'
        install = home / 'AppData/Local/OpenAI/Codex/bin'
        old, new = (install / version / 'codex.exe' for version in ('old', 'new'))
        for executable in (old, new):
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'fixture')
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        with patch.dict(os.environ, {'USERPROFILE': str(home), 'LOCALAPPDATA': str(self.base / 'redirected'),
                                     'AI_BRIDGE_CODEX': ''}), patch.object(b.shutil, 'which', return_value=None):
            self.assertEqual(b.executable_for('chatgpt', self.base, {}), [str(new.resolve())])
            self.assertEqual(b.executable_for('chatgpt', self.base, {'codex_path': str(old)}),
                             [str(old.resolve())])

    def test_real_async_worker_reports_missing_cli(self):
        self.config['codex_path'] = str(self.base / 'nonexistent-cli.exe')
        b.atomic_json(self.base / 'bridge-config.json', self.config)
        processes = []
        def capture_worker(*args):
            processes.append(REAL_LAUNCH_WORKER(*args))
        with patch.object(b, 'launch_worker', capture_worker):
            item = self.wait(self.submit(sender='claude', recipient='chatgpt'))
        for process in processes:
            process.wait(timeout=10)
        self.assertEqual(item['status'], 'failed')
        self.assertIn('codex CLI was not found', item['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
