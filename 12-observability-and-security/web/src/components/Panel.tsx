import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { Approval, Trace } from '../api/types'
import { Waterfall, type Step } from './Waterfall'

const PESOS = new Intl.NumberFormat('es-AR', {
  style: 'currency',
  currency: 'ARS',
  maximumFractionDigits: 0,
})

const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 4,
})

/** How often the queue is re-queried: a new approval should not wait for an F5. */
const REFRESH_MS = 10_000

const DATE = new Intl.DateTimeFormat('es-AR', {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

/** A decision already made: it stays visible, it is not erased when resolved. */
type Decision = {
  approval: Approval
  decision: 'approved' | 'rejected'
  response?: string
}

function amountOf(args: Record<string, unknown>): string | null {
  const value = args.amount
  return typeof value === 'number' ? PESOS.format(value) : null
}

function summary(args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .filter(([key]) => key !== 'amount')
    .map(([key, value]) => `${key}: ${String(value)}`)
  return parts.join(' · ')
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return 'No pude cargar el panel.'
}

const TONES = {
  ok: 'text-ok',
  waiting: 'text-waiting',
  alert: 'text-alert',
  signal: 'text-signal',
  slate: 'text-slate',
} as const

function Indicator({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone: keyof typeof TONES
}) {
  return (
    <div className="card px-4 py-3">
      <dt className="text-xs text-slate">{label}</dt>
      <dd className={`mt-1 font-mono text-2xl font-semibold ${TONES[tone]}`}>{value}</dd>
    </div>
  )
}

/**
 * What the operator sees: what is waiting for a decision and what just happened.
 *
 * Approvals are the part that cannot fail silently: a waiting refund is money
 * on hold, so the row says what will be done, for how much and who asked for
 * it, before the buttons.
 */
export function Panel() {
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [traces, setTraces] = useState<Trace[]>([])
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [detail, setDetail] = useState<Trace | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const alive = useRef(true)

  const load = useCallback(async () => {
    try {
      // `all`: pending ones go to the queue, resolved ones to the decision
      // log. This way the reason for a decision survives a refresh.
      const [list, recent] = await Promise.all([api.approvals('all'), api.traces(20)])
      if (!alive.current) return
      setApprovals(list.approvals.filter((a) => a.status === 'pending'))
      setDecisions(
        list.approvals
          .filter((a) => a.status === 'approved' || a.status === 'rejected')
          .sort((a, b) => (b.decided ?? b.created).localeCompare(a.decided ?? a.created))
          .map((a) => ({
            approval: a,
            decision: a.status as 'approved' | 'rejected',
          })),
      )
      setTraces(recent.traces)
      setError(null)
    } catch (failure) {
      if (alive.current) setError(errorMessage(failure))
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    // The first load goes in a timeout: the effect only subscribes to the
    // timer, it does not update state synchronously.
    const start = setTimeout(() => void load(), 0)
    const timer = setInterval(() => void load(), REFRESH_MS)
    return () => {
      alive.current = false
      clearTimeout(start)
      clearInterval(timer)
    }
  }, [load])

  async function decide(approval: Approval, decision: 'approved' | 'rejected') {
    const note = (notes[approval.id] ?? '').trim()
    setApprovals((previous) => previous.filter((a) => a.id !== approval.id))
    try {
      const result = await api.decideApproval(approval.id, decision, note)
      const resolved = result.approval
      // The decision does not disappear: it moves to the resolved list with
      // its reason, so the operator can read what happened without auditing
      // the database.
      setDecisions((previous) => [
        {
          approval: resolved ?? {
            ...approval,
            status: decision,
            reason: note,
            decided: new Date().toISOString(),
            decider: 'operator',
          },
          decision,
          response: result.response,
        },
        ...previous,
      ])
    } catch (failure) {
      setApprovals((previous) => [approval, ...previous])
      setError(errorMessage(failure))
    }
  }

  async function openTrace(id: string) {
    try {
      setDetail(await api.trace(id))
    } catch (failure) {
      setError(errorMessage(failure))
    }
  }

  const withErrors = traces.filter((t) => t.status === 'error').length

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center gap-3">
        <dl className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
          <Indicator label="Pendientes" value={approvals.length} tone="waiting" />
          <Indicator label="Decididas" value={decisions.length} tone="ok" />
          <Indicator label="Trazas" value={traces.length} tone="signal" />
          <Indicator label="Con error" value={withErrors} tone={withErrors ? 'alert' : 'slate'} />
        </dl>
        <button
          type="button"
          onClick={() => void load()}
          className="button border border-line text-slate hover:text-ink"
        >
          Actualizar
        </button>
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-alert/40 bg-alert/10 px-3 py-2 text-sm text-alert"
        >
          {error}
        </p>
      ) : null}

      <section aria-labelledby="title-approvals">
        <h2
          id="title-approvals"
          className="font-mono text-xs uppercase tracking-[0.15em] text-slate"
        >
          Esperando tu decisión
        </h2>
        {approvals.length === 0 ? (
          <p className="mt-3 text-sm text-slate">
            {loading ? 'Buscando…' : 'Nada esperando tu decisión.'}
          </p>
        ) : (
          <ul className="mt-3 space-y-3">
            {approvals.map((approval) => {
              const money = amountOf(approval.args)
              return (
                <li key={approval.id}>
                  <article
                    aria-label={`${approval.tool} en revisión`}
                    className="card border-l-4 border-l-waiting p-4"
                  >
                    <header className="flex flex-wrap items-baseline gap-x-3">
                      <h3 className="font-mono text-sm">{approval.tool}</h3>
                      {money ? (
                        <span className="text-base font-semibold text-ink">{money}</span>
                      ) : null}
                      <span className="font-mono text-[11px] text-slate">{approval.id}</span>
                    </header>
                    {summary(approval.args) ? (
                      <p className="mt-1 font-mono text-[11px] text-slate">
                        {summary(approval.args)}
                      </p>
                    ) : null}
                    <div className="mt-3 flex flex-wrap items-end gap-2">
                      <label className="sr-only" htmlFor={`note-${approval.id}`}>
                        Motivo de la decisión
                      </label>
                      <input
                        id={`note-${approval.id}`}
                        value={notes[approval.id] ?? ''}
                        placeholder="motivo (opcional)"
                        onChange={(e) =>
                          setNotes((previous) => ({ ...previous, [approval.id]: e.target.value }))
                        }
                        className="field min-w-40 flex-1 !py-1.5 text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => void decide(approval, 'approved')}
                        className="button bg-ok/90 !py-1.5 text-xs text-[#04180f] hover:bg-ok"
                      >
                        Aprobar
                      </button>
                      <button
                        type="button"
                        onClick={() => void decide(approval, 'rejected')}
                        className="button border border-alert/60 !py-1.5 text-xs text-alert hover:bg-alert/10"
                      >
                        Rechazar
                      </button>
                    </div>
                  </article>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="title-decisions">
        <h2
          id="title-decisions"
          className="font-mono text-xs uppercase tracking-[0.15em] text-slate"
        >
          Decisiones tomadas
        </h2>
        {decisions.length === 0 ? (
          <p className="mt-3 text-sm text-slate">Todavía no decidiste nada.</p>
        ) : (
          <ul className="mt-3 space-y-3">
            {decisions.map(({ approval, decision, response }) => {
              const money = amountOf(approval.args)
              const approved = decision === 'approved'
              return (
                <li key={approval.id}>
                  <article
                    aria-label={`${approval.tool}: decisión ${approved ? 'aprobada' : 'rechazada'}`}
                    className={`card border-l-4 p-4 ${approved ? 'border-l-ok' : 'border-l-alert'}`}
                  >
                    <header className="flex flex-wrap items-baseline gap-x-3">
                      <span
                        className={`chip ${approved ? 'border-ok/40 text-ok' : 'border-alert/40 text-alert'}`}
                      >
                        {approved ? 'Aprobada' : 'Rechazada'}
                      </span>
                      <h3 className="font-mono text-sm">{approval.tool}</h3>
                      {money ? <span className="text-sm text-slate">{money}</span> : null}
                      {approval.decided ? (
                        <span className="font-mono text-[11px] text-slate">
                          {DATE.format(new Date(approval.decided))}
                        </span>
                      ) : null}
                    </header>
                    {approval.reason ? (
                      <p className="mt-1 text-sm">
                        <span className="text-slate">Motivo: </span>
                        {approval.reason}
                      </p>
                    ) : (
                      <p className="mt-1 text-sm text-slate">Sin motivo anotado.</p>
                    )}
                    {approval.decider ? (
                      <p className="mt-1 font-mono text-[11px] text-slate">
                        por {approval.decider}
                      </p>
                    ) : null}
                    {response ? (
                      <p className="mt-2 border-l border-line pl-2 text-[11px] text-slate">
                        {response}
                      </p>
                    ) : null}
                  </article>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="title-traces">
        <h2 id="title-traces" className="font-mono text-xs uppercase tracking-[0.15em] text-slate">
          Actividad reciente
        </h2>
        {traces.length === 0 ? (
          <p className="mt-3 text-sm text-slate">
            {loading ? 'Buscando…' : 'Todavía no hay trazas.'}
          </p>
        ) : (
          <ul className="card mt-3 divide-y divide-line overflow-hidden">
            {traces.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => void openTrace(t.id)}
                  className="flex w-full flex-wrap items-baseline gap-x-3 px-4 py-2.5 text-left hover:bg-raised"
                >
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${t.status === 'error' ? 'bg-alert' : 'bg-ok'}`}
                    aria-hidden="true"
                  />
                  <span className="font-mono text-xs">{t.id}</span>
                  <span className="font-mono text-[11px] text-slate">{t.endpoint}</span>
                  <span className="ml-auto font-mono text-[11px] text-slate">
                    {t.duration_ms} ms · {t.steps} pasos
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {detail ? (
        <section aria-labelledby="title-detail">
          <h2
            id="title-detail"
            className="font-mono text-xs uppercase tracking-[0.15em] text-slate"
          >
            Traza {detail.id}
          </h2>
          <p className="mt-1 font-mono text-[11px] text-slate">
            {detail.provider}/{detail.model} · {detail.tokens_input} in · {detail.tokens_output} out
            · {USD.format(detail.cost_usd)}
          </p>
          <div className="mt-3">
            <Waterfall steps={(detail.spans ?? []) as Step[]} traceId={detail.id} />
          </div>
        </section>
      ) : null}
    </div>
  )
}
