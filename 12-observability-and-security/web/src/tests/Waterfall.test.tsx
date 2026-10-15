import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Waterfall } from '../components/Waterfall'
import type { LiveSpan } from '../api/types'

function step(extra: Partial<LiveSpan>): LiveSpan {
  return {
    seq: 0,
    trace_id: 'tra_1',
    span_type: 'node',
    name: 'guardrail_input',
    duration_ms: 3,
    status: 'ok',
    ...extra,
  }
}

describe('Waterfall', () => {
  it('shows one row per step, in order, with its duration', () => {
    render(
      <Waterfall
        steps={[
          step({}),
          step({ seq: 1, name: 'agent', duration_ms: 1120, span_type: 'node' }),
          step({ seq: 2, name: 'guardrail_output' }),
        ]}
      />,
    )

    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('guardrail_input')
    expect(rows[1]).toHaveTextContent('agent')
    expect(rows[1]).toHaveTextContent('1,12 s')
    expect(rows[2]).toHaveTextContent('guardrail_output')
  })

  it('the bar is proportional: the slow step looks long', () => {
    render(
      <Waterfall
        steps={[
          step({ name: 'guardrail_input', duration_ms: 3 }),
          step({ seq: 1, name: 'agent', duration_ms: 1200 }),
        ]}
      />,
    )

    const [short, long] = screen
      .getAllByTestId('bar')
      .map((el) => Number(el.getAttribute('data-width')))
    expect(long).toBeGreaterThan(short)
    expect(long).toBeLessThanOrEqual(100)
  })

  it('the step that is running has no duration yet', () => {
    render(
      <Waterfall
        steps={[step({}), step({ seq: 1, name: 'agent', duration_ms: undefined })]}
        running="agent"
      />,
    )

    expect(screen.getByText('en curso')).toBeInTheDocument()
  })

  it('a failed step shows its error, which is what needs fixing', () => {
    render(
      <Waterfall
        steps={[step({ seq: 1, name: 'check_stock', status: 'error', error: 'tienda caída' })]}
      />,
    )

    expect(screen.getByText('tienda caída')).toBeInTheDocument()
  })

  it('with no steps, it invites sending something instead of showing an empty screen', () => {
    render(<Waterfall steps={[]} />)

    expect(screen.getByText(/mandá un mensaje/i)).toBeInTheDocument()
  })
})
