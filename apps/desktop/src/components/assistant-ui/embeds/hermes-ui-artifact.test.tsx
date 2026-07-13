import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MarkdownTextContent } from '@/components/assistant-ui/markdown-text'
import type {
  HermesUiChecklistArtifact,
  HermesUiDayTimelineArtifact,
  HermesUiFlowStateNextBlockArtifact,
  HermesUiFlowStatePlanningSessionArtifact,
  HermesUiFormArtifact,
  HermesUiMiniKanbanArtifact,
  HermesUiMutationPreviewArtifact,
  HermesUiPlanningFunnelArtifact,
  HermesUiTaskBreakdownArtifact,
  HermesUiTaskContextArtifact,
  HermesUiTaskTableArtifact
} from '@/lib/hermes-ui-artifacts'

const requestComposerSubmit = vi.fn()

vi.mock('@/app/chat/composer/focus', () => ({
  requestComposerSubmit: (text: string, opts?: unknown) => requestComposerSubmit(text, opts)
}))

import {
  ChecklistArtifactCard,
  DayTimelineCard,
  FlowStateNextBlockCard,
  FlowStatePlanningSessionCard,
  FlowStateTaskBatchCard,
  FormArtifactCard,
  MiniKanbanCard,
  MutationPreviewCard,
  PlanningFunnelCard,
  TaskBreakdownCard,
  TaskContextCard,
  TaskGraphCard,
  TaskTableCard,
  TaskTriageArtifactCard,
  UrgencyEnergyMatrixCard,
  WorkloadBarsCard
} from './hermes-ui-artifact'
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

const formArtifact: HermesUiFormArtifact = {
  direction: 'rtl',
  fields: [
    { id: 'outcome', label: 'מה חשוב היום?', required: true, type: 'short-text' },
    {
      id: 'energy',
      label: 'אנרגיה',
      options: [{ label: 'גבוהה', value: 'high' }],
      type: 'single-choice'
    }
  ],
  id: 'morning-outcome',
  submitLabel: 'שלח ל־Hermes',
  title: 'תכנון היום',
  type: 'form'
}

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


const planningSessionArtifact: HermesUiFlowStatePlanningSessionArtifact = {
  categories: [
    {
      count: 2,
      examples: [
        { dueDate: '2026-07-09', id: 'task-1', priority: 'high', title: 'להחליט מה הבלוק הבא' }
      ],
      id: 'work-pressure',
      label: 'עומס עבודה',
      recommendation: 'להוציא רק בלוק אחד לביצוע ולא לפתוח את כל הבקלוג.',
      tone: 'work'
    }
  ],
  direction: 'rtl',
  id: 'flowstate-day-plan',
  mode: 'day-start',
  nextBlock: {
    doneEnough: 'להבין מה הדבר הקטן הבא ולהתחיל אותו.',
    durationMinutes: 25,
    id: 'next-block',
    rationale: 'זה מוריד עומס בלי להפוך לתכנון ארוך.',
    taskIds: ['task-1'],
    title: 'הבלוק הבא'
  },
  tasks: [
    {
      dueDate: '2026-07-09',
      id: 'task-1',
      priority: 'high',
      rationale: 'קטן וברור מספיק להתחלה.',
      recommendation: 'today',
      recommendedDueDate: '2026-07-09',
      recommendedPriority: 'high',
      status: 'todo',
      title: 'להחליט מה הבלוק הבא'
    }
  ],
  title: 'תכנון היום',
  type: 'flowstate-planning-session'
}

beforeEach(() => {
  window.localStorage.clear()
  requestComposerSubmit.mockClear()
})

afterEach(cleanup)

describe('ChecklistArtifactCard', () => {

  it('validates, persists, and submits a deterministic RTL form response', () => {
    const { unmount } = render(<FormArtifactCard artifact={formArtifact} />)

    expect(document.querySelector('[data-hermes-ui-artifact="form"]')?.getAttribute('dir')).toBe('rtl')
    fireEvent.click(screen.getByRole('button', { name: 'שלח ל־Hermes' }))
    expect(screen.getByText('שדה חובה')).toBeTruthy()
    expect(requestComposerSubmit).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('מה חשוב היום?'), { target: { value: 'לסיים את המצגת' } })
    fireEvent.click(screen.getByLabelText('גבוהה'))
    unmount()

    render(<FormArtifactCard artifact={formArtifact} />)
    expect((screen.getByLabelText('מה חשוב היום?') as HTMLInputElement).value).toBe('לסיים את המצגת')
    const submitButton = screen.getByRole('button', { name: 'שלח ל־Hermes' })
    fireEvent.click(submitButton)
    fireEvent.click(submitButton)

    expect(requestComposerSubmit).toHaveBeenCalledTimes(1)
    expect(submitButton).toHaveProperty('disabled', true)
    const message = requestComposerSubmit.mock.calls[0]?.[0]
    const response = JSON.parse(message.slice('Hermes UI form response:\n'.length))

    expect(response).toEqual({
      actionId: 'submit',
      artifactId: 'morning-outcome',
      idempotencyKey: expect.stringMatching(/^form:/),
      schemaVersion: 1,
      type: 'form-response',
      values: { outcome: 'לסיים את המצגת', energy: 'high' }
    })
    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.any(String), {
      allowWhileBusy: true,
      hidden: true,
      target: 'main'
    })
  })

  it('prefills a normalized numeric default in the rendered form', () => {
    render(
      <FormArtifactCard
        artifact={{
          fields: [{ defaultValue: '25', id: 'duration', label: 'משך בדקות', required: true, type: 'number' }],
          id: 'laundry-duration',
          type: 'form'
        }}
      />
    )

    expect((screen.getByLabelText('משך בדקות') as HTMLInputElement).value).toBe('25')
  })

  it('accepts, persists, and submits canonical 24-hour time in an RTL form', () => {
    const timeForm: HermesUiFormArtifact = {
      direction: 'rtl',
      fields: [{ id: 'scheduled_time', label: 'שעת סיום קשיחה', required: true, type: 'time' }],
      id: 'hard-stop-time',
      submitLabel: 'בנה את תכנית היום',
      type: 'form'
    }

    const { unmount } = render(<FormArtifactCard artifact={timeForm} />)

    const input = screen.getByLabelText('שעת סיום קשיחה') as HTMLInputElement

    expect(input.type).toBe('text')
    expect(input.dir).toBe('ltr')
    expect(input.inputMode).toBe('numeric')
    expect(input.placeholder).toBe('HH:mm')

    fireEvent.change(input, { target: { value: '20:00' } })
    unmount()

    render(<FormArtifactCard artifact={timeForm} />)
    expect(screen.getByLabelText('שעת סיום קשיחה')).toHaveProperty('value', '20:00')
    fireEvent.click(screen.getByRole('button', { name: 'בנה את תכנית היום' }))

    const message = requestComposerSubmit.mock.calls[0]?.[0]
    const response = JSON.parse(message.slice('Hermes UI form response:\n'.length))
    expect(response.values).toEqual({ scheduled_time: '20:00' })
  })

  it('blocks non-canonical or out-of-range time values', () => {
    const timeForm: HermesUiFormArtifact = {
      fields: [{ id: 'scheduled_time', label: 'Hard stop', required: true, type: 'time' }],
      id: 'hard-stop-validation',
      submitLabel: 'Submit',
      type: 'form'
    }

    render(<FormArtifactCard artifact={timeForm} />)
    const input = screen.getByLabelText('Hard stop')

    for (const value of ['8:00', '24:00', '20:']) {
      fireEvent.change(input, { target: { value } })
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
      expect(requestComposerSubmit).not.toHaveBeenCalled()
      expect(screen.getByText('Use 24-hour time (HH:mm)')).toBeTruthy()
    }
  })

  it('renders FlowState planning sessions with categories, next block, task controls, and submit routing', () => {
    render(<FlowStatePlanningSessionCard artifact={planningSessionArtifact} />)

    expect(screen.getByText('תכנון היום')).toBeTruthy()
    expect(screen.getByText('עומס עבודה')).toBeTruthy()
    expect(screen.getAllByText('הבלוק הבא').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'היום' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'שלח החלטות ל־Hermes' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'היום' }))
    fireEvent.click(screen.getByRole('button', { name: 'שלח החלטות ל־Hermes' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.stringContaining('FlowState'), { target: 'main' })
    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.stringContaining('task-1'), { target: 'main' })
  })

  it('renders flowstate-planning-session rich code blocks as an inline card', async () => {
    render(
      <RichCodeBlock
        code={JSON.stringify(planningSessionArtifact, null, 2)}
        fallback={<pre>fallback code block</pre>}
        language="hermes-ui"
      />
    )

    await waitFor(() => expect(screen.getByText('תכנון היום')).toBeTruthy())
    expect(document.querySelector('[data-hermes-ui-artifact="flowstate-planning-session"]')).toBeTruthy()
    expect(screen.queryByText('fallback code block')).toBeNull()
  })

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

  it('renders valid hermes-ui blocks and offers recovery without exposing invalid JSON', async () => {
    const { rerender } = render(
      <RichCodeBlock code={JSON.stringify(artifact)} fallback={<pre>fallback code block</pre>} language="hermes-ui" />
    )

    await waitFor(() => expect(screen.getByText('Obsidian source-of-truth policy')).toBeTruthy())
    expect(screen.queryByText('fallback code block')).toBeNull()

    rerender(<RichCodeBlock code="{ nope" fallback={<pre>fallback code block</pre>} language="hermes-ui" />)

    expect(screen.queryByText('fallback code block')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('Interactive form could not be shown')
    fireEvent.click(screen.getByRole('button', { name: 'Ask Hermes to resend' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith(
      expect.stringContaining('resend it as one complete valid hermes-ui artifact'),
      { target: 'main' }
    )
  })

  it('hides incomplete hermes-ui JSON while the form is still streaming', () => {
    const { rerender } = render(
      <RichCodeBlock code={'{"type":"form","fields":['} fallback={<pre>raw partial JSON</pre>} language="hermes-ui" streaming />
    )

    expect(screen.getByRole('status').textContent).toBe('Preparing interactive form…')
    expect(screen.queryByText('raw partial JSON')).toBeNull()

    rerender(
      <RichCodeBlock code={'{"type":"form","fields":['} fallback={<pre>raw partial JSON</pre>} language="hermes-ui" streaming={false} />
    )

    expect(screen.queryByText('raw partial JSON')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('Interactive form could not be shown')
  })
})


describe('Planning interview primitives', () => {
  const taskBreakdown: HermesUiTaskBreakdownArtifact = {
    direction: 'rtl',
    id: 'breakdown-vague-task',
    scope: 'working-session',
    steps: [
      { id: 'discover', title: 'לברר מה חסר', doneEnough: 'יש רשימה של שלוש אי־ודאויות' },
      { id: 'draft', title: 'לכתוב טיוטה קצרה', doneEnough: 'יש טיוטה שאפשר לקבל עליה תגובה', estimateMinutes: 25 },
      { id: 'polish', title: 'ללטש את כל המימוש', doneEnough: 'הכול מושלם', optional: true }
    ],
    stoppingRule: 'עוצרים אחרי טיוטה שניתנת לבדיקה; ליטוש מלא נשאר אופציונלי.',
    submitLabel: 'עדכן את הפירוק',
    targetOutcome: 'להפוך משימה עמומה להתקדמות שאפשר להתחיל עכשיו',
    task: { id: 'task-vague', title: 'לקדם את אתר בינה' },
    title: 'פירוק עבודה לעריכה',
    type: 'task-breakdown'
  }

  const planningFunnel: HermesUiPlanningFunnelArtifact = {
    direction: 'rtl',
    id: 'planning-funnel-test',
    steps: [
      { id: 'capture', label: 'לקלוט משימות', status: 'done' },
      { id: 'context', label: 'להבין הקשר', status: 'current' },
      { id: 'breakdown', label: 'לפרק לצעדים קטנים', status: 'pending' },
      { id: 'schedule', label: 'לשבץ לפי זמן ואנרגיה', status: 'pending' }
    ],
    title: 'משפך תכנון קצר',
    type: 'planning-funnel'
  }

  const taskContext: HermesUiTaskContextArtifact = {
    actions: [
      {
        id: 'ask-context',
        label: 'שאל שאלה אחת',
        submitText: 'תשאל שאלה אחת קצרה על ההקשר של המשימה הזו.'
      }
    ],
    connections: ['בריאות', 'הכנה לפגישה'],
    direction: 'rtl',
    id: 'task-context-test',
    progress: 'לדעת אם צריך פעולה לפני הפגישה',
    task: {
      dueDate: '2026-07-06',
      id: 'task-health-tests',
      priority: 'high',
      status: 'todo',
      title: 'לראות שאני מקבל את הבדיקות לפני הפגישה עם הרופאה'
    },
    title: 'כרטיס הבנת משימה',
    type: 'task-context',
    unknowns: ['מתי הפגישה?', 'האם צריך להתקשר או רק לבדוק?']
  }

  it('renders a compact planning funnel', () => {
    render(<PlanningFunnelCard artifact={planningFunnel} />)

    expect(screen.getByText('משפך תכנון קצר')).toBeTruthy()
    expect(screen.getByText('להבין הקשר')).toBeTruthy()
    expect(screen.getByText('עכשיו')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="planning-funnel"]')).toBeTruthy()
  })

  it('renders a task context card and routes the next question to Hermes', () => {
    render(<TaskContextCard artifact={taskContext} />)

    expect(screen.getByText('כרטיס הבנת משימה')).toBeTruthy()
    expect(screen.getByText('מה נחשב התקדמות')).toBeTruthy()
    expect(screen.getByText('מתי הפגישה?')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'שאל שאלה אחת' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('תשאל שאלה אחת קצרה על ההקשר של המשימה הזו.', { target: 'main' })
  })

  it('lets the user edit, reorder, remove, and submit a bounded task breakdown', () => {
    render(<TaskBreakdownCard artifact={taskBreakdown} />)

    expect(screen.getByText('פירוק עבודה לעריכה')).toBeTruthy()
    expect(screen.getByText('סשן עבודה')).toBeTruthy()
    expect(screen.getByText('אופציונלי')).toBeTruthy()

    fireEvent.change(screen.getByDisplayValue('לכתוב טיוטה קצרה'), { target: { value: 'לכתוב שלד של הטיוטה' } })
    fireEvent.change(screen.getByDisplayValue('יש טיוטה שאפשר לקבל עליה תגובה'), {
      target: { value: 'יש שלד עם כותרת ושלושה סעיפים' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'העבר את לכתוב שלד של הטיוטה למעלה' }))
    fireEvent.click(screen.getByRole('button', { name: 'הסר את ללטש את כל המימוש' }))
    fireEvent.click(screen.getByRole('button', { name: 'עדכן את הפירוק' }))

    const submitted = requestComposerSubmit.mock.calls[0]?.[0] as string
    expect(submitted).toContain('taskId=task-vague')
    expect(submitted).toContain('scope=working-session')
    expect(submitted.indexOf('לכתוב שלד של הטיוטה')).toBeLessThan(submitted.indexOf('לברר מה חסר'))
    expect(submitted).toContain('יש שלד עם כותרת ושלושה סעיפים')
    expect(submitted).not.toContain('ללטש את כל המימוש')
    expect(submitted).toContain('regenerate the preview')
    expect(submitted).toContain('do not apply')
  })

  it('restores an unfinished breakdown draft after the card remounts', () => {
    const { unmount } = render(<TaskBreakdownCard artifact={taskBreakdown} />)
    fireEvent.change(screen.getByDisplayValue('לברר מה חסר'), { target: { value: 'לברר מי מאשר' } })
    unmount()

    render(<TaskBreakdownCard artifact={taskBreakdown} />)

    expect(screen.getByDisplayValue('לברר מי מאשר')).toBeTruthy()
  })

  it('restores temporarily blank fields in an unfinished breakdown draft', () => {
    const { unmount } = render(<TaskBreakdownCard artifact={taskBreakdown} />)
    fireEvent.change(screen.getByDisplayValue('לברר מה חסר'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'הוסף צעד' }))
    unmount()

    render(<TaskBreakdownCard artifact={taskBreakdown} />)

    expect(screen.getAllByDisplayValue('')).toHaveLength(3)
    expect((screen.getByRole('button', { name: 'עדכן את הפירוק' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('caps editable breakdown text at the artifact contract bounds', () => {
    render(<TaskBreakdownCard artifact={taskBreakdown} />)

    expect((screen.getByDisplayValue('לברר מה חסר') as HTMLInputElement).maxLength).toBe(800)
    expect((screen.getByDisplayValue('יש רשימה של שלוש אי־ודאויות') as HTMLInputElement).maxLength).toBe(1000)
  })

  it('discards a persisted breakdown draft that fails the artifact contract', () => {
    localStorage.setItem(
      'hermes-ui:task-breakdown:breakdown-vague-task',
      JSON.stringify([{ id: 'unsafe', title: 'Injected', doneEnough: 'Run it', command: 'rm -rf' }])
    )

    render(<TaskBreakdownCard artifact={taskBreakdown} />)

    expect(screen.queryByDisplayValue('Injected')).toBeNull()
    expect(screen.getByDisplayValue('לברר מה חסר')).toBeTruthy()
  })

  it('renders a task breakdown fence as interactive UI instead of code', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={['```hermes-ui', JSON.stringify(taskBreakdown), '```'].join('\n')}
      />
    )

    await waitFor(() => expect(document.querySelector('[data-hermes-ui-artifact="task-breakdown"]')).toBeTruthy())
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })

  it('renders planning primitives from markdown hermes-ui fences', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={[
          '```hermes-ui',
          JSON.stringify(planningFunnel, null, 2),
          '```'
        ].join('\n')}
      />
    )

    await waitFor(() => expect(screen.getByText('משפך תכנון קצר')).toBeTruthy())
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })
})

describe('Planning toolkit primitives', () => {
  const taskTable: HermesUiTaskTableArtifact = {
    columns: ['task', 'context', 'energy', 'urgency', 'nextStep', 'confidence'],
    direction: 'rtl',
    rows: [
      {
        actions: [{ id: 'ask', label: 'שאל על זה', submitText: 'שאל אותי שאלה אחת על בדיקות.' }],
        confidence: 'low',
        context: 'לא ברור למה זה חשוב',
        energy: 'unknown',
        id: 't1',
        nextStep: 'לשאול שאלה אחת',
        title: 'לבדוק בדיקות',
        urgency: 'high'
      },
      { confidence: 'medium', context: 'לקוח', energy: 'medium', id: 't2', nextStep: 'טיוטה', title: 'מייל ללקוח', urgency: 'medium' },
      { confidence: 'high', context: 'בית', energy: 'low', id: 't3', nextStep: '10 דקות', title: 'לסדר שולחן', urgency: 'low' }
    ],
    title: 'השוואת משימות',
    type: 'task-table'
  }

  const miniKanban: HermesUiMiniKanbanArtifact = {
    direction: 'rtl',
    lanes: [
      {
        id: 'today',
        tasks: [{ actions: [{ id: 'route', label: 'בחר להיום', submitText: 'שים את t1 ב־today preview.' }], id: 't1', title: 'טיוטה ללקוח' }],
        title: 'היום'
      },
      { id: 'need-context', tasks: [{ id: 't2', note: 'צריך להבין משמעות', title: 'בדיקות' }], title: 'צריך הקשר' }
    ],
    title: 'מיון קצר',
    type: 'mini-kanban'
  }

  const dayTimeline: HermesUiDayTimelineArtifact = {
    blocks: [
      { actions: [{ id: 'accept-block', label: 'השתמש בבלוק', submitText: 'תציג preview לבלוק הזה.' }], endTime: '10:25', id: 'b1', kind: 'focus', label: 'טיוטה ללקוח', startTime: '10:00', status: 'candidate' },
      { durationMinutes: 15, id: 'float-1', kind: 'floating', label: 'בדיקה קצרה', status: 'planned' }
    ],
    currentTime: '09:45',
    date: '2026-07-09',
    direction: 'rtl',
    title: 'תכנון יום אפשרי',
    type: 'day-timeline'
  }

  const mutationPreview: HermesUiMutationPreviewArtifact = {
    actions: [
      { id: 'approve', label: 'מאשר את השינויים האלה', submitText: 'מאשר לבצע את preview הזה ב־FlowState.' },
      { id: 'revise', label: 'צריך תיקון', submitText: 'תעדכן את ה־preview לפני שינוי.' },
      { id: 'cancel', label: 'בטל', submitText: 'בטל את ה־preview ואל תשנה את FlowState.' }
    ],
    changes: [
      {
        after: { date: '2026-07-09', time: '10:00' },
        before: { date: null, status: 'todo' },
        operation: 'schedule-instance',
        risk: 'low',
        taskId: 't1',
        title: 'טיוטה ללקוח',
        untouched: ['priority', 'title']
      }
    ],
    direction: 'rtl',
    title: 'Preview לפני שינוי',
    type: 'mutation-preview'
  }

  it('renders task-table with unknowns and routes row actions', () => {
    render(<TaskTableCard artifact={taskTable} />)

    expect(screen.getByText('השוואת משימות')).toBeTruthy()
    expect(screen.getByText('לבדוק בדיקות')).toBeTruthy()
    expect(screen.getByText('לא ידוע')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="task-table"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'שאל על זה' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('שאל אותי שאלה אחת על בדיקות.', { target: 'main' })
  })

  it('renders mini-kanban lanes and routes task actions', () => {
    render(<MiniKanbanCard artifact={miniKanban} />)

    expect(screen.getByText('מיון קצר')).toBeTruthy()
    expect(screen.getByText('צריך הקשר')).toBeTruthy()
    expect(screen.getByText('צריך להבין משמעות')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="mini-kanban"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'בחר להיום' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('שים את t1 ב־today preview.', { target: 'main' })
  })

  it('renders day-timeline with current time and routes block actions', () => {
    render(<DayTimelineCard artifact={dayTimeline} />)

    expect(screen.getByText('תכנון יום אפשרי')).toBeTruthy()
    expect(screen.getAllByText('09:45').length).toBeGreaterThan(0)
    expect(screen.getByText('טיוטה ללקוח')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="day-timeline"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'השתמש בבלוק' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('תציג preview לבלוק הזה.', { target: 'main' })
  })

  it('renders mutation-preview as preview-only and routes full approval labels', () => {
    render(<MutationPreviewCard artifact={mutationPreview} />)

    expect(screen.getByText('Preview לפני שינוי')).toBeTruthy()
    expect(screen.getByText('Preview בלבד. לא מתבצע שינוי ב־FlowState מהרכיב הזה.')).toBeTruthy()
    expect(screen.getByText('schedule-instance')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="mutation-preview"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'מאשר את השינויים האלה' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('מאשר לבצע את preview הזה ב־FlowState.', { target: 'main' })
  })

  it('renders matrix, workload bars, and task graph primitives', () => {
    render(
      <>
        <UrgencyEnergyMatrixCard
          artifact={{
            cells: [{ label: 'דחוף באנרגיה נמוכה', tasks: [{ id: 't1', priority: 'high', title: 'שיחה קצרה' }], x: 'low', y: 'high' }],
            direction: 'rtl',
            title: 'בחירה לפי אנרגיה',
            type: 'urgency-energy-matrix',
            xAxis: 'energy',
            yAxis: 'urgency'
          }}
        />
        <WorkloadBarsCard
          artifact={{
            bars: [{ id: 'overdue', label: 'באיחור', max: 10, tone: 'warning', value: 3 }],
            direction: 'rtl',
            title: 'עומס כללי',
            type: 'workload-bars'
          }}
        />
        <TaskGraphCard
          artifact={{
            direction: 'rtl',
            edges: [{ label: 'תלוי ב', source: 'task', target: 'person' }],
            nodes: [
              { id: 'task', kind: 'task', label: 'לשלוח הצעה' },
              { id: 'person', kind: 'person', label: 'לקוח' }
            ],
            title: 'קשרי משימה',
            type: 'task-graph'
          }}
        />
      </>
    )

    expect(screen.getByText('בחירה לפי אנרגיה')).toBeTruthy()
    expect(screen.getByText('דחוף באנרגיה נמוכה')).toBeTruthy()
    expect(screen.getByText('עומס כללי')).toBeTruthy()
    expect(screen.getByText('באיחור')).toBeTruthy()
    expect(screen.getByText('קשרי משימה')).toBeTruthy()
    expect(screen.getByText(/תלוי ב/)).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="urgency-energy-matrix"]')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="workload-bars"]')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="task-graph"]')).toBeTruthy()
  })

  it('renders a new primitive from markdown hermes-ui fences', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={[
          '```hermes-ui',
          JSON.stringify(mutationPreview, null, 2),
          '```'
        ].join('\n')}
      />
    )

    await waitFor(() => expect(screen.getByText('Preview לפני שינוי')).toBeTruthy())
    expect(document.querySelector('[data-hermes-ui-artifact="mutation-preview"]')).toBeTruthy()
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })
})
