import type { Span } from '../api/types'

/** One step of the waterfall: works for a stored one and for one arriving over SSE. */
export type Step = Pick<Span, 'seq' | 'name' | 'status'> &
  Partial<Pick<Span, 'duration_ms' | 'error'>> & { type?: string }

/** 1,2 s or 340 ms: the duration reads at a glance, without the eye doing math. */
export function formatDuration(ms: number | undefined): string {
  if (ms === undefined || ms === null) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2).replace('.', ',')} s`
}

/** User-facing (Spanish) labels for the technical step names. */
const LABELS: Record<string, string> = {
  guardrail_input: 'Chequeo de entrada',
  guardrail_output: 'Chequeo de salida',
  agent: 'Razonamiento',
  tool: 'Herramienta',
}

/** The technical name is for whoever investigates; the person needs this. */
export function labelFor(step: Step): string {
  if (step.type === 'tool' || step.name.startsWith('tool:')) {
    return `Herramienta ${step.name.replace(/^tool:/, '')}`
  }
  return LABELS[step.name] ?? step.name.replace(/_/g, ' ')
}
