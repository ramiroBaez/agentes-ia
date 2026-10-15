/**
 * The API contract, as the types the interface uses.
 *
 * They mirror what `api.py` returns; if a field changes there, it changes here.
 * Whatever the interface does not use is left out: there is no need to model
 * the whole database.
 */

/** One agent step, as it comes out of the trace (spans). */
export type Span = {
  seq: number
  type: 'node' | 'tool' | 'guardrail' | 'approval' | string
  name: string
  args?: unknown
  result?: string | null
  detail?: unknown
  duration_ms: number
  status: 'ok' | 'error' | string
  error?: string | null
}

/** A span as it travels over SSE: the closing one carries duration and status. */
export type LiveSpan = Pick<Span, 'seq' | 'name'> & {
  span_type: string
  trace_id: string
  duration_ms?: number
  status?: string
  error?: string | null
}

export type Trace = {
  id: string
  client_id?: string | null
  conversation_id?: string | null
  endpoint: string
  provider?: string | null
  model?: string | null
  steps: number
  tokens_input: number
  tokens_output: number
  cost_usd: number
  duration_ms: number
  status: string
  flags: string[]
  created: string
  spans?: Span[]
}

export type Approval = {
  id: string
  client_id: string
  conversation_id?: string | null
  trace_id?: string | null
  tool: string
  args: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected' | string
  requester?: string | null
  decider?: string | null
  reason?: string | null
  created: string
  decided?: string | null
}

export type Turn = {
  status: 'ok' | 'waiting_approval' | string
  response: string
  trace_id: string
  conversation_id: string
  client_id: string
  flags: string[]
  approval: Approval | null
  message_id?: string | null
  spans?: Span[]
}

export type Message = {
  id: string
  role: 'client' | 'agent' | 'system'
  text: string
  traceId?: string
  waitingApproval?: boolean
  /** The approval this message is waiting on: the chat learns about the decision through it. */
  approvalId?: string
  /** How that approval ended, once an operator has decided. */
  decision?: 'approved' | 'rejected'
  feedback?: 1 | -1
}

/**
 * The scopes the API requires, with the exact name it uses.
 *
 * They copy `SCOPES_CONOCIDOS` from `security.py`. If the interface checks a
 * scope spelled differently from the one the API validates, the screen hides
 * itself and nobody understands why: the key is fine and the panel is missing.
 */
export const SCOPES = {
  admin: 'admin:all',
  traces: 'traces:read',
  approvals: 'approvals:write',
  chat: 'chat:write',
} as const

/** Can this session see the traces and approvals panel? */
export function canSeePanel(scopes: string[]): boolean {
  return scopes.includes(SCOPES.admin) || scopes.includes(SCOPES.traces)
}
