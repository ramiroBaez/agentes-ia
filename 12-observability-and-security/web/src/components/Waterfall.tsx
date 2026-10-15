import type { LiveSpan } from '../api/types'
import { formatDuration, labelFor, type Step } from './steps'

export type { Step }

/** Bar color by kind of step: it shows at a glance where the time went. */
function colorFor(step: Step): string {
  // Stored spans carry `type`; the ones arriving over SSE carry `span_type`.
  const type = step.type ?? (step as { span_type?: string }).span_type ?? ''
  if (type === 'tool' || step.name.startsWith('tool:')) return 'bg-ok'
  if (type === 'guardrail' || step.name.startsWith('guardrail')) return 'bg-waiting'
  if (type === 'approval') return 'bg-alert'
  return 'bg-signal'
}

/**
 * The waterfall: the agent's steps lined up in time.
 *
 * This is the part that makes the product an operations panel and not just
 * another chat: you see which step is running, how long the rest took and
 * where it got stuck. Each step's bar is proportional to the slowest step of
 * the trace.
 */
export function Waterfall({
  steps,
  running,
  traceId,
}: {
  steps: (Step | LiveSpan)[]
  running?: string | null
  traceId?: string | null
}) {
  const slowest = Math.max(1, ...steps.map((s) => (s as Step).duration_ms ?? 0))

  if (steps.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line p-4 text-sm text-slate">
        Todavía no hay pasos: mandá un mensaje y vas a ver acá, en vivo, qué hace el agente.
      </p>
    )
  }

  return (
    <div>
      {traceId ? <p className="chip text-slate">{traceId}</p> : null}
      <ol className="mt-3 divide-y divide-line/70 overflow-hidden rounded-lg border border-line bg-paper/40">
        {steps.map((raw, index) => {
          const step = raw as Step
          const isRunning = running === step.name && step.duration_ms === undefined
          const width = Math.max(2, Math.round(((step.duration_ms ?? 0) / slowest) * 100))
          const failed = step.status === 'error'
          return (
            <li key={`${step.seq}-${index}`} className="px-3 py-2">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="w-5 shrink-0 font-mono text-[11px] text-slate">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm sm:max-w-56 sm:flex-none sm:basis-56">
                  {labelFor(step)}
                  <span className="ml-2 font-mono text-[11px] text-slate">{step.name}</span>
                </span>
                <span className="h-2 min-w-24 flex-1 rounded-full bg-line/60">
                  <span
                    data-testid="bar"
                    data-width={width}
                    style={{ width: `${width}%` }}
                    className={`block h-2 rounded-full ${
                      failed
                        ? 'bg-alert'
                        : isRunning
                          ? 'animate-pulse bg-signal'
                          : `${colorFor(step)} opacity-80`
                    }`}
                  />
                </span>
                <span className="w-16 shrink-0 text-right font-mono text-[11px] text-slate">
                  {isRunning ? 'en curso' : formatDuration(step.duration_ms)}
                </span>
              </div>
              {failed && step.error ? (
                <p className="mt-1 pl-8 font-mono text-[11px] text-alert">{step.error}</p>
              ) : null}
            </li>
          )
        })}
      </ol>
      {steps.some((s) => (s as Step).status === 'error') ? (
        <p className="mt-3 text-xs text-alert">
          Un paso falló. Queda en la traza para que se pueda mirar.
        </p>
      ) : null}
    </div>
  )
}
