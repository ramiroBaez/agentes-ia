import { type ReactNode, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import { readSSE } from '../api/sse'
import type { Approval, Message } from '../api/types'
import { Waterfall, type Step } from './Waterfall'

/** How often the chat asks the API whether an operator has decided yet. */
const DECISION_POLL_MS = 3_000

/** The message that tells the client what was decided and why. */
function decisionText(approval: Approval, response: string): string {
  const approved = approval.status === 'approved'
  const reason = approval.reason?.trim()
  const header = [
    `Decisión: reembolso ${approved ? 'aprobado' : 'rechazado'}`,
    approval.decider ? ` por ${approval.decider}` : '',
    reason ? `. Motivo: ${reason}.` : '. Sin motivo anotado.',
  ].join('')
  // On rejection the agent's reply already repeats the reason: don't say it twice.
  const repeats = !approved && reason && response.includes(reason)
  return repeats || !response
    ? header
    : `${header}

${response}`
}

const GREETING = 'Escribí tu consulta sobre un pedido, un producto o un reembolso.'

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return 'No pude hablar con la API. Revisá que esté levantada.'
  return 'No pude completar la consulta.'
}

/** Models answer with **bold**: show it as such, without pulling in a whole parser. */
function withBold(text: string): ReactNode {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  if (parts.length === 1) return text
  return parts.map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))
}

/**
 * The conversation with the agent and, alongside it, what the agent is doing.
 *
 * The steps are not requested separately: they arrive in the same stream as
 * the answer, so the waterfall fills itself while the model thinks. If the
 * case needs human approval, the answer says so and the thread stays alive
 * for when the operator decides.
 */
export function Conversation() {
  const [messages, setMessages] = useState<Message[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [running, setRunning] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [traceId, setTraceId] = useState<string | null>(null)
  const [conversationId, setConversationId] = useState<string | undefined>(undefined)
  const box = useRef<HTMLTextAreaElement>(null)

  // The approvals the chat is waiting on: when an operator decides in the
  // panel, the "waiting" message is updated and the client gets the notice.
  const awaited = messages
    .filter((m) => m.waitingApproval && m.approvalId)
    .map((m) => m.approvalId as string)
    .join(',')

  useEffect(() => {
    if (!awaited) return
    let alive = true
    const check = async () => {
      for (const id of awaited.split(',')) {
        try {
          const { approval, response } = await api.approval(id)
          // `response` arrives a moment after the decision: without it the
          // turn is still closing and we ask again.
          if (!alive || approval.status === 'pending' || response === null) continue
          setMessages((previous) => [
            ...previous.map((m) =>
              m.approvalId === id
                ? {
                    ...m,
                    waitingApproval: false,
                    decision: approval.status as 'approved' | 'rejected',
                  }
                : m,
            ),
            {
              id: `decision-${id}`,
              role: 'agent',
              text: decisionText(approval, response),
              decision: approval.status as 'approved' | 'rejected',
            },
          ])
        } catch {
          // A network hiccup changes nothing: we retry on the next cycle.
        }
      }
    }
    const timer = setInterval(() => void check(), DECISION_POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [awaited])

  async function send() {
    const message = text.trim()
    if (!message || sending) return
    setMessages((previous) => [
      ...previous,
      { id: `m${previous.length}`, role: 'client', text: message },
    ])
    setText('')
    setSteps([])
    setRunning(null)
    setTraceId(null)
    setError(null)
    setSending(true)
    box.current?.focus()

    try {
      const response = await api.turn(message, conversationId)
      await readSSE(
        response.body!,
        (event) => {
          if (event.type === 'span') {
            const step = event.data as unknown as Step
            setSteps((previous) =>
              previous.some((s) => s.seq === step.seq) ? previous : [...previous, step],
            )
            setRunning(null)
          } else if (event.type === 'end') {
            const end = event.data as unknown as {
              response: string
              trace_id: string
              conversation_id: string
              message_id?: string
              status: string
              approval?: { id: string } | null
            }
            setTraceId(end.trace_id)
            setConversationId(end.conversation_id)
            setRunning(null)
            if (end.response) {
              setMessages((previous) => [
                ...previous,
                {
                  id: end.message_id ?? `m${previous.length}`,
                  role: 'agent',
                  text: end.response,
                  traceId: end.trace_id,
                  waitingApproval: end.status === 'waiting_approval',
                  approvalId: end.approval?.id,
                },
              ])
            }
          }
        },
        () => setRunning(null),
      )
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSending(false)
    }
  }

  async function rate(message: Message, value: 1 | -1) {
    if (!message.id.startsWith('msg_')) return
    try {
      await api.feedback(message.id, value, '')
      setMessages((previous) =>
        previous.map((m) => (m.id === message.id ? { ...m, feedback: value } : m)),
      )
    } catch (failure) {
      setError(errorMessage(failure))
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <section className="card flex min-h-[60vh] flex-col p-4 sm:p-5">
        <h2 className="sr-only">Conversación</h2>
        <ol className="flex-1 space-y-4" aria-label="Mensajes">
          {messages.length === 0 ? (
            <li className="rounded-lg border border-dashed border-line p-5 text-sm text-slate">
              {GREETING}
            </li>
          ) : null}
          {messages.map((message) => (
            <li
              key={message.id}
              className={
                message.role === 'client'
                  ? 'ml-auto w-fit max-w-[46ch] rounded-2xl rounded-br-sm bg-signal/15 px-4 py-3 text-sm text-ink ring-1 ring-signal/30'
                  : 'max-w-[60ch] rounded-2xl rounded-bl-sm bg-raised px-4 py-3 text-sm ring-1 ring-line'
              }
            >
              <p className="whitespace-pre-wrap">{withBold(message.text)}</p>
              {message.waitingApproval ? (
                <p className="chip mt-2 border-waiting/40 text-waiting">
                  <span
                    className="h-1.5 w-1.5 animate-pulse rounded-full bg-waiting"
                    aria-hidden="true"
                  />
                  esperando aprobación de un operador
                </p>
              ) : null}
              {message.decision && !message.id.startsWith('decision-') ? (
                <p
                  className={`chip mt-2 ${
                    message.decision === 'approved'
                      ? 'border-ok/40 text-ok'
                      : 'border-alert/40 text-alert'
                  }`}
                >
                  {message.decision === 'approved' ? 'reembolso aprobado' : 'reembolso rechazado'}
                </p>
              ) : null}
              {message.role === 'agent' && !message.waitingApproval && !message.decision ? (
                <p className="mt-2 flex gap-2 text-slate">
                  <button
                    type="button"
                    aria-label="La respuesta me sirvió"
                    onClick={() => rate(message, 1)}
                    className={`rounded px-1.5 font-mono text-xs ${message.feedback === 1 ? 'bg-ok/15 text-ok' : 'hover:text-ok'}`}
                  >
                    bien
                  </button>
                  <button
                    type="button"
                    aria-label="La respuesta no me sirvió"
                    onClick={() => rate(message, -1)}
                    className={`rounded px-1.5 font-mono text-xs ${message.feedback === -1 ? 'bg-alert/15 text-alert' : 'hover:text-alert'}`}
                  >
                    mal
                  </button>
                  {message.feedback ? (
                    <span className="font-mono text-[11px]">guardado</span>
                  ) : null}
                </p>
              ) : null}
            </li>
          ))}
        </ol>

        {sending && !error ? (
          <p
            className="mt-4 flex items-center gap-2 font-mono text-xs text-slate"
            aria-live="polite"
          >
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-signal" aria-hidden="true" />
            pensando…
          </p>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-alert/40 bg-alert/10 px-3 py-2 text-sm text-alert"
          >
            {error}
          </p>
        ) : null}

        <form
          className="mt-4 flex items-end gap-3 border-t border-line pt-4"
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          <label className="sr-only" htmlFor="message">
            Mensaje para el agente
          </label>
          <textarea
            id="message"
            ref={box}
            rows={2}
            value={text}
            placeholder={GREETING}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
            className="field resize-none"
          />
          <button
            type="submit"
            disabled={sending || !text.trim()}
            className="button button-primary shrink-0"
          >
            {sending ? 'esperando…' : 'Enviar'}
          </button>
        </form>
      </section>

      <aside className="lg:sticky lg:top-6 lg:self-start">
        <h2 className="font-mono text-xs uppercase tracking-[0.15em] text-slate">
          Qué está haciendo el agente
        </h2>
        <div className="mt-3">
          <Waterfall steps={steps} running={running} traceId={traceId} />
        </div>
      </aside>
    </div>
  )
}
