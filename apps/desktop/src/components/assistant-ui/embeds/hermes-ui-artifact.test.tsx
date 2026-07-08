import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MarkdownTextContent } from '@/components/assistant-ui/markdown-text'
import type { HermesUiChecklistArtifact, HermesUiFlowStateNextBlockArtifact } from '@/lib/hermes-ui-artifacts'

const requestComposerSubmit = vi.fn()

vi.mock('@/app/chat/composer/focus', () => ({
  requestComposerSubmit: (text: string, opts?: unknown) => requestComposerSubmit(text, opts)
}))

import { ChecklistArtifactCard, FlowStateNextBlockCard, FlowStateTaskBatchCard, TaskTriageArtifactCard } from './hermes-ui-artifact'
import { RichCodeBlock } from './registry'

const artifact: HermesUiChecklistArtifact = {
  description: 'Operational source-of-truth checklist for Obsidian-backed durable context.',
  id: 'obsidian-source-of-truth-policy',
  items: [
    { id: 'obsidian-profile-vault', label: 'Active profile: office-work' },
    { id: 'obsidian-source-truth', label: 'Obsidian is the source of truth.' }
  ],
  title: 'Obsidian source-of-truth policy',
  type: 'checklist'
}

const storageKey = 'hermes-ui:checklist:obsidian-source-of-truth-policy'

const nextBlockArtifact: HermesUiFlowStateNextBlockArtifact = {
  actions: [
    {
      id: 'preview',
      label: 'תראה לי preview לפני שינוי ב־FlowState',
      submitText: 'תעשה preview לבלוק הזה ב־FlowState: taskId=task-1 date=2026-07-08 time=10:30 duration=25'
    },
    {
      id: 'apply-after-approval',
      label: 'מאשר להוסיף את הבלוק ל־FlowState',
      submitText: 'מאשר להוסיף את הבלוק הזה ל־FlowState: taskId=task-1 date=2026-07-08 time=10:30 duration=25'
    }
  ],
  direction: 'rtl',
  doneEnough: 'מסמך קצר שמוכן לשליחה לבדיקה.',
  durationMinutes: 25,
  id: 'next-block-1',
  previewSummary: {
    duration: 25,
    scheduledDate: '2026-07-08',
    scheduledTime: '10:30'
  },
  rationale: 'זה מספיק קטן כדי לפתוח מומנטום בלי להפוך לתכנון יום מלא.',
  task: {
    dueDate: '2026-07-10',
    id: 'task-1',
    priority: 'high',
    title: 'לכתוב טיוטה ללקוח'
  },
  title: 'הבלוק הבא',
  type: 'flowstate-next-block'
}

beforeEach(() => {
  window.localStorage.clear()
  requestComposerSubmit.mockClear()
})

afterEach(cleanup)

describe('ChecklistArtifactCard', () => {
  it('renders title, items, checkboxes, and progress', () => {
    render(<ChecklistArtifactCard artifact={artifact} />)

    expect(screen.getByText('Obsidian source-of-truth policy')).toBeTruthy()
    expect(screen.getByLabelText('Active profile: office-work')).toBeTruthy()
    expect(screen.getByLabelText('Obsidian is the source of truth.')).toBeTruthy()
    expect(screen.getByText('0 / 2')).toBeTruthy()
  })

  it('clicking a checkbox updates progress and persists localStorage', () => {
    render(<ChecklistArtifactCard artifact={artifact} />)

    fireEvent.click(screen.getByLabelText('Active profile: office-work'))

    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect(JSON.parse(window.localStorage.getItem(storageKey) || '{}')).toEqual({
      'obsidian-profile-vault': true,
      'obsidian-source-truth': false
    })
  })

  it('Mark all and Clear update all items', () => {
    render(<ChecklistArtifactCard artifact={artifact} />)

    fireEvent.click(screen.getByRole('button', { name: 'Mark all' }))
    expect(screen.getByText('2 / 2')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.getByText('0 / 2')).toBeTruthy()
  })

  it('loads persisted state and ignores stale stored ids', () => {
    window.localStorage.setItem(
      storageKey,
      JSON.stringify({
        'gone-item': true,
        'obsidian-profile-vault': true
      })
    )

    render(<ChecklistArtifactCard artifact={artifact} />)

    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect(screen.getByLabelText('Active profile: office-work')).toHaveProperty('checked', true)
  })

  it('renders HTML-looking labels as text only', () => {
    render(
      <ChecklistArtifactCard
        artifact={{
          ...artifact,
          items: [{ id: 'script-label', label: '<img src=x onerror=alert(1)><script>alert(1)</script>' }]
        }}
      />
    )

    expect(screen.getByText('<img src=x onerror=alert(1)><script>alert(1)</script>')).toBeTruthy()
    expect(document.querySelector('script')).toBeNull()
    expect(document.querySelector('img')).toBeNull()
  })

  it('renders Hebrew checklists right-to-left with localized action labels', () => {
    render(
      <ChecklistArtifactCard
        artifact={{
          ...artifact,
          description: 'רשימה בעברית עם הסבר קצר.',
          items: [
            {
              actions: [
                {
                  copyText: 'דחה את המשימה hebrew-item למחר',
                  id: 'postpone-tomorrow',
                  label: 'דחה למחר'
                },
                {
                  copyText: 'משימה: פרופיל פעיל',
                  id: 'copy-task',
                  label: 'העתק לכאן'
                }
              ],
              description: 'הסבר נוסף שמופיע כשורה נפרדת וקריאה יותר.',
              id: 'hebrew-item',
              label: 'פרופיל פעיל: Hermes עובד בפרופיל office-work.'
            }
          ],
          title: 'מדיניות מקור האמת של Obsidian'
        }}
      />
    )

    const card = screen.getByLabelText('מדיניות מקור האמת של Obsidian')

    expect(card.getAttribute('dir')).toBe('rtl')
    expect(screen.getByRole('button', { name: 'סמן הכול' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'נקה' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'דחה למחר' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'העתק לכאן' })).toBeTruthy()
    expect(screen.getByText('הסבר נוסף שמופיע כשורה נפרדת וקריאה יותר.')).toBeTruthy()
  })

  it('renders questionnaire artifacts through the Hermes UI renderer', async () => {
    render(
      <RichCodeBlock
        code={JSON.stringify({
          direction: 'rtl',
          id: 'flowstate-questionnaire',
          questions: [
            {
              helpText: 'אפשר לענות בקצרה.',
              id: 'goal',
              prompt: 'מה המטרה של הבלוק הבא?'
            }
          ],
          title: 'שאלון קצר',
          type: 'questionnaire'
        })}
        fallback={<pre>fallback code block</pre>}
        language="hermes-ui"
      />
    )

    await waitFor(() => expect(screen.getByText('שאלון קצר')).toBeTruthy())
    expect(screen.getByText('מה המטרה של הבלוק הבא?')).toBeTruthy()
    expect(screen.queryByText('fallback code block')).toBeNull()
  })

  it('renders questionnaire fences from markdown as an inline card, not Code · hermes-ui', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={[
          '```hermes-ui',
          JSON.stringify(
            {
              direction: 'rtl',
              id: 'obsidian-source-of-truth-questionnaire-live-test',
              title: 'שאלון קצר: מדיניות Obsidian',
              description: 'בדיקת רינדור חיה של questionnaire artifact בתוך Hermes Desktop.',
              questions: [
                {
                  id: 'profile-vault',
                  prompt: 'האם הפרופיל הפעיל הוא office-work וה־vault הקנוני הוא ה־MAIN VULT?',
                  helpText: 'סמן אם זה מוצג לך כפריט אינטראקטיבי ולא כ־JSON.'
                }
              ],
              type: 'questionnaire'
            },
            null,
            2
          ),
          '```'
        ].join('\n')}
      />
    )

    await waitFor(() => expect(screen.getByText('שאלון קצר: מדיניות Obsidian')).toBeTruthy())
    expect(screen.getByText('האם הפרופיל הפעיל הוא office-work וה־vault הקנוני הוא ה־MAIN VULT?')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="questionnaire"]')).toBeTruthy()
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })

  it('normalizes Streamdown language-* class names before rich fence lookup', async () => {
    render(
      <RichCodeBlock
        code={JSON.stringify({
          direction: 'rtl',
          id: 'normalized-questionnaire',
          questions: [{ id: 'q1', prompt: 'שאלה קצרה?' }],
          title: 'שאלון מנורמל',
          type: 'questionnaire'
        })}
        fallback={<pre>fallback code block</pre>}
        language=" language-hermes-ui "
      />
    )

    await waitFor(() => expect(screen.getByText('שאלון מנורמל')).toBeTruthy())
    expect(screen.queryByText('fallback code block')).toBeNull()
  })

  it('sends checklist actions to the composer without requiring copy paste', () => {
    render(
      <ChecklistArtifactCard
        artifact={{
          ...artifact,
          direction: 'rtl',
          items: [
            {
              actions: [
                {
                  id: 'relevant-today',
                  label: 'רלוונטית להיום',
                  submitText:
                    'החלטת triage למשימת FlowState\nID: 477d9abb-4164-499c-8918-d48f09bf312a\nכותרת: להגיש משרות ל10+2\nהחלטה: רלוונטית להיום\nנא להציג לי preview לפני שינוי אמיתי ב־FlowState.'
                }
              ],
              id: 'flowstate-task',
              label: 'להגיש משרות ל10+2'
            }
          ],
          title: 'FlowState — batch הבא'
        }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'רלוונטית להיום' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.stringContaining('477d9abb'), { target: 'main' })
    expect(screen.getByRole('button', { name: 'נשלח' })).toBeTruthy()
  })

  it('renders a compact task triage card with date and priority controls', () => {
    render(
      <TaskTriageArtifactCard
        artifact={{
          direction: 'rtl',
          id: 'flowstate-task-triage',
          task: {
            dueDate: '2026-07-08',
            id: 'af6d08a0-ec26-41eb-b8c2-2a3c19637c2f',
            priority: 'medium',
            status: 'todo',
            title: 'לסדר את המקרר'
          },
          title: 'FlowState — החלטה אחת עכשיו',
          type: 'task-triage'
        }}
      />
    )

    expect(screen.getByText('לסדר את המקרר')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'רלוונטית להיום' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'לא להיום' })).toBeTruthy()
    expect(screen.getByLabelText('שנה תאריך')).toHaveProperty('value', '2026-07-08')
    expect(screen.getByLabelText('שנה דחיפות')).toHaveProperty('value', 'medium')
  })


  it('collects FlowState batch decisions and submits them to Hermes behind the scenes', () => {
    render(
      <FlowStateTaskBatchCard
        artifact={{
          direction: 'rtl',
          id: 'flowstate-batch',
          tasks: [
            {
              dueDate: '2026-07-06',
              id: '477d9abb-4164-499c-8918-d48f09bf312a',
              priority: 'medium',
              rationale: 'משימה חיצונית עם ערך, אבל לא חייבת להיות היום אם אין חלון עבודה.',
              recommendation: 'today',
              recommendedDueDate: '2026-07-08',
              recommendedPriority: 'high',
              status: 'todo',
              title: 'להגיש משרות ל10+2'
            }
          ],
          title: 'FlowState — המלצות assistant',
          type: 'flowstate-task-batch'
        }}
      />
    )

    expect(screen.getByText('להגיש משרות ל10+2')).toBeTruthy()
    expect(screen.getByLabelText('תאריך מוצע')).toHaveProperty('value', '2026-07-08')
    expect(screen.getByLabelText('דחיפות מוצעת')).toHaveProperty('value', 'high')
    fireEvent.click(screen.getByLabelText('להגיש משרות ל10+2 — בוצע'))
    fireEvent.click(screen.getByRole('button', { name: 'שלח החלטות ל־Hermes' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.stringContaining('477d9abb'), { target: 'main' })
    expect(requestComposerSubmit.mock.calls[0]?.[0]).toContain('דחיפות מוצעת: גבוהה')
    expect(requestComposerSubmit.mock.calls[0]?.[0]).toContain('סימון ביצוע: בוצע')
  })

  it('renders a compact FlowState next-block card and submits action text through the composer', () => {
    render(<FlowStateNextBlockCard artifact={nextBlockArtifact} />)

    expect(screen.getByText('לכתוב טיוטה ללקוח')).toBeTruthy()
    expect(screen.getByText('25 דקות')).toBeTruthy()
    expect(screen.getByText('מסמך קצר שמוכן לשליחה לבדיקה.')).toBeTruthy()
    expect(screen.getByText('זה מספיק קטן כדי לפתוח מומנטום בלי להפוך לתכנון יום מלא.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'תראה לי preview לפני שינוי ב־FlowState' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'מאשר להוסיף את הבלוק ל־FlowState' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'תראה לי preview לפני שינוי ב־FlowState' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith(
      'תעשה preview לבלוק הזה ב־FlowState: taskId=task-1 date=2026-07-08 time=10:30 duration=25',
      { target: 'main' }
    )
  })

  it('renders valid hermes-ui rich code blocks and falls back for invalid payloads', async () => {
    const { rerender } = render(
      <RichCodeBlock code={JSON.stringify(artifact)} fallback={<pre>fallback code block</pre>} language="hermes-ui" />
    )

    await waitFor(() => expect(screen.getByText('Obsidian source-of-truth policy')).toBeTruthy())
    expect(screen.queryByText('fallback code block')).toBeNull()

    rerender(<RichCodeBlock code="{ nope" fallback={<pre>fallback code block</pre>} language="hermes-ui" />)

    expect(screen.getByText('fallback code block')).toBeTruthy()
  })
})
