import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../api/client'

/** An API response, shaped the way FastAPI returns it. */
function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('API client', () => {
  let calls: { url: string; init: RequestInit }[]

  beforeEach(() => {
    calls = []
    document.cookie = 'csrf=token-123'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function respondWith(respond: (url: string, init: RequestInit) => Response) {
    vi.stubGlobal('fetch', async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init })
      return respond(url, init)
    })
  }

  it('exchanges the API key for a browser session', async () => {
    respondWith(() => json({ status: 'ok' }))

    await api.login('test-key')

    expect(calls[0].url).toContain('/api/session')
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe(
      'Bearer test-key',
    )
  })

  it('sends the CSRF token on writes, which is what the API requires', async () => {
    respondWith(() => json({ status: 'ok' }))

    await api.decideApproval('apr_1', 'approved', 'looks fine')

    const headers = calls[0].init.headers as Record<string, string>
    expect(headers['X-CSRF-Token']).toBe('token-123')
    expect(calls[0].url).toContain('/api/approvals/apr_1/decision')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      decision: 'approved',
      note: 'looks fine',
    })
  })

  it('does not send CSRF on reads', async () => {
    respondWith(() => json({ traces: [] }))

    await api.traces()

    const headers = calls[0].init.headers as Record<string, string>
    expect(headers['X-CSRF-Token']).toBeUndefined()
    expect(calls[0].url).toContain('/api/traces?limit=50')
  })

  it('asks for all approvals with the status query param', async () => {
    respondWith(() => json({ approvals: [] }))

    await api.approvals('all')

    expect(calls[0].url).toContain('/api/approvals?status=all')
  })

  it('returns the turn stream without parsing it (SSE reads it)', async () => {
    const response = new Response('data: x\n\n', {
      headers: { 'content-type': 'text/event-stream' },
    })
    respondWith(() => response)

    const stream = await api.turn('hola', 'con_1')

    expect(stream).toBe(response)
    expect(calls[0].url).toContain('/api/chat/stream')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      message: 'hola',
      conversation_id: 'con_1',
    })
  })

  it('translates an expired session into an understandable message', async () => {
    respondWith(() =>
      json({ error: { code: 401, reason: 'unauthenticated', message: 'x' } }, 401),
    )

    const error = await api.traces().catch((e: ApiError) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe(401)
    expect((error as ApiError).message).toMatch(/sesión/i)
  })

  it('a rejected API key at login says the key is wrong', async () => {
    respondWith(() =>
      json({ error: { code: 401, reason: 'unauthenticated', message: 'x' } }, 401),
    )

    const error = (await api.login('bad-key').catch((e) => e)) as ApiError

    expect(error.code).toBe(401)
    expect(error.message).toMatch(/no existe o está desactivada/i)
  })

  it('translates the quota limit without asking the user for anything else', async () => {
    respondWith(() =>
      json({ error: { code: 429, reason: 'limit_exceeded', message: 'x' } }, 429),
    )

    const error = (await api.turn('hola').catch((e) => e)) as ApiError

    expect(error.code).toBe(429)
    expect(error.message).toMatch(/límite/i)
  })

  it('reports that the approval was already resolved and does not ask again', async () => {
    respondWith(() =>
      json({ error: { code: 409, reason: 'already_resolved', message: 'x' } }, 409),
    )

    const error = (await api.decideApproval('apr_1', 'approved').catch((e) => e)) as ApiError

    expect(error.code).toBe(409)
    expect(error.message).toMatch(/ya estaba/i)
  })

  it('translates a missing CSRF token', async () => {
    respondWith(() => json({ error: { code: 403, reason: 'invalid_csrf', message: 'x' } }, 403))

    const error = (await api.decideApproval('apr_1', 'approved').catch((e) => e)) as ApiError

    expect(error.message).toMatch(/token de seguridad/i)
  })

  it('if the API goes down, it does not show an internal error to the operator', async () => {
    respondWith(() => json({ error: { code: 500, reason: 'error', message: 'x' } }, 500))

    const error = (await api.traces().catch((e) => e)) as ApiError

    expect(error.code).toBe(500)
    expect(error.message).toMatch(/no pude/i)
    expect(error.message).not.toMatch(/Traceback|Error:/)
  })
})
