import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../api/client'
import { Panel } from '../components/Panel'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return {
    ...original,
    api: { approvals: vi.fn(), traces: vi.fn(), trace: vi.fn(), decideApproval: vi.fn() },
  }
})

const approvals = vi.mocked(api.approvals)
const traces = vi.mocked(api.traces)
const trace = vi.mocked(api.trace)
const decide = vi.mocked(api.decideApproval)

const REFUND = {
  id: 'apr_1',
  client_id: 'cli_1',
  conversation_id: 'con_1',
  trace_id: 'tra_1',
  tool: 'emit_refund',
  args: { order_id: 'ped_77', amount: 49990, reason: 'producto roto' },
  status: 'pending',
  requester: 'agent',
  created: '2026-09-25T12:00:00Z',
}

const TRACE = {
  id: 'tra_1',
  client_id: 'cli_1',
  conversation_id: 'con_1',
  endpoint: '/chat/stream',
  provider: 'anthropic',
  model: 'claude-sonnet-5',
  steps: 5,
  tokens_input: 1200,
  tokens_output: 180,
  cost_usd: 0.0043,
  duration_ms: 2870,
  status: 'ok',
  flags: [],
  created: '2026-09-25T12:00:00Z',
}

beforeEach(() => {
  approvals.mockReset().mockResolvedValue({ approvals: [] })
  traces.mockReset().mockResolvedValue({ traces: [] })
  trace.mockReset()
  decide.mockReset().mockResolvedValue({ status: 'ok' })
})

describe('Panel', () => {
  it('lists what is waiting for a decision, with the amount in view', async () => {
    approvals.mockResolvedValue({ approvals: [REFUND] })

    render(<Panel />)

    const row = await screen.findByRole('article', { name: /emit_refund/i })
    expect(within(row).getByText('$ 49.990')).toBeInTheDocument()
    expect(within(row).getByText(/producto roto/)).toBeInTheDocument()
  })

  it('approving removes the approval from the list but keeps it on record with its reason', async () => {
    approvals.mockResolvedValue({ approvals: [REFUND] })
    decide.mockResolvedValue({
      status: 'ok',
      response: 'Quedó registrado el reembolso.',
      approval: {
        ...REFUND,
        status: 'approved',
        reason: '',
        decider: 'operadora',
        decided: '2026-09-25T12:05:00Z',
      },
    })

    render(<Panel />)
    await userEvent.click(await screen.findByRole('button', { name: /aprobar/i }))

    expect(decide).toHaveBeenCalledWith('apr_1', 'approved', '')
    await waitFor(() =>
      expect(screen.queryByRole('article', { name: /en revisión/i })).toBeNull(),
    )

    const resolved = await screen.findByRole('article', { name: /decisión aprobada/i })
    expect(within(resolved).getByText('Aprobada')).toBeInTheDocument()
    expect(within(resolved).getByText(/sin motivo anotado/i)).toBeInTheDocument()
    expect(within(resolved).getByText(/operadora/)).toBeInTheDocument()
    expect(within(resolved).getByText(/quedó registrado/i)).toBeInTheDocument()
  })

  it('rejecting lets you note why, and the reason stays in view', async () => {
    approvals.mockResolvedValue({ approvals: [REFUND] })

    render(<Panel />)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText(/motivo/i), 'el cliente no lo pidió')
    await user.click(screen.getByRole('button', { name: /rechazar/i }))

    expect(decide).toHaveBeenCalledWith('apr_1', 'rejected', 'el cliente no lo pidió')

    const resolved = await screen.findByRole('article', { name: /decisión rechazada/i })
    expect(within(resolved).getByText('Rechazada')).toBeInTheDocument()
    expect(within(resolved).getByText('el cliente no lo pidió')).toBeInTheDocument()
  })

  it('decides several and all of them stay, without overwriting each other', async () => {
    const other = { ...REFUND, id: 'apr_2', tool: 'emit_credit' }
    approvals.mockResolvedValue({ approvals: [REFUND, other] })
    decide.mockResolvedValue({ status: 'ok' })

    render(<Panel />)
    const user = userEvent.setup()
    const [first, second] = await screen.findAllByRole('article', { name: /en revisión/i })
    await user.click(within(first).getByRole('button', { name: /aprobar/i }))
    await waitFor(() =>
      expect(screen.queryAllByRole('article', { name: /en revisión/i })).toHaveLength(1),
    )
    await user.click(within(second).getByRole('button', { name: /aprobar/i }))

    expect(await screen.findAllByRole('article', { name: /decisión aprobada/i })).toHaveLength(2)
  })

  it('if the decision fails, the approval goes back to the list and nothing is recorded', async () => {
    approvals.mockResolvedValue({ approvals: [REFUND] })
    decide.mockRejectedValue(
      new ApiError(409, 'already_resolved', 'La aprobación ya estaba resuelta.'),
    )

    render(<Panel />)
    await userEvent.click(await screen.findByRole('button', { name: /aprobar/i }))

    expect(await screen.findByRole('article', { name: /en revisión/i })).toBeInTheDocument()
    expect(screen.getByText(/ya estaba resuelta/i)).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: /decisión/i })).toBeNull()
  })

  it('when the panel opens, decisions already made come back with their reason', async () => {
    // Without this, reloading the page erased the log: the state lived only in
    // the component and the reason for the decision was lost.
    const rejected = {
      ...REFUND,
      status: 'rejected',
      reason: 'ya lo pidió por otro canal',
      decider: 'operadora',
      decided: '2026-09-25T12:05:00Z',
    }
    approvals.mockResolvedValue({ approvals: [REFUND, rejected] })

    render(<Panel />)

    const resolved = await screen.findByRole('article', { name: /decisión rechazada/i })
    expect(approvals).toHaveBeenCalledWith('all')
    expect(within(resolved).getByText('ya lo pidió por otro canal')).toBeInTheDocument()
    expect(screen.getAllByRole('article', { name: /en revisión/i })).toHaveLength(1)
  })

  it('re-queries the queue on its own: a new approval appears without reloading', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      render(<Panel />)
      expect(await screen.findByText(/nada esperando/i)).toBeInTheDocument()

      approvals.mockResolvedValue({ approvals: [REFUND] })
      await vi.advanceTimersByTimeAsync(10_000)

      expect(await screen.findByRole('article', { name: /en revisión/i })).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('if nothing is waiting, it says so without noise', async () => {
    render(<Panel />)

    expect(await screen.findByText(/nada esperando/i)).toBeInTheDocument()
  })

  it('opening a trace shows its steps', async () => {
    traces.mockResolvedValue({ traces: [TRACE] })
    trace.mockResolvedValue({
      ...TRACE,
      spans: [
        { seq: 0, type: 'guardrail', name: 'guardrail_input', duration_ms: 3, status: 'ok' },
        {
          seq: 1,
          type: 'tool',
          name: 'emit_refund',
          duration_ms: 42,
          status: 'error',
          error: 'rechazada',
        },
      ],
    })

    render(<Panel />)
    await userEvent.click(await screen.findByRole('button', { name: /tra_1/ }))

    expect(trace).toHaveBeenCalledWith('tra_1')
    expect(await screen.findByText('rechazada')).toBeInTheDocument()
  })
})
