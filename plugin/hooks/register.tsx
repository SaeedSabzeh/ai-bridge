import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Exchange } from '../types'

// The local AI Bridge folder; it keeps one JSON file per question under data/requests.
const BRIDGE = 'C:/Users/SAEED/Desktop/ChatGPT/2026-11-07/new-chat-2/ai-bridge'
const REQUESTS = `${BRIDGE}/data/requests`
const PYTHON = 'C:/Users/SAEED/AppData/Local/Programs/Python/Python311/python.exe'
// A question still queued this long after it was sent gets its worker started from here:
// Codex's Windows sandbox can stop the worker it starts for questions addressed to Claude.
const GRACE_MS = 8000
// Starts a detached bridge worker that outlives this plugin; args: bridge folder, request ID.
const LAUNCH = 'import sys, pathlib; sys.path.insert(0, sys.argv[1]); import ai_bridge; ai_bridge.launch_worker(pathlib.Path(sys.argv[1]), sys.argv[2])'
const PANE = 'chatgpt-conversation'
const TITLE = 'Claude ↔ ChatGPT'
const NAMES: Record<string, string> = { claude: 'Claude', chatgpt: 'ChatGPT', user: 'You' }
const STEP = 6

const exchanges = atom({ plugin: 'chatgpt-bridge', key: 'exchanges' } as const, [], { shape: 'exchanges-2' })
const shown = atom({ plugin: 'chatgpt-bridge', key: 'shown' } as const, STEP, { shape: 'shown-1' })
const problem = atom({ plugin: 'chatgpt-bridge', key: 'problem' } as const, '', { shape: 'problem-1' })

// Module-local cache: file name → [mtime, parsed exchange]. Starts over on reload.
const cache = new Map<string, [number, Exchange]>()
let fingerprint = ''
// Requests this module already started a worker for; the bridge itself ignores a second start.
const started = new Set<string>()

const name = (who: string) => NAMES[who] ?? who
const text = (value: unknown) => (typeof value === 'string' ? value : value == null ? '' : String(value))
const clip = (value: string, max: number) => (value.length > max ? value.slice(0, max) + '\n\n…' : value)
const waiting = (item: Exchange) => item.status === 'queued' || item.status === 'running'
// A manual question waits for the recipient's live session to answer it from its inbox; no worker runs.
const inInbox = (item: Exchange) => item.status === 'queued' && item.mode === 'manual'
const pending = (item: Exchange) =>
  inInbox(item) ? `Waiting in ${name(item.to)}'s inbox (manual question)` : `${name(item.to)} is thinking…`

function when(stamp: string) {
  const date = new Date(stamp)
  if (Number.isNaN(date.getTime())) return stamp
  const today = new Date().toDateString() === date.toDateString()
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return today ? time : `${date.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`
}

function parse(raw: string): Exchange | undefined {
  const data = JSON.parse(raw)
  if (!data || typeof data.id !== 'string') return undefined
  return {
    id: data.id, mode: text(data.mode), from: text(data.sender), to: text(data.recipient), task: text(data.task_id),
    status: text(data.status), created: text(data.created_at), updated: text(data.updated_at),
    question: text(data.question), answer: text(data.answer), error: text(data.error),
  }
}

// Re-reads only files whose modification time changed, then updates the list, status and toasts.
async function scan($: EngineInterface) {
  let entries
  try {
    entries = (await $.fs.list(REQUESTS)).filter(entry => entry.kind === 'file' && entry.name.endsWith('.json'))
  } catch {
    await update($, problem, () => 'The bridge folder was not found. Check the path in the chatgpt-bridge mod.')
    return
  }
  const next = entries.map(entry => `${entry.name}:${entry.mtimeMs}`).sort().join('|')
  if (next === fingerprint) return
  fingerprint = next
  const before = await read($, exchanges)
  const list: Exchange[] = []
  for (const entry of entries) {
    const hit = cache.get(entry.name)
    if (hit && hit[0] === entry.mtimeMs) {
      list.push(hit[1])
      continue
    }
    try {
      const item = parse(await $.fs.read(`${REQUESTS}/${entry.name}`))
      if (item) {
        cache.set(entry.name, [entry.mtimeMs, item])
        list.push(item)
      }
    } catch {
      // A file being written right now; the next tick reads it.
    }
  }
  list.sort((a, b) => a.created.localeCompare(b.created))
  // Tell the person when a reply lands, without starting a turn.
  const old = new Map(before.map(item => [item.id, item]))
  for (const item of list) {
    const was = old.get(item.id)
    if (was && waiting(was) && !waiting(item)) {
      $.ui.toast(item.answer ? `${name(item.to)} replied` : `${name(item.to)}: request ${item.status}`)
    }
  }
  const busy = list.find(item => waiting(item) && !inInbox(item))
  // Only Claude's inbox is something Saeed can act on here ("answer ChatGPT's questions").
  const inbox = list.filter(item => inInbox(item) && item.to === 'claude').length
  $.ui.status(busy ? pending(busy) : inbox ? `Claude has ${inbox === 1 ? 'a question' : `${inbox} questions`} from ChatGPT waiting` : undefined)
  await update($, problem, () => '')
  await update($, exchanges, () => list)
}

// Starts the bridge worker for automatic questions nobody picked up, from outside any sandbox.
async function rescue($: EngineInterface) {
  const now = Date.now()
  for (const item of await read($, exchanges)) {
    const age = now - new Date(item.created).getTime()
    if (item.status !== 'queued' || item.mode !== 'auto' || !(age > GRACE_MS) || started.has(item.id)) continue
    started.add(item.id)
    try {
      const run = await $.process.run([PYTHON, '-c', LAUNCH, BRIDGE, item.id], { cwd: BRIDGE, timeoutMs: 20000 })
      if (run.exitCode !== 0) $.ui.toast(`Could not start the bridge worker: ${run.stderr.trim().slice(-160)}`)
    } catch (error) {
      $.ui.toast(`Could not start the bridge worker: ${String(error).slice(0, 160)}`)
    }
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'chatgpt', description: 'Show the Claude ↔ ChatGPT conversation panel' })
    await scan($)
    await rescue($)
    $.clock.every(2500, async () => {
      await scan($)
      await rescue($)
    })
    void $.ui.open({ id: PANE, title: TITLE })
    return next(e)
  })

  on('command.run', { command: 'chatgpt' }, async $ => {
    await scan($)
    await $.ui.open({ id: PANE, title: TITLE, focus: true })
    return { text: 'Opened the Claude ↔ ChatGPT panel.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Markdown } = $.ui.resolve(e)
    const list = await read($, exchanges)
    const count = await read($, shown)
    const issue = await read($, problem)
    const visible = list.slice(-count)
    const hidden = list.length - visible.length

    return (
      <Box flexDirection="column" gap={1}>
        {issue && <Text color="red">{issue}</Text>}
        {!issue && list.length === 0 && (
          <Box flexDirection="column">
            <Text bold>No conversation yet.</Text>
            <Text dimColor>When Claude asks ChatGPT for an idea, a second opinion or a translation check, it shows up here.</Text>
          </Box>
        )}
        {hidden > 0 && (
          <Box flexDirection="row" gap={1}>
            <Button key="more" label={`Show ${Math.min(STEP, hidden)} earlier`} onPress={() => void update($, shown, n => n + STEP)} />
            <Text dimColor>{hidden} earlier</Text>
          </Box>
        )}
        {visible.map(item => (
          <Box key={item.id} flexDirection="column">
            <Text>
              <Text bold>{name(item.from)} → {name(item.to)}</Text>
              <Text dimColor>  ·  {when(item.created)}{item.task ? `  ·  ${item.task}` : ''}</Text>
            </Text>
            <Markdown text={clip(item.question, 1500)} />
            {item.answer ? (
              <Box flexDirection="column" marginTop={1}>
                <Text bold color="green">{name(item.to)} replied  <Text dimColor>{when(item.updated)}</Text></Text>
                <Markdown text={clip(item.answer, 6000)} />
              </Box>
            ) : waiting(item) ? (
              <Text color="yellow">{pending(item)}</Text>
            ) : (
              <Text color="red">{item.status === 'cancelled' ? 'Cancelled' : `No reply: ${item.error || item.status}`}</Text>
            )}
          </Box>
        ))}
        {count > STEP && hidden === 0 && list.length > STEP && <Button key="less" label="Show fewer" onPress={() => void update($, shown, () => STEP)} />}
      </Box>
    )
  })
}
