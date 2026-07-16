import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MarkdownTextContent } from '@/components/assistant-ui/markdown-text'

const requestComposerSubmit = vi.fn()

vi.mock('@/app/chat/composer/focus', () => ({
  requestComposerSubmit: (text: string, options?: unknown) => requestComposerSubmit(text, options)
}))

import HermesUiArtifactRenderer from './hermes-ui-artifact'

const artifactFixture = {
  baselineId: 'day-plan-2026-07-16-r1',
  date: '2026-07-16',
  description: 'כל מה שקבוע, מחויב או מוצע ליום הזה.',
  direction: 'rtl',
  id: 'daily-plan-2026-07-16',
  schemaVersion: 1,
  sections: [
    {
      id: 'calendar',
      kind: 'calendar',
      rows: [
        {
          context: [{ id: 'calendar-context', text: 'פגישת תכנון שבועית עם הצוות.' }],
          contextConfidence: 'verified',
          durationMinutes: 45,
          energy: 'medium',
          id: 'calendar-1',
          planPlacement: 'core',
          source: { kind: 'calendar', recordId: 'event-1', revision: 'etag-1' },
          sourceMutationAllowed: false,
          sourceStatus: 'open',
          temporal: { endTime: '09:45', startTime: '09:00' },
          title: 'פגישת צוות'
        },
        {
          contextConfidence: 'partial',
          id: 'calendar-2',
          planPlacement: 'optional',
          source: { kind: 'calendar', recordId: 'event-2', revision: 'etag-2' },
          sourceMutationAllowed: false,
          sourceStatus: 'open',
          temporal: { endTime: '13:00', startTime: '12:30' },
          title: 'שיחת לקוח'
        }
      ],
      title: 'אירועי היומן היום'
    },
    {
      id: 'due-today',
      kind: 'due-today',
      rows: [
        {
          context: [{ id: 'task-context', text: 'הטיוטה חייבת להיות מוכנה לפני שיחת הלקוח.' }],
          contextConfidence: 'partial',
          doneEnough: 'טיוטה מלאה שאפשר לשלוח לבדיקה.',
          dueDate: '2026-07-16',
          durationMinutes: 50,
          energy: 'high',
          generalizationProposals: [
            {
              claimId: 'claim-new',
              id: 'generalize-1',
              rationale: 'ייתכן שזה נכון לכל משימות הלקוח, אבל זה דורש אישור נפרד.',
              scope: { kind: 'project', referenceId: 'project-client' }
            }
          ],
          id: 'task-1',
          learnedClaims: [
            {
              id: 'claim-old',
              provenance: [
                {
                  capturedAt: '2026-07-10T08:00:00Z',
                  id: 'prov-old',
                  sourceKind: 'obsidian',
                  text: 'תיקון קודם'
                }
              ],
              scope: { kind: 'exact-task', referenceId: 'flow-task-1' },
              state: 'active',
              text: 'המשימה דורשת עשרים דקות.'
            },
            {
              id: 'claim-new',
              provenance: [
                {
                  capturedAt: '2026-07-16T07:30:00Z',
                  id: 'prov-new',
                  sourceKind: 'hermes',
                  text: 'תיקון מהיום'
                }
              ],
              scope: { kind: 'exact-task', referenceId: 'flow-task-1' },
              state: 'superseded',
              text: 'המשימה דורשת חמישים דקות.'
            }
          ],
          learningConflicts: [{ id: 'conflict-1', newClaimId: 'claim-new', priorClaimId: 'claim-old' }],
          nextStep: 'לכתוב את שלושת הסעיפים המרכזיים.',
          planPlacement: 'core',
          priority: 'high',
          provenance: [
            {
              capturedAt: '2026-07-16T07:00:00Z',
              id: 'flow-prov-1',
              sourceKind: 'flowstate',
              sourceRecordId: 'flow-task-1',
              text: 'FlowState live inventory'
            }
          ],
          quickActions: [{ id: 'mark-done', label: 'סמן כהושלם' }],
          source: { kind: 'flowstate', recordId: 'flow-task-1', revision: 'rev-7' },
          sourceMutationAllowed: true,
          sourceStatus: 'open',
          title: 'לסיים טיוטה ללקוח'
        },
        {
          contextConfidence: 'unknown',
          dueDate: '2026-07-16',
          energy: 'unknown',
          id: 'task-2',
          planPlacement: 'unassigned',
          source: { kind: 'flowstate', recordId: 'flow-task-2', revision: 'rev-2' },
          sourceMutationAllowed: true,
          sourceStatus: 'in-progress',
          title: 'לבדוק את מסך ההתחברות'
        }
      ],
      title: 'משימות להיום'
    },
    {
      id: 'overdue',
      kind: 'overdue',
      rows: [
        {
          contextConfidence: 'partial',
          dueDate: '2026-07-14',
          id: 'overdue-1',
          planPlacement: 'optional',
          source: { kind: 'flowstate', recordId: 'flow-overdue-1', revision: 'rev-1' },
          sourceMutationAllowed: true,
          sourceStatus: 'open',
          title: 'להגיש קבלה'
        },
        {
          contextConfidence: 'unknown',
          dueDate: '2026-07-15',
          id: 'overdue-2',
          planPlacement: 'not-today',
          source: { kind: 'flowstate', recordId: 'flow-overdue-2', revision: 'rev-4' },
          sourceMutationAllowed: true,
          sourceStatus: 'open',
          title: 'לעדכן מסמך תפעול'
        }
      ],
      title: 'באיחור'
    },
    {
      id: 'suggestions',
      kind: 'suggestions',
      rows: [
        {
          contextConfidence: 'partial',
          expectedImpact: 'מוריד חסימה לפני הצהריים.',
          id: 'suggestion-1',
          planPlacement: 'unassigned',
          source: { kind: 'notion', recordId: 'notion-1', revision: 'notion-r1' },
          sourceMutationAllowed: true,
          sourceStatus: 'open',
          suggestionConfidence: 'high',
          suggestionRationale: 'יש תלות של לקוח שמחכה לתשובה.',
          title: 'לענות לנועה'
        },
        {
          contextConfidence: 'unknown',
          expectedImpact: 'מפנה עומס קטן.',
          id: 'suggestion-2',
          planPlacement: 'unassigned',
          source: { kind: 'obsidian', recordId: 'note-2', revision: 'hash-2' },
          sourceMutationAllowed: false,
          sourceStatus: 'unknown',
          suggestionConfidence: 'medium',
          suggestionRationale: 'הוזכר פעמיים השבוע.',
          title: 'לסכם את שיחת המחקר'
        },
        {
          contextConfidence: 'partial',
          expectedImpact: 'מגן על סוף היום.',
          id: 'suggestion-3',
          planPlacement: 'unassigned',
          source: { kind: 'hermes', recordId: 'assistant-3', revision: 'assistant-r3' },
          sourceMutationAllowed: false,
          sourceStatus: 'unknown',
          suggestionConfidence: 'medium',
          suggestionRationale: 'נשאר חלון קצר לפני סיום היום.',
          title: 'לסגור את היום בעשר דקות'
        }
      ],
      title: 'הצעות'
    },
    {
      id: 'more-suggestions',
      kind: 'more-suggestions',
      rows: [
        {
          contextConfidence: 'unknown',
          expectedImpact: 'ניקוי סביבת העבודה.',
          id: 'suggestion-4',
          planPlacement: 'unassigned',
          source: { kind: 'hermes', recordId: 'assistant-4' },
          sourceMutationAllowed: false,
          sourceStatus: 'unknown',
          suggestionConfidence: 'low',
          suggestionRationale: 'משימה קצרה שאפשר לדחות.',
          title: 'לסדר את שולחן העבודה'
        },
        {
          contextConfidence: 'partial',
          expectedImpact: 'מכין את מחר.',
          id: 'suggestion-5',
          planPlacement: 'unassigned',
          source: { kind: 'hermes', recordId: 'assistant-5' },
          sourceMutationAllowed: false,
          sourceStatus: 'unknown',
          suggestionConfidence: 'low',
          suggestionRationale: 'אין לכך השפעה ישירה על היום.',
          title: 'לרשום רעיון לשבוע הבא'
        }
      ],
      title: 'הצעות נוספות'
    }
  ],
  timezone: 'Asia/Jerusalem',
  title: 'תכנון היום',
  type: 'daily-planning-list'
} as const

const artifact = {
  ...artifactFixture,
  sections: artifactFixture.sections.map((section, index) => ({
    ...section,
    rows: index === 2 || index === 4 ? [] : index === 3 ? section.rows.slice(0, 1) : section.rows
  }))
}

beforeEach(() => {
  requestComposerSubmit.mockClear()
})

afterEach(cleanup)

describe('DailyPlanningListCard', () => {
  it('keeps only the newest copy when a retry repeats the same plan', async () => {
    render(
      <>
        <HermesUiArtifactRenderer code={JSON.stringify(artifact)} />
        <HermesUiArtifactRenderer code={JSON.stringify(artifact)} />
      </>
    )

    await waitFor(() => expect(document.querySelectorAll('[data-hermes-ui-artifact="daily-planning-list"]')).toHaveLength(1))
  })

  it('rejects a long task dump instead of rendering an endless list', async () => {
    const longPlan = structuredClone(artifact) as unknown as {
      sections: Array<{ rows: Array<Record<string, unknown>> }>
    }

    const template = longPlan.sections[1].rows[0]

    longPlan.sections[1].rows = Array.from({ length: 21 }, (_, index) => ({
      ...structuredClone(template),
      id: `long-task-${index}`,
      source: { kind: 'flowstate', recordId: `long-source-${index}`, revision: `rev-${index}` },
      title: `משימה ארוכה ${index + 1}`
    }))

    render(<HermesUiArtifactRenderer code={JSON.stringify(longPlan)} />)

    expect(document.querySelector('[data-hermes-ui-artifact="daily-planning-list"]')).toBeNull()
    expect(screen.queryByText('משימה ארוכה 1')).toBeNull()
  })

  it('renders the representative daily plan through the real markdown fence path instead of a code block', async () => {
    const fenceArtifact = {
      ...artifact,
      sections: artifact.sections.map((section) => ({
        ...section,
        rows: []
      }))
    }

    render(
      <MarkdownTextContent
        isRunning={false}
        text={['```hermes-ui', JSON.stringify(fenceArtifact), '```'].join('\n')}
      />
    )

    await waitFor(() => expect(document.querySelector('[data-hermes-ui-artifact="daily-planning-list"]')).toBeTruthy())
    expect(screen.getByText('תכנון היום')).toBeTruthy()
    expect(screen.queryByText(/Code\s*·\s*hermes-ui/i)).toBeNull()
  })

  it('renders the live Calendar id through markdown', async () => {
    const liveShapedArtifact = structuredClone(artifact) as unknown as {
      sections: Array<{ rows: Array<Record<string, unknown>> }>
    }

    ;(liveShapedArtifact.sections[0].rows[0].source as Record<string, unknown>).recordId =
      '_6phj8e316or3cb9l6hgm8b9kckrj4bb2cosm6b9kcorjapb66so3acr268'

    render(
      <MarkdownTextContent
        isRunning={false}
        text={['```hermes-ui', JSON.stringify(liveShapedArtifact), '```'].join('\n')}
      />
    )

    await waitFor(() => expect(document.querySelector('[data-hermes-ui-artifact="daily-planning-list"]')).toBeTruthy())
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.body.textContent).not.toContain('undefined')
  })

  it('renders only non-empty RTL sections', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)

    const card = document.querySelector('[data-hermes-ui-artifact="daily-planning-list"]')
    expect(card?.getAttribute('dir')).toBe('rtl')
    expect(screen.getByText('אירועי היומן היום')).toBeTruthy()
    expect(screen.getByText('משימות להיום')).toBeTruthy()
    expect(screen.queryByText('באיחור')).toBeNull()
    expect(screen.getByText('הצעות')).toBeTruthy()
    expect(screen.getByText('לענות לנועה')).toBeTruthy()
    expect(screen.queryByText('הצעות נוספות')).toBeNull()
  })

  it('expands rows without hiding siblings and exposes provenance and prior claims', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)

    fireEvent.click(screen.getByRole('button', { name: 'הרחב לסיים טיוטה ללקוח' }))
    expect(screen.getByText('לבדוק את מסך ההתחברות')).toBeTruthy()
    expect(screen.getAllByText('הטיוטה חייבת להיות מוכנה לפני שיחת הלקוח.').length).toBeGreaterThan(0)
    expect(screen.getByText(/FlowState live inventory/)).toBeTruthy()
    expect(screen.getByText('המשימה דורשת עשרים דקות.')).toBeTruthy()
    expect(screen.getByText('המשימה דורשת חמישים דקות.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'כווץ לסיים טיוטה ללקוח' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('keeps source, placement, and context confidence independent', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)

    expect(screen.queryByLabelText('סטטוס מקור — לסיים טיוטה ללקוח')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'הרחב לסיים טיוטה ללקוח' }))

    const source = screen.getByLabelText('סטטוס מקור — לסיים טיוטה ללקוח') as HTMLSelectElement
    const placement = screen.getByLabelText('מיקום היום — לסיים טיוטה ללקוח') as HTMLSelectElement
    const confidence = screen.getByLabelText('ודאות הקשר — לסיים טיוטה ללקוח') as HTMLSelectElement

    fireEvent.change(source, { target: { value: 'done' } })
    expect(source.value).toBe('done')
    expect(placement.value).toBe('core')
    expect(confidence.value).toBe('partial')

    fireEvent.change(placement, { target: { value: 'not-today' } })
    expect(source.value).toBe('done')
    expect(confidence.value).toBe('partial')
  })

  it('keeps Calendar source state read-only while allowing plan edits', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)
    fireEvent.click(screen.getByRole('button', { name: 'הרחב פגישת צוות' }))

    expect(screen.getByLabelText('סטטוס מקור — פגישת צוות')).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('מיקום היום — פגישת צוות')).toHaveProperty('disabled', false)
    expect(screen.getByLabelText('ודאות הקשר — פגישת צוות')).toHaveProperty('disabled', false)
  })

  it('orders Calendar rows by time and requires explicit suggestion opt-in', () => {
    const reversed = {
      ...structuredClone(artifact),
      sections: artifact.sections.map((section, index) => index === 0 ? { ...section, rows: [...section.rows].reverse() } : section)
    }

    render(<HermesUiArtifactRenderer code={JSON.stringify(reversed)} />)

    const calendarSection = screen.getByTestId('daily-planning-section-calendar')
    const calendarTitles = within(calendarSection).getAllByRole('heading', { level: 4 })
    expect(calendarTitles.map(title => title.textContent)).toEqual(['פגישת צוות', 'שיחת לקוח'])

    const suggestion = screen.getByTestId('daily-planning-row-suggestion-1')
    fireEvent.click(within(suggestion).getByRole('button', { name: 'הוסף את לענות לנועה לתכנית' }))
    fireEvent.click(within(suggestion).getByRole('button', { name: 'הסר את לענות לנועה מהתכנית' }))
    expect(requestComposerSubmit).not.toHaveBeenCalled()
  })

  it('tracks exact priority, date, duration, energy, context, next-step, and done-enough diffs', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)
    fireEvent.click(screen.getByRole('button', { name: 'הרחב לסיים טיוטה ללקוח' }))

    fireEvent.change(screen.getByLabelText('עדיפות — לסיים טיוטה ללקוח'), { target: { value: 'medium' } })
    fireEvent.change(screen.getByLabelText('תאריך יעד — לסיים טיוטה ללקוח'), { target: { value: '2026-07-17' } })
    fireEvent.change(screen.getByLabelText('משך — לסיים טיוטה ללקוח'), { target: { value: '65' } })
    fireEvent.change(screen.getByLabelText('אנרגיה — לסיים טיוטה ללקוח'), { target: { value: 'medium' } })
    fireEvent.change(screen.getByLabelText('הקשר — לסיים טיוטה ללקוח'), { target: { value: 'הלקוח מחכה לטיוטה לפני השיחה.' } })
    fireEvent.change(screen.getByLabelText('הצעד הבא — לסיים טיוטה ללקוח'), { target: { value: 'לפתוח את המסמך ולנסח פתיחה.' } })
    fireEvent.change(screen.getByLabelText('מספיק לסיום — לסיים טיוטה ללקוח'), { target: { value: 'טיוטה מלאה עם שלושה סעיפים.' } })
    fireEvent.click(screen.getByRole('button', { name: 'סקירת שינויים' }))

    expect(screen.getAllByText('high → medium')).toHaveLength(2)
    expect(screen.getByText('2026-07-16 → 2026-07-17')).toBeTruthy()
    expect(screen.getByText('50 → 65')).toBeTruthy()
    expect(screen.getByText('הטיוטה חייבת להיות מוכנה לפני שיחת הלקוח. → הלקוח מחכה לטיוטה לפני השיחה.')).toBeTruthy()
    expect(screen.getByText('לכתוב את שלושת הסעיפים המרכזיים. → לפתוח את המסמך ולנסח פתיחה.')).toBeTruthy()
    expect(screen.getByText('טיוטה מלאה שאפשר לשלוח לבדיקה. → טיוטה מלאה עם שלושה סעיפים.')).toBeTruthy()
    expect(requestComposerSubmit).not.toHaveBeenCalled()
  })

  it('builds an exact separated review without submitting during edits or review', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)
    fireEvent.click(screen.getByRole('button', { name: 'הרחב לסיים טיוטה ללקוח' }))

    fireEvent.change(screen.getByLabelText('סטטוס מקור — לסיים טיוטה ללקוח'), { target: { value: 'done' } })
    fireEvent.change(screen.getByLabelText('מיקום היום — לסיים טיוטה ללקוח'), { target: { value: 'optional' } })
    fireEvent.change(screen.getByLabelText('משך — לסיים טיוטה ללקוח'), { target: { value: '65' } })
    expect(requestComposerSubmit).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'סקירת שינויים' }))
    expect(screen.getByText('שינויים במערכות המקור')).toBeTruthy()
    expect(screen.getByText('שינויים בתכנית היום')).toBeTruthy()
    expect(screen.getByText('שינויים בלמידה מתמשכת')).toBeTruthy()
    expect(screen.getByText('open → done')).toBeTruthy()
    expect(screen.getByText('core → optional')).toBeTruthy()
    expect(requestComposerSubmit).not.toHaveBeenCalled()
  })

  it('blocks unresolved learning conflicts, keeps generalization opt-in, and submits once resolved', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)
    fireEvent.click(screen.getByRole('button', { name: 'הרחב לסיים טיוטה ללקוח' }))

    const generalization = screen.getByLabelText('החל על project-client') as HTMLInputElement
    expect(generalization.checked).toBe(false)
    fireEvent.click(screen.getByLabelText('הצע את claim-new כלמידה מתמשכת'))
    fireEvent.click(screen.getByRole('button', { name: 'סקירת שינויים' }))
    expect(screen.getByText('נדרשת הכרעה בין טענה קודמת לחדשה')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'שלח בקשת preview ל־Hermes' })).toHaveProperty('disabled', true)

    fireEvent.click(screen.getByLabelText('הפעל את הטענה החדשה'))
    const submit = screen.getByRole('button', { name: 'שלח בקשת preview ל־Hermes' })
    expect(submit).toHaveProperty('disabled', false)
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(requestComposerSubmit).toHaveBeenCalledTimes(1)
    const message = requestComposerSubmit.mock.calls[0]?.[0] as string
    const response = JSON.parse(message.slice('Hermes UI daily planning response:\n'.length))
    expect(response).toMatchObject({
      action: 'request-preview',
      artifactId: 'daily-plan-2026-07-16',
      baselineId: 'day-plan-2026-07-16-r1',
      schemaVersion: 1,
      type: 'daily-planning-list-response'
    })
    expect(response.sourceChanges).toEqual([])
    expect(response.dayPlanChanges).toEqual([])
    expect(response.learningChanges).toHaveLength(1)
    expect(response.conflictResolutions).toEqual([
      { conflictId: 'conflict-1', decision: 'activate-new', rowId: 'task-1' }
    ])
    expect(response.generalizationProposals).toEqual([])
    expect(response.continuationInstruction).toContain('typed source previews')
    expect(requestComposerSubmit).toHaveBeenCalledWith(expect.any(String), {
      allowWhileBusy: true,
      hidden: true,
      target: 'main'
    })
  })

  it('undoes one row and resets the complete draft to its immutable baseline', () => {
    render(<HermesUiArtifactRenderer code={JSON.stringify(artifact)} />)

    const row = screen.getByTestId('daily-planning-row-task-1')
    fireEvent.click(within(row).getByRole('button', { name: 'הרחב לסיים טיוטה ללקוח' }))
    fireEvent.change(within(row).getByLabelText('מיקום היום — לסיים טיוטה ללקוח'), { target: { value: 'optional' } })
    expect(within(row).getByText('שונה')).toBeTruthy()
    fireEvent.click(within(row).getByRole('button', { name: 'בטל שינויים בלסיים טיוטה ללקוח' }))
    expect(within(row).queryByText('שונה')).toBeNull()

    fireEvent.change(screen.getByLabelText('מיקום היום — לסיים טיוטה ללקוח'), { target: { value: 'optional' } })
    fireEvent.click(screen.getByRole('button', { name: 'הרחב לבדוק את מסך ההתחברות' }))
    fireEvent.change(screen.getByLabelText('ודאות הקשר — לבדוק את מסך ההתחברות'), { target: { value: 'verified' } })
    fireEvent.click(screen.getByRole('button', { name: 'אפס את כל השינויים' }))
    expect(screen.queryAllByText('שונה')).toHaveLength(0)
  })
})
