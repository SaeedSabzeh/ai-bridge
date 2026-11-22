import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'chatgpt-bridge',
  component: 'Pane',
  requestId: 'chatgpt-conversation',
  props: {
    title: 'Claude ↔ ChatGPT',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

const minutesAgo = (n: number) => new Date(Date.now() - n * 60000).toISOString()
const FILES: Record<string, object> = {
  'a.json': { id: 'a'.repeat(32), sender: 'claude', recipient: 'chatgpt', mode: 'auto', task_id: 'design',
              status: 'answered', created_at: minutesAgo(9), updated_at: minutesAgo(8),
              question: 'Cap rounds or not?', answer: 'Keep continuity; warn later.', error: null },
  'b.json': { id: 'b'.repeat(32), sender: 'claude', recipient: 'chatgpt', mode: 'auto', task_id: 'design',
              status: 'running', created_at: minutesAgo(1), updated_at: minutesAgo(1),
              question: 'Does your pick change?', answer: null, error: null },
  'c.json': { id: 'c'.repeat(32), sender: 'chatgpt', recipient: 'claude', mode: 'manual', task_id: 'handoff',
              status: 'queued', created_at: minutesAgo(0.5), updated_at: minutesAgo(0.5),
              question: 'Brief me on the cleanup.', answer: null, error: null },
  'd.json': { id: 'd'.repeat(32), sender: 'chatgpt', recipient: 'claude', mode: 'auto', task_id: 'x',
              status: 'failed', created_at: minutesAgo(30), updated_at: minutesAgo(30),
              question: 'Old question', answer: null, error: 'claude CLI was not found.' },
}

test('cards show speakers, colored status chips and rounds on every surface', async ($, on) => {
  // Nothing sits beneath the plugin: these hooks stand for the engine and the bridge folder.
  on('command.register', () => ({ value: { command: 'chatgpt' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('fs.list', () => ({
    value: Object.keys(FILES).map(name => ({ name, kind: 'file', size: 1, mtimeMs: 1, isLink: false })),
  }))
  // The engine hands over Windows paths with backslashes.
  on('fs.read', (_, e) => ({ value: JSON.stringify(FILES[String(e.path).split(/[\\/]/).pop() ?? '']) }))
  // The rescue may start a worker for an old queued auto question; none here is one.
  on('process.run', () => ({ value: { exitCode: 0, stdout: JSON.stringify({ root: '/bridge', base: '/bridge', python: 'python' }),
    stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })

  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: /^ Answered $/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^ Working $/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^ Waiting in inbox $/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^ Failed $/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /round 2/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /ChatGPT's reply/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Waiting in Claude's inbox/ })).toBeDefined()
    // Newest first: the inbox question (30 s old) is drawn before the oldest, failed one.
    const chips = (await ui.findAll({ type: 'Text', text: /^ (Answered|Working|Waiting in inbox|Failed) $/ })).map(t => t.text.trim())
    expect(chips).toEqual(['Waiting in inbox', 'Working', 'Answered', 'Failed'])
    await ui.unmount()
  }
})

const HEX = 'f'.repeat(32)
const TOPIC_FILES: Record<string, object> = {
  'e.json': { id: '1'.repeat(32), sender: 'claude', recipient: 'chatgpt', mode: 'auto', task_id: HEX, topic: 'Experiment review',
              status: 'answered', created_at: minutesAgo(9), updated_at: minutesAgo(8),
              question: 'First question', answer: 'First answer', error: null },
  'f.json': { id: '2'.repeat(32), sender: 'claude', recipient: 'chatgpt', mode: 'auto', task_id: HEX,
              status: 'answered', created_at: minutesAgo(5), updated_at: minutesAgo(4),
              question: 'Follow-up question', answer: 'Follow-up answer', error: null },
  'g.json': { id: '3'.repeat(32), sender: 'claude', recipient: 'chatgpt', mode: 'auto', task_id: '9'.repeat(32),
              status: 'answered', created_at: minutesAgo(2), updated_at: minutesAgo(1),
              question: 'Untitled question', answer: 'Untitled answer', error: null },
}

test('cards show the topic label instead of a hex task ID', async ($, on) => {
  on('command.register', () => ({ value: { command: 'chatgpt' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('fs.list', () => ({
    value: Object.keys(TOPIC_FILES).map(name => ({ name, kind: 'file', size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => ({ value: JSON.stringify(TOPIC_FILES[String(e.path).split(/[\\/]/).pop() ?? '']) }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: JSON.stringify({ root: '/bridge', base: '/bridge', python: 'python' }),
    stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  // The record that carries a topic shows it, and a follow-up in the same task without one reuses it.
  expect((await ui.findAll({ type: 'Text', text: /Experiment review/ })).length).toBe(2)
  // A hex task ID with no topic anywhere is never shown.
  expect(await ui.findAll({ type: 'Text', text: /[0-9a-f]{32}/ })).toHaveLength(0)
  await ui.unmount()
})
