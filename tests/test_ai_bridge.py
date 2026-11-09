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
        self.mailbox = b.Mailbox(self.base, self.config, runner)
        self.worker_patch = patch.object(b, 'launch_worker', side_effect=lambda base, job_id: self.mailbox.pools[b.read_json(base / 'data/requests' / (job_id + '.json'))['recipient']].submit(self.mailbox.work, job_id))
        self.worker_patch.start()

    def tearDown(self):
        self.worker_patch.stop()
        self.release.set()
        self.mailbox.close()
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
        self.assertEqual(item['error_code'], 'worker_error')
        self.assertIn('Simulated unavailable', item['error_detail'])

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
        self.assertIn('save_checkpoint', [tool['name'] for tool in output[1]['result']['tools']])
        item = json.loads(output[2]['result']['content'][0]['text'])
        self.assertEqual(item['sender'], 'claude')
        self.assertEqual(item['recipient'], 'chatgpt')

    def test_codex_conversation_view_is_filtered_and_read_only(self):
        item = self.submit(mode='manual', task_id='sample', question='Show this exchange',
                           context='PRIVATE_CONTEXT_MARKER')
        self.submit(mode='manual', task_id='other', question='Different task')
        path = self.base / 'data/requests' / (item['id'] + '.json')
        item.update(mode='auto', updated_at='2000-01-01T00:00:00+00:00')
        b.atomic_json(path, item)
        before = path.read_bytes()
        calls = [
            ('initialize', {'protocolVersion': '2025-06-18'}),
            ('tools/list', {}),
            ('resources/read', {'uri': 'ui://ai-bridge/conversation.html'}),
            ('tools/call', {'name': 'show_conversation', 'arguments': {'task_id': 'sample'}}),
            ('tools/call', {'name': 'refresh_conversation', 'arguments': {'task_id': 'sample'}}),
            ('tools/call', {'name': 'refresh_conversation', 'arguments': {'request_id': item['id']}}),
        ]
        requests = [{'jsonrpc': '2.0', 'id': i, 'method': method, 'params': params}
                    for i, (method, params) in enumerate(calls)]
        run = subprocess.run(
            [sys.executable, str(SCRIPT), '--data-dir', str(self.base), 'mcp',
             '--identity', 'chatgpt', '--conversation-view'],
            input='\n'.join(map(json.dumps, requests)) + '\n',
            capture_output=True, encoding='utf-8', timeout=20,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        hello, tools, resource, show, refresh, details = [json.loads(line)['result'] for line in run.stdout.splitlines()]
        self.assertIn('resources', hello['capabilities'])
        self.assertIn('show_conversation', [tool['name'] for tool in tools['tools']])
        self.assertIn('text/html;profile=mcp-app', resource['contents'][0]['mimeType'])
        self.assertIn('refresh_conversation', resource['contents'][0]['text'])
        self.assertEqual(show['_meta'], refresh['_meta'])
        self.assertEqual([entry['id'] for entry in show['_meta']['conversation']['exchanges']], [item['id']])
        self.assertEqual(show['_meta']['conversation']['exchanges'][0]['status'], 'Waiting')
        self.assertIn('Show this exchange', show['content'][0]['text'])
        self.assertNotIn('PRIVATE_CONTEXT_MARKER', json.dumps(show))
        self.assertEqual(details['_meta']['request']['context'], 'PRIVATE_CONTEXT_MARKER')
        self.assertEqual(path.read_bytes(), before)

    def test_named_topics_are_isolated_and_checkpoint_persists(self):
        first = self.wait(self.submit(topic='Kaggle', question='Only in the first topic'))
        second = self.wait(self.submit(topic='Kaggle', question='A separate run'))
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertNotIn(first['question'], self.calls[-1][2])
        notes = 'Decision: use grouped folds. Open: is the gain stable?'
        b.mcp_call(self.base, 'chatgpt', 'save_checkpoint', {'task_id': first['task_id'], 'summary': notes})
        process = subprocess.run([sys.executable, '-B', str(SCRIPT), '--data-dir', str(self.base),
                                  'checkpoint', first['task_id']], capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['summary'], notes)
        followup = self.wait(self.submit(task_id=first['task_id'], question='Continue the first run'))
        self.assertEqual(followup['topic'], 'Kaggle')
        self.assertIn(first['question'], self.calls[-1][2])
        self.assertNotIn(second['question'], self.calls[-1][2])

    def test_two_distinct_requests_wait_and_finish_once_each(self):
        self.release.clear()
        first = self.submit(question='First queued job')
        second = self.submit(question='Second queued job')
        deadline = time.monotonic() + 3
        while self.mailbox.get(first['id'])['status'] == 'queued' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.mailbox.get(first['id'])['status'], 'running')
        self.assertEqual(self.mailbox.get(second['id'])['status'], 'queued')
        self.release.set()
        self.assertEqual(self.wait(first)['status'], 'answered')
        self.assertEqual(self.wait(second)['status'], 'answered')
        self.assertEqual([call[1] for call in self.calls], [first['question'], second['question']])

    def test_killed_worker_fails_promptly_without_replay(self):
        box = b.Mailbox(self.base, self.config, external_workers=True)
        with patch.object(b, 'launch_worker'):
            item = box.submit(dict(sender='claude', recipient='chatgpt', question='Kill the worker fixture'))
        code = '''
import sys, time
from pathlib import Path
from ai_bridge import Mailbox, config_for
base = Path(sys.argv[1])
def hold(*args):
    time.sleep(30)
Mailbox(base, config_for(base), runner=hold, external_workers=True).work(sys.argv[2])
'''
        process = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.base), item['id']], cwd=SCRIPT.parent)
        try:
            deadline = time.monotonic() + 5
            while box.get(item['id'])['status'] == 'queued' and time.monotonic() < deadline:
                time.sleep(.025)
            self.assertEqual(box.get(item['id'])['status'], 'running')
        finally:
            process.kill()
            process.wait(timeout=5)
        final = box.get(item['id'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['error_code'], 'worker_stopped')
        self.assertIn('before returning', final['error'])
        self.assertEqual(box.get(item['id']), final)

    def test_worker_that_never_started_fails_after_startup_deadline(self):
        box = b.Mailbox(self.base, self.config, external_workers=True)
        with patch.object(b, 'launch_worker'):
            item = box.submit(dict(sender='claude', recipient='chatgpt', question='Never start this worker'))
        self.assertEqual(box.get(item['id'])['status'], 'queued')
        item['start_by'] = '2000-01-01T00:00:00.000000+00:00'
        b.atomic_json(box.path(item['id']), item)
        self.assertEqual(box.get(item['id'])['error_code'], 'launch_failed')

    def test_panel_finds_bridge_and_python_in_moved_directory(self):
        moved = self.base / 'moved bridge with spaces'
        project = moved / 'nested project'
        project.mkdir(parents=True)
        for filename in ('ai_bridge.py', 'peer_sessions.py'):
            (moved / filename).touch()
        hook = (SCRIPT.parent / 'plugin/hooks/register.tsx').read_text(encoding='utf-8')
        bootstrap = hook.split('const DISCOVER = `', 1)[1].split('`', 1)[0]
        env = os.environ.copy()
        env.pop('AI_BRIDGE_HOME', None)
        result = subprocess.run([sys.executable, '-B', '-c', bootstrap], cwd=project, env=env,
                                capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        location = json.loads(result.stdout)
        self.assertEqual(Path(location['root']), moved.resolve())
        self.assertEqual(Path(location['python']).resolve(), Path(sys.executable).resolve())

    def test_provider_limit_is_actionable_and_never_retried_on_resume(self):
        session = 'saved-session-123'
        b.SessionStore(self.base).set('claude', 'topic', session)
        result = {'is_error': True, 'num_turns': 1, 'api_error': 'usage_limit_reached',
                  'result': "You've hit your weekly limit · resets Nov 9, 7pm (Europe/Rome)"}
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'auth_status'), \
                patch.object(b, 'run_process', return_value=(1, json.dumps(result), '')) as process:
            with self.assertRaises(b.ReviewError) as caught:
                b.cli_reply('claude', 'Continue', '', self.base, self.config, task_id='topic')
        self.assertEqual(process.call_count, 1)
        self.assertEqual(caught.exception.code, 'usage_limit')
        self.assertIn('Claude has reached its weekly limit', str(caught.exception))
        self.assertIn('Nov 9, 7pm (Europe/Rome)', str(caught.exception))
        self.assertEqual(b.SessionStore(self.base).get('claude', 'topic'), session)

    def test_resume_only_falls_back_for_confirmed_missing_session(self):
        session = 'saved-session-123'
        b.SessionStore(self.base).set('claude', 'topic', session)
        progress = []
        responses = [(1, '', 'No conversation found with session ID: ' + session),
                     (0, json.dumps({'result': 'answer', 'session_id': 'new-session-123'}), '')]
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'auth_status'), \
                patch.object(b, 'run_process', side_effect=responses) as process:
            answer = b.cli_reply('claude', 'Continue', '', self.base, self.config, task_id='topic',
                                 checkpoint='Older decision', history='Recent exchange',
                                 progress=lambda message, **fields: progress.append(fields))
        self.assertEqual(answer, 'answer')
        self.assertEqual(process.call_count, 2)
        self.assertIn('--resume', process.call_args_list[0].args[0])
        self.assertIn('Older decision', process.call_args_list[1].args[3])
        self.assertIn('Recent exchange', process.call_args_list[1].args[3])
        self.assertEqual(progress[-1]['continuity'], 'recovered')
        self.assertEqual(b.SessionStore(self.base).get('claude', 'topic'), 'new-session-123')

    def test_codex_missing_session_starts_fresh_with_real_cli_message(self):
        session = '00000000-0000-4000-8000-000000000000'
        b.SessionStore(self.base).set('chatgpt', 'topic', session)
        # Exact stderr from `codex exec resume` with an unknown thread ID (checked against the real CLI).
        missing = ('Error: thread/resume: thread/resume failed: no rollout found for thread id '
                   + session + ' (code -32600)\n')
        fresh = '\n'.join(json.dumps(event) for event in (
            {'type': 'thread.started', 'thread_id': 'new-thread-123'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}},
            {'type': 'turn.completed'}))
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'auth_status'), \
                patch.object(b, 'run_process', side_effect=[(0, '--ignore-user-config', ''), (0, '', ''), (1, '', missing), (0, fresh, '')]) as process:
            answer = b.cli_reply('chatgpt', 'Continue', '', self.base, self.config, task_id='topic')
        self.assertEqual(answer, 'answer')
        turns = process.call_args_list[2:]  # The first two calls only read Codex's --help output.
        self.assertEqual(len(turns), 2)
        self.assertIn('resume', turns[0].args[0])
        self.assertNotIn('resume', turns[1].args[0])
        self.assertEqual(b.SessionStore(self.base).get('chatgpt', 'topic'), 'new-thread-123')

    def test_codex_other_resume_failure_keeps_session_and_does_not_retry(self):
        session = '00000000-0000-4000-8000-000000000000'
        b.SessionStore(self.base).set('chatgpt', 'topic', session)
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'auth_status'), \
                patch.object(b, 'run_process', side_effect=[(0, '--ignore-user-config', ''), (0, '', ''), (1, '', 'Error: stream disconnected before completion')]) as process:
            with self.assertRaises(b.ReviewError) as caught:
                b.cli_reply('chatgpt', 'Continue', '', self.base, self.config, task_id='topic')
        self.assertEqual(caught.exception.code, 'provider_error')
        self.assertEqual(process.call_count, 3)  # Two --help checks and one provider turn, no retry.
        self.assertEqual(b.SessionStore(self.base).get('chatgpt', 'topic'), session)

    def test_resume_and_checkpoint_recovery_across_worker_processes(self):
        box = b.Mailbox(self.base, self.config, external_workers=True)
        code = '''
import json, sys
from pathlib import Path
import ai_bridge as bridge
base, job, missing = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == 'missing'
bridge.executable_for = lambda *args: ['fixture-claude']
bridge.auth_status = lambda *args: None
def reply(command, cwd, timeout, prompt):
    if missing and '--resume' in command:
        return 1, '', 'No conversation found with session ID: fixture-session'
    return 0, json.dumps({'result': 'fixture reply', 'session_id': 'fixture-session'}), ''
bridge.run_process = reply
bridge.Mailbox(base, bridge.config_for(base), runner=bridge.cli_reply, external_workers=True).work(job)
'''

        def run_worker(**fields):
            missing = fields.pop('missing', False)
            with patch.object(b, 'launch_worker'):
                item = box.submit(dict(sender='chatgpt', recipient='claude', **fields))
            process = subprocess.run([sys.executable, '-B', '-c', code, str(self.base), item['id'],
                                      'missing' if missing else 'present'], cwd=SCRIPT.parent,
                                     capture_output=True, encoding='utf-8', timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            return box.get(item['id'])

        first = run_worker(topic='Model validation', question='Original decision')
        self.assertEqual(first['continuity'], 'new')
        task_id = first['task_id']
        resumed = run_worker(task_id=task_id, question='Continue after restart')
        self.assertEqual(resumed['continuity'], 'resumed')
        self.assertNotIn('shared_history', resumed)
        box.checkpoint(task_id, 'Decision: grouped folds. Open: inspect drift.')
        for index in range(6):
            item = box.submit(dict(sender='chatgpt', recipient='claude', mode='manual',
                                   task_id=task_id, question=f'Recent exchange {index}'))
            box.reply(item['id'], dict(sender='claude', answer='Understood'))
        recovered = run_worker(task_id=task_id, question='Recover the discussion', missing=True)
        self.assertEqual(recovered['continuity'], 'recovered')
        self.assertIn('grouped folds', recovered['shared_checkpoint'])
        self.assertIn('inspect drift', recovered['shared_checkpoint'])
        self.assertIn('Recent exchange 0', recovered['shared_history'])
        self.assertNotIn('Original decision', recovered['shared_history'])

    def test_transient_process_launch_is_retried_only_before_start(self):
        with patch.object(b.subprocess, 'Popen') as spawn, patch.object(b.time, 'sleep'):
            process = spawn.return_value
            process.communicate.return_value = (b'hello', b'')
            process.returncode = 0
            spawn.side_effect = [OSError(b.errno.EAGAIN, 'resource unavailable'), process]
            self.assertEqual(b.run_process(['fixture'], self.base, 10), (0, 'hello', ''))
        self.assertEqual(spawn.call_count, 2)
        process.communicate.assert_called_once()

    def test_uncertain_timeout_does_not_replay_a_saved_session(self):
        b.SessionStore(self.base).set('claude', 'topic', 'new-session-123')
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'auth_status'), \
                patch.object(b, 'run_process', side_effect=b.ReviewError('timeout', 'Timed out.')) as process:
            with self.assertRaises(b.ReviewError):
                b.cli_reply('claude', 'Continue', '', self.base, self.config, task_id='topic')
        self.assertEqual(process.call_count, 1)
        self.assertEqual(b.SessionStore(self.base).get('claude', 'topic'), 'new-session-123')

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
        self.assertEqual(item['error_code'], 'cli_missing')
        self.assertIn('codex CLI was not found', item['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
