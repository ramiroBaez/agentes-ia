import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../api/client'
import { Conversation } from '../components/Conversation'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return {
    ...original,
    api: {
      turn: vi.fn(),
      feedback: vi.fn(),
      approval: vi.fn(),
    },
  }
})

const turn = vi.mocked(api.turn)
const feedback = vi.mocked(api.feedback)
const approval = vi.mocked(api.approval)

/** An SSE stream with the events the API sends, in a single body. */
function stream(events: { type: string; data: unknown }[]): Response {
  const body = events
    .map((e) => `data: ${JSON.stringify({ type: e.type, data: e.data })}\n\n`)
    .join('')
  return new Response(body, { headers: { 'content-type': 'text/event-stream' } })
}

const END = {
  status: 'ok',
  response: 'Tenemos auriculares Bluetooth a $49.990.',
  trace_id: 'tra_abc',
  conversation_id: 'con_1',
  client_id: 'cli_1',
  flags: [],
  approval: null,
  message_id: 'msg_1',
  spans: [],
}

async function send(text = '¿tenés auriculares?') {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Mensaje para el agente'), text)
  await user.click(screen.getByRole('button', { name: /enviar/i }))
  return user
}

describe('Conversation', () => {
  beforeEach(() => {
    turn.mockReset()
    feedback.mockReset()
    approval.mockReset()
    feedback.mockResolvedValue({ id: 'fb_1' })
  })

  it('shows the question and the agent answer', async () => {
    turn.mockResolvedValue(stream([{ type: 'end', data: END }]))

    render(<Conversation />)
    await send()

    expect(screen.getByText('¿tenés auriculares?')).toBeInTheDocument()
    expect(await screen.findByText('Tenemos auriculares Bluetooth a $49.990.')).toBeInTheDocument()
  })

  it('shows the agent steps as it answers', async () => {
    turn.mockResolvedValue(
      stream([
        {
          type: 'span',
          data: { seq: 0, name: 'guardrail_input', duration_ms: 3, status: 'ok' },
        },
        { type: 'span', data: { seq: 1, name: 'agent', duration_ms: 900, status: 'ok' } },
        { type: 'end', data: END },
      ]),
    )

    render(<Conversation />)
    await send()

    await waitFor(() => expect(screen.getByText('agent')).toBeInTheDocument())
    expect(screen.getByText('3 ms')).toBeInTheDocument()
  })

  it('warns when the case is left waiting for a person', async () => {
    turn.mockResolvedValue(
      stream([
        {
          type: 'end',
          data: {
            ...END,
            status: 'waiting_approval',
            response: 'Tu caso quedó en revisión humana: un operador lo revisa y te avisamos.',
            approval: { id: 'apr_1', tool: 'emit_refund', args: { amount: 49990 } },
          },
        },
      ]),
    )

    render(<Conversation />)
    await send('quiero un reembolso')

    await waitFor(() => expect(screen.getByText(/revisión humana/i)).toBeInTheDocument())
  })

  it('if the quota runs out, it says so clearly, without jargon', async () => {
    turn.mockRejectedValue(
      new ApiError(
        429,
        'limit_exceeded',
        'Llegaste al límite de consultas de tu plan. Probá en un minuto.',
      ),
    )

    render(<Conversation />)
    await send()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/límite/i))
  })

  it('a thumbs-up on an answer is sent to the API and marked', async () => {
    turn.mockResolvedValue(stream([{ type: 'end', data: END }]))

    render(<Conversation />)
    await send()
    const good = await screen.findByRole('button', { name: 'La respuesta me sirvió' })
    await userEvent.click(good)

    expect(feedback).toHaveBeenCalledWith('msg_1', 1, '')
  })

  it('Enter sends the message and shift+Enter makes a new line', async () => {
    turn.mockResolvedValue(stream([{ type: 'end', data: END }]))

    render(<Conversation />)
    const user = userEvent.setup()
    const box = screen.getByLabelText('Mensaje para el agente')
    await user.type(box, 'primera línea{Shift>}{Enter}{/Shift}segunda')
    await user.type(box, '{Enter}')

    expect(turn).toHaveBeenCalledWith('primera línea\nsegunda', undefined)
  })

  describe('when an operator decides', () => {
    // A function: a Response body can only be read once.
    const waiting = () =>
      stream([
        {
          type: 'end',
          data: {
            ...END,
            status: 'waiting_approval',
            response: 'Tu caso quedó en revisión humana: un operador lo revisa y te avisamos.',
            approval: { id: 'apr_1', tool: 'emit_refund', args: { amount: 49990 } },
          },
        },
      ])
    const DECIDED = {
      id: 'apr_1',
      client_id: 'cli_1',
      tool: 'emit_refund',
      args: { amount: 49990 },
      created: '2026-09-25T12:00:00Z',
    }

    it('updates the "waiting" notice and reports the decision with its reason', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        turn.mockResolvedValue(waiting())
        approval.mockResolvedValue({
          approval: {
            ...DECIDED,
            status: 'rejected',
            reason: 'ya lo pidió por otro canal',
            decider: 'ana',
          },
          response:
            'Un operador revisó tu pedido y por ahora no aprobó el reembolso: ya lo pidió por otro canal.',
        })

        render(<Conversation />)
        await send('quiero un reembolso')
        expect(await screen.findByText(/esperando aprobación/i)).toBeInTheDocument()

        await vi.advanceTimersByTimeAsync(3_000)

        expect(await screen.findByText(/reembolso rechazado por ana/i)).toBeInTheDocument()
        expect(screen.getByText(/Motivo: ya lo pidió por otro canal/)).toBeInTheDocument()
        expect(screen.queryByText(/esperando aprobación/i)).toBeNull()
      } finally {
        vi.useRealTimers()
      }
    })

    it('if the decision has no response yet, it keeps waiting', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        turn.mockResolvedValue(waiting())
        approval.mockResolvedValue({
          approval: { ...DECIDED, status: 'approved', reason: 'ok' },
          response: null,
        })

        render(<Conversation />)
        await send('quiero un reembolso')
        await screen.findByText(/esperando aprobación/i)
        await vi.advanceTimersByTimeAsync(3_000)

        expect(screen.getByText(/esperando aprobación/i)).toBeInTheDocument()
        expect(screen.queryByText(/Decisión:/)).toBeNull()
      } finally {
        vi.useRealTimers()
      }
    })
  })
})
