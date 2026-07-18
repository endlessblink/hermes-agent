'use client'

import { type ComponentType, lazy, type LazyExoticComponent, type ReactNode, Suspense } from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import { parseHermesUiArtifact } from '@/lib/hermes-ui-artifacts'

import { RichBoundary } from './rich-boundary'
import type { RichFenceProps } from './types'

// Root renderer for fenced code blocks: a language → lazy-renderer table. Each
// renderer is its own split chunk (mermaid pulls in the mermaid lib, svg pulls
// in DOMPurify), loaded only when a block of that language actually appears.
const LAZY_FENCE: Record<string, LazyExoticComponent<ComponentType<RichFenceProps>>> = {
  'hermes-ui': lazy(() => import('./hermes-ui-artifact')),
  mermaid: lazy(() => import('./mermaid-embed')),
  svg: lazy(() => import('./svg-embed'))
}

export const RICH_FENCE_LANGUAGES: ReadonlySet<string> = new Set(Object.keys(LAZY_FENCE))

interface RichCodeBlockProps extends RichFenceProps {
  /** Rendered for unhandled languages, while the chunk loads, and on failure
   *  (typically the normal syntax-highlighted code block). */
  fallback: ReactNode
  language?: string
}

// Include the validator's reason so the model can actually correct the
// artifact — a reasonless retry regenerates the same invalid payload and
// loops forever (the resend button used to be an infinite loop for any
// deterministic schema mistake).
function hermesUiRetryPrompt(reason?: string): string {
  const why = reason ? ` Validator error: ${reason}.` : ''

  return `The interactive form could not be rendered.${why} Resend it as one complete valid hermes-ui artifact — the fence body must be pure JSON using only supported keys, with no surrounding explanation — and wait for my response.`
}

function InvalidHermesUiNotice({ reason }: { reason?: string }) {
  return (
    <div className="my-3 rounded-xl border border-amber-500/35 bg-amber-500/8 px-3 py-3 text-sm" role="alert">
      <div className="font-medium text-foreground">Interactive form could not be shown</div>
      <div className="mt-1 text-muted-foreground">
        {reason ? `Invalid UI artifact: ${reason}.` : 'Hermes returned an incomplete or invalid UI artifact.'}
      </div>
      <button
        className="mt-3 rounded-lg border border-border bg-background px-3 py-1.5 font-medium text-foreground hover:bg-muted"
        onClick={() => requestComposerSubmit(hermesUiRetryPrompt(reason), { target: 'main' })}
        type="button"
      >
        Ask Hermes to resend
      </button>
    </div>
  )
}

export function RichCodeBlock({ code, fallback, language, streaming }: RichCodeBlockProps) {
  const normalizedLanguage = language
    ?.trim()
    .toLowerCase()
    .replace(/^language-/, '')

  const Renderer = normalizedLanguage ? LAZY_FENCE[normalizedLanguage] : undefined

  if (!Renderer) {
    return <>{fallback}</>
  }

  if (normalizedLanguage === 'hermes-ui') {
    const parsed = parseHermesUiArtifact(code)

    if (!parsed.ok) {
      if (streaming) {
        return (
          <div
            aria-live="polite"
            className="my-3 rounded-xl border border-border/80 bg-muted/25 px-3 py-2 text-sm text-muted-foreground"
            role="status"
          >
            Preparing interactive form…
          </div>
        )
      }

      return <InvalidHermesUiNotice reason={parsed.error} />
    }
  }

  return (
    <RichBoundary fallback={fallback} resetKey={code}>
      <Suspense fallback={fallback}>
        <Renderer code={code} streaming={streaming} />
      </Suspense>
    </RichBoundary>
  )
}
