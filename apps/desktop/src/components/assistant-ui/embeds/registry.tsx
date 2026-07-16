'use client'

import { type ComponentType, lazy, type LazyExoticComponent, type ReactNode, Suspense } from 'react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import { useI18n } from '@/i18n'
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

function HermesUiPreparing() {
  const { t } = useI18n()

  return (
    <div
      aria-live="polite"
      className="my-3 rounded-xl border border-border/80 bg-muted/25 px-3 py-2 text-sm text-muted-foreground"
      role="status"
    >
      {t.assistantUi.preparing}
    </div>
  )
}

function InvalidHermesUiNotice() {
  const { t } = useI18n()

  return (
    <div className="my-3 rounded-xl border border-amber-500/35 bg-amber-500/8 px-3 py-3 text-sm" role="alert">
      <div className="font-medium text-foreground">{t.assistantUi.invalidTitle}</div>
      <div className="mt-1 text-muted-foreground">{t.assistantUi.invalidDescription}</div>
      <button
        className="mt-3 rounded-lg border border-border bg-background px-3 py-1.5 font-medium text-foreground hover:bg-muted"
        onClick={() => requestComposerSubmit(t.assistantUi.resendPrompt, { target: 'main' })}
        type="button"
      >
        {t.assistantUi.resend}
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
    if (!parseHermesUiArtifact(code).ok) {
      return streaming ? <HermesUiPreparing /> : <InvalidHermesUiNotice />
    }

    return (
      <RichBoundary fallback={<InvalidHermesUiNotice />} resetKey={code}>
        <Suspense fallback={<HermesUiPreparing />}>
          <Renderer code={code} streaming={streaming} />
        </Suspense>
      </RichBoundary>
    )
  }

  return (
    <RichBoundary fallback={fallback} resetKey={code}>
      <Suspense fallback={fallback}>
        <Renderer code={code} streaming={streaming} />
      </Suspense>
    </RichBoundary>
  )
}
