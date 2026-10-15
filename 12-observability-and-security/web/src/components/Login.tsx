import { useState } from 'react'

import { ApiError, api } from '../api/client'

/**
 * The sign-in screen: the API key.
 *
 * The key belongs to a client, not to a person: there are no users or
 * passwords in this project. The input is of type password and is cleared as
 * soon as it is used, so it does not stay written on a shared screen.
 */
export function Login({ onLogin }: { onLogin: () => void }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)

  async function signIn() {
    if (!key.trim() || signingIn) return
    setSigningIn(true)
    setError(null)
    try {
      await api.login(key.trim())
      setKey('')
      onLogin()
    } catch (failure) {
      setKey('')
      if (failure instanceof ApiError) setError(failure.message)
      else if (failure instanceof Error) setError('La API no está levantada o no responde.')
      else setError('No pude entrar.')
    } finally {
      setSigningIn(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-6 py-10">
      <div className="card p-8 shadow-2xl shadow-black/40">
        <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.2em] text-signal">
          <span className="h-2 w-2 rounded-full bg-signal" aria-hidden="true" />
          Aurora Shop Ops
        </p>
        <h1 className="mt-3 text-2xl font-semibold">Entrá con tu API key</h1>
        <p className="mt-2 text-sm text-slate">
          Es la llave que te dio el despliegue. Se canjea por una cookie de sesión y no se vuelve a
          mostrar.
        </p>

        <form
          className="mt-6 space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            void signIn()
          }}
        >
          <label className="block text-sm text-slate" htmlFor="api-key">
            API key
          </label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="field font-mono"
          />
          <button
            type="submit"
            disabled={!key.trim() || signingIn}
            className="button button-primary w-full"
          >
            {signingIn ? 'Entrando…' : 'Entrar'}
          </button>
        </form>

        {error ? (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-alert/40 bg-alert/10 px-3 py-2 text-sm text-alert"
          >
            {error}
          </p>
        ) : null}
      </div>
    </main>
  )
}
