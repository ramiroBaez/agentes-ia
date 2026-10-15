import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return {
    ...original,
    api: {
      login: vi.fn(),
      logout: vi.fn(),
      me: vi.fn(),
      health: vi.fn(),
      turn: vi.fn(),
      feedback: vi.fn(),
      approvals: vi.fn(),
      traces: vi.fn(),
      trace: vi.fn(),
      decideApproval: vi.fn(),
    },
  }
})

const { api } = await import('../api/client')
const me = vi.mocked(api.me)
const health = vi.mocked(api.health)
const logout = vi.mocked(api.logout)
const turn = vi.mocked(api.turn)

const OPS = { client_id: 'cli_1', name: 'Ops', scopes: ['admin:all'], plan: 'pro' }

beforeEach(() => {
  me.mockReset().mockRejectedValue(new Error('no session'))
  health.mockReset().mockResolvedValue({ status: 'ok', version: '1.0.0', service: 'agent' })
  logout.mockReset().mockResolvedValue(undefined)
  turn.mockReset()
  vi.mocked(api.approvals).mockReset().mockResolvedValue({ approvals: [] })
  vi.mocked(api.traces).mockReset().mockResolvedValue({ traces: [] })
})

describe('App', () => {
  it('without a live cookie, it asks for the API key', async () => {
    render(<App />)

    expect(await screen.findByLabelText(/API key/i)).toBeInTheDocument()
  })

  it('with a live cookie, it goes straight to the conversation', async () => {
    me.mockResolvedValue(OPS)
    render(<App />)

    expect(await screen.findByLabelText('Mensaje para el agente')).toBeInTheDocument()
    // The name of whoever signed in is in view: this is an operator console.
    expect(screen.getByText('Ops')).toBeInTheDocument()
  })

  it('the panel is one click away, and only for whoever can see traces', async () => {
    me.mockResolvedValue(OPS)
    const user = userEvent.setup()
    render(<App />)
    await screen.findByLabelText('Mensaje para el agente')

    await user.click(screen.getByRole('tab', { name: /panel/i }))

    expect(await screen.findByText(/nada esperando/i)).toBeInTheDocument()
  })

  it('a chat client does not see the panel', async () => {
    me.mockResolvedValue({ ...OPS, scopes: ['chat:write'] })
    render(<App />)

    await screen.findByLabelText('Mensaje para el agente')
    expect(screen.queryByRole('tab', { name: /panel/i })).toBeNull()
  })

  it('an operator with traces:read sees the panel without being admin', async () => {
    // The scope the API requires is `traces:read`: if the screen checks a
    // differently spelled one, the tab hides from an operator who can see it.
    me.mockResolvedValue({ ...OPS, scopes: ['chat:write', 'traces:read'] })
    render(<App />)

    await screen.findByLabelText('Mensaje para el agente')
    expect(screen.getByRole('tab', { name: /panel/i })).toBeInTheDocument()
  })

  it('signing out clears the session and goes back to the login', async () => {
    me.mockResolvedValue(OPS)
    me.mockResolvedValueOnce(OPS).mockRejectedValueOnce(new Error('no session'))
    const user = userEvent.setup()
    render(<App />)
    await screen.findByLabelText('Mensaje para el agente')

    await user.click(screen.getByRole('button', { name: /salir/i }))

    expect(logout).toHaveBeenCalled()
    await waitFor(() => expect(screen.getByLabelText(/API key/i)).toBeInTheDocument())
  })

  it('if the API does not respond, it says so at the top and does not stay mute', async () => {
    health.mockRejectedValue(new Error('Failed to fetch'))
    me.mockResolvedValue(OPS)
    render(<App />)

    expect(await screen.findByText(/no está levantada/i)).toBeInTheDocument()
  })
})
