import { describe, expect, it } from 'vitest'

import {
  parseHermesUiArtifact,
  parseHermesUiTaskBreakdownDraftSteps,
  stableArtifactStorageKey
} from './hermes-ui-artifacts'

const breakdown = {
  proposalId: 'proposal-launch',
  proposalRevision: 3,
  schemaVersion: 1,
  scope: 'working-session',
  steps: [
    { doneEnough: 'Audience is named', estimateMinutes: 15, subtaskId: 'audience', title: 'Choose audience' },
    { clientId: 'draft', doneEnough: 'One page exists', optional: true, title: 'Draft brief' }
  ],
  task: { baseRevision: 7, id: 'task-42', title: 'Prepare launch brief' },
  type: 'task-breakdown'
} as const

const canonicalApproval = {
  action: 'subtask_batch',
  baseRevision: 7,
  contractVersion: 'task-v1',
  operationId: 'breakdown:proposal-launch:r3',
  operations: [{ clientId: 'draft', doneEnough: 'One page exists', kind: 'create', order: 1, title: 'Draft brief' }],
  previewDigest: 'a'.repeat(64),
  previewExpiresAt: '2099-07-16T12:00:00Z',
  proposalId: 'proposal-launch',
  proposalRevision: 3,
  requestHash: 'b'.repeat(64),
  taskId: 'task-42'
} as const

describe('parseHermesUiArtifact', () => {
  it('parses only the bounded task-breakdown contract and gives each revision a stable key', () => {
    const result = parseHermesUiArtifact(JSON.stringify(breakdown))

    expect(result.ok).toBe(true)
    expect(result.ok && result.artifact.type === 'task-breakdown' && result.artifact.steps[0]).toMatchObject({
      estimateMinutes: 15,
      subtaskId: 'audience'
    })
    expect(result.ok && stableArtifactStorageKey(result.artifact)).toBe(
      'hermes-ui:task-breakdown:task-42-pa3kfp:proposal-launch-1ye09k1:r3:b7'
    )
  })

  it('rejects unknown envelope fields, unstable step identities, and out-of-bounds edits', () => {
    expect(parseHermesUiArtifact(JSON.stringify({ ...breakdown, onSubmit: 'unsafe' }))).toEqual({
      error: 'Unsupported task-breakdown field: onSubmit',
      ok: false
    })
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...breakdown,
          steps: [
            { clientId: 'same', doneEnough: 'A', title: 'A' },
            { clientId: 'same', doneEnough: 'B', title: 'B' }
          ]
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...breakdown,
          steps: [{ clientId: 'both', doneEnough: 'Done', subtaskId: 'both', title: 'No' }]
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...breakdown,
          steps: [{ clientId: 'slow', doneEnough: 'Done', estimateMinutes: 481, title: 'No' }]
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...breakdown,
          proposalId: 'p'.repeat(121)
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...breakdown,
          task: { ...breakdown.task, id: 't'.repeat(161) }
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...breakdown,
          task: { ...breakdown.task, id: ' task-42 ' }
        })
      ).ok
    ).toBe(false)
  })

  it('allows empty editable draft text but never unsupported persisted fields', () => {
    expect(parseHermesUiTaskBreakdownDraftSteps([{ clientId: 'new', doneEnough: '', title: '' }])).toEqual([
      { clientId: 'new', doneEnough: '', title: '' }
    ])
    expect(
      parseHermesUiTaskBreakdownDraftSteps([{ clientId: 'new', command: 'rm', doneEnough: 'Done', title: 'Unsafe' }])
    ).toBeNull()
  })

  it('keeps identities that normalize alike in separate draft storage slots', () => {
    const first = parseHermesUiArtifact(
      JSON.stringify({
        ...breakdown,
        task: { ...breakdown.task, id: 'task/a' }
      })
    )

    const second = parseHermesUiArtifact(
      JSON.stringify({
        ...breakdown,
        task: { ...breakdown.task, id: 'task a' }
      })
    )

    expect(first.ok).toBe(true)
    expect(second.ok).toBe(true)
    expect(first.ok && second.ok && stableArtifactStorageKey(first.artifact)).not.toBe(
      second.ok ? stableArtifactStorageKey(second.artifact) : undefined
    )
  })

  it('parses exact canonical mutation proof and keeps operation order', () => {
    const result = parseHermesUiArtifact(
      JSON.stringify({
        canonicalApproval,
        changes: [
          {
            after: { steps: 2 },
            before: { steps: 1 },
            operation: 'update',
            taskId: 'task-42',
            title: 'Prepare launch brief'
          }
        ],
        title: 'Approve exact breakdown',
        type: 'mutation-preview'
      })
    )

    expect(result.ok).toBe(true)
    expect(
      result.ok && result.artifact.type === 'mutation-preview' && result.artifact.canonicalApproval.operations[0]
    ).toMatchObject({
      clientId: 'draft',
      kind: 'create',
      order: 1
    })
    expect(result.ok && stableArtifactStorageKey(result.artifact)).toBe(
      'hermes-ui:mutation-preview:task-42-pa3kfp:proposal-launch-1ye09k1:r3:b7'
    )
  })

  it('fails closed when canonical proof is changed, ambiguous, or for another task', () => {
    const artifact = {
      canonicalApproval,
      changes: [{ operation: 'update', taskId: 'task-42', title: 'Prepare launch brief' }],
      type: 'mutation-preview'
    }

    expect(parseHermesUiArtifact(JSON.stringify({ ...artifact, actions: [{ id: 'approve' }] })).ok).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          canonicalApproval: { ...canonicalApproval, requestHash: 'not-a-digest' }
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          canonicalApproval: {
            ...canonicalApproval,
            operations: [{ clientId: 'draft', kind: 'create', subtaskId: 'existing', title: 'Draft' }]
          }
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          changes: [{ operation: 'update', taskId: 'other-task', title: 'Other' }]
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          changes: [{ operation: 'complete', taskId: 'task-42', title: 'Unrelated action' }]
        })
      ).ok
    ).toBe(false)
  })

  it('parses an exact Notion mutation approval and rejects cross-tool or changed apply data', () => {
    const expires = '2099-07-16T12:00:00Z'
    const apply = {
      action: 'set_status',
      data_source_id: 'source-1',
      mode: 'apply',
      operation_id: 'notion-status-1',
      page_id: 'page-1',
      preview_digest: `sha256:${'a'.repeat(64)}`,
      preview_expires_at: expires,
      status_name: 'In progress',
      status_property: 'Status'
    }
    const artifact = {
      canonicalApproval: {
        apply,
        contractVersion: 'notion-bridge-v1',
        previewExpiresAt: expires,
        tool: 'notion_mutation'
      },
      changes: [
        { after: { Status: 'In progress' }, operation: 'update', targetId: 'page-1', title: 'Prepare proposal' }
      ],
      type: 'notion-mutation-preview'
    }
    const result = parseHermesUiArtifact(JSON.stringify(artifact))

    expect(result.ok).toBe(true)
    expect(
      result.ok && result.artifact.type === 'notion-mutation-preview' && result.artifact.canonicalApproval.apply
    ).toEqual(apply)
    expect(result.ok && stableArtifactStorageKey(result.artifact)).toContain('notion-status-1')
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          canonicalApproval: { ...artifact.canonicalApproval, tool: 'notion_flowstate_activate' }
        })
      ).ok
    ).toBe(false)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          canonicalApproval: { ...artifact.canonicalApproval, apply: { ...apply, page_id: 'other-page' } }
        })
      ).ok
    ).toBe(true)
    expect(
      parseHermesUiArtifact(
        JSON.stringify({
          ...artifact,
          canonicalApproval: {
            ...artifact.canonicalApproval,
            apply: { ...apply, preview_expires_at: '2099-07-16T12:01:00Z' }
          }
        })
      ).ok
    ).toBe(false)
  })

  it('rejects every artifact type outside the reduced lane', () => {
    expect(parseHermesUiArtifact(JSON.stringify({ items: [], type: 'checklist' }))).toEqual({
      error: 'Unsupported artifact type',
      ok: false
    })
  })
})
