import { describe, expect, it } from 'vitest'

import {
  buildHermesUiFormResponse,
  type HermesUiChecklistArtifact,
  type HermesUiFormArtifact,
  parseHermesUiArtifact,
  parseHermesUiTaskBreakdownDraftSteps,
  stableArtifactStorageKey
} from './hermes-ui-artifacts'

const validChecklist = {
  description: 'Interactive checklist rendered inline by Hermes Desktop.',
  id: 'Obsidian Source Truth',
  items: [
    { id: 'profile-vault', label: 'Active profile: office-work' },
    { id: 'source-truth', label: 'Obsidian is the source of truth.' }
  ],
  title: 'Obsidian source-of-truth policy',
  type: 'checklist'
}

const validForm: HermesUiFormArtifact = {
  direction: 'rtl',
  fields: [
    { id: 'outcome', label: 'מה יהפוך את היום למוצלח?', required: true, type: 'short-text' },
    {
      id: 'energy',
      label: 'כמה אנרגיה יש לך?',
      options: [
        { label: 'נמוכה', value: 'low' },
        { label: 'גבוהה', value: 'high' }
      ],
      type: 'single-choice'
    }
  ],
  id: 'morning-outcome',
  submitLabel: 'שלח ל־Hermes',
  title: 'תכנון היום',
  type: 'form'
}

describe('form artifacts', () => {
  it('builds a canonical response with a stable idempotency key', () => {
    const first = buildHermesUiFormResponse(validForm, { outcome: 'מצגת', energy: 'high' })
    const reordered = buildHermesUiFormResponse(validForm, { energy: 'high', outcome: 'מצגת' })

    expect(first).toEqual(reordered)
    expect(first).toEqual({
      actionId: 'submit',
      artifactId: 'morning-outcome',
      idempotencyKey: expect.stringMatching(/^form:/),
      schemaVersion: 1,
      type: 'form-response',
      values: { outcome: 'מצגת', energy: 'high' }
    })
  })

  it('parses a bounded interactive form and preserves its RTL contract', () => {
    const result = parseHermesUiArtifact(JSON.stringify(validForm))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'form' && result.artifact.fields).toHaveLength(2)
    expect(result.ok && result.artifact.type === 'form' && result.artifact.direction).toBe('rtl')
  })

  it('normalizes concise string options emitted by the assistant', () => {
    const result = parseHermesUiArtifact(JSON.stringify({
      direction: 'rtl',
      fields: [{
        id: 'must_happen_today',
        label: 'התחייבויות אמיתיות להיום',
        options: ['לקנות חלב ולחם', 'להתקדם עם סרט הרובוטים'],
        required: true,
        type: 'multi-choice'
      }],
      id: 'flowstate-today-reality-check',
      submitLabel: 'לבנות תכנית ריאלית להיום',
      title: 'מה באמת חייב לקרות היום?',
      type: 'form'
    }))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'form' && result.artifact.fields[0]?.options).toEqual([
      { label: 'לקנות חלב ולחם', value: 'לקנות חלב ולחם' },
      { label: 'להתקדם עם סרט הרובוטים', value: 'להתקדם עם סרט הרובוטים' }
    ])
  })

  it('rejects fields outside the bounded form schema', () => {
    const result = parseHermesUiArtifact(JSON.stringify({
      ...validForm,
      fields: [{ ...validForm.fields[0], onClick: 'alert(1)' }]
    }))

    expect(result).toEqual({ error: 'fields[0] contains unsupported properties', ok: false })
  })

  it('adds a correction field to checkbox-only approval forms', () => {
    const result = parseHermesUiArtifact(JSON.stringify({
      direction: 'rtl',
      fields: [{ id: 'approve', label: 'לאשר את התכנית', required: true, type: 'boolean' }],
      id: 'approve-plan',
      submitLabel: 'שלח החלטה',
      title: 'אישור התכנית',
      type: 'form'
    }))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'form' && result.artifact.fields).toEqual([
      { description: undefined, id: 'approve', label: 'לאשר את התכנית', options: undefined, placeholder: undefined, required: true, type: 'boolean' },
      { id: 'revision', label: 'תיקון או הקשר (אופציונלי)', required: false, type: 'long-text' }
    ])
  })

  it('accepts and normalizes a numeric default emitted for a number field', () => {
    const result = parseHermesUiArtifact(JSON.stringify({
      direction: 'rtl',
      fields: [
        { id: 'scheduled_time', label: 'שעת התחלה', required: true, type: 'time' },
        { default: 25, id: 'duration', label: 'משך בדקות', required: true, type: 'number' }
      ],
      id: 'schedule-laundry-work-block-2026-07-13',
      submitLabel: 'הכן תצוגה מקדימה',
      title: 'מתי לשים את הכביסה ב־Canvas?',
      type: 'form'
    }))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'form' && result.artifact.fields[1]?.defaultValue).toBe('25')
  })

  it('accepts canonical 24-hour time defaults and rejects ambiguous or invalid values', () => {
    const withTimeDefault = (value: string) => JSON.stringify({
      direction: 'rtl',
      fields: [
        { default: value, id: 'scheduled_time', label: 'שעת התחלה', required: true, type: 'time' }
      ],
      id: 'schedule-time',
      type: 'form'
    })

    const valid = parseHermesUiArtifact(withTimeDefault('20:00'))

    expect(valid.ok).toBe(true)
    expect(valid.ok && valid.artifact.type === 'form' && valid.artifact.fields[0]?.defaultValue).toBe('20:00')
    expect(parseHermesUiArtifact(withTimeDefault('8:00'))).toEqual({
      error: 'fields[0].default must use 24-hour HH:mm format',
      ok: false
    })
    expect(parseHermesUiArtifact(withTimeDefault('24:00'))).toEqual({
      error: 'fields[0].default must use 24-hour HH:mm format',
      ok: false
    })
  })
})

const obsidianPolicyChecklist = {
  description: 'Operational source-of-truth checklist for Obsidian-backed durable context.',
  id: 'obsidian-source-of-truth-policy',
  items: [
    {
      id: 'obsidian-profile-vault',
      label:
        'Active profile: office-work; canonical vault: /media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED; visible workspace: /media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED/MAIN VULT'
    },
    {
      id: 'obsidian-source-truth',
      label: 'Obsidian is the source of truth for durable context. Built-in memory and conversation summaries are only pointers/caches.'
    },
    {
      id: 'obsidian-routing-policy',
      label: 'Routing policy note: MAIN VULT/_System/Hermes Governance/Hermes Vault Routing Policy.md.'
    },
    {
      id: 'obsidian-no-hermes-memory',
      label: 'Never create or write notes under Hermes Memory/; route durable notes under MAIN VULT/.'
    },
    {
      id: 'obsidian-create-edit-routing',
      label:
        'Create/edit routing: _System/ for Hermes governance/reports; _System/Hermes Knowledge Graph/ for internal agent/profile context; 🚀 My Projects/, 💼 Work/, 📦 My Stuff/ only for user-facing/project-facing content.'
    },
    {
      id: 'obsidian-read-relevant-note',
      label:
        'For project/profile questions, continuation after long chats, setup/MCP/tooling details, or durable decisions: read the relevant Obsidian note before answering.'
    },
    {
      id: 'obsidian-update-durable-knowledge',
      label:
        'If this turn creates/changes durable knowledge, update/create an Obsidian note before final response; keep Hermes memory compact.'
    },
    {
      id: 'obsidian-turn-ledgers',
      label: 'Turn ledgers go to MAIN VULT/_System/Hermes Turn Logs; curated facts still belong in project/profile notes.'
    },
    {
      id: 'obsidian-start-indexes',
      label:
        'Start indexes: MAIN VULT/_System/INDEX.md; MAIN VULT/_System/Hermes Knowledge Graph/Hermes Knowledge Graph.md; MAIN VULT/_System/Hermes Governance/Legacy Hermes Memory Index.md; MAIN VULT/_System/Hermes Governance/Hermes Vault Routing Policy.md.'
    }
  ],
  title: 'Obsidian source-of-truth policy',
  type: 'checklist'
}

describe('parseHermesUiArtifact', () => {
  it('parses a bounded task breakdown with a stable persistence identity', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        direction: 'rtl',
        id: 'launch-brief-breakdown',
        proposalId: 'proposal-launch-brief',
        proposalRevision: 3,
        schemaVersion: 1,
        scope: 'working-session',
        steps: [
          { doneEnough: 'יש רשימת קהלים', estimateMinutes: 15, subtaskId: 'audience', title: 'להגדיר קהל' },
          { clientId: 'draft', doneEnough: 'יש טיוטה מלאה אחת', optional: true, title: 'לכתוב טיוטה' }
        ],
        stoppingRule: 'לעצור אחרי טיוטה שניתנת למשוב',
        submitLabel: 'להתחיל בצעד הראשון',
        targetOutcome: 'בריף שאפשר להעביר לעיצוב',
        task: { baseRevision: 7, id: 'task-42', title: 'להכין\u0000 בריף השקה' },
        title: 'פירוק למשימת עבודה',
        type: 'task-breakdown'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'task-breakdown' && result.artifact.task.title).toBe('להכין בריף השקה')
    expect(result.ok && result.artifact.type === 'task-breakdown' && result.artifact.steps[0]?.estimateMinutes).toBe(15)
    expect(result.ok && result.artifact.type === 'task-breakdown' && result.artifact.steps[0]?.subtaskId).toBe('audience')
    expect(result.ok && result.artifact.type === 'task-breakdown' && result.artifact.steps[1]?.clientId).toBe('draft')
    expect(result.ok && stableArtifactStorageKey(result.artifact)).toBe('hermes-ui:task-breakdown:task-42:proposal-launch-brief:r3:b7')
  })

  it('rejects unsupported task-breakdown keys and invalid or duplicate step identities', () => {
    const validBreakdown = {
      proposalId: 'proposal-1',
      proposalRevision: 1,
      schemaVersion: 1,
      scope: 'next-move',
      steps: [{ clientId: 'first', doneEnough: 'The first move is complete', title: 'Start' }],
      task: { baseRevision: 4, id: 'task-1', title: 'Large task' },
      type: 'task-breakdown'
    }

    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, onSubmit: 'unsafe' }))).toEqual({
      error: 'Unsupported task-breakdown field: onSubmit',
      ok: false
    })
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, task: { ...validBreakdown.task, href: 'javascript:alert(1)' } })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ clientId: 'first', command: 'rm', doneEnough: 'Done', title: 'A' }] })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ clientId: 'same', doneEnough: 'Done A', title: 'A' }, { clientId: 'same', doneEnough: 'Done B', title: 'B' }] }))).toEqual({
      error: 'Duplicate step identity: clientId:same',
      ok: false
    })
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ clientId: 'new', doneEnough: 'Done', subtaskId: 'existing', title: 'A' }] }))).toEqual({
      error: 'steps[0] must contain exactly one of subtaskId or clientId',
      ok: false
    })
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ doneEnough: 'Done', title: 'A' }] }))).toEqual({
      error: 'steps[0] must contain exactly one of subtaskId or clientId',
      ok: false
    })
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ doneEnough: 'Done', subtaskId: 's'.repeat(256), title: 'A' }] })).ok).toBe(true)
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ doneEnough: 'Done', subtaskId: 's'.repeat(257), title: 'A' }] })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...validBreakdown, steps: [{ clientId: 'c'.repeat(161), doneEnough: 'Done', title: 'A' }] })).ok).toBe(false)
  })

  it('enforces task-breakdown envelope, scope, step count, and estimate bounds', () => {
    const artifact = {
      proposalId: 'proposal-1',
      proposalRevision: 2,
      schemaVersion: 1,
      scope: 'full-delivery',
      steps: [{ clientId: 'first', doneEnough: 'The first move is complete', estimateMinutes: 480, title: 'Start' }],
      task: { baseRevision: 9, id: 'task-1', title: 'Large task' },
      type: 'task-breakdown'
    }

    expect(parseHermesUiArtifact(JSON.stringify(artifact)).ok).toBe(true)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, schemaVersion: 2 })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, proposalRevision: 0 })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, task: { ...artifact.task, baseRevision: 0 } })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, scope: 'finish-everything' })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, steps: [] })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, steps: Array.from({ length: 13 }, (_, index) => ({ clientId: `s-${index}`, doneEnough: 'Done', title: 'Step' })) })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, steps: [{ clientId: 'first', doneEnough: 'Done', estimateMinutes: 481, title: 'Start' }] }))).toEqual({
      error: 'steps[0].estimateMinutes must be an integer from 1 to 480',
      ok: false
    })
    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, steps: [{ clientId: 'first', title: 'Start' }] }))).toEqual({
      error: 'steps[0].doneEnough is required',
      ok: false
    })
  })

  it('accepts bounded incomplete draft steps but rejects oversized or unsafe drafts', () => {
    expect(parseHermesUiTaskBreakdownDraftSteps([{ clientId: 'draft-1', doneEnough: '', title: '' }])).toEqual([
      { clientId: 'draft-1', doneEnough: '', estimateMinutes: undefined, optional: undefined, subtaskId: undefined, title: '' }
    ])
    expect(parseHermesUiTaskBreakdownDraftSteps([
      { clientId: 'draft-1', doneEnough: 'Done', title: 'x'.repeat(501) }
    ])).toBeNull()
    expect(parseHermesUiTaskBreakdownDraftSteps([
      { clientId: 'draft-1', command: 'unsafe', doneEnough: 'Done', title: 'Start' }
    ])).toBeNull()
  })

  it('parses a valid checklist artifact', () => {
    const result = parseHermesUiArtifact(JSON.stringify(validChecklist))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'checklist' && result.artifact.items).toHaveLength(2)
  })

  it('parses the Obsidian source-of-truth policy checklist', () => {
    const result = parseHermesUiArtifact(JSON.stringify(obsidianPolicyChecklist))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'checklist' && result.artifact.items).toHaveLength(9)
    expect(result.ok && result.artifact.type === 'checklist' && result.artifact.items[4]?.label).toContain('🚀 My Projects')
  })

  it('preserves an explicit rtl direction for Hebrew artifacts', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        ...validChecklist,
        direction: 'rtl',
        items: [{ id: 'hebrew-item', label: 'פרופיל פעיל: office-work' }],
        title: 'מדיניות מקור האמת'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.direction).toBe('rtl')
  })

  it('parses questionnaire artifacts so they render instead of falling back to code', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        description: 'ענה על השאלות כדי שאוכל להמשיך נכון.',
        direction: 'rtl',
        id: 'flowstate-questionnaire',
        questions: [
          {
            helpText: 'אפשר לענות בקצרה.',
            id: 'goal',
            prompt: 'מה המטרה של הבלוק הבא?'
          },
          {
            id: 'energy',
            question: 'כמה אנרגיה יש לך עכשיו?'
          }
        ],
        title: 'שאלון קצר',
        type: 'questionnaire'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type).toBe('questionnaire')
    expect(result.ok && result.artifact.type === 'questionnaire' && result.artifact.items[0]?.label).toBe('מה המטרה של הבלוק הבא?')
    expect(result.ok && result.artifact.type === 'questionnaire' && result.artifact.items[0]?.description).toBe('אפשר לענות בקצרה.')
  })



  it('parses a compact planning funnel artifact', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        direction: 'rtl',
        id: 'flowstate-planning-funnel',
        steps: [
          { id: 'capture', label: 'לקלוט משימות', status: 'done' },
          { id: 'context', label: 'להבין הקשר', status: 'current' },
          { id: 'breakdown', label: 'לפרק לצעדים קטנים', status: 'pending' },
          { id: 'schedule', label: 'לשבץ לפי זמן ואנרגיה', status: 'pending' }
        ],
        title: 'משפך תכנון קצר',
        type: 'planning-funnel'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'planning-funnel' && result.artifact.steps[1]?.status).toBe('current')
  })

  it('parses a task context artifact for understanding before prioritizing', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        actions: [
          {
            id: 'ask-context',
            label: 'שאל אותי על ההקשר',
            submitText: 'תשאל אותי שאלה אחת קצרה כדי להבין את ההקשר של המשימה הזו לפני שאתה מחליט איפה לשבץ אותה.'
          }
        ],
        connections: ['בריאות', 'הכנה לפגישה'],
        direction: 'rtl',
        id: 'task-context-health-tests',
        progress: 'לדעת אם צריך פעולה לפני הפגישה',
        task: {
          dueDate: '2026-07-06',
          id: 'af10aa8a-3391-486d-911c-599144f3ae16',
          priority: 'high',
          status: 'todo',
          title: 'לראות שאני מקבל את הבדיקות לפני הפגישה עם הרופאה'
        },
        title: 'כרטיס הבנת משימה',
        type: 'task-context',
        unknowns: ['מתי הפגישה?', 'האם צריך להתקשר או רק לבדוק?']
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'task-context' && result.artifact.unknowns).toHaveLength(2)
  })


  it('parses the FlowState planning session artifact required by the planning surface', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        categories: [
          {
            count: 3,
            examples: [
              { dueDate: '2026-07-09', id: 'task-1', priority: 'high', title: 'להחליט מה הבלוק הבא' },
              { dueDate: null, id: 'task-2', priority: 'medium', title: 'לנקות משימות לא רלוונטיות' }
            ],
            id: 'work-pressure',
            label: 'עומס עבודה',
            recommendation: 'להוציא רק בלוק אחד לביצוע ולא לפתוח את כל הבקלוג.',
            tone: 'work'
          }
        ],
        description: 'תכנון יום קצר בתוך Hermes, ללא שינוי ישיר ב־FlowState.',
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
          },
          {
            dueDate: null,
            id: 'task-2',
            priority: 'medium',
            recommendation: 'not_today',
            status: 'todo',
            title: 'לנקות משימות לא רלוונטיות'
          }
        ],
        title: 'תכנון היום',
        type: 'flowstate-planning-session'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type).toBe('flowstate-planning-session')
    expect(result.ok && result.artifact.type === 'flowstate-planning-session' && result.artifact.categories[0]?.examples).toHaveLength(2)
    expect(result.ok && result.artifact.type === 'flowstate-planning-session' && result.artifact.nextBlock?.taskIds).toEqual(['task-1'])
  })

  it('rejects FlowState planning sessions with too many category examples', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        categories: [
          {
            count: 3,
            examples: [
              { id: 'a', title: 'A' },
              { id: 'b', title: 'B' },
              { id: 'c', title: 'C' }
            ],
            id: 'too-many',
            label: 'יותר מדי',
            recommendation: 'להציג עד שני פריטים לדוגמה.',
            tone: 'work'
          }
        ],
        mode: 'quick-triage',
        tasks: [{ id: 'task-1', title: 'משימה אחת' }],
        type: 'flowstate-planning-session'
      })
    )

    expect(result.ok).toBe(false)
    expect(!result.ok && result.error).toContain('examples has too many items')
  })


  it('rejects invalid JSON', () => {
    expect(parseHermesUiArtifact('{ nope').ok).toBe(false)
  })

  it('rejects unsupported artifact types', () => {
    const result = parseHermesUiArtifact(JSON.stringify({ type: 'html', value: '<button>Run</button>' }))

    expect(result.ok).toBe(false)
  })

  it('rejects duplicate item ids', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({ ...validChecklist, items: [{ id: 'same', label: 'A' }, { id: 'same', label: 'B' }] })
    )

    expect(result.ok).toBe(false)
    expect(!result.ok && result.error).toContain('Duplicate')
  })

  it('parses safe copy actions for task-oriented checklists', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        ...validChecklist,
        items: [
          {
            actions: [
              {
                copyText: 'דחה את המשימה 25c8 למחר',
                id: 'postpone-tomorrow',
                label: 'דחה למחר'
              },
              {
                copyText: 'משימה: לבדוק בדיקות לפני הרופאה',
                id: 'copy-task',
                label: 'העתק לכאן'
              }
            ],
            id: 'flowstate-task',
            label: 'לראות שאני מקבל את הבדיקות לפני הפגישה עם הרופאה'
          }
        ]
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'checklist' && result.artifact.items[0]?.actions?.[0]?.label).toBe('דחה למחר')
  })


  it('parses safe submit actions for assistant-routed task triage decisions', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        ...validChecklist,
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
        ]
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'checklist' && result.artifact.items[0]?.actions?.[0]?.submitText).toContain(
      '477d9abb'
    )
  })

  it('rejects malformed item actions', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        ...validChecklist,
        items: [{ actions: [{ id: 'bad', label: 'Bad', copyText: 'x'.repeat(1201) }], id: 'task', label: 'Task' }]
      })
    )

    expect(result.ok).toBe(false)
  })



  it('parses a compact FlowState task triage artifact', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        direction: 'rtl',
        id: 'flowstate-triage-task',
        task: {
          dueDate: '2026-07-08',
          id: 'af6d08a0-ec26-41eb-b8c2-2a3c19637c2f',
          priority: 'medium',
          status: 'todo',
          title: 'לסדר את המקרר'
        },
        title: 'FlowState — החלטה אחת עכשיו',
        type: 'task-triage'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'task-triage' && result.artifact.task.priority).toBe('medium')
  })


  it('parses a FlowState batch artifact with assistant recommendations', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
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
        title: 'FlowState — triage assistant',
        type: 'flowstate-task-batch'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'flowstate-task-batch' && result.artifact.tasks[0]?.recommendedPriority).toBe('high')
  })

  it('parses a compact FlowState next-block artifact and preserves rtl direction', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        actions: [
          {
            id: 'preview',
            label: 'תראה לי preview לפני שינוי ב־FlowState',
            submitText:
              'תעשה preview לבלוק הזה ב־FlowState: taskId=task-1 date=2026-07-08 time=10:30 duration=25'
          },
          {
            id: 'apply-after-approval',
            label: 'מאשר להוסיף את הבלוק ל־FlowState',
            submitText:
              'מאשר להוסיף את הבלוק הזה ל־FlowState: taskId=task-1 date=2026-07-08 time=10:30 duration=25'
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
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'flowstate-next-block' && result.artifact.direction).toBe('rtl')
    expect(result.ok && result.artifact.type === 'flowstate-next-block' && result.artifact.previewSummary.scheduledTime).toBe('10:30')
    expect(result.ok && result.artifact.type === 'flowstate-next-block' && result.artifact.actions).toHaveLength(2)
  })

  it('requires FlowState next-block task id/title and preview summary', () => {
    const missingTask = parseHermesUiArtifact(JSON.stringify({ type: 'flowstate-next-block' }))

    const missingPreview = parseHermesUiArtifact(
      JSON.stringify({
        doneEnough: 'Done enough',
        durationMinutes: 25,
        task: { id: 'task-1', title: 'Task' },
        type: 'flowstate-next-block'
      })
    )

    expect(missingTask.ok).toBe(false)
    expect(missingPreview.ok).toBe(false)
  })

  it('rejects unknown mutation fields on FlowState next-block artifacts', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        doneEnough: 'Done enough',
        durationMinutes: 25,
        previewSummary: { duration: 25, scheduledDate: '2026-07-08', scheduledTime: '10:30' },
        status: 'done',
        task: { id: 'task-1', title: 'Task' },
        type: 'flowstate-next-block'
      })
    )

    expect(result.ok).toBe(false)
    expect(!result.ok && result.error).toContain('Unsupported')
  })

  it('rejects next-block actions without submitText so buttons cannot become no-ops', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        actions: [{ copyText: 'copy only', id: 'copy-only', label: 'Copy only' }],
        doneEnough: 'Done enough',
        durationMinutes: 25,
        previewSummary: { duration: 25, scheduledDate: '2026-07-08', scheduledTime: '10:30' },
        rationale: 'Small and safe.',
        task: { id: 'task-1', title: 'Task' },
        type: 'flowstate-next-block'
      })
    )

    expect(result.ok).toBe(false)
    expect(!result.ok && result.error).toContain('submitText')
  })

  it('rejects too-long FlowState next-block text', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        doneEnough: 'x'.repeat(281),
        durationMinutes: 25,
        previewSummary: { duration: 25, scheduledDate: '2026-07-08', scheduledTime: '10:30' },
        task: { id: 'task-1', title: 'Task' },
        type: 'flowstate-next-block'
      })
    )

    expect(result.ok).toBe(false)
  })

  it('rejects malformed FlowState task triage priority', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        task: { id: 'task', priority: 'urgent', title: 'Task' },
        type: 'task-triage'
      })
    )

    expect(result.ok).toBe(false)
  })

  it('rejects excessive item counts and string lengths', () => {
    const tooManyItems = Array.from({ length: 101 }, (_, index) => ({ id: `item-${index}`, label: 'Item' }))
    expect(parseHermesUiArtifact(JSON.stringify({ ...validChecklist, items: tooManyItems })).ok).toBe(false)

    expect(parseHermesUiArtifact(JSON.stringify({ ...validChecklist, title: 'x'.repeat(161) })).ok).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({ ...validChecklist, items: [{ id: 'item', label: 'x'.repeat(801) }] })
      ).ok
    ).toBe(false)
  })

  it('parses a task-table artifact with explicit unknowns', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        columns: ['task', 'context', 'timeSize', 'energy', 'urgency', 'nextStep', 'confidence'],
        direction: 'rtl',
        rows: [
          { confidence: 'low', context: 'לא ברור למה זה חשוב', energy: 'unknown', id: 't1', nextStep: 'לשאול שאלה אחת', timeSize: 'unknown', title: 'לבדוק בדיקות', urgency: 'high' },
          { confidence: 'medium', context: 'לקוח', energy: 'medium', id: 't2', nextStep: 'לכתוב טיוטה', timeSize: 'small', title: 'מייל ללקוח', urgency: 'medium' },
          { confidence: 'high', context: 'בית', energy: 'low', id: 't3', nextStep: '10 דקות סידור', timeSize: 'tiny', title: 'לסדר שולחן', urgency: 'low' }
        ],
        title: 'השוואת משימות',
        type: 'task-table'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'task-table' && result.artifact.rows).toHaveLength(3)
  })

  it('parses a mini-kanban artifact', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        lanes: [
          { id: 'today', tasks: [{ confidence: 'medium', id: 't1', title: 'טיוטה ללקוח' }], title: 'היום' },
          { id: 'need-context', tasks: [{ id: 't2', note: 'צריך להבין משמעות', title: 'בדיקות' }], title: 'צריך הקשר' }
        ],
        title: 'מיון קצר',
        type: 'mini-kanban'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'mini-kanban' && result.artifact.lanes[1]?.id).toBe('need-context')
  })

  it('parses a day-timeline artifact', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        blocks: [
          { endTime: '10:25', id: 'b1', kind: 'focus', label: 'טיוטה ללקוח', startTime: '10:00', status: 'candidate' },
          { durationMinutes: 15, id: 'float-1', kind: 'floating', label: 'בדיקה קצרה', status: 'planned' }
        ],
        currentTime: '09:45',
        date: '2026-07-09',
        title: 'תכנון יום אפשרי',
        type: 'day-timeline'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'day-timeline' && result.artifact.currentTime).toBe('09:45')
  })

  it('parses a mutation-preview artifact with approval actions', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
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
        title: 'Preview לפני שינוי',
        type: 'mutation-preview'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'mutation-preview' && result.artifact.actions).toHaveLength(3)
  })

  it('parses an exact FlowState subtask approval without model-authored actions', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        canonicalApproval: {
          action: 'subtask_batch',
          baseRevision: 7,
          contractVersion: 'task-v1',
          operationId: 'breakdown:proposal-1:r3',
          operations: [
            {
              clientId: 'draft',
              doneEnough: 'A reviewable draft exists',
              estimateMinutes: 20,
              kind: 'create',
              order: 0,
              title: 'Draft the outline'
            },
            { kind: 'delete', subtaskId: 'obsolete' }
          ],
          previewDigest: 'a'.repeat(64),
          previewExpiresAt: '2099-07-16T10:00:00.000Z',
          proposalId: 'proposal-1',
          proposalRevision: 3,
          requestHash: 'b'.repeat(64),
          taskId: 'task-1'
        },
        changes: [
          {
            after: { steps: 1 },
            before: { steps: 1 },
            operation: 'update',
            taskId: 'task-1',
            title: 'Prepare launch'
          }
        ],
        title: 'Approve exact breakdown',
        type: 'mutation-preview'
      })
    )

    expect(result.ok).toBe(true)
    expect(
      result.ok &&
      result.artifact.type === 'mutation-preview' &&
      result.artifact.canonicalApproval?.operations[0]
    ).toMatchObject({ clientId: 'draft', kind: 'create', order: 0 })
  })

  it('fails closed on malformed, changed, or ambiguously actionable canonical approvals', () => {
    const canonicalApproval = {
      action: 'subtask_batch',
      baseRevision: 7,
      contractVersion: 'task-v1',
      operationId: 'breakdown:proposal-1:r3',
      operations: [{ clientId: 'draft', kind: 'create', order: 0, title: 'Draft' }],
      previewDigest: 'a'.repeat(64),
      previewExpiresAt: '2099-07-16T10:00:00.000Z',
      proposalId: 'proposal-1',
      proposalRevision: 3,
      requestHash: 'b'.repeat(64),
      taskId: 'task-1'
    }

    const artifact = {
      canonicalApproval,
      changes: [{ operation: 'update', taskId: 'task-1', title: 'Prepare launch' }],
      type: 'mutation-preview'
    }

    expect(parseHermesUiArtifact(JSON.stringify(artifact)).ok).toBe(true)
    expect(parseHermesUiArtifact(JSON.stringify({
      ...artifact,
      actions: [{ id: 'approve', label: 'Approve', submitText: 'Trust me' }]
    })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({
      ...artifact,
      canonicalApproval: { ...canonicalApproval, requestHash: 'not-a-digest' }
    })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({
      ...artifact,
      canonicalApproval: {
        ...canonicalApproval,
        operations: [{ clientId: 'draft', kind: 'create', subtaskId: 'existing', title: 'Draft' }]
      }
    })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({
      ...artifact,
      canonicalApproval: { ...canonicalApproval, proposalRevision: 0 }
    })).ok).toBe(false)
  })

  it('parses an urgency-energy-matrix artifact', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        cells: [
          {
            label: 'דחוף באנרגיה נמוכה',
            tasks: [{ confidence: 'low', id: 't1', priority: 'high', title: 'שיחה קצרה' }],
            x: 'low',
            y: 'high'
          }
        ],
        title: 'בחירה לפי אנרגיה',
        type: 'urgency-energy-matrix',
        xAxis: 'energy',
        yAxis: 'urgency'
      })
    )

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'urgency-energy-matrix' && result.artifact.cells[0]?.tasks[0]?.confidence).toBe('low')
  })

  it('parses workload-bars and task-graph artifacts', () => {
    const bars = parseHermesUiArtifact(
      JSON.stringify({
        bars: [
          { id: 'overdue', label: 'באיחור', max: 10, tone: 'warning', value: 3 },
          { id: 'no-date', label: 'ללא תאריך', tone: 'neutral', value: 8 }
        ],
        title: 'עומס כללי',
        type: 'workload-bars'
      })
    )

    const graph = parseHermesUiArtifact(
      JSON.stringify({
        edges: [{ label: 'תלוי ב', source: 'task', target: 'person' }],
        nodes: [
          { id: 'task', kind: 'task', label: 'לשלוח הצעה' },
          { id: 'person', kind: 'person', label: 'לקוח' }
        ],
        title: 'קשרי משימה',
        type: 'task-graph'
      })
    )

    expect(bars.ok && bars.artifact.type === 'workload-bars' && bars.artifact.bars[0]?.tone).toBe('warning')
    expect(graph.ok && graph.artifact.type === 'task-graph' && graph.artifact.edges[0]?.target).toBe('person')
  })

  it('rejects malformed planning primitive payloads', () => {
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          columns: ['task'],
          rows: [{ id: 'one', title: 'One' }, { id: 'two', title: 'Two' }],
          type: 'task-table'
        })
      ).ok
    ).toBe(false)

    expect(parseHermesUiArtifact(JSON.stringify({ lanes: [{ id: 'same', tasks: [], title: 'A' }, { id: 'same', tasks: [], title: 'B' }], type: 'mini-kanban' })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ blocks: [{ id: 'b1', kind: 'deep', label: 'Bad' }], date: '2026-07-09', type: 'day-timeline' })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ actions: [{ id: 'copy', label: 'Copy', copyText: 'copy' }], changes: [], type: 'mutation-preview' })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ cells: [{ tasks: [], x: 'unknown', y: 'high' }], type: 'urgency-energy-matrix', xAxis: 'energy', yAxis: 'urgency' })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ bars: [{ id: 'b', label: 'Bad', value: -1 }], type: 'workload-bars' })).ok).toBe(false)
    expect(parseHermesUiArtifact(JSON.stringify({ edges: [{ source: 'a', target: 'missing' }], nodes: [{ id: 'a', label: 'A' }], type: 'task-graph' })).ok).toBe(false)
  })

  it('uses a stable normalized storage key when an id is present', () => {
    const result = parseHermesUiArtifact(JSON.stringify(validChecklist))

    expect(result.ok && stableArtifactStorageKey(result.artifact as HermesUiChecklistArtifact)).toBe(
      'hermes-ui:checklist:obsidian-source-truth'
    )
  })

  it('uses a stable hash when no id is present', () => {
    const first = { ...validChecklist, id: undefined }
    const second = { items: first.items, title: first.title, description: first.description, type: first.type }
    const firstResult = parseHermesUiArtifact(JSON.stringify(first))
    const secondResult = parseHermesUiArtifact(JSON.stringify(second))

    expect(firstResult.ok && secondResult.ok).toBe(true)
    expect(
      firstResult.ok &&
        secondResult.ok &&
        firstResult.artifact.type === 'checklist' &&
        secondResult.artifact.type === 'checklist' &&
        stableArtifactStorageKey(firstResult.artifact) === stableArtifactStorageKey(secondResult.artifact)
    ).toBe(true)
  })
})
