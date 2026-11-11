export type Exchange = {
  id: string
  mode: string
  from: string
  to: string
  task: string
  status: string
  created: string
  updated: string
  question: string
  answer: string
  error: string
  topic: string
  errorCode: string
  continuity: string
  workspace: string
}

declare module 'claude-code' {
  interface PluginState {
    'chatgpt-bridge': {
      exchanges: Shaped<Exchange[]>
      shown: Shaped<number>
      problem: Shaped<string>
    }
  }
}
