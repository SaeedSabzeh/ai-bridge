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

    def test_tool_replies_are_brief_and_inbox_shows_waiting_questions_in_full(self):
        asked = b.mcp_call(self.base, 'claude', 'ask_peer', {'question': 'Long question ' + 'x' * 400,
                                                             'context': 'BULKY_CONTEXT', 'mode': 'manual'})
        self.assertEqual(asked['status'], 'queued')
        self.assertIn('do not submit', asked['next'])
        for hidden in ('question', 'context', 'events'):
            self.assertNotIn(hidden, asked)
        inbox = b.mcp_call(self.base, 'chatgpt', 'check_inbox', {})
        self.assertEqual(inbox['waiting_for_you'], 1)
        self.assertEqual(inbox['requests'][0]['context'], 'BULKY_CONTEXT')
        self.assertTrue(inbox['requests'][0]['question'].endswith('x' * 400))
        self.assertEqual(b.mcp_call(self.base, 'claude', 'check_inbox', {}), {'waiting_for_you': 0, 'requests': []})
        replied = b.mcp_call(self.base, 'chatgpt', 'reply_to_peer', {'request_id': asked['id'], 'answer': 'Done'})
        self.assertNotIn('context', replied)
        read = b.mcp_call(self.base, 'claude', 'read_reply', {'request_id': asked['id']})
        self.assertEqual((read['status'], read['answer']), ('answered', 'Done'))
        self.assertNotIn('BULKY_CONTEXT', json.dumps(read))
        self.assertNotIn('next', read)
        summary = b.mcp_call(self.base, 'chatgpt', 'check_inbox', {})
        self.assertEqual(summary['waiting_for_you'], 0)
        self.assertLessEqual(len(summary['requests'][0]['question']), 201)
        self.assertNotIn('answer', summary['requests'][0])
        self.assertNotIn('context', summary['requests'][0])

    def test_ask_peer_waits_for_the_automatic_answer(self):
        answered = b.mcp_call(self.base, 'chatgpt', 'ask_peer', {'question': 'Wait for me', 'wait_seconds': 10})
        self.assertEqual(answered['status'], 'answered')
        self.assertIn('Wait for me', answered['answer'])
        self.release.clear()
        pending = b.mcp_call(self.base, 'chatgpt', 'ask_peer', {'question': 'Too slow', 'wait_seconds': 0})
        self.assertIn(pending['status'], ('queued', 'running'))
        self.assertNotIn('answer', pending)
        self.assertEqual(b.mcp_call(self.base, 'chatgpt', 'read_reply',
                                    {'request_id': pending['id'], 'wait_seconds': 0})['status'], pending['status'])
        self.release.set()
        later = b.mcp_call(self.base, 'chatgpt', 'read_reply', {'request_id': pending['id'], 'wait_seconds': 10})
        self.assertEqual(later['status'], 'answered')
        for bad in ('5', 51, -1, True, 1.5):
            with self.assertRaises(b.BridgeError):
                b.mcp_call(self.base, 'chatgpt', 'ask_peer', {'question': 'Bad wait', 'wait_seconds': bad})

    def test_stale_manual_question_expires_and_never_blocks_automatic_requests(self):
        old = self.submit(mode='manual', question='Forgotten question')
        path = self.base / 'data/requests' / (old['id'] + '.json')
        stale = (b.dt.datetime.now(b.dt.timezone.utc) - b.dt.timedelta(hours=b.MANUAL_EXPIRY_HOURS + 1))
        old['created_at'] = stale.isoformat(timespec='microseconds')
        b.atomic_json(path, old)
        expired = b.api(self.base, '/api/requests/' + old['id'])
        self.assertEqual((expired['status'], expired['error_code']), ('failed', 'expired'))
        self.assertEqual(b.read_json(path)['status'], 'failed')
        fresh = self.submit(mode='manual', question='Asked just now')
        self.assertEqual(b.api(self.base, '/api/requests/' + fresh['id'])['status'], 'queued')
        for _ in range(b.MAX_PENDING - 1):
            self.submit(mode='manual')
        self.assertEqual(self.wait(self.submit(question='Automatic still works'))['status'], 'answered')

    def test_damaged_or_stray_files_do_not_break_the_mailbox(self):
        good = self.submit(mode='manual', question='Still readable')
        folder = self.base / 'data/requests'
        (folder / ('d' * 32 + '.json')).write_text('{not json', encoding='utf-8')
        (folder / ('e' * 32 + '.json')).write_text('{"id": 1}', encoding='utf-8')
        (folder / 'notes.json').write_text('[]', encoding='utf-8')
        for name, stamp in (('1' * 32, 'broken'), ('2' * 32, '2026-11-10T12:00:00')):  # Not a date; no time zone.
            b.atomic_json(folder / (name + '.json'), {**good, 'id': name, 'created_at': stamp, 'updated_at': stamp})
        self.assertEqual([item['id'] for item in b.api(self.base, '/api/requests')['requests']], [good['id']])
        self.assertEqual(self.submit(mode='manual', question='A new one')['status'], 'queued')
        with self.assertRaises(b.BridgeError) as caught:
            b.api(self.base, '/api/requests/' + 'd' * 32)
        self.assertEqual(caught.exception.status, 500)
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 'read_reply', 'arguments': {'request_id': 'e' * 32, 'wait_seconds': 0}}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'check_inbox', 'arguments': {}}}]
        run = subprocess.run([sys.executable, str(SCRIPT), '--data-dir', str(self.base), 'mcp', '--identity', 'claude'],
                             input='\n'.join(map(json.dumps, requests)) + '\n', capture_output=True, encoding='utf-8', timeout=10)
        damaged, inbox = [json.loads(line)['result'] for line in run.stdout.splitlines()]
        self.assertTrue(damaged['isError'])
        self.assertFalse(inbox['isError'])
        self.assertEqual(json.loads(inbox['content'][0]['text'])['waiting_for_you'], 2)

    def test_question_or_context_with_a_credential_is_refused(self):
        # Built at run time so this file itself never contains anything that looks like a credential.
        samples = ['gh' + 'p_' + 'a1B2' * 9, 's' + 'k-' + 'proj-' + 'Zz09' * 8, 'AK' + 'IA' + 'ABCD1234EFGH5678',
                   '-----BEGIN RSA ' + 'PRIVATE KEY-----']
        for secret in samples:
            for field in ('question', 'context'):
                with self.assertRaises(b.BridgeError) as caught:
                    self.submit(mode='manual', **{field: 'please check ' + secret})
                self.assertIn(field, str(caught.exception))
                self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(list((self.base / 'data/requests').glob('*.json')), [])
        self.assertIsNone(b.secret_in('task sk-learn pipeline, risk-adjusted, token = os.environ["KEY"]'))

    def test_workspace_is_validated_stored_and_used_as_the_peer_folder(self):
        project = self.base / 'my project'
        project.mkdir()
        for bad in ('relative/folder', str(self.base / 'missing'), 5, 'x' * 1001):
            with self.assertRaises(b.BridgeError):
                self.submit(mode='manual', workspace=bad)
        self.assertEqual(self.submit(mode='manual', workspace=str(project))['workspace'], str(project.resolve()))
        fixture = b.mcp_call(self.base, 'claude', 'ask_peer', {'question': 'q', 'mode': 'manual'}, str(project))
        self.assertEqual(fixture['workspace'], str(project.resolve()))
        seen = []

        def process(cmd, cwd, timeout, input_text=None, env=None):
            seen.append((cmd, Path(cwd), input_text))
            return (0, '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n', '')
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'ready'), \
                patch.object(b, 'run_process', side_effect=process), patch.object(b, 'codex_windows_sandbox', return_value='elevated'):
            b.cli_reply('chatgpt', 'Look at the code', '', self.base, self.config, sender='claude', workspace=str(project))
            b.cli_reply('chatgpt', 'No folder', '', self.base, self.config, sender='claude', workspace=str(self.base / 'gone'))
            b.cli_reply('chatgpt', 'Locked down', '', self.base, {**self.config, 'peer_tools': False},
                        sender='claude', workspace=str(project))
        (with_folder, without_folder, locked) = seen
        self.assertEqual(with_folder[1], project)
        self.assertIn(str(project), with_folder[2])
        self.assertIn('read-only', with_folder[2])
        self.assertIn('windows.sandbox="elevated"', with_folder[0])
        self.assertIn('web_search="live"', with_folder[0])
        self.assertNotIn('features.shell_tool=false', with_folder[0])
        self.assertEqual(without_folder[1], self.base / 'data/workers/chatgpt')
        self.assertEqual(locked[1], self.base / 'data/workers/chatgpt')
        self.assertIn('features.shell_tool=false', locked[0])
        self.assertIn('do not run tools', locked[2])
        for cmd, _, _ in seen:
            self.assertIn('sandbox_mode="read-only"', cmd)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)

    def test_codex_answer_is_the_last_message_after_lookups(self):
        events = '\n'.join(json.dumps(event) for event in (
            {'type': 'thread.started', 'thread_id': 'new-thread-123'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'I will read the file first.'}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'git diff', 'exit_code': 0}},
            {'type': 'item.completed', 'item': {'type': 'web_search', 'query': 'python release'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'The final answer.'}},
            {'type': 'turn.completed'}))
        notes = []
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'ready'), \
                patch.object(b, 'run_process', return_value=(0, events, '')):
            answer = b.cli_reply('chatgpt', 'Review', '', self.base, self.config, task_id='topic',
                                 progress=lambda message, **fields: notes.append(message))
        self.assertEqual(answer, 'The final answer.')
        self.assertIn('Codex ran 2 read-only lookups before answering', notes)

    def test_sign_in_is_checked_every_turn_and_only_the_codex_version_check_is_remembered(self):
        executable = self.base / 'fake-cli.exe'
        executable.write_bytes(b'fixture')
        with patch.object(b, 'auth_status') as auth, \
                patch.object(b, 'run_process', return_value=(0, '--ignore-user-config', '')) as process:
            for _ in range(3):
                b.ready('chatgpt', [str(executable)], self.base, self.base)
            self.assertEqual((auth.call_count, process.call_count), (3, 2))
            b.ready('claude', [str(executable)], self.base, self.base)
            self.assertEqual((auth.call_count, process.call_count), (4, 2))
            os.utime(executable, (1000, 1000))  # An updated CLI is checked again.
            b.ready('chatgpt', [str(executable)], self.base, self.base)
            self.assertEqual(process.call_count, 4)
        # A login that changed to API billing after an earlier good check is still refused.
        with patch.object(b, 'run_process', return_value=(0, 'Logged in using an API key', '')):
            with self.assertRaises(b.BridgeError):
                b.ready('chatgpt', [str(executable)], self.base, self.base)

    def test_resumed_session_gets_the_current_rules_and_only_the_final_codex_message(self):
        b.SessionStore(self.base).set('chatgpt', 'topic', '00000000-0000-4000-8000-000000000000')
        events = '\n'.join(json.dumps(event) for event in (
            {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Working...'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Final answer'}}))
        with patch.object(b, 'executable_for', return_value=['fake']), patch.object(b, 'ready'), \
                patch.object(b, 'run_process', return_value=(0, events, '')) as process:
            answer = b.cli_reply('chatgpt', 'Continue', '', self.base, self.config, task_id='topic')
        self.assertEqual(answer, 'Final answer')
        command, prompt = process.call_args.args[0], process.call_args.args[3]
        self.assertIn('resume', command)
        self.assertIn('replacing any earlier rule', prompt)
        self.assertIn('never edit', prompt)

    def test_config_accepts_model_settings_and_rejects_bad_ones(self):
        path = self.base / 'bridge-config.json'
        b.atomic_json(path, {**self.config, 'codex_model': 'gpt-example', 'codex_effort': 'high', 'claude_model': 'opus'})
        captured = []

        def process(cmd, cwd, timeout, input_text=None, env=None):
            captured.append(cmd)
            return (0, '{"is_error":false,"result":"ok"}', '') if 'fake-claude' in cmd else \
                (0, '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n', '')
        for peer in b.PEERS:
            with patch.object(b, 'executable_for', return_value=['fake-' + peer]), patch.object(b, 'ready'), \
                    patch.object(b, 'run_process', side_effect=process):
                b.cli_reply(peer, 'Hello', '', self.base, b.config_for(self.base))
        claude, codex = captured
        self.assertEqual(claude[claude.index('--model') + 1], 'opus')
        self.assertEqual(codex[codex.index('--model') + 1], 'gpt-example')
        self.assertIn('model_reasoning_effort="high"', codex)
        for key, value in (('codex_model', '--oss'), ('claude_effort', 'high; rm'), ('peer_tools', 'yes'),
                           ('timeout_seconds', 5), ('codex_windows_sandbox', 'none')):
            b.atomic_json(path, {**self.config, key: value})
            with self.assertRaises(b.BridgeError):
                b.config_for(self.base)

    def test_readme_is_the_only_guide_and_setup_never_overwrites_it(self):
        readme = SCRIPT.parent / 'README.md'
        before = readme.read_bytes()
        self.assertEqual(b.guide(self.base), readme.read_text(encoding='utf-8'))
        with patch('builtins.print'):
            b.setup(self.base, refresh_docs=True)
        self.assertEqual((self.base / 'README.md').read_text(encoding='utf-8'), readme.read_text(encoding='utf-8'))
        self.assertIn('ask --from YOUR_NAME', (self.base / 'CLAUDE.md').read_text(encoding='utf-8'))
        self.assertIn('Note for ChatGPT', (self.base / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertEqual(readme.read_bytes(), before)

    # Image requests. Codex is replaced by a fake run that leaves a picture where Codex's image tool would.
    THREAD = '01a1ff29-4272-7870-8495-cd1070e8839e'

    def image_setup(self, caption='A blue square.', make_image=True, during_run=None):
        project = self.base / 'project'
        project.mkdir(exist_ok=True)
        home = self.base / 'codex-home'
        folder = home / 'generated_images' / self.THREAD
        folder.mkdir(parents=True, exist_ok=True)
        runs = []

        def run(command, cwd, timeout, prompt=None, env=None):
            if command[-1] == '-':
                runs.append((command, cwd, prompt))
                if make_image:
                    (folder / 'exec-1.png').write_bytes(b'\x89PNG fake')
                if during_run:
                    during_run()
                events = [{'type': 'thread.started', 'thread_id': self.THREAD}, {'type': 'turn.started'}]
                if caption:
                    events.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': caption}})
                events.append({'type': 'turn.completed'})
                return 0, '\n'.join(map(json.dumps, events)), ''
            return 0, '', ''

        patches = [patch.object(b, 'executable_for', return_value=['codex']), patch.object(b, 'ready'),
                   patch.object(b, 'run_process', side_effect=run), patch.dict(os.environ, {'CODEX_HOME': str(home)})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return project, runs

    def test_image_request_saves_a_new_file_through_codex_image_tool(self):
        project, runs = self.image_setup()
        item = b.mcp_call(self.base, 'claude', 'generate_image',
                          {'prompt': 'A blue square on white', 'save_to': 'art/square.png', 'wait_seconds': 10}, str(project))
        self.assertEqual(item['status'], 'answered', item)
        self.assertEqual(item['kind'], 'image')
        saved = project / 'art' / 'square.png'
        self.assertEqual(Path(item['image_path']), saved)
        self.assertEqual(saved.read_bytes(), b'\x89PNG fake')
        self.assertIn('A blue square.', item['answer'])
        self.assertIn(str(saved), item['answer'])
        command, cwd, prompt = runs[0]
        self.assertIn('features.image_generation=true', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('--ephemeral', command)
        self.assertNotEqual(Path(cwd).resolve(), project.resolve())
        self.assertTrue(prompt.startswith(b.IMAGE_RULES))
        self.assertTrue(prompt.endswith('A blue square on white'))
        # A retry of the same request returns it instead of refusing the file it created.
        record = b.read_json(self.base / 'data/requests' / (item['id'] + '.json'))
        again = b.api(self.base, '/api/requests', {key: record[key] for key in
                      ('id', 'sender', 'recipient', 'kind', 'question', 'save_to', 'workspace')})
        self.assertEqual(again['status'], 'answered')
        with self.assertRaises(b.BridgeError):
            b.api(self.base, '/api/requests', {key: record[key] for key in ('id', 'sender', 'recipient', 'question', 'workspace')})
        self.assertEqual(len(runs), 1)

    def test_image_never_replaces_a_file_and_takes_the_real_file_type(self):
        project, _ = self.image_setup(caption='')
        (project / 'pic.png').write_bytes(b'keep me')
        item = b.mcp_call(self.base, 'claude', 'generate_image',
                          {'prompt': 'A cat', 'save_to': 'pic.jpg', 'wait_seconds': 10}, str(project))
        self.assertEqual(item['status'], 'answered', item)
        self.assertEqual(Path(item['image_path']), project / 'pic-2.png')
        self.assertEqual((project / 'pic.png').read_bytes(), b'keep me')
        self.assertTrue(item['answer'].startswith('Saved the image to'))
        self.assertEqual(sorted(path.name for path in project.iterdir()), ['pic-2.png', 'pic.png'])  # No temporary file left.

    def test_image_request_rules(self):
        project, runs = self.image_setup()
        (project / 'taken.png').write_bytes(b'x')
        self.assertIn('generate_image', [tool['name'] for tool in b.mcp_tools('claude')])
        self.assertNotIn('generate_image', [tool['name'] for tool in b.mcp_tools('chatgpt')])
        with self.assertRaises(b.BridgeError):
            b.mcp_call(self.base, 'chatgpt', 'generate_image', {'prompt': 'x', 'save_to': 'a.png'}, str(project))
        for save_to in ('../outside.png', str(self.base / 'outside.png'), 'picture.gif', 'taken.png', 'folder/',
                        'taken.png:hidden.png', 'sub:dir/a.png'):
            with self.subTest(save_to=save_to), self.assertRaises(b.BridgeError):
                b.mcp_call(self.base, 'claude', 'generate_image', {'prompt': 'x', 'save_to': save_to}, str(project))
        with self.assertRaises(b.BridgeError):
            b.mcp_call(self.base, 'claude', 'generate_image', {'prompt': 'x', 'save_to': 'a.png'}, None)
        image = dict(sender='claude', recipient='chatgpt', kind='image', question='x', save_to='a.png', workspace=str(project))
        for change in ({'mode': 'manual'}, {'context': 'extra'}, {'task_id': 'topic'}, {'sender': 'user'}, {'kind': 'video'}):
            with self.subTest(change=change), self.assertRaises(b.BridgeError):
                b.api(self.base, '/api/requests', {**image, **change})
        with patch.dict(os.environ, {'AI_BRIDGE_WORKER': '1'}), self.assertRaises(b.BridgeError):
            b.mcp_call(self.base, 'claude', 'generate_image', {'prompt': 'x', 'save_to': 'a.png'}, str(project))
        self.assertEqual(runs, [])

    def test_image_failures_are_reported_and_save_nothing(self):
        project, _ = self.image_setup(caption='I cannot draw that.', make_image=False)
        item = b.mcp_call(self.base, 'claude', 'generate_image',
                          {'prompt': 'Something', 'save_to': 'none.png', 'wait_seconds': 10}, str(project))
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['error_code'], 'no_image')
        self.assertIn('I cannot draw that.', item['error'])
        self.assertFalse((project / 'none.png').exists())

    def test_image_is_not_saved_when_the_request_stopped_meanwhile(self):
        def give_up():
            path = next((self.base / 'data/requests').glob('*.json'))
            record = b.read_json(path)
            record.update(status='failed', error='Deadline passed')
            b.atomic_json(path, record)
        project, _ = self.image_setup(during_run=give_up)
        item = b.mcp_call(self.base, 'claude', 'generate_image',
                          {'prompt': 'Late', 'save_to': 'late.png', 'wait_seconds': 10}, str(project))
        self.assertEqual(item['status'], 'failed')
        time.sleep(.2)  # Let the worker finish after the record already failed.
        self.assertFalse((project / 'late.png').exists())
        self.assertNotIn('image_path', b.api(self.base, '/api/requests/' + item['id']))

    @unittest.skipUnless(os.name == 'nt', 'Directory junctions are a Windows feature')
    def test_image_is_not_saved_when_the_workspace_was_swapped_for_a_link(self):
        import _winapi
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()

        def swap():
            (self.base / 'project').rename(self.base / 'project-old')
            _winapi.CreateJunction(str(elsewhere), str(self.base / 'project'))
        project, _ = self.image_setup(during_run=swap)
        item = b.mcp_call(self.base, 'claude', 'generate_image',
                          {'prompt': 'Swapped', 'save_to': 'swap.png', 'wait_seconds': 10}, str(project))
        self.assertEqual(item['status'], 'failed', item)
        self.assertEqual(item['error_code'], 'save_failed')
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
