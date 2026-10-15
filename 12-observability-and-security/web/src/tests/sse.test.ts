import { describe, expect, it, vi } from 'vitest'

import { readSSE } from '../api/sse'

/** A response body that arrives in chunks, the way it really does over the network. */
function body(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

/** An event as `api.py` writes it: the type goes inside the JSON. */
function event(type: string, data: unknown): string {
  return `data: ${JSON.stringify({ type, data })}\n\n`
}

describe('readSSE', () => {
  it('delivers events with the type carried by the JSON', async () => {
    const received: { type: string; data: Record<string, unknown> }[] = []

    await readSSE(
      body([
        event('span', { seq: 0, name: 'guardrail_input', duration_ms: 3 }),
        event('end', { response: 'hola' }),
      ]),
      (e) => received.push(e),
    )

    expect(received).toEqual([
      { type: 'span', data: { seq: 0, name: 'guardrail_input', duration_ms: 3 } },
      { type: 'end', data: { response: 'hola' } },
    ])
  })

  it('also understands the spec event: field', async () => {
    const received: string[] = []

    await readSSE(
      body(['event: end\ndata: {"response":"hola"}\n\n']),
      (e) => received.push(e.type),
    )

    expect(received).toEqual(['end'])
  })

  it('reassembles an event that arrives split into two chunks', async () => {
    const received: string[] = []
    const whole = event('end', { response: 'hola' })

    await readSSE(
      body([whole.slice(0, 12), whole.slice(12)]),
      (e) => received.push(e.type),
    )

    expect(received).toEqual(['end'])
  })

  it('a data: line that is not JSON does not take down the stream', async () => {
    const received: { type: string; data: Record<string, unknown> }[] = []

    await readSSE(
      body(['data: no soy json\n\n', event('end', { response: 'hola' })]),
      (e) => received.push(e),
    )

    expect(received[1]).toEqual({ type: 'end', data: { response: 'hola' } })
  })

  it('discards keep-alive comments', async () => {
    const received: string[] = []

    await readSSE(
      body([': keep-alive\n\n', event('end', {})]),
      (e) => received.push(e.type),
    )

    expect(received).toEqual(['end'])
  })

  it('signals when the stream ends', async () => {
    const done = vi.fn()
    await readSSE(body([event('end', {})]), () => {}, done)

    expect(done).toHaveBeenCalledOnce()
  })
})
