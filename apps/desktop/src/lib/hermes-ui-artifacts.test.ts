import { describe, expect, it } from 'vitest'

import {
  type HermesUiChecklistArtifact,
  parseHermesUiArtifact,
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
