import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { createClientSessionState } from '@/lib/chat-runtime'
import { emitDesktopDiagnostic } from '@/lib/desktop-diagnostics'
import type { TodoItem } from '@/lib/todos'
import { $clarifyRequest, clearClarifyRequest } from '@/store/clarify'
import { $activeSessionId } from '@/store/session'
import { $todosBySession, clearSessionTodos, setSessionTodos } from '@/store/todos'
import type { RpcEvent } from '@/types/hermes'

import { useMessageStream } from './index'

vi.mock('@/lib/desktop-diagnostics', () => ({
  emitDesktopDiagnostic: vi.fn(),
  sendDesktopHeartbeat: vi.fn()
}))

const SID = 'session-1'
const todo = (id: string, status: TodoItem['status']): TodoItem => ({ content: `task ${id}`, id, status })

let handleEvent: ((event: RpcEvent) => void) | null = null
let stateByRuntimeId: Map<string, ClientSessionState>

let continueFromCompressionExhausted: (
  sessionId: string,
  errorMessage: string
) => boolean | Promise<boolean | void> | void

function Harness() {
  const activeSessionIdRef = useRef<string | null>(SID)
  const sessionStateByRuntimeIdRef = useRef(stateByRuntimeId)
  const queryClientRef = useRef(new QueryClient())

  const stream = useMessageStream({
    activeSessionIdRef,
    hydrateFromStoredSession: vi.fn(async () => undefined),
    queryClient: queryClientRef.current,
    continueFromCompressionExhausted,
    refreshHermesConfig: vi.fn(async () => undefined),
    refreshSessions: vi.fn(async () => undefined),
    sessionStateByRuntimeIdRef,
    updateSessionState: (sessionId, updater) => {
      const current = sessionStateByRuntimeIdRef.current.get(sessionId) ?? createClientSessionState()
      const next = updater(current)
      sessionStateByRuntimeIdRef.current.set(sessionId, next)

      return next
    }
  })

  useEffect(() => {
    handleEvent = stream.handleGatewayEvent
  }, [stream.handleGatewayEvent])

  return null
}

async function mountStream() {
  render(<Harness />)
  await waitFor(() => expect(handleEvent).not.toBeNull())
}

const complete = () => act(() => handleEvent!({ payload: { text: 'done' }, session_id: SID, type: 'message.complete' }))

describe('useMessageStream turn-end todo cleanup', () => {
  beforeEach(() => {
    handleEvent = null
    continueFromCompressionExhausted = vi.fn(async () => undefined)
    stateByRuntimeId = new Map()
    $activeSessionId.set(SID)
    clearSessionTodos(SID)
    clearClarifyRequest()
  })

  afterEach(() => {
    cleanup()
    $activeSessionId.set(null)
    clearSessionTodos(SID)
    clearClarifyRequest()
    vi.restoreAllMocks()
  })

  it('drops a still-active task list when the turn completes', async () => {
    await mountStream()
    setSessionTodos(SID, [todo('a', 'completed'), todo('b', 'in_progress')])

    complete()

    expect($todosBySession.get()[SID]).toBeUndefined()
  })

  it('keeps a finished list on completion so its linger shows the final checkmarks', async () => {
    await mountStream()
    setSessionTodos(SID, [todo('a', 'completed')])

    complete()

    // Not cleared immediately — the finished-list linger still owns it.
    expect($todosBySession.get()[SID]).toHaveLength(1)
  })

  it('drops a still-active task list when the turn errors out', async () => {
    await mountStream()
    setSessionTodos(SID, [todo('a', 'in_progress')])

    act(() => handleEvent!({ payload: { message: 'boom' }, session_id: SID, type: 'error' }))

    expect($todosBySession.get()[SID]).toBeUndefined()
  })

  it('keeps needsInput set when an unrelated tool completes after clarify.request', async () => {
    await mountStream()

    act(() =>
      handleEvent!({
        payload: { choices: ['A', 'B'], question: 'Pick one', request_id: 'clarify-1' },
        session_id: SID,
        type: 'clarify.request'
      })
    )
    expect(stateByRuntimeId.get(SID)?.needsInput).toBe(true)
    expect($clarifyRequest.get()?.requestId).toBe('clarify-1')

    act(() =>
      handleEvent!({
        payload: { args: { query: 'profile' }, name: 'memory', result: { nodes: 1 }, tool_id: 'memory-1' },
        session_id: SID,
        type: 'tool.complete'
      })
    )

    expect(stateByRuntimeId.get(SID)?.needsInput).toBe(true)
    expect($clarifyRequest.get()?.requestId).toBe('clarify-1')
  })

  it('clears needsInput when the clarify tool completes after the request resolves', async () => {
    await mountStream()

    act(() =>
      handleEvent!({
        payload: { choices: ['A', 'B'], question: 'Pick one', request_id: 'clarify-1' },
        session_id: SID,
        type: 'clarify.request'
      })
    )
    expect(stateByRuntimeId.get(SID)?.needsInput).toBe(true)

    act(() =>
      handleEvent!({
        payload: {
          args: { choices: ['A', 'B'], question: 'Pick one' },
          name: 'clarify',
          result: { question: 'Pick one', user_response: 'A' },
          tool_id: 'clarify-1'
        },
        session_id: SID,
        type: 'tool.complete'
      })
    )

    expect(stateByRuntimeId.get(SID)?.needsInput).toBe(false)
  })

  it('settles a running turn when session.info reports the backend is no longer running', async () => {
    await mountStream()

    stateByRuntimeId.set(SID, {
      ...createClientSessionState(),
      awaitingResponse: true,
      busy: true,
      messages: [
        {
          id: 'assistant-pending',
          parts: [],
          pending: true,
          role: 'assistant'
        }
      ],
      streamId: 'assistant-pending',
      turnStartedAt: Date.now()
    })
    expect(stateByRuntimeId.get(SID)?.busy).toBe(true)
    expect(stateByRuntimeId.get(SID)?.awaitingResponse).toBe(true)

    act(() =>
      handleEvent!({
        payload: { running: false },
        session_id: SID,
        type: 'session.info'
      })
    )

    const state = stateByRuntimeId.get(SID)

    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(state?.turnStartedAt).toBeNull()
    expect(state?.messages.at(-1)?.pending).toBe(false)
  })

  it('treats message.complete with error status as a failed turn', async () => {
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: { status: 'error', text: 'Context length exceeded and cannot compress further.' },
        session_id: SID,
        type: 'message.complete'
      })
    )

    const state = stateByRuntimeId.get(SID)

    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(state?.turnStartedAt).toBeNull()
    expect(state?.messages.at(-1)?.pending).toBe(false)
    expect(state?.messages.at(-1)?.error).toBe('Context length exceeded and cannot compress further.')
  })

  it('ignores assistant deltas that arrive after a terminal turn event', async () => {
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: { status: 'error', text: 'The turn stopped and the chat is ready.' },
        session_id: SID,
        type: 'message.complete'
      })
    )
    act(() =>
      handleEvent!({
        payload: { text: 'late fragment from the retired worker' },
        session_id: SID,
        type: 'message.delta'
      })
    )

    await new Promise(resolve => window.setTimeout(resolve, 20))

    const state = stateByRuntimeId.get(SID)
    const serializedMessages = JSON.stringify(state?.messages ?? [])

    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(serializedMessages).not.toContain('late fragment from the retired worker')
  })

  it('ignores tool activity that arrives after a terminal turn event', async () => {
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: { status: 'complete', text: 'The requested work is complete.' },
        session_id: SID,
        type: 'message.complete'
      })
    )

    const messagesAtCompletion = JSON.stringify(stateByRuntimeId.get(SID)?.messages ?? [])

    act(() =>
      handleEvent!({
        payload: { name: 'execute_code', tool_id: 'late-tool' },
        session_id: SID,
        type: 'tool.start'
      })
    )
    act(() =>
      handleEvent!({
        payload: { name: 'execute_code', result: { ok: true }, tool_id: 'late-tool' },
        session_id: SID,
        type: 'tool.complete'
      })
    )

    const state = stateByRuntimeId.get(SID)

    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(state?.messages.some(message => message.pending)).toBe(false)
    expect(JSON.stringify(state?.messages ?? [])).toBe(messagesAtCompletion)
  })

  it('ignores recoverable Codex OAuth refresh errors while the turn is retrying', async () => {
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: { message: 'HTTP 401: Encountered invalidated oauth token for user, failing request' },
        session_id: SID,
        type: 'error'
      })
    )

    let state = stateByRuntimeId.get(SID)
    expect(state?.busy).toBe(true)
    expect(state?.awaitingResponse).toBe(true)
    expect(state?.messages.some(message => message.error?.includes('invalidated oauth token'))).toBe(false)

    act(() =>
      handleEvent!({
        payload: { text: 'Recovered after refreshing auth.' },
        session_id: SID,
        type: 'message.complete'
      })
    )

    state = stateByRuntimeId.get(SID)
    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(state?.messages.at(-1)?.error).toBeUndefined()
    expect(state?.messages.at(-1)?.parts.at(-1)).toMatchObject({ type: 'text', text: 'Recovered after refreshing auth.' })
  })

  it('requests automatic same-session recovery when compression times out', async () => {
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: {
          same_session_recovery: true,
          status: 'error',
          text: 'Context length exceeded (358,245 tokens). Cannot compress further.'
        },
        session_id: SID,
        type: 'message.complete'
      })
    )

    expect(continueFromCompressionExhausted).toHaveBeenCalledWith(
      SID,
      'Context length exceeded (358,245 tokens). Cannot compress further.'
    )

    const state = stateByRuntimeId.get(SID)

    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(state?.turnStartedAt).toBeNull()
    expect(state?.messages.at(-1)?.error).toBeUndefined()
  })

  it('continues a saved turn instead of showing an idle-timeout failure', async () => {
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: {
          idle_timeout: true,
          same_session_recovery: true,
          status: 'error',
          text: 'Your turn was saved and will recover in the same conversation.'
        },
        session_id: SID,
        type: 'message.complete'
      })
    )

    expect(continueFromCompressionExhausted).toHaveBeenCalledWith(
      SID,
      'Your turn was saved and will recover in the same conversation.'
    )

    const state = stateByRuntimeId.get(SID)
    expect(state?.busy).toBe(false)
    expect(state?.awaitingResponse).toBe(false)
    expect(state?.messages.at(-1)?.error).toBeUndefined()
  })

  it('shows the saved turn failure when automatic recovery is refused', async () => {
    continueFromCompressionExhausted = vi.fn(async () => false)
    await mountStream()

    act(() => handleEvent!({ payload: undefined, session_id: SID, type: 'message.start' }))
    act(() =>
      handleEvent!({
        payload: {
          same_session_recovery: true,
          status: 'error',
          text: 'The saved turn could not be recovered automatically.'
        },
        session_id: SID,
        type: 'message.complete'
      })
    )

    await waitFor(() =>
      expect(stateByRuntimeId.get(SID)?.messages.at(-1)?.error).toBe(
        'The saved turn could not be recovered automatically.'
      )
    )
  })

  it('forwards gateway diagnostic events to desktop diagnostics', async () => {
    await mountStream()

    act(() =>
      handleEvent!({
        payload: {
          component: 'compression',
          event: 'timeout',
          message: 'Context compression timed out',
          severity: 'error',
          details: { elapsed_seconds: 240 }
        },
        session_id: SID,
        type: 'diagnostic.event'
      })
    )

    expect(emitDesktopDiagnostic).toHaveBeenCalledWith({
      component: 'compression',
      event: 'timeout',
      message: 'Context compression timed out',
      severity: 'error',
      details: { elapsed_seconds: 240, sessionId: SID }
    })
  })
})
