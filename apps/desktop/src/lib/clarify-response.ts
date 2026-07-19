import type { HermesGateway } from '@/hermes'
import type { Translations } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { type ClarifyRequest, clearClarifyRequest } from '@/store/clarify'
import { notifyError } from '@/store/notifications'

type ClarifyCopy = Translations['assistant']['clarify']

export async function respondToClarifyRequest({
  allowEmpty = false,
  answer,
  copy,
  gateway,
  onBeforeSend,
  onError,
  request
}: {
  allowEmpty?: boolean
  answer: string
  copy: ClarifyCopy
  gateway: HermesGateway | null | undefined
  onBeforeSend?: () => void
  onError?: (error: unknown) => void
  request: ClarifyRequest | null | undefined
}): Promise<boolean | 'stale'> {
  const trimmed = answer.trim()

  if (!trimmed && !allowEmpty) {
    return false
  }

  if (!request?.requestId) {
    notifyError(new Error(copy.notReady), copy.sendFailed)

    return false
  }

  if (!gateway) {
    notifyError(new Error(copy.gatewayDisconnected), copy.sendFailed)

    return false
  }

  onBeforeSend?.()

  try {
    await gateway.request<{ ok?: boolean }>('clarify.respond', {
      request_id: request.requestId,
      answer: trimmed
    })
    triggerHaptic('submit')
    clearClarifyRequest(request.requestId, request.sessionId)

    return true
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)

    // The gateway no longer tracks this clarify (the turn moved on, or a
    // backend restart dropped it). Keeping the stale request would hijack
    // EVERY subsequent send into this failing path — the "can't send
    // anything while a form is on screen" trap. Drop the stale request so
    // the composer returns to normal routing; the caller re-sends the text.
    if (/no pending answer request/i.test(message)) {
      clearClarifyRequest(request.requestId, request.sessionId)
      onError?.(error)

      return 'stale'
    }

    notifyError(error, copy.sendFailed)
    onError?.(error)

    return false
  }
}
