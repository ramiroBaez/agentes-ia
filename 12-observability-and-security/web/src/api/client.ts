import type { Approval, Trace } from './types'

/** The API hangs off `/api`: Vite and nginx forward it to the backend. */
const BASE = '/api'

/** An API error, already translated into something that can be shown. */
export class ApiError extends Error {
  readonly code: number
  readonly reason: string

  constructor(code: number, reason: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.reason = reason
  }
}

const SESSION_EXPIRED = 'Tu sesión venció. Volvé a entrar con tu API key.'

/**
 * The errors the API defines, in the language of whoever will read them.
 *
 * The API is deliberately terse (`reason: limit_exceeded`); the text we read on
 * screen is much more specific, because "something failed" helps nobody. The
 * keys are the `reason` codes of `security.py` and `api.py`.
 */
const TRANSLATIONS: Record<string, string> = {
  unauthenticated: SESSION_EXPIRED,
  without_token: SESSION_EXPIRED,
  malformed_token: SESSION_EXPIRED,
  signature_invalid: SESSION_EXPIRED,
  expired: SESSION_EXPIRED,
  without_permission: 'Tu clave no tiene permiso para esa operación.',
  invalid_csrf: 'Faltó el token de seguridad de la página. Recargá y probá de nuevo.',
  limit_exceeded: 'Llegaste al límite de consultas de tu plan. Probá en un minuto.',
  too_many_attempts: 'Demasiados intentos. Esperá un momento y probá de nuevo.',
  already_resolved: 'Esa aprobación ya estaba resuelta: no la volvés a aplicar.',
  not_found: 'No encontramos lo que buscabas.',
}

function translate(code: number, reason: string, message: string): string {
  const known = TRANSLATIONS[reason]
  if (known) return known
  if (code >= 500) return 'No pude completar la operación. Probá de nuevo en un momento.'
  return message || 'No pude completar la operación.'
}

/** Name of the CSRF cookie, the same one `settings.py` defines (`NAME_COOKIE_CSRF`). */
const CSRF_COOKIE = 'csrf'

/** Name of the CSRF header, the same one `settings.py` defines (`CSRF_HEADER`). */
const CSRF_HEADER = 'X-CSRF-Token'

function cookie(name: string): string {
  const prefix = `${name}=`
  const found = document.cookie.split('; ').find((c) => c.startsWith(prefix))
  return found ? decodeURIComponent(found.slice(prefix.length)) : ''
}

type Options = { method?: 'GET' | 'POST' | 'DELETE'; body?: unknown; apiKey?: string }

async function request<T>(path: string, options: Options = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { accept: 'application/json' }
  if (options.body !== undefined) headers['content-type'] = 'application/json'
  if (options.apiKey) headers.Authorization = `Bearer ${options.apiKey}`
  // The backend requires the CSRF token on every write (double-submit cookie).
  if (method !== 'GET') headers[CSRF_HEADER] = cookie(CSRF_COOKIE)

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  return parse<T>(response)
}

async function parse<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T
  let reason = 'error'
  let message = ''
  try {
    const body = (await response.json()) as { error?: { reason?: string; message?: string } }
    reason = body.error?.reason ?? reason
    message = body.error?.message ?? ''
  } catch {
    // No JSON (an nginx 502, for example): we stick with the status code.
  }
  throw new ApiError(response.status, reason, translate(response.status, reason, message))
}

export const api = {
  /** Who is signed in right now: if the cookie is still alive, go straight in. */
  async me() {
    return request<{ client_id: string; name: string; scopes: string[]; plan: string }>('/session')
  },

  async login(apiKey: string) {
    try {
      await request('/session', { method: 'POST', apiKey })
    } catch (failure) {
      // At login a 401 means the key itself is wrong (there is no session yet
      // that could have expired), so it gets a more precise message.
      if (failure instanceof ApiError && failure.code === 401) {
        throw new ApiError(401, failure.reason, 'Esa API key no existe o está desactivada.')
      }
      throw failure
    }
  },

  async logout() {
    await request('/session', { method: 'DELETE' })
  },

  async health() {
    return request<{ status: string; version: string; service: string }>('/health')
  },

  /** The raw turn: the body is an SSE stream, read by `readSSE`. */
  async turn(message: string, conversationId?: string): Promise<Response> {
    const response = await fetch(`${BASE}/chat/stream`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        [CSRF_HEADER]: cookie(CSRF_COOKIE),
      },
      credentials: 'same-origin',
      body: JSON.stringify({ message, conversation_id: conversationId ?? null }),
    })
    if (!response.ok) await parse(response)
    return response
  },

  async traces(limit = 50) {
    return request<{ traces: Trace[] }>(`/traces?limit=${limit}`)
  },

  async trace(traceId: string) {
    return request<Trace>(`/traces/${traceId}`)
  },

  async approvals(status: 'pending' | 'approved' | 'rejected' | 'all' = 'pending') {
    return request<{ approvals: Approval[] }>(`/approvals?status=${status}`)
  },

  /** How an approval is going; once resolved, it brings what the agent answered the client. */
  async approval(approvalId: string) {
    return request<{ approval: Approval; response: string | null }>(`/approvals/${approvalId}`)
  },

  async decideApproval(approvalId: string, decision: 'approved' | 'rejected', note = '') {
    return request<{
      status: string
      response?: string
      trace_id?: string
      /** The already-resolved approval: carries `reason`, `decided` and `decider`. */
      approval?: Approval
    }>(`/approvals/${approvalId}/decision`, {
      method: 'POST',
      body: { decision, note },
    })
  },

  async feedback(messageId: string, value: 1 | -1, comment = '') {
    return request<{ id: string }>('/feedback', {
      method: 'POST',
      body: { message_id: messageId, value, comment },
    })
  },
}
