/** Events the API sends over SSE. */
export type SSEEvent = {
  type: string
  data: Record<string, unknown>
}

type OnEvent = (event: SSEEvent) => void

/**
 * Reads a `text/event-stream` and hands over each event already parsed.
 *
 * The API sends the type **inside** the JSON (`data: {"type": ..., "data": ...}`)
 * so the client can dispatch everything from a single `onmessage`; the spec's
 * `event:` field is accepted too, because some proxies and recorders add it.
 * Chunks arrive split anywhere (a line can fall between two chunks), so they
 * are accumulated until a blank line shows up, which is what separates one
 * event from the next.
 */
export async function readSSE(
  stream: ReadableStream<Uint8Array>,
  onEvent: OnEvent,
  onDone?: () => void,
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const process = (block: string) => {
    let fieldType = ''
    const lines: string[] = []
    for (const line of block.split('\n')) {
      // Each line of an SSE event is separated by \n (some servers send \r too).
      const clean = line.replace(/\r$/, '')
      if (clean.startsWith(':')) continue // comment: keep-alive
      if (clean.startsWith('event:')) {
        fieldType = clean.slice(6).trim()
      } else if (clean.startsWith('data:')) {
        lines.push(clean.slice(5).replace(/^ /, ''))
      }
    }
    if (lines.length === 0) return
    const raw = lines.join('\n')
    let data: unknown = {}
    try {
      data = JSON.parse(raw)
    } catch {
      // A `data:` that is not JSON must not take down the whole stream.
      data = { text: raw }
    }
    const object = (data ?? {}) as { type?: string } & Record<string, unknown>
    const { type: jsonType, ...rest } = object
    onEvent({
      type: jsonType ?? fieldType ?? 'message',
      data: (rest.data ?? rest) as Record<string, unknown>,
    })
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let cut = buffer.indexOf('\n\n')
      while (cut !== -1) {
        process(buffer.slice(0, cut))
        buffer = buffer.slice(cut + 2)
        cut = buffer.indexOf('\n\n')
      }
    }
    buffer += decoder.decode()
    if (buffer.trim()) process(buffer)
  } finally {
    reader.releaseLock()
    onDone?.()
  }
}
