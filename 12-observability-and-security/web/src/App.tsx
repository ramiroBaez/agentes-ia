import { useEffect, useState } from 'react'

import { ApiError, api } from './api/client'
import { canSeePanel } from './api/types'
import { Conversation } from './components/Conversation'
import { Login } from './components/Login'
import { Panel } from './components/Panel'

type Session = { client_id: string; name: string; scopes: string[]; plan: string }

const TABS = [
  { id: 'conversation', label: 'Conversación' },
  { id: 'panel', label: 'Panel' },
] as const

type Tab = (typeof TABS)[number]['id']

/**
 * The console: sign-in, and then two tabs.
 *
 * There is no router or global state: the session is re-queried on open and
 * each view holds itself up. It is a single-screen tool, not an application
 * with ten of them.
 */
export function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [checking, setChecking] = useState(true)
  const [tab, setTab] = useState<Tab>('conversation')
  const [health, setHealth] = useState<{ status: string; version: string } | null>(null)
  const [down, setDown] = useState(false)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [identity, status] = await Promise.all([api.me(), api.health()])
        if (!alive) return
        setSession(identity)
        setHealth(status)
      } catch (failure) {
        if (!alive) return
        // 401 is normal when opening without a cookie; it is not an error to show.
        if (!(failure instanceof ApiError && failure.code === 401)) setDown(true)
      } finally {
        if (alive) setChecking(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  async function logout() {
    try {
      await api.logout()
    } finally {
      setSession(null)
    }
  }

  if (checking) {
    return (
      <p className="mx-auto mt-24 flex items-center justify-center gap-2 font-mono text-sm text-slate">
        <span className="h-2 w-2 animate-pulse rounded-full bg-signal" aria-hidden="true" />
        Buscando la API…
      </p>
    )
  }

  if (!session) {
    return (
      <>
        {down ? (
          <p
            role="alert"
            className="mx-auto mt-8 max-w-md rounded-lg border border-alert/40 bg-alert/10 px-3 py-2 text-sm text-alert"
          >
            La API no está levantada o no responde.
          </p>
        ) : null}
        <Login
          onLogin={async () => {
            setDown(false)
            setSession(await api.me())
          }}
        />
      </>
    )
  }

  const tabs = TABS.filter((t) => t.id !== 'panel' || canSeePanel(session.scopes))

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-10 border-b border-line bg-paper/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 sm:px-6">
          <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.2em]">
            <span
              className={`h-2 w-2 rounded-full ${down ? 'bg-alert' : 'animate-pulse bg-ok'}`}
              aria-hidden="true"
            />
            Aurora Shop Ops
          </p>
          <div role="tablist" aria-label="Secciones" className="flex gap-1">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`view-${t.id}`}
                onClick={() => setTab(t.id)}
                className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                  tab === t.id
                    ? 'bg-raised text-ink ring-1 ring-line'
                    : 'text-slate hover:text-ink'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-3 font-mono text-[11px] text-slate">
            {down ? (
              <span role="alert" className="text-alert">
                API caída
              </span>
            ) : health ? (
              <span>{health.version}</span>
            ) : null}
            <span className="chip text-ink">{session.name}</span>
            <span className="chip border-signal/40 text-signal">{session.plan}</span>
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded px-1 hover:text-alert"
            >
              Salir
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        <div
          role="tabpanel"
          id="view-conversation"
          aria-labelledby="tab-conversation"
          hidden={tab !== 'conversation'}
        >
          <Conversation />
        </div>
        <div
          role="tabpanel"
          id="view-panel"
          aria-labelledby="tab-panel"
          hidden={tab !== 'panel'}
        >
          <Panel />
        </div>
      </main>
    </div>
  )
}
