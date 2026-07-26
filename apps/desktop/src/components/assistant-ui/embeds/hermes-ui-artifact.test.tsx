import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ComposerScopeProvider, MAIN_COMPOSER_SCOPE } from '@/app/chat/composer/scope'
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
  HermesUiTaskProfileReviewArtifact,
  HermesUiTaskTableArtifact,
  HermesUiWeekPlannerArtifact
} from '@/lib/hermes-ui-artifacts'

const requestComposerSubmit = vi.fn()
const continuePersonalAssistantInterview = vi.fn()
// Default: the live-state probe is unavailable, so cards render exactly as before.
// Tests that care about replayed cards override this per case.
const fetchCurrentPersonalAssistantInterview = vi.fn<
  () => Promise<{ interview: { interviewId?: unknown; interviewRevision?: unknown } | null; nextArtifact: unknown }>
>(async () => {
  throw new Error('live interview probe unavailable')
})
const respondToPersonalAssistantInterview = vi.fn()

vi.mock('@/app/chat/composer/focus', () => ({
  requestComposerSubmit: (text: string, opts?: unknown) => requestComposerSubmit(text, opts)
}))

vi.mock('@/store/personal-assistant', () => ({
  continuePersonalAssistantInterview: (text: string, runtimeSessionId?: string) =>
    continuePersonalAssistantInterview(text, runtimeSessionId),
  fetchCurrentPersonalAssistantInterview: () => fetchCurrentPersonalAssistantInterview(),
  respondToPersonalAssistantInterview: (params: unknown) => respondToPersonalAssistantInterview(params)
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
  TaskProfileReviewCard,
  stableInterviewRequestId,
  TaskTableCard,
  TaskTriageArtifactCard,
  UrgencyEnergyMatrixCard,
  WeekPlannerCard,
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
      examples: [{ dueDate: '2026-07-09', id: 'task-1', priority: 'high', title: 'להחליט מה הבלוק הבא' }],
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
  continuePersonalAssistantInterview.mockReset()
  continuePersonalAssistantInterview.mockResolvedValue(undefined)
  fetchCurrentPersonalAssistantInterview.mockReset()
  fetchCurrentPersonalAssistantInterview.mockRejectedValue(new Error('live interview probe unavailable'))
  respondToPersonalAssistantInterview.mockReset()
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
      continuationInstruction:
        'Continue the active workflow after processing this response. Supporting tool results are not completion; stop only when the workflow is complete or another user answer is required.',
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

    expect(input.type).toBe('time')
    expect(input.dir).toBe('ltr')
    expect(input.inputMode).toBe('numeric')
    expect(input.placeholder).toBe('HH:mm')
    expect(input.step).toBe('60')

    fireEvent.change(input, { target: { value: '20:00' } })
    unmount()

    render(<FormArtifactCard artifact={timeForm} />)
    expect(screen.getByLabelText('שעת סיום קשיחה')).toHaveProperty('value', '20:00')
    fireEvent.click(screen.getByRole('button', { name: 'בנה את תכנית היום' }))

    const message = requestComposerSubmit.mock.calls[0]?.[0]
    const response = JSON.parse(message.slice('Hermes UI form response:\n'.length))
    expect(response.values).toEqual({ scheduled_time: '20:00' })
  })

  it('treats an entered other time as the required single-choice selection', () => {
    const mealPrepForm: HermesUiFormArtifact = {
      direction: 'rtl',
      fields: [
        {
          id: 'meal_prep_start',
          label: 'שעת התחלה מועדפת',
          options: [
            { label: '19:30, כבלוק ערב מרכזי', value: '19:30, כבלוק ערב מרכזי' },
            { label: '20:00, הכנה מצומצמת לפני האימון', value: '20:00, הכנה מצומצמת לפני האימון' }
          ],
          required: true,
          type: 'single-choice'
        },
        { id: 'correction', label: 'שעה אחרת', required: false, type: 'time' }
      ],
      id: 'evening-meal-prep-start',
      submitLabel: 'לבחור שעה',
      type: 'form'
    }

    const { unmount } = render(<FormArtifactCard artifact={mealPrepForm} />)
    const otherTime = screen.getByLabelText('שעה אחרת') as HTMLInputElement

    expect(otherTime.type).toBe('time')
    fireEvent.change(otherTime, { target: { value: '20:30' } })
    unmount()

    render(<FormArtifactCard artifact={mealPrepForm} />)
    fireEvent.click(screen.getByRole('button', { name: 'לבחור שעה' }))

    expect(screen.queryByText('שדה חובה')).toBeNull()
    const message = requestComposerSubmit.mock.calls[0]?.[0]
    const response = JSON.parse(message.slice('Hermes UI form response:\n'.length))
    expect(response.values).toEqual({ correction: '20:30', meal_prep_start: '20:30' })
  })

  it('accepts a first-class custom answer for a required single-choice field', () => {
    const customForm: HermesUiFormArtifact = {
      fields: [
        {
          allowCustomAnswer: true,
          customAnswerLabel: 'My own answer',
          id: 'next_move',
          label: 'What should happen next?',
          options: [
            { label: 'Plan', value: 'plan' },
            { label: 'Delegate', value: 'delegate' }
          ],
          required: true,
          type: 'single-choice'
        }
      ],
      id: 'next-move',
      submitLabel: 'Continue',
      type: 'form'
    }

    const { unmount } = render(<FormArtifactCard artifact={customForm} />)
    fireEvent.change(screen.getByLabelText('My own answer'), { target: { value: 'Call the clinic first' } })
    unmount()

    render(<FormArtifactCard artifact={customForm} />)
    expect(screen.getByLabelText('My own answer')).toHaveProperty('value', 'Call the clinic first')
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.queryByText('Required field')).toBeNull()
    const message = requestComposerSubmit.mock.calls[0]?.[0]
    const response = JSON.parse(message.slice('Hermes UI form response:\n'.length))
    expect(response.values).toEqual({ next_move: 'Call the clinic first' })
  })

  it('submits selected options and custom text from a multi-choice field', () => {
    const customForm: HermesUiFormArtifact = {
      fields: [
        {
          allowCustomAnswer: true,
          customAnswerLabel: 'Add another area',
          id: 'areas',
          label: 'Areas',
          options: [
            { label: 'Work', value: 'work' },
            { label: 'Home', value: 'home' }
          ],
          required: true,
          type: 'multi-choice'
        }
      ],
      id: 'areas',
      submitLabel: 'Continue',
      type: 'form'
    }

    render(<FormArtifactCard artifact={customForm} />)
    fireEvent.click(screen.getByLabelText('Work'))
    fireEvent.change(screen.getByLabelText('Add another area'), { target: { value: 'Health' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    const message = requestComposerSubmit.mock.calls[0]?.[0]
    const response = JSON.parse(message.slice('Hermes UI form response:\n'.length))
    expect(response.values).toEqual({ areas: ['work', 'Health'] })
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
      expect(input).toHaveProperty('value', '')
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
      expect(requestComposerSubmit).not.toHaveBeenCalled()
      expect(screen.getByText('Required field')).toBeTruthy()
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

  it('hides an unfinished hermes-ui fence after leading prose while streaming', () => {
    render(
      <MarkdownTextContent
        isRunning
        text={['אני מכין את התוכנית.', '```hermes-ui', '{"type":"day-timeline","title":"מחר","items":['].join('\n')}
      />
    )

    expect(screen.getByText('אני מכין את התוכנית.')).toBeTruthy()
    expect(screen.queryByText(/day-timeline/)).toBeNull()
    expect(screen.queryByText(/hermes-ui/)).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('Preparing interactive form…')
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
      expect.stringMatching(/resend it as one complete valid hermes-ui artifact/i),
      { target: 'main' }
    )
  })

  it('gives Hermes the flexible table contract when a task-table needs recovery', () => {
    render(
      <RichCodeBlock
        code={JSON.stringify({
          columns: [{ key: 'status', label: 'מצב' }],
          rows: [{ cells: { status: { nested: 'unsafe' } }, id: 'r1', title: 'משימה' }],
          type: 'task-table'
        })}
        fallback={<pre>fallback code block</pre>}
        language="hermes-ui"
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Ask Hermes to resend' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.stringMatching(/column.*key.*label.*cells.*scalar/i), {
      target: 'main'
    })
  })

  it('hides incomplete hermes-ui JSON while the form is still streaming', () => {
    const { rerender } = render(
      <RichCodeBlock
        code={'{"type":"form","fields":['}
        fallback={<pre>raw partial JSON</pre>}
        language="hermes-ui"
        streaming
      />
    )

    expect(screen.getByRole('status').textContent).toBe('Preparing interactive form…')
    expect(screen.queryByText('raw partial JSON')).toBeNull()

    rerender(
      <RichCodeBlock
        code={'{"type":"form","fields":['}
        fallback={<pre>raw partial JSON</pre>}
        language="hermes-ui"
        streaming={false}
      />
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

  const taskProfileReview: HermesUiTaskProfileReviewArtifact = {
    direction: 'rtl',
    interviewId: 'weekly-1',
    profileFields: [
      { id: 'urgency', label: 'דחיפות', value: 'בינונית' },
      { id: 'doneEnough', label: 'מה נחשב מספיק', value: 'יש תשובה והצעד הבא ברור' }
    ],
    progress: { current: 2, total: 8 },
    question: {
      allowCustomAnswer: true,
      id: 'urgency',
      label: 'עד כמה זה דחוף השבוע?',
      options: [
        { label: 'גבוהה', value: 'high' },
        { label: 'בינונית', value: 'medium' }
      ],
      profileFieldId: 'urgency',
      required: true,
      type: 'single-choice'
    },
    revision: 8,
    task: { id: 'pet-results', title: 'בדיקת תוצאות PET' },
    type: 'task-profile-review'
  }

  it('renders the first profile question without an empty profile editor', () => {
    render(
      <TaskProfileReviewCard
        artifact={{
          ...taskProfileReview,
          profileFields: [],
          progress: { current: 1, total: 3 },
          question: {
            ...taskProfileReview.question,
            profileFieldId: 'urgency'
          }
        }}
      />
    )

    expect(screen.getByText('עד כמה זה דחוף השבוע?')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'עריכת פרופיל המשימה' })).toBeNull()
    expect(screen.getByRole('button', { name: 'אישור והמשך' })).toBeTruthy()
  })

  it('renders daily grounding as one calm question and confirms it was saved for today', async () => {
    respondToPersonalAssistantInterview.mockResolvedValue({
      duplicate: false,
      interview: { revision: 2 },
      receipt: {},
      stateVersion: 13
    })
    render(
      <TaskProfileReviewCard
        artifact={{
          ...taskProfileReview,
          profileFields: [{ id: 'energy', label: 'אנרגיה כרגע', value: '' }],
          progress: { current: 1, total: 4 },
          question: {
            ...taskProfileReview.question,
            description: 'כמה אנרגיה יש לך כרגע להמשך היום?',
            id: 'energy',
            label: 'אנרגיה כרגע',
            profileFieldId: 'energy'
          },
          task: { id: 'day-context', title: 'תכנון שאר היום' }
        }}
      />
    )

    expect(screen.getByText('כמה אנרגיה יש לך כרגע להמשך היום?')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'עריכת פרופיל המשימה' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'השהיה' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'חזרה למשימה קודמת' })).toBeNull()
    expect(screen.queryByText('—')).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'תשובה אחרת' })).toBeNull()
    expect(screen.getByText('1 / 4').getAttribute('dir')).toBe('ltr')

    fireEvent.click(screen.getByRole('radio', { name: 'תשובה אחרת' }))

    expect(screen.getByRole('textbox', { name: 'תשובה אחרת' })).toBeTruthy()

    fireEvent.click(screen.getByLabelText('גבוהה'))
    fireEvent.click(screen.getByRole('button', { name: 'שמירה והמשך' }))

    await waitFor(() => expect(screen.getByText('נשמר להיום')).toBeTruthy())
  })

  it('progressively reveals a large profile summary', () => {
    render(
      <TaskProfileReviewCard
        artifact={{
          ...taskProfileReview,
          profileFields: Array.from({ length: 20 }, (_, index) => ({
            id: `field-${index}`,
            label: `שדה ${index + 1}`,
            value: `ערך ${index + 1}`
          }))
        }}
      />
    )

    expect(screen.getByText('שדה 1')).toBeTruthy()
    expect(screen.queryByText('שדה 7')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'הצג עוד 14' }))

    expect(screen.getByText('שדה 20')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'הצג פחות' })).toBeTruthy()
  })

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

  it('renders the next committed interview question without a model continuation', async () => {
    respondToPersonalAssistantInterview.mockResolvedValue({
      duplicate: false,
      interview: { revision: 9 },
      nextArtifact: {
        ...taskProfileReview,
        progress: { current: 3, total: 8 },
        question: {
          ...taskProfileReview.question,
          id: 'importance',
          label: 'כמה המשימה חשובה?',
          profileFieldId: 'importance'
        },
        revision: 9
      },
      receipt: {},
      stateVersion: 12
    })
    render(
      <ComposerScopeProvider
        value={{
          ...MAIN_COMPOSER_SCOPE,
          runtimeSessionId: 'runtime:personal-assistant',
          target: 'tile:personal-assistant'
        }}
      >
        <TaskProfileReviewCard artifact={taskProfileReview} />
      </ComposerScopeProvider>
    )

    expect(screen.getByText('2 / 8')).toBeTruthy()
    expect(screen.getByText('בדיקת תוצאות PET')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('גבוהה'))
    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    await waitFor(() => expect(respondToPersonalAssistantInterview).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('כמה המשימה חשובה?')).toBeTruthy())
    expect(screen.getByText('3 / 8')).toBeTruthy()
    expect(continuePersonalAssistantInterview).not.toHaveBeenCalled()
    expect(respondToPersonalAssistantInterview).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedRevision: 8,
        interviewId: 'weekly-1',
        questionId: 'urgency',
        response: { action: 'answer', selectedValues: ['high'] },
        taskId: 'pet-results'
      })
    )
  })

  it('does not continue when the interview answer was not persisted', async () => {
    respondToPersonalAssistantInterview.mockRejectedValue(new Error('gateway offline'))
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    fireEvent.click(screen.getByLabelText('גבוהה'))
    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    await waitFor(() => expect(screen.getByText('התשובה לא נשמרה. אפשר לנסות שוב.')).toBeTruthy())
    expect(continuePersonalAssistantInterview).not.toHaveBeenCalled()
    expect(screen.getByLabelText('גבוהה')).toHaveProperty('checked', true)
  })

  it('reuses the same request identity when a saved interview answer is retried', () => {
    const response = {
      action: 'answer' as const,
      fieldEdits: { notes: 'בבית', constraints: ['שקט', 'ללא נסיעות'] },
      selectedValues: ['home']
    }

    const first = stableInterviewRequestId({
      expectedRevision: 4,
      interviewId: 'planning-2026-07-24',
      questionId: 'location',
      response,
      taskId: 'day-context'
    })
    const retried = stableInterviewRequestId({
      expectedRevision: 4,
      interviewId: 'planning-2026-07-24',
      questionId: 'location',
      response: {
        ...response,
        fieldEdits: { constraints: ['שקט', 'ללא נסיעות'], notes: 'בבית' }
      },
      taskId: 'day-context'
    })
    const changedAnswer = stableInterviewRequestId({
      expectedRevision: 4,
      interviewId: 'planning-2026-07-24',
      questionId: 'location',
      response: { action: 'answer', selectedValues: ['outside'] },
      taskId: 'day-context'
    })

    expect(retried).toBe(first)
    expect(changedAnswer).not.toBe(first)
  })

  it('does not claim a saved answer failed when background continuation throws', async () => {
    respondToPersonalAssistantInterview.mockResolvedValue({
      duplicate: false,
      interview: { revision: 9 },
      receipt: {},
      stateVersion: 12
    })
    continuePersonalAssistantInterview.mockRejectedValueOnce(new Error('continuation unavailable'))
    render(
      <TaskProfileReviewCard
        artifact={{
          ...taskProfileReview,
          profileFields: [{ id: 'energy', label: 'אנרגיה', value: '' }],
          progress: { current: 1, total: 4 },
          question: {
            ...taskProfileReview.question,
            id: 'energy',
            label: 'כמה אנרגיה יש לך?',
            options: [{ label: 'נמוכה', value: 'low' }],
            profileFieldId: 'energy'
          },
          task: { id: 'day-context', title: 'תכנון שאר היום' }
        }}
      />
    )

    fireEvent.click(screen.getByLabelText('נמוכה'))
    fireEvent.click(screen.getByRole('button', { name: 'שמירה והמשך' }))

    await waitFor(() => expect(screen.getByText('נשמר להיום')).toBeTruthy())
    expect(screen.queryByText('התשובה לא נשמרה. אפשר לנסות שוב.')).toBeNull()
    expect(respondToPersonalAssistantInterview).toHaveBeenCalledTimes(1)
  })

  it('refreshes a stale review revision without discarding the local draft', async () => {
    respondToPersonalAssistantInterview
      .mockRejectedValueOnce({ code: 'interview_version_conflict', currentRevision: 10, latest: { revision: 10 } })
      .mockResolvedValueOnce({ duplicate: false, interview: { revision: 11 }, receipt: {}, stateVersion: 14 })
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    fireEvent.click(screen.getByLabelText('גבוהה'))
    fireEvent.click(screen.getByRole('button', { name: 'עריכת פרופיל המשימה' }))
    fireEvent.change(screen.getByLabelText('ערוך מה נחשב מספיק'), { target: { value: 'יש תשובה כתובה' } })
    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    await waitFor(() => expect(screen.getByText(/הטיוטה שלך נשמרה/)).toBeTruthy())
    expect(screen.getByLabelText('גבוהה')).toHaveProperty('checked', true)
    expect(screen.getByLabelText('ערוך מה נחשב מספיק')).toHaveProperty('value', 'יש תשובה כתובה')
    expect(continuePersonalAssistantInterview).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))
    await waitFor(() => expect(continuePersonalAssistantInterview).toHaveBeenCalledTimes(1))
    expect(respondToPersonalAssistantInterview.mock.calls[1]?.[0]).toEqual(
      expect.objectContaining({
        expectedRevision: 10,
        response: expect.objectContaining({ fieldEdits: { doneEnough: 'יש תשובה כתובה' } })
      })
    )
  })

  it('continues from durable state when the visible answer was already committed before reconnect', async () => {
    respondToPersonalAssistantInterview.mockRejectedValueOnce(
      Object.assign(new Error('Personal Assistant interview version conflict'), {
        code: 4093,
        data: {
          code: 'interview_version_conflict',
          latest: {
            interviewRevision: 9,
            readinessApproved: true,
            tasks: [{ taskId: 'pet-results', profile: { urgency: 'high' } }]
          }
        }
      })
    )
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    fireEvent.click(screen.getByLabelText('גבוהה'))
    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    await waitFor(() => expect(continuePersonalAssistantInterview).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('התשובה לא נשמרה. אפשר לנסות שוב.')).toBeNull()
    expect(continuePersonalAssistantInterview).toHaveBeenCalledWith(
      expect.stringContaining('after committed answer'),
      undefined
    )
  })

  it('retires a replayed card whose planning session is already over', async () => {
    fetchCurrentPersonalAssistantInterview.mockResolvedValue({ interview: null, nextArtifact: null })
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    await waitFor(() => expect(screen.getByText(/התכנון הזה כבר הסתיים/)).toBeTruthy())
    expect(screen.getByRole('button', { name: 'אישור והמשך' })).toHaveProperty('disabled', true)
  })

  it('catches a replayed card up to the question the interview actually reached', async () => {
    fetchCurrentPersonalAssistantInterview.mockResolvedValue({
      interview: { interviewId: taskProfileReview.interviewId, interviewRevision: 12 },
      nextArtifact: {
        ...taskProfileReview,
        progress: { current: 4, total: 8 },
        question: { ...taskProfileReview.question, id: 'effort', label: 'כמה מאמץ זה דורש?' },
        revision: 12
      }
    })
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    await waitFor(() => expect(screen.getByText('כמה מאמץ זה דורש?')).toBeTruthy())
    expect(screen.getByText('4 / 8')).toBeTruthy()
  })

  it('advances to the next question when another writer bumped the interview mid-answer', async () => {
    respondToPersonalAssistantInterview.mockRejectedValueOnce(
      Object.assign(new Error('Personal Assistant interview version conflict'), {
        code: 4093,
        data: {
          code: 'interview_version_conflict',
          latest: {
            interviewRevision: 9,
            tasks: [{ taskId: 'pet-results', profile: { urgency: 'high' } }]
          },
          nextArtifact: {
            ...taskProfileReview,
            progress: { current: 3, total: 8 },
            question: {
              ...taskProfileReview.question,
              id: 'importance',
              label: 'כמה המשימה חשובה?'
            },
            revision: 9
          }
        }
      })
    )
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    fireEvent.click(screen.getByLabelText('גבוהה'))
    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    await waitFor(() => expect(screen.getByText('כמה המשימה חשובה?')).toBeTruthy())
    expect(screen.getByText('3 / 8')).toBeTruthy()
    expect(continuePersonalAssistantInterview).not.toHaveBeenCalled()
    expect(screen.queryByText('להמשיך מהתשובה שנשמרה')).toBeNull()
    expect(screen.queryByText('נשמר להיום')).toBeNull()
  })

  it('keeps a complete task review card disabled until streaming finishes', async () => {
    const code = JSON.stringify(taskProfileReview)
    const { rerender } = render(
      <RichCodeBlock code={code} fallback={<pre>fallback code block</pre>} language="hermes-ui" streaming />
    )

    expect(screen.getByRole('status').textContent).toMatch(/Preparing/)
    expect(screen.queryByRole('button', { name: 'אישור והמשך' })).toBeNull()

    rerender(
      <RichCodeBlock code={code} fallback={<pre>fallback code block</pre>} language="hermes-ui" streaming={false} />
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'אישור והמשך' })).toBeTruthy())
  })

  it('supports multiple suggested answers plus a custom answer', async () => {
    respondToPersonalAssistantInterview.mockResolvedValue({
      duplicate: false,
      interview: { revision: 9 },
      receipt: {},
      stateVersion: 12
    })
    const multiReview: HermesUiTaskProfileReviewArtifact = {
      ...taskProfileReview,
      question: {
        ...taskProfileReview.question,
        id: 'connections-question',
        label: 'למה המשימה קשורה?',
        options: [
          { label: 'בריאות', value: 'health' },
          { label: 'פגישה', value: 'meeting' }
        ],
        profileFieldId: 'urgency',
        type: 'multi-choice'
      }
    }
    render(<TaskProfileReviewCard artifact={multiReview} />)

    fireEvent.click(screen.getByLabelText('בריאות'))
    fireEvent.click(screen.getByLabelText('פגישה'))
    fireEvent.change(screen.getByLabelText('תשובה אחרת'), { target: { value: 'גם משפחה' } })
    fireEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    await waitFor(() => expect(respondToPersonalAssistantInterview).toHaveBeenCalledTimes(1))
    expect(respondToPersonalAssistantInterview.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        response: { action: 'answer', customAnswer: 'גם משפחה', selectedValues: ['health', 'meeting'] }
      })
    )
  })

  it('persists pause and exposes revisit without requiring an answer', async () => {
    respondToPersonalAssistantInterview.mockResolvedValue({
      duplicate: false,
      interview: { revision: 9 },
      receipt: {},
      stateVersion: 12
    })
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    expect(screen.getByRole('button', { name: 'חזרה למשימה קודמת' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'השהיה' }))

    await waitFor(() => expect(respondToPersonalAssistantInterview).toHaveBeenCalledTimes(1))
    expect(respondToPersonalAssistantInterview.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ response: { action: 'pause' } })
    )
  })

  it('commits back with the controller action understood by both clients', async () => {
    respondToPersonalAssistantInterview.mockResolvedValue({
      duplicate: false,
      interview: { revision: 9 },
      receipt: {},
      stateVersion: 12
    })
    render(<TaskProfileReviewCard artifact={taskProfileReview} />)

    fireEvent.click(screen.getByRole('button', { name: 'חזרה למשימה קודמת' }))

    await waitFor(() => expect(respondToPersonalAssistantInterview).toHaveBeenCalledTimes(1))
    expect(respondToPersonalAssistantInterview.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ response: { action: 'back' } })
    )
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
      <MarkdownTextContent isRunning={false} text={['```hermes-ui', JSON.stringify(taskBreakdown), '```'].join('\n')} />
    )

    await waitFor(() => expect(document.querySelector('[data-hermes-ui-artifact="task-breakdown"]')).toBeTruthy())
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })

  it('renders planning primitives from markdown hermes-ui fences', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={['```hermes-ui', JSON.stringify(planningFunnel, null, 2), '```'].join('\n')}
      />
    )

    await waitFor(() => expect(screen.getByText('משפך תכנון קצר')).toBeTruthy())
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })
})

describe('Planning toolkit primitives', () => {
  const taskTable: HermesUiTaskTableArtifact = {
    actions: [{ id: 'adjust-day', label: 'התאם את היום', submitText: 'תתאים את תכנון היום לזמן ולאנרגיה שלי.' }],
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
      {
        confidence: 'medium',
        context: 'לקוח',
        energy: 'medium',
        id: 't2',
        nextStep: 'טיוטה',
        title: 'מייל ללקוח',
        urgency: 'medium'
      },
      {
        confidence: 'high',
        context: 'בית',
        energy: 'low',
        id: 't3',
        nextStep: '10 דקות',
        title: 'לסדר שולחן',
        urgency: 'low'
      }
    ],
    title: 'השוואת משימות',
    type: 'task-table'
  }

  const miniKanban: HermesUiMiniKanbanArtifact = {
    actions: [{ id: 'revise-week', label: 'תקן את כל הטיוטה', submitText: 'בוא נתקן את טיוטת השבוע.' }],
    direction: 'rtl',
    lanes: [
      {
        id: 'today',
        tasks: [
          {
            actions: [{ id: 'route', label: 'בחר להיום', submitText: 'שים את t1 ב־today preview.' }],
            id: 't1',
            title: 'טיוטה ללקוח'
          }
        ],
        title: 'היום'
      },
      { id: 'need-context', tasks: [{ id: 't2', note: 'צריך להבין משמעות', title: 'בדיקות' }], title: 'צריך הקשר' }
    ],
    title: 'מיון קצר',
    type: 'mini-kanban'
  }

  const dayTimeline: HermesUiDayTimelineArtifact = {
    actions: [
      { id: 'choose-plan', label: 'לבחור בתוכנית', submitText: 'בחר את תוכנית היום המלאה.' },
      { id: 'adjust-plan', label: 'להתאים את התוכנית', submitText: 'התאם את תוכנית היום המלאה.' }
    ],
    blocks: [
      {
        actions: [{ id: 'accept-block', label: 'השתמש בבלוק', submitText: 'תציג preview לבלוק הזה.' }],
        endTime: '10:25',
        id: 'b1',
        kind: 'focus',
        label: 'טיוטה ללקוח',
        startTime: '10:00',
        status: 'candidate'
      },
      { durationMinutes: 15, id: 'float-1', kind: 'floating', label: 'בדיקה קצרה', status: 'planned' }
    ],
    currentTime: '09:45',
    date: '2026-07-09',
    direction: 'rtl',
    title: 'תכנון יום אפשרי',
    type: 'day-timeline'
  }

  const weekPlanner: HermesUiWeekPlannerArtifact = {
    actions: [{ id: 'approve-week', label: 'לאשר', submitText: 'מאשר את טיוטת השבוע הזו.' }],
    currentDate: '2026-07-19',
    days: Array.from({ length: 7 }, (_, index) => ({
      blocks:
        index === 0
          ? [
              {
                actions: [{ id: 'revise', label: 'לשנות', submitText: 'בוא נתקן את הבלוק הראשון.' }],
                doneEnough: 'קובץ וידאו אמיתי שאפשר להמשיך ממנו.',
                durationMinutes: 35,
                id: 'robot-film',
                kind: 'focus',
                label: 'סרטון הרמס',
                note: 'לפתוח את החומרים ולהקליט טייק ראשון אחד.',
                status: 'planned'
              }
            ]
          : [],
      date: `2026-07-${String(19 + index).padStart(2, '0')}`,
      id: `day-${index + 1}`,
      label: ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת'][index]
    })),
    direction: 'rtl',
    title: 'השבוע שלי',
    type: 'week-planner',
    weekStart: '2026-07-19'
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

  it('omits unknown task details and routes row actions', () => {
    render(<TaskTableCard artifact={taskTable} />)

    expect(screen.getByText('השוואת משימות')).toBeTruthy()
    expect(screen.getByText('לבדוק בדיקות')).toBeTruthy()
    expect(screen.queryByText('לא ידוע')).toBeNull()
    expect(document.querySelector('[data-hermes-ui-artifact="task-table"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'שאל על זה' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('שאל אותי שאלה אחת על בדיקות.', { target: 'main' })
    fireEvent.click(screen.getByRole('button', { name: 'התאם את היום' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith('תתאים את תכנון היום לזמן ולאנרגיה שלי.', { target: 'main' })
  })

  it('turns a long model-defined inventory into a readable progressive task list', () => {
    const inventoryTable: HermesUiTaskTableArtifact = {
      columns: [
        'task',
        { key: 'source', label: 'מקור' },
        { key: 'status', label: 'מצב' },
        { key: 'due', label: 'מועד' }
      ],
      direction: 'rtl',
      rows: Array.from({ length: 19 }, (_, index) => ({
        cells: {
          due: index === 0 ? '20.7' : 'ללא מועד',
          source: index % 2 === 0 ? 'FlowState' : 'Notion',
          status: index === 0 ? 'בוצע היום' : 'פתוח'
        },
        id: `inventory-${index}`,
        title: `משימה ${index + 1}`
      })),
      title: 'הרשימה המלאה',
      type: 'task-table'
    }

    render(<TaskTableCard artifact={inventoryTable} />)

    expect(screen.getByText('19 משימות')).toBeTruthy()
    expect(screen.getByRole('list', { name: /הרשימה המלאה/ })).toBeTruthy()
    expect(screen.getAllByRole('listitem')).toHaveLength(8)
    expect(screen.getAllByText('מקור')).toHaveLength(8)
    expect(screen.getAllByText('מצב')).toHaveLength(8)
    expect(screen.getAllByText('מועד')).toHaveLength(8)
    expect(screen.getByText('משימה 1')).toBeTruthy()
    expect(screen.getByText('בוצע היום')).toBeTruthy()
    expect(screen.queryByText('לא ידוע')).toBeNull()
    expect(screen.queryByText('פעולה')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'הצג עוד 11' }))

    expect(screen.getAllByRole('listitem')).toHaveLength(19)
    expect(screen.getByRole('button', { name: 'הצג פחות' })).toBeTruthy()
  })

  it('renders mini-kanban lanes and routes task actions', () => {
    render(<MiniKanbanCard artifact={miniKanban} />)

    expect(screen.getByText('מיון קצר')).toBeTruthy()
    expect(screen.getByText('צריך הקשר')).toBeTruthy()
    expect(screen.getByText('צריך להבין משמעות')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="mini-kanban"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'בחר להיום' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('שים את t1 ב־today preview.', { target: 'main' })
    fireEvent.click(screen.getByRole('button', { name: 'תקן את כל הטיוטה' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith('בוא נתקן את טיוטת השבוע.', { target: 'main' })
  })

  it('keeps day-timeline block actions hidden until that block is opened', () => {
    render(<DayTimelineCard artifact={dayTimeline} />)

    expect(screen.getByText('תכנון יום אפשרי')).toBeTruthy()
    expect(screen.getAllByText('09:45').length).toBeGreaterThan(0)
    expect(screen.getByText('טיוטה ללקוח')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="day-timeline"]')).toBeTruthy()

    expect(screen.queryByRole('button', { name: 'השתמש בבלוק' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /טיוטה ללקוח/ }))
    fireEvent.click(screen.getByRole('button', { name: 'השתמש בבלוק' }))

    expect(requestComposerSubmit).toHaveBeenCalledWith('תציג preview לבלוק הזה.', { target: 'main' })
    fireEvent.click(screen.getByRole('button', { name: 'לבחור בתוכנית' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith('בחר את תוכנית היום המלאה.', { target: 'main' })
  })

  it('omits absent metadata and internal block kinds from a day timeline', () => {
    render(
      <DayTimelineCard
        artifact={{
          blocks: [
            { id: 'task', label: 'לשלוח את הפנייה', startTime: '17:10' },
            { id: 'rest', kind: 'break', label: 'מנוחה', startTime: '17:40' }
          ],
          date: '2026-07-22',
          direction: 'rtl',
          type: 'day-timeline'
        }}
      />
    )

    expect(screen.queryByText('לא ידוע')).toBeNull()
    expect(screen.queryByText('break')).toBeNull()
    expect(screen.getByText('לשלוח את הפנייה')).toBeTruthy()
    expect(screen.getByText('מנוחה')).toBeTruthy()
  })

  it('renders every week-planner detail visually and routes item and week actions', async () => {
    render(<WeekPlannerCard artifact={weekPlanner} />)

    expect(screen.getByText('השבוע שלי')).toBeTruthy()
    expect(screen.getByText('ראשון')).toBeTruthy()
    expect(screen.getByText('שבת')).toBeTruthy()
    expect(screen.getByText('סרטון הרמס')).toBeTruthy()
    expect(screen.getByText('לפתוח את החומרים ולהקליט טייק ראשון אחד.')).toBeTruthy()
    expect(screen.getByText('קובץ וידאו אמיתי שאפשר להמשיך ממנו.')).toBeTruthy()
    expect(document.querySelector('[data-current-day="true"]')).toBeTruthy()
    expect(document.querySelector('[data-hermes-ui-artifact="week-planner"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'לשנות' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith('בוא נתקן את הבלוק הראשון.', { target: 'main' })
    fireEvent.click(screen.getByRole('button', { name: 'לאשר' }))
    expect(requestComposerSubmit).toHaveBeenCalledWith('מאשר את טיוטת השבוע הזו.', { target: 'main' })

    cleanup()
    render(
      <MarkdownTextContent isRunning={false} text={['```hermes-ui', JSON.stringify(weekPlanner), '```'].join('\n')} />
    )
    await waitFor(() => expect(document.querySelector('[data-hermes-ui-artifact="week-planner"]')).toBeTruthy())
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
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
            cells: [
              {
                label: 'דחוף באנרגיה נמוכה',
                tasks: [{ id: 't1', priority: 'high', title: 'שיחה קצרה' }],
                x: 'low',
                y: 'high'
              }
            ],
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
        text={['```hermes-ui', JSON.stringify(mutationPreview, null, 2), '```'].join('\n')}
      />
    )

    await waitFor(() => expect(screen.getByText('Preview לפני שינוי')).toBeTruthy())
    expect(document.querySelector('[data-hermes-ui-artifact="mutation-preview"]')).toBeTruthy()
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })
})
