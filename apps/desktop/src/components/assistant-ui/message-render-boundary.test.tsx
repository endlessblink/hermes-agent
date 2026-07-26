import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MessageRenderBoundary } from './message-render-boundary'

afterEach(cleanup)

function Boom({ error }: { error: Error | null }): null {
  if (error) {
    throw error
  }

  return null
}

const lookupError = new Error('useClientLookup: Index 2 out of bounds (length: 2)')

describe('MessageRenderBoundary', () => {
  it('renders children when nothing throws', () => {
    render(
      <MessageRenderBoundary>
        <div>content</div>
      </MessageRenderBoundary>
    )

    expect(screen.getByText('content')).toBeTruthy()
  })

  it('swallows the transient useClientLookup out-of-bounds store race', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    const { container } = render(
      <MessageRenderBoundary>
        <Boom error={lookupError} />
      </MessageRenderBoundary>
    )

    expect(container.innerHTML).toBe('')
    spy.mockRestore()
  })

  it('does not revive an errored boundary instance when the snapshot changes', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    const { container, rerender } = render(
      <MessageRenderBoundary>
        <Boom error={lookupError} />
      </MessageRenderBoundary>
    )

    rerender(
      <MessageRenderBoundary>
        <Boom error={null} />
      </MessageRenderBoundary>
    )

    rerender(
      <MessageRenderBoundary>
        <div>must stay unmounted</div>
      </MessageRenderBoundary>
    )

    expect(container.innerHTML).toBe('')
    spy.mockRestore()
  })

  it('recovers with a fresh boundary for the next consistent snapshot', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    const { rerender } = render(
      <MessageRenderBoundary key="a">
        <Boom error={lookupError} />
      </MessageRenderBoundary>
    )

    rerender(
      <MessageRenderBoundary key="b">
        <div>recovered</div>
      </MessageRenderBoundary>
    )

    expect(screen.getByText('recovered')).toBeTruthy()
    spy.mockRestore()
  })

  it('re-throws unrelated errors so real bugs still surface', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    expect(() =>
      render(
        <MessageRenderBoundary>
          <Boom error={new Error('genuine render bug')} />
        </MessageRenderBoundary>
      )
    ).toThrow('genuine render bug')

    spy.mockRestore()
  })
})
