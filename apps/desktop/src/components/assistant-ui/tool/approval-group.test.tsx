import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { $clarifyRequest, setClarifyRequest } from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { clearAllPrompts, setApprovalRequest } from '@/store/prompts'
import { $activeSessionId } from '@/store/session'
import { clearDismissedToolRows } from '@/store/tool-dismiss'
import { $toolDisclosureStates } from '@/store/tool-view'

import { Thread } from '../thread'

// Regression coverage for the "approval must never be buried" bug. Tools now
// render as a flat list (no collapsible "N steps" group), so a pending tool's
// inline ApprovalBar is always in the visual flow — never inside a `hidden`
// body. These assert the bar shows only when an approval is live and is never
// trapped under a `hidden` ancestor.

const createdAt = new Date('2026-06-03T00:00:00.000Z')

const resizeObservers = new Set<TestResizeObserver>()

class TestResizeObserver {
  private target: Element | null = null

  constructor(private readonly callback: ResizeObserverCallback) {
    resizeObservers.add(this)
  }

  observe(target: Element) {
    this.target = target
  }

  unobserve() {}

  disconnect() {
    resizeObservers.delete(this)
  }
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
  window.setTimeout(() => callback(performance.now()), 0)
)
vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))

Element.prototype.scrollTo = function scrollTo() {}

Element.prototype.animate = function animate() {
  return {
    cancel: () => {},
    finished: Promise.resolve()
  } as unknown as Animation
}

function stubOffsetDimension(
  prop: 'offsetHeight' | 'offsetWidth',
  clientProp: 'clientHeight' | 'clientWidth',
  fallback: number
) {
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop)

  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      return previous?.get?.call(this) || (this as HTMLElement)[clientProp] || fallback
    }
  })
}

stubOffsetDimension('offsetWidth', 'clientWidth', 800)
stubOffsetDimension('offsetHeight', 'clientHeight', 600)

// A running assistant message with two tools: a completed read_file plus a
// pending terminal (no result), rendered as a flat two-row list.
function groupedPendingMessage(): ThreadMessage {
  return {
    id: 'assistant-group-1',
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: 'read-1',
        toolName: 'read_file',
        args: { path: '/etc/hosts' },
        argsText: JSON.stringify({ path: '/etc/hosts' }),
        result: { content: '127.0.0.1 localhost' }
      },
      {
        type: 'tool-call',
        toolCallId: 'term-1',
        toolName: 'terminal',
        args: { command: 'rm -rf /tmp/x' },
        argsText: JSON.stringify({ command: 'rm -rf /tmp/x' })
      }
    ],
    status: { type: 'running' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

function groupedPendingClarifyMessage(): ThreadMessage {
  return {
    id: 'assistant-group-clarify',
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: 'memory-1',
        toolName: 'memory',
        args: { query: 'profile' },
        argsText: JSON.stringify({ query: 'profile' }),
        result: { nodes: 25 }
      },
      {
        type: 'tool-call',
        toolCallId: 'read-1',
        toolName: 'read_file',
        args: { path: '/tmp/a' },
        argsText: JSON.stringify({ path: '/tmp/a' }),
        result: { content: 'ok' }
      },
      {
        type: 'tool-call',
        toolCallId: 'clarify-1',
        toolName: 'clarify',
        args: { choices: ['One', 'Two'], question: 'Pick one' },
        argsText: JSON.stringify({ choices: ['One', 'Two'], question: 'Pick one' })
      }
    ],
    status: { type: 'running' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

function completedClarifyWithPendingRequestMessage(): ThreadMessage {
  return {
    id: 'assistant-completed-clarify',
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: 'clarify-1',
        toolName: 'clarify',
        args: { choices: ['Use FlowState', 'Use plain plan'], question: 'Choose the planning surface' },
        argsText: JSON.stringify({
          choices: ['Use FlowState', 'Use plain plan'],
          question: 'Choose the planning surface'
        }),
        result: {
          choices_offered: ['Use FlowState', 'Use plain plan'],
          question: 'Choose the planning surface',
          user_response: ''
        }
      }
    ],
    status: { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

function pendingOnlyMessage(): ThreadMessage {
  return {
    id: 'assistant-pending-only',
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: 'term-only',
        toolName: 'terminal',
        args: { command: 'sleep 10' },
        argsText: JSON.stringify({ command: 'sleep 10' })
      }
    ],
    status: { type: 'running' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

function completedOnlyMessage(): ThreadMessage {
  return {
    id: 'assistant-completed-only',
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: 'read-only',
        toolName: 'read_file',
        args: { path: '/etc/hosts' },
        argsText: JSON.stringify({ path: '/etc/hosts' }),
        result: { content: '127.0.0.1 localhost' }
      }
    ],
    status: { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

function failedOnlyMessage(): ThreadMessage {
  return {
    id: 'assistant-failed-only',
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: 'term-failed',
        toolName: 'terminal',
        args: { command: 'exit 1' },
        argsText: JSON.stringify({ command: 'exit 1' }),
        isError: true,
        result: { stderr: 'boom' }
      }
    ],
    status: { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

function GroupHarness({ message }: { message: ThreadMessage }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [message],
    isRunning: message.status?.type === 'running',
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

beforeEach(() => {
  clearAllPrompts()
  $activeSessionId.set('sess-1')
  $toolDisclosureStates.set({})
  clearDismissedToolRows()
})

afterEach(() => {
  cleanup()
  clearAllPrompts()
  $activeSessionId.set(null)
  $gateway.set(null)
  clearDismissedToolRows()
})

describe('flat tool list approval surfacing', () => {
  it('renders no inline approval bar when there is no live approval', async () => {
    const { container } = render(<GroupHarness message={groupedPendingMessage()} />)

    // The pending terminal row mounts immediately, but its inline ApprovalBar
    // returns null while $approvalRequest is empty.
    await waitFor(() => {
      expect(container.querySelectorAll('[data-slot="tool-block"]').length).toBeGreaterThan(0)
    })
    expect(container.querySelector('[data-slot="tool-approval-inline"]')).toBeNull()
  })

  it('surfaces the approval inline and never under a hidden ancestor', async () => {
    setApprovalRequest({ command: 'rm -rf /tmp/x', description: 'dangerous command', sessionId: 'sess-1' })

    const { container } = render(<GroupHarness message={groupedPendingMessage()} />)

    await waitFor(() => {
      const bar = container.querySelector('[data-slot="tool-approval-inline"]')
      expect(bar).not.toBeNull()
      // Flat rows live directly in the flow — nothing should ever wrap the bar
      // in a `hidden` subtree.
      expect(bar?.closest('[hidden]')).toBeNull()
    })
  })

  it('does not trap a live clarify question inside the compact tool scroller', async () => {
    setClarifyRequest({
      choices: ['One', 'Two'],
      question: 'Pick one',
      requestId: 'clarify-request-1',
      sessionId: 'sess-1'
    })

    const { container } = render(<GroupHarness message={groupedPendingClarifyMessage()} />)

    await waitFor(() => {
      expect(container.querySelector('[data-slot="clarify-inline"]')).not.toBeNull()
    })

    expect(container.querySelector('.tool-group-scroll')).toBeNull()
  })

  it('keeps a pending clarify card visible even if the tool row completed first', async () => {
    setClarifyRequest({
      choices: ['Use FlowState', 'Use plain plan'],
      question: 'Choose the planning surface',
      requestId: 'clarify-request-completed-row',
      sessionId: 'sess-1'
    })

    const { container } = render(<GroupHarness message={completedClarifyWithPendingRequestMessage()} />)

    await waitFor(() => {
      expect(container.querySelector('[data-slot="clarify-inline"]')).not.toBeNull()
    })

    expect(screen.getByText('Choose the planning surface')).toBeTruthy()
    expect(screen.getByText('Use FlowState')).toBeTruthy()
    expect(screen.queryByText('Asked a question')).toBeNull()
  })

  it('sends an empty clarify answer when Skip is clicked', async () => {
    const request = vi.fn().mockResolvedValue({ ok: true })
    $gateway.set({ request } as unknown as HermesGateway)
    setClarifyRequest({
      choices: ['One', 'Two'],
      question: 'Pick one',
      requestId: 'clarify-request-skip',
      sessionId: 'sess-1'
    })

    render(<GroupHarness message={groupedPendingClarifyMessage()} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Skip' }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('clarify.respond', {
        answer: '',
        request_id: 'clarify-request-skip'
      })
    })
    expect($clarifyRequest.get()).toBeNull()
  })

  it('keeps the clarify card visible when sending the answer fails', async () => {
    const request = vi.fn().mockRejectedValue(new Error('backend offline'))
    $gateway.set({ request } as unknown as HermesGateway)
    setClarifyRequest({
      choices: ['One', 'Two'],
      question: 'Pick one',
      requestId: 'clarify-request-fail',
      sessionId: 'sess-1'
    })

    const { container } = render(<GroupHarness message={groupedPendingClarifyMessage()} />)

    fireEvent.click(await screen.findByRole('button', { name: /One/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Continue/ }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('clarify.respond', {
        answer: 'One',
        request_id: 'clarify-request-fail'
      })
    })
    await waitFor(() => {
      expect(container.querySelector('[data-slot="clarify-inline"]')).not.toBeNull()
    })
    expect($clarifyRequest.get()?.requestId).toBe('clarify-request-fail')
  })

  it('lets completed tool rows be dismissed', async () => {
    const { container } = render(<GroupHarness message={completedOnlyMessage()} />)

    const dismiss = await screen.findByLabelText('Dismiss')

    expect(container.querySelectorAll('[data-slot="tool-block"]').length).toBeGreaterThan(1)

    fireEvent.click(dismiss)

    await waitFor(() => {
      expect(screen.queryByLabelText('Dismiss')).toBeNull()
    })
  })

  it('keeps a dismissed row hidden after a remount (virtualization)', async () => {
    // The thread virtualizes, so a row's component unmounts/remounts as it
    // scrolls. Dismissal must persist across that — component-local state would
    // forget it and the row would pop back. Simulate the remount by unmounting
    // and rendering the same message fresh.
    const first = render(<GroupHarness message={completedOnlyMessage()} />)

    fireEvent.click(await screen.findByLabelText('Dismiss'))

    await waitFor(() => {
      expect(screen.queryByLabelText('Dismiss')).toBeNull()
    })

    first.unmount()

    const { container } = render(<GroupHarness message={completedOnlyMessage()} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-slot="tool-block"]').length).toBeGreaterThan(0)
    })

    expect(screen.queryByLabelText('Dismiss')).toBeNull()
  })

  it('lets failed tool rows be dismissed', async () => {
    render(<GroupHarness message={failedOnlyMessage()} />)

    const dismiss = await screen.findByLabelText('Dismiss')

    fireEvent.click(dismiss)

    await waitFor(() => {
      expect(screen.queryByLabelText('Dismiss')).toBeNull()
    })
  })

  it('does not show dismiss for pending tool rows', async () => {
    const { container } = render(<GroupHarness message={pendingOnlyMessage()} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-slot="tool-block"]').length).toBeGreaterThan(0)
    })

    expect(screen.queryByLabelText('Dismiss')).toBeNull()
  })
})
