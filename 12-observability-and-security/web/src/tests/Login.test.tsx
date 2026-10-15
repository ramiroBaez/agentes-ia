import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../api/client'
import { Login } from '../components/Login'

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: { login: vi.fn() } }
})

const login = vi.mocked(api.login)

describe('Login', () => {
  beforeEach(() => {
    login.mockReset().mockResolvedValue(undefined)
  })

  it('asks for the API key and signs in', async () => {
    const user = userEvent.setup()
    render(<Login onLogin={() => {}} />)

    await user.type(screen.getByLabelText(/API key/i), 'ak_123')
    await user.click(screen.getByRole('button', { name: /entrar/i }))

    expect(login).toHaveBeenCalledWith('ak_123')
  })

  it('does not allow submitting an empty key', () => {
    render(<Login onLogin={() => {}} />)

    expect(screen.getByRole('button', { name: /entrar/i })).toBeDisabled()
  })

  it('if the key is no good, it says so without blaming the user', async () => {
    login.mockRejectedValue(
      new ApiError(401, 'unauthenticated', 'Esa API key no existe o está desactivada.'),
    )
    const user = userEvent.setup()
    render(<Login onLogin={() => {}} />)

    await user.type(screen.getByLabelText(/API key/i), 'ak_bad')
    await user.click(screen.getByRole('button', { name: /entrar/i }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/no existe o está desactivada/i),
    )
  })

  it('warns when the API is not running', async () => {
    login.mockRejectedValue(new Error('Failed to fetch'))
    const user = userEvent.setup()
    render(<Login onLogin={() => {}} />)

    await user.type(screen.getByLabelText(/API key/i), 'ak_123')
    await user.click(screen.getByRole('button', { name: /entrar/i }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/no está levantada/i))
  })

  it('the key does not stay written on screen when it fails', async () => {
    login.mockRejectedValue(new ApiError(401, 'unauthenticated', 'No.'))
    const user = userEvent.setup()
    render(<Login onLogin={() => {}} />)

    await user.type(screen.getByLabelText(/API key/i), 'ak_bad')
    await user.click(screen.getByRole('button', { name: /entrar/i }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByLabelText(/API key/i)).toHaveValue('')
  })
})
