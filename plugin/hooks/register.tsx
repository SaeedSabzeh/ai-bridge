import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Exchange } from '../types'

const DISCOVER = `
import json, os, sys
from pathlib import Path

def valid(folder):
    return (folder / 'ai_bridge.py').is_file() and (folder / 'peer_sessions.py').is_file()

cwd = Path.cwd()
override = os.environ.get('AI_BRIDGE_HOME')
folders = [Path(override).expanduser()] if override else [cwd, *cwd.parents]
root = next((folder.resolve() for folder in folders if valid(folder)), None)
base = root
if root is None and not override:
    try:
        settings = json.loads((Path.home() / '.claude.json').read_text(encoding='utf-8-sig'))
        args = settings.get('mcpServers', {}).get('local-ai-bridge', {}).get('args', [])
        script = next((Path(arg) for arg in args if Path(arg).name == 'ai_bridge.py'), None)
        if script and valid(script.parent):
            root = script.resolve().parent
            base = Path(args[args.index('--data-dir') + 1]).resolve() if '--data-dir' in args else root
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        pass
if root is None:
    raise SystemExit('Bridge location not found.')
print(json.dumps(dict(root=str(root), base=str(base), python=sys.executable)))
`
type BridgeLocation = { root: string; base: string; python: string }
let location: BridgeLocation | undefined
// A question still queued this long after it was sent gets its worker started from here:
// Codex's Windows sandbox can stop the worker it starts for questions addressed to Claude.
const GRACE_MS = 8000
// Starts a detached bridge worker that outlives this plugin; args: bridge folder, request ID.
const LAUNCH = 'import sys, pathlib, ai_bridge; ai_bridge.launch_worker(pathlib.Path(sys.argv[1]), sys.argv[2])'
const PANE = 'chatgpt-conversation'
const TITLE = 'Claude ↔ ChatGPT'
const NAMES: Record<string, string> = { claude: 'Claude', chatgpt: 'ChatGPT', user: 'You' }
const STEP = 6

const exchanges = atom({ plugin: 'chatgpt-bridge', key: 'exchanges' } as const, [], { shape: 'exchanges-3' })
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
// Each speaker keeps one color everywhere: card border, name and reply heading. Theme keys follow light/dark mode.
const COLORS: Record<string, 'claude' | 'suggestion' | 'permission'> = { claude: 'claude', chatgpt: 'suggestion', user: 'permission' }
const colorOf = (who: string) => COLORS[who] ?? 'subtle'
const waiting = (item: Exchange) => item.status === 'queued' || item.status === 'running'
// A manual question waits for the recipient's live session to answer it from its inbox; no worker runs.
const inInbox = (item: Exchange) => item.status === 'queued' && item.mode === 'manual'
const pending = (item: Exchange) =>
  inInbox(item) ? `Waiting in ${name(item.to)}'s inbox (manual question)` :
    item.status === 'queued' ? `Waiting for ${name(item.to)}` : `${name(item.to)} is working…`

// The colored chip in each card's corner.
function status(item: Exchange): { label: string; color: 'success' | 'warning' | 'suggestion' | 'error' | 'inactive' } {
  if (item.status === 'answered') return { label: 'Answered', color: 'success' }
  if (inInbox(item)) return { label: 'Waiting in inbox', color: 'suggestion' }
  if (item.status === 'queued') return { label: 'Waiting', color: 'warning' }
  if (item.status === 'running') return { label: 'Working', color: 'warning' }
  if (item.status === 'cancelled') return { label: 'Cancelled', color: 'inactive' }
  return { label: 'Failed', color: 'error' }
}

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
    topic: text(data.topic), errorCode: text(data.error_code), continuity: text(data.continuity), workspace: text(data.workspace),
  }
}

async function discover($: EngineInterface, cwd: string) {
  for (const launcher of [['python'], ['py', '-3']]) {
    try {
      const run = await $.process.run([...launcher, '-c', DISCOVER], { cwd, timeoutMs: 5000 })
      if (run.exitCode === 0) {
        const value = JSON.parse(run.stdout)
        if (value.root && value.base && value.python) return value as BridgeLocation
      }
    } catch {
      // Try the Windows Python launcher if Python is not on PATH.
    }
  }
  await update($, problem, () => 'Bridge location not found. Open the bridge folder, register its MCP entry, or set AI_BRIDGE_HOME.')
}

// Re-reads only files whose modification time changed, then updates the list, status and toasts.
async function scan($: EngineInterface) {
  if (!location) return
  const requests = `${location.base}/data/requests`
  let entries
  try {
    entries = (await $.fs.list(requests)).filter(entry => entry.kind === 'file' && entry.name.endsWith('.json'))
  } catch {
    await update($, problem, () => 'The bridge mailbox could not be read. Run python ai_bridge.py doctor from the bridge folder.')
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
      const item = parse(await $.fs.read(`${requests}/${entry.name}`))
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
  if (!location) return
  const now = Date.now()
  for (const item of await read($, exchanges)) {
    const age = now - new Date(item.created).getTime()
    if (item.status !== 'queued' || item.mode !== 'auto' || !(age > GRACE_MS) || started.has(item.id)) continue
    started.add(item.id)
    try {
      const run = await $.process.run([location.python, '-c', LAUNCH, location.base, item.id], { cwd: location.root, timeoutMs: 20000 })
      if (run.exitCode !== 0) $.ui.toast(`Could not start the bridge worker: ${run.stderr.trim().slice(-160)}`)
    } catch (error) {
      $.ui.toast(`Could not start the bridge worker: ${String(error).slice(0, 160)}`)
    }
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'chatgpt', description: 'Show the Claude ↔ ChatGPT conversation panel' })
    location = await discover($, e.cwd)
    await scan($)
    await rescue($)
    $.clock.every(2500, async () => {
      if (location && (await read($, exchanges)).some(item => item.mode === 'auto' && waiting(item))) {
        const { root, base, python } = location
        try {
          await $.process.run([python, `${root}/ai_bridge.py`, '--data-dir', base, 'reconcile'], { cwd: root, timeoutMs: 4000 })
        } catch {
          // The mailbox scan still shows the last recorded state.
        }
      }
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
    // Newest first: the latest exchange sits at the top of the panel.
    const visible = list.slice(-count).reverse()
    const hidden = list.length - visible.length
    // Round number of each exchange within its topic, so a back-and-forth reads as one conversation.
    const rounds = new Map<string, number>()
    const seen = new Map<string, number>()
    for (const item of list) {
      const n = (seen.get(item.task) ?? 0) + 1
      seen.set(item.task, n)
      rounds.set(item.id, n)
    }
    // First topic label seen for each task, so follow-ups without a label still read well.
    const topics = new Map<string, string>()
    for (const item of list) if (item.topic && item.task && !topics.has(item.task)) topics.set(item.task, item.topic)
    const label = (item: Exchange) => {
      if (item.topic) return item.topic
      const known = topics.get(item.task)
      if (known) return known
      return item.task && !/^[0-9a-f]{32}$/.test(item.task) ? item.task : ''
    }

    return (
      <Box flexDirection="column" gap={1}>
        {issue && <Text color="error">{issue}</Text>}
        {!issue && list.length === 0 && (
          <Box flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
            <Text bold>No conversation yet.</Text>
            <Text dimColor>When Claude asks ChatGPT for an idea, a second opinion or a translation check, it shows up here.</Text>
          </Box>
        )}
        {visible.map(item => {
          const chip = status(item)
          const round = rounds.get(item.id) ?? 1
          return (
            <Box key={item.id} flexDirection="column" borderStyle="round" borderColor={colorOf(item.from)} paddingX={1}>
              <Box flexDirection="row" justifyContent="space-between" flexWrap="wrap" columnGap={1}>
                <Text>
                  <Text bold color={colorOf(item.from)}>{name(item.from)}</Text>
                  <Text dimColor>{' → '}</Text>
                  <Text bold color={colorOf(item.to)}>{name(item.to)}</Text>
                </Text>
                <Text backgroundColor={chip.color} color="inverseText" bold>{` ${chip.label} `}</Text>
              </Box>
              <Text dimColor>
                {when(item.created)}
                {label(item) ? `  ·  ${label(item)}` : ''}
                {item.task && round > 1 ? `  ·  round ${round}` : ''}
                {item.continuity === 'resumed' || item.continuity === 'recovered' ? `  ·  ${item.continuity} session` : ''}
                {item.workspace ? `  ·  can read ${item.workspace.split(/[\\/]/).filter(Boolean).pop() ?? item.workspace}` : ''}
              </Text>
              <Markdown text={clip(item.question, 1500)} />
              {item.answer ? (
                <Box flexDirection="column" marginTop={1}>
                  <Text bold color={colorOf(item.to)}>
                    {`${name(item.to)}'s reply`}
                    <Text dimColor>{'  ·  '}{when(item.updated)}</Text>
                  </Text>
                  <Markdown text={clip(item.answer, 6000)} />
                </Box>
              ) : waiting(item) ? (
                <Text color={chip.color}>{pending(item)}</Text>
              ) : (
                <Text color="error">{item.status === 'cancelled' ? 'Cancelled' : `No reply: ${item.error || item.status}`}</Text>
              )}
            </Box>
          )
        })}
        {hidden > 0 && (
          <Box flexDirection="row" gap={1}>
            <Button key="more" label={`Show ${Math.min(STEP, hidden)} earlier`} onPress={() => void update($, shown, n => n + STEP)} />
            <Text dimColor>{hidden} earlier</Text>
          </Box>
        )}
        {count > STEP && hidden === 0 && list.length > STEP && <Button key="less" label="Show fewer" onPress={() => void update($, shown, () => STEP)} />}
      </Box>
    )
  })
}
