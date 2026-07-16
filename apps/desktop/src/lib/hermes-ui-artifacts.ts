export type HermesUiDirection = 'auto' | 'ltr' | 'rtl'
export type HermesUiTaskBreakdownScope = 'next-move' | 'working-session' | 'full-delivery'

export interface HermesUiTaskBreakdownStep {
  clientId?: string
  doneEnough: string
  estimateMinutes?: number
  optional?: boolean
  subtaskId?: string
  title: string
}

export interface HermesUiTaskBreakdownArtifact {
  type: 'task-breakdown'
  schemaVersion: 1
  proposalId: string
  proposalRevision: number
  direction?: HermesUiDirection
  id?: string
  title?: string
  description?: string
  task: {
    baseRevision: number
    id: string
    title: string
  }
  scope: HermesUiTaskBreakdownScope
  targetOutcome?: string
  stoppingRule?: string
  steps: HermesUiTaskBreakdownStep[]
  submitLabel?: string
}

export type HermesUiMutationOperation = 'create' | 'delete' | 'update'
export type HermesUiMutationRisk = 'low' | 'medium' | 'high'
export type HermesUiVisibleRecord = Record<string, boolean | null | number | string>

export interface HermesUiMutationPreviewChange {
  taskId: string
  title: string
  operation: HermesUiMutationOperation
  after?: HermesUiVisibleRecord
  before?: HermesUiVisibleRecord
  risk?: HermesUiMutationRisk
  untouched?: string[]
}

export type HermesUiCanonicalSubtaskOperation =
  | {
      kind: 'create'
      clientId: string
      title: string
      description?: string
      doneEnough?: null | string
      estimateMinutes?: null | number
      completedPomodoros?: number
      canvasPosition?: null | { x: number; y: number }
      isCompleted?: boolean
      order?: number
    }
  | {
      kind: 'update'
      subtaskId: string
      title?: string
      description?: string
      doneEnough?: null | string
      estimateMinutes?: null | number
      completedPomodoros?: number
      canvasPosition?: null | { x: number; y: number }
      isCompleted?: boolean
      order?: number
    }
  | { kind: 'delete'; subtaskId: string }

export interface HermesUiCanonicalSubtaskApproval {
  action: 'subtask_batch'
  baseRevision: number
  contractVersion: 'task-v1'
  operationId: string
  operations: HermesUiCanonicalSubtaskOperation[]
  previewDigest: string
  previewExpiresAt: string
  proposalId: string
  proposalRevision: number
  requestHash: string
  taskId: string
}

export interface HermesUiMutationPreviewArtifact {
  type: 'mutation-preview'
  canonicalApproval: HermesUiCanonicalSubtaskApproval
  changes: HermesUiMutationPreviewChange[]
  direction?: HermesUiDirection
  id?: string
  title?: string
  description?: string
}

export type HermesUiArtifact = HermesUiMutationPreviewArtifact | HermesUiTaskBreakdownArtifact

export interface HermesUiArtifactParseSuccess {
  artifact: HermesUiArtifact
  ok: true
}

export interface HermesUiArtifactParseFailure {
  error: string
  ok: false
}

export type HermesUiArtifactParseResult = HermesUiArtifactParseFailure | HermesUiArtifactParseSuccess

export const HERMES_UI_TASK_BREAKDOWN_LIMITS = {
  doneEnoughLength: 1000,
  stepCount: 12,
  titleLength: 500
} as const

const MAX_ID_LENGTH = 160
const MAX_PROPOSAL_ID_LENGTH = 120
const MAX_SUBTASK_ID_LENGTH = 256
const MAX_TASK_ID_LENGTH = 160
const MAX_TITLE_LENGTH = 800
const MAX_DESCRIPTION_LENGTH = 1000
const MAX_CANONICAL_DESCRIPTION_LENGTH = 10_000
const MAX_CANONICAL_DONE_ENOUGH_LENGTH = 2_000
const MAX_MUTATION_CHANGES = 10
const MAX_CANONICAL_OPERATIONS = 50
const MAX_VISIBLE_FIELDS = 12
const SHA256_HEX_RE = /^[0-9a-f]{64}$/
const ISO_TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/

const TASK_BREAKDOWN_KEYS = new Set([
  'description',
  'direction',
  'id',
  'proposalId',
  'proposalRevision',
  'schemaVersion',
  'scope',
  'steps',
  'stoppingRule',
  'submitLabel',
  'targetOutcome',
  'task',
  'title',
  'type'
])

const TASK_KEYS = new Set(['baseRevision', 'id', 'title'])
const STEP_KEYS = new Set(['clientId', 'doneEnough', 'estimateMinutes', 'optional', 'subtaskId', 'title'])
const MUTATION_PREVIEW_KEYS = new Set(['canonicalApproval', 'changes', 'description', 'direction', 'id', 'title', 'type'])
const CHANGE_KEYS = new Set(['after', 'before', 'operation', 'risk', 'taskId', 'title', 'untouched'])

const APPROVAL_KEYS = new Set([
  'action',
  'baseRevision',
  'contractVersion',
  'operationId',
  'operations',
  'previewDigest',
  'previewExpiresAt',
  'proposalId',
  'proposalRevision',
  'requestHash',
  'taskId'
])

const OPERATION_KEYS = new Set([
  'canvasPosition',
  'clientId',
  'completedPomodoros',
  'description',
  'doneEnough',
  'estimateMinutes',
  'isCompleted',
  'kind',
  'order',
  'subtaskId',
  'title'
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isFailure(value: unknown): value is HermesUiArtifactParseFailure {
  return isRecord(value) && value.ok === false && typeof value.error === 'string'
}

function unsupportedField(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  field: string
): HermesUiArtifactParseFailure | undefined {
  const unsupported = Object.keys(value).find(key => !allowed.has(key))

  return unsupported ? { error: `Unsupported ${field} field: ${unsupported}`, ok: false } : undefined
}

function normalizedText(
  value: unknown,
  maxLength: number,
  field: string,
  required = false
): HermesUiArtifactParseFailure | string | undefined {
  if (value === undefined || value === null) {
    return required ? { error: `${field} is required`, ok: false } : undefined
  }

  if (typeof value !== 'string') {
    return { error: `${field} must be a string`, ok: false }
  }

  const text = value.replace(/\0/g, '').trim()

  if (text.length > maxLength) {
    return { error: `${field} is too long`, ok: false }
  }

  if (required && !text) {
    return { error: `${field} is required`, ok: false }
  }

  return text || undefined
}

function exactText(
  value: unknown,
  maxLength: number,
  field: string,
  required = false
): HermesUiArtifactParseFailure | string | undefined {
  if (value === undefined) {
    return required ? { error: `${field} is required`, ok: false } : undefined
  }

  if (typeof value !== 'string') {
    return { error: `${field} must be a string`, ok: false }
  }

  if (value.length > maxLength) {
    return { error: `${field} is too long`, ok: false }
  }

  if ((required && !value) || value !== value.trim() || value.includes('\0')) {
    return { error: `${field} must be exact trimmed text`, ok: false }
  }

  return value
}

function requiredNormalizedText(value: unknown, maxLength: number, field: string): HermesUiArtifactParseFailure | string {
  return normalizedText(value, maxLength, field, true) ?? { error: `${field} is required`, ok: false }
}

function requiredExactText(value: unknown, maxLength: number, field: string): HermesUiArtifactParseFailure | string {
  return exactText(value, maxLength, field, true) ?? { error: `${field} is required`, ok: false }
}

function positiveSafeInteger(value: unknown, field: string): HermesUiArtifactParseFailure | number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    return { error: `${field} must be a positive safe integer`, ok: false }
  }

  return value as number
}

function parseDirection(value: unknown): HermesUiArtifactParseFailure | HermesUiDirection | undefined {
  if (value === undefined) {
    return undefined
  }

  return value === 'auto' || value === 'ltr' || value === 'rtl'
    ? value
    : { error: 'direction must be auto, ltr, or rtl', ok: false }
}

function parseBase(value: Record<string, unknown>) {
  const direction = parseDirection(value.direction)
  const id = normalizedText(value.id, MAX_ID_LENGTH, 'id')
  const title = normalizedText(value.title, MAX_TITLE_LENGTH, 'title')
  const description = normalizedText(value.description, MAX_DESCRIPTION_LENGTH, 'description')

  for (const candidate of [direction, id, title, description]) {
    if (isFailure(candidate)) {
      return candidate
    }
  }

  return {
    description: description as string | undefined,
    direction: direction as HermesUiDirection | undefined,
    id: id as string | undefined,
    title: title as string | undefined
  }
}

function parseTaskBreakdownSteps(
  value: unknown,
  allowEmptyEditableFields: boolean
): HermesUiArtifactParseFailure | HermesUiTaskBreakdownStep[] {
  if (!Array.isArray(value) || value.length === 0) {
    return { error: 'task-breakdown steps are required', ok: false }
  }

  if (value.length > HERMES_UI_TASK_BREAKDOWN_LIMITS.stepCount) {
    return { error: 'task-breakdown has too many steps', ok: false }
  }

  const identities = new Set<string>()
  const steps: HermesUiTaskBreakdownStep[] = []

  for (const [index, candidate] of value.entries()) {
    if (!isRecord(candidate)) {
      return { error: `steps[${index}] must be an object`, ok: false }
    }

    const unsupported = unsupportedField(candidate, STEP_KEYS, `steps[${index}]`)

    if (unsupported) {
      return unsupported
    }

    const hasClientId = candidate.clientId !== undefined
    const hasSubtaskId = candidate.subtaskId !== undefined

    if (hasClientId === hasSubtaskId) {
      return { error: `steps[${index}] must contain exactly one of subtaskId or clientId`, ok: false }
    }

    const identityField = hasSubtaskId ? 'subtaskId' : 'clientId'

    const identity = requiredExactText(
      candidate[identityField],
      hasSubtaskId ? MAX_SUBTASK_ID_LENGTH : MAX_ID_LENGTH,
      `steps[${index}].${identityField}`
    )

    if (typeof identity !== 'string') {
      return identity
    }

    const identityKey = `${identityField}:${identity}`

    if (identities.has(identityKey)) {
      return { error: `Duplicate step identity: ${identityKey}`, ok: false }
    }

    identities.add(identityKey)

    const title = normalizedText(
      candidate.title,
      HERMES_UI_TASK_BREAKDOWN_LIMITS.titleLength,
      `steps[${index}].title`,
      !allowEmptyEditableFields
    )

    const doneEnough = normalizedText(
      candidate.doneEnough,
      HERMES_UI_TASK_BREAKDOWN_LIMITS.doneEnoughLength,
      `steps[${index}].doneEnough`,
      !allowEmptyEditableFields
    )

    if (isFailure(title)) {
      return title
    }

    if (isFailure(doneEnough)) {
      return doneEnough
    }

    let estimateMinutes: number | undefined

    if (candidate.estimateMinutes !== undefined) {
      if (
        !Number.isInteger(candidate.estimateMinutes) ||
        (candidate.estimateMinutes as number) < 1 ||
        (candidate.estimateMinutes as number) > 480
      ) {
        return { error: `steps[${index}].estimateMinutes must be an integer from 1 to 480`, ok: false }
      }

      estimateMinutes = candidate.estimateMinutes as number
    }

    if (candidate.optional !== undefined && typeof candidate.optional !== 'boolean') {
      return { error: `steps[${index}].optional must be a boolean`, ok: false }
    }

    steps.push({
      ...(hasClientId ? { clientId: identity } : { subtaskId: identity }),
      doneEnough: (doneEnough as string | undefined) ?? '',
      ...(estimateMinutes === undefined ? {} : { estimateMinutes }),
      ...(candidate.optional === undefined ? {} : { optional: candidate.optional }),
      title: (title as string | undefined) ?? ''
    })
  }

  return steps
}

export function parseHermesUiTaskBreakdownDraftSteps(value: unknown): HermesUiTaskBreakdownStep[] | null {
  const result = parseTaskBreakdownSteps(value, true)

  return Array.isArray(result) ? result : null
}

function parseTaskBreakdown(value: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = unsupportedField(value, TASK_BREAKDOWN_KEYS, 'task-breakdown')

  if (unsupported) {
    return unsupported
  }

  if (value.schemaVersion !== 1) {
    return { error: 'schemaVersion must be 1', ok: false }
  }

  const base = parseBase(value)

  if (isFailure(base)) {
    return base
  }

  const proposalId = requiredExactText(value.proposalId, MAX_PROPOSAL_ID_LENGTH, 'proposalId')
  const proposalRevision = positiveSafeInteger(value.proposalRevision, 'proposalRevision')

  if (typeof proposalId !== 'string') {
    return proposalId
  }

  if (isFailure(proposalRevision)) {
    return proposalRevision
  }

  if (!isRecord(value.task)) {
    return { error: 'task-breakdown task is required', ok: false }
  }

  const unsupportedTask = unsupportedField(value.task, TASK_KEYS, 'task')

  if (unsupportedTask) {
    return unsupportedTask
  }

  const taskId = requiredExactText(value.task.id, MAX_TASK_ID_LENGTH, 'task.id')
  const taskTitle = requiredNormalizedText(value.task.title, MAX_TITLE_LENGTH, 'task.title')
  const baseRevision = positiveSafeInteger(value.task.baseRevision, 'task.baseRevision')

  if (typeof taskId !== 'string') {
    return taskId
  }

  if (typeof taskTitle !== 'string') {
    return taskTitle
  }

  if (isFailure(baseRevision)) {
    return baseRevision
  }

  if (value.scope !== 'next-move' && value.scope !== 'working-session' && value.scope !== 'full-delivery') {
    return { error: 'scope must be next-move, working-session, or full-delivery', ok: false }
  }

  const steps = parseTaskBreakdownSteps(value.steps, false)

  if (!Array.isArray(steps)) {
    return steps
  }

  const targetOutcome = normalizedText(value.targetOutcome, MAX_DESCRIPTION_LENGTH, 'targetOutcome')
  const stoppingRule = normalizedText(value.stoppingRule, MAX_DESCRIPTION_LENGTH, 'stoppingRule')
  const submitLabel = normalizedText(value.submitLabel, 80, 'submitLabel')

  for (const candidate of [targetOutcome, stoppingRule, submitLabel]) {
    if (isFailure(candidate)) {
      return candidate
    }
  }

  return {
    artifact: {
      ...base,
      proposalId,
      proposalRevision,
      schemaVersion: 1,
      scope: value.scope,
      steps,
      stoppingRule: stoppingRule as string | undefined,
      submitLabel: submitLabel as string | undefined,
      targetOutcome: targetOutcome as string | undefined,
      task: { baseRevision, id: taskId, title: taskTitle },
      type: 'task-breakdown'
    },
    ok: true
  }
}

function parseCanonicalOperation(
  value: unknown,
  index: number
): HermesUiArtifactParseFailure | HermesUiCanonicalSubtaskOperation {
  const field = `canonicalApproval.operations[${index}]`

  if (!isRecord(value)) {
    return { error: `${field} must be an object`, ok: false }
  }

  const unsupported = unsupportedField(value, OPERATION_KEYS, field)

  if (unsupported) {
    return unsupported
  }

  if (value.kind !== 'create' && value.kind !== 'update' && value.kind !== 'delete') {
    return { error: `${field}.kind must be create, update, or delete`, ok: false }
  }

  const isCreate = value.kind === 'create'
  const identityField = isCreate ? 'clientId' : 'subtaskId'
  const incompatibleIdentity = isCreate ? 'subtaskId' : 'clientId'
  const identity = requiredExactText(value[identityField], isCreate ? MAX_ID_LENGTH : MAX_SUBTASK_ID_LENGTH, `${field}.${identityField}`)

  if (typeof identity !== 'string') {
    return identity
  }

  if (value[incompatibleIdentity] !== undefined) {
    return { error: `${field} contains an incompatible identity`, ok: false }
  }

  if (value.kind === 'delete') {
    if (Object.keys(value).some(key => key !== 'kind' && key !== 'subtaskId')) {
      return { error: `${field} delete contains unsupported mutation fields`, ok: false }
    }

    return { kind: 'delete', subtaskId: identity }
  }

  const title = exactText(value.title, HERMES_UI_TASK_BREAKDOWN_LIMITS.titleLength, `${field}.title`, isCreate)
  const description = exactText(value.description, MAX_CANONICAL_DESCRIPTION_LENGTH, `${field}.description`)

  if (isFailure(title)) {
    return title
  }

  if (isFailure(description)) {
    return description
  }

  let doneEnough: null | string | undefined

  if (value.doneEnough === null) {
    doneEnough = null
  } else {
    const candidate = exactText(value.doneEnough, MAX_CANONICAL_DONE_ENOUGH_LENGTH, `${field}.doneEnough`)

    if (isFailure(candidate)) {
      return candidate
    }

    doneEnough = candidate
  }

  let estimateMinutes: null | number | undefined

  if (value.estimateMinutes === null) {
    estimateMinutes = null
  } else if (value.estimateMinutes !== undefined) {
    if (!Number.isSafeInteger(value.estimateMinutes) || (value.estimateMinutes as number) < 1 || (value.estimateMinutes as number) > 1440) {
      return { error: `${field}.estimateMinutes must be null or an integer from 1 to 1440`, ok: false }
    }

    estimateMinutes = value.estimateMinutes as number
  }

  let completedPomodoros: number | undefined

  if (value.completedPomodoros !== undefined) {
    if (!Number.isSafeInteger(value.completedPomodoros) || (value.completedPomodoros as number) < 0) {
      return { error: `${field}.completedPomodoros must be a non-negative safe integer`, ok: false }
    }

    completedPomodoros = value.completedPomodoros as number
  }

  let canvasPosition: null | { x: number; y: number } | undefined

  if (value.canvasPosition === null) {
    canvasPosition = null
  } else if (value.canvasPosition !== undefined) {
    if (
      !isRecord(value.canvasPosition) ||
      Object.keys(value.canvasPosition).length !== 2 ||
      typeof value.canvasPosition.x !== 'number' ||
      !Number.isFinite(value.canvasPosition.x) ||
      typeof value.canvasPosition.y !== 'number' ||
      !Number.isFinite(value.canvasPosition.y)
    ) {
      return { error: `${field}.canvasPosition must be null or finite x/y coordinates`, ok: false }
    }

    canvasPosition = { x: value.canvasPosition.x, y: value.canvasPosition.y }
  }

  if (value.isCompleted !== undefined && typeof value.isCompleted !== 'boolean') {
    return { error: `${field}.isCompleted must be a boolean`, ok: false }
  }

  let order: number | undefined

  if (value.order !== undefined) {
    if (!Number.isSafeInteger(value.order) || (value.order as number) < 0) {
      return { error: `${field}.order must be a non-negative safe integer`, ok: false }
    }

    order = value.order as number
  }

  const mutable = {
    ...(title === undefined ? {} : { title }),
    ...(description === undefined ? {} : { description }),
    ...(doneEnough === undefined ? {} : { doneEnough }),
    ...(estimateMinutes === undefined ? {} : { estimateMinutes }),
    ...(completedPomodoros === undefined ? {} : { completedPomodoros }),
    ...(canvasPosition === undefined ? {} : { canvasPosition }),
    ...(value.isCompleted === undefined ? {} : { isCompleted: value.isCompleted }),
    ...(order === undefined ? {} : { order })
  }

  if (!isCreate && Object.keys(mutable).length === 0) {
    return { error: `${field} update requires at least one changed field`, ok: false }
  }

  return isCreate
    ? { clientId: identity, kind: 'create', title: title as string, ...mutable }
    : { kind: 'update', subtaskId: identity, ...mutable }
}

function parseCanonicalApproval(value: unknown): HermesUiArtifactParseFailure | HermesUiCanonicalSubtaskApproval {
  if (!isRecord(value)) {
    return { error: 'canonicalApproval is required', ok: false }
  }

  const unsupported = unsupportedField(value, APPROVAL_KEYS, 'canonicalApproval')

  if (unsupported) {
    return unsupported
  }

  if (value.contractVersion !== 'task-v1' || value.action !== 'subtask_batch') {
    return { error: 'canonicalApproval contract/action is invalid', ok: false }
  }

  const operationId = requiredExactText(value.operationId, MAX_ID_LENGTH, 'canonicalApproval.operationId')
  const taskId = requiredExactText(value.taskId, MAX_TASK_ID_LENGTH, 'canonicalApproval.taskId')
  const proposalId = requiredExactText(value.proposalId, MAX_PROPOSAL_ID_LENGTH, 'canonicalApproval.proposalId')
  const baseRevision = positiveSafeInteger(value.baseRevision, 'canonicalApproval.baseRevision')
  const proposalRevision = positiveSafeInteger(value.proposalRevision, 'canonicalApproval.proposalRevision')

  if (typeof operationId !== 'string') {
    return operationId
  }

  if (typeof taskId !== 'string') {
    return taskId
  }

  if (typeof proposalId !== 'string') {
    return proposalId
  }

  if (isFailure(baseRevision)) {
    return baseRevision
  }

  if (isFailure(proposalRevision)) {
    return proposalRevision
  }

  if (typeof value.previewDigest !== 'string' || !SHA256_HEX_RE.test(value.previewDigest)) {
    return { error: 'canonicalApproval.previewDigest must be a lowercase SHA-256 digest', ok: false }
  }

  if (typeof value.requestHash !== 'string' || !SHA256_HEX_RE.test(value.requestHash)) {
    return { error: 'canonicalApproval.requestHash must be a lowercase SHA-256 digest', ok: false }
  }

  if (
    typeof value.previewExpiresAt !== 'string' ||
    value.previewExpiresAt.length > 64 ||
    !ISO_TIMESTAMP_RE.test(value.previewExpiresAt) ||
    !Number.isFinite(Date.parse(value.previewExpiresAt))
  ) {
    return { error: 'canonicalApproval.previewExpiresAt must be an ISO timestamp', ok: false }
  }

  if (!Array.isArray(value.operations) || value.operations.length === 0 || value.operations.length > MAX_CANONICAL_OPERATIONS) {
    return { error: 'canonicalApproval.operations must contain 1 to 50 operations', ok: false }
  }

  const identities = new Set<string>()
  const operations: HermesUiCanonicalSubtaskOperation[] = []

  for (const [index, candidate] of value.operations.entries()) {
    const operation = parseCanonicalOperation(candidate, index)

    if (isFailure(operation)) {
      return operation
    }

    const identity = operation.kind === 'create' ? `clientId:${operation.clientId}` : `subtaskId:${operation.subtaskId}`

    if (identities.has(identity)) {
      return { error: `Duplicate canonical subtask operation identity: ${identity}`, ok: false }
    }

    identities.add(identity)
    operations.push(operation)
  }

  return {
    action: 'subtask_batch',
    baseRevision,
    contractVersion: 'task-v1',
    operationId,
    operations,
    previewDigest: value.previewDigest,
    previewExpiresAt: value.previewExpiresAt,
    proposalId,
    proposalRevision,
    requestHash: value.requestHash,
    taskId
  }
}

function parseVisibleRecord(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiVisibleRecord | undefined {
  if (value === undefined) {
    return undefined
  }

  if (!isRecord(value) || Object.keys(value).length > MAX_VISIBLE_FIELDS) {
    return { error: `${field} must be a bounded object`, ok: false }
  }

  const record: HermesUiVisibleRecord = {}

  for (const [key, candidate] of Object.entries(value)) {
    if (!key || key.length > 80 || (candidate !== null && !['boolean', 'number', 'string'].includes(typeof candidate))) {
      return { error: `${field} contains an unsupported value`, ok: false }
    }

    if (typeof candidate === 'number' && !Number.isFinite(candidate)) {
      return { error: `${field} contains a non-finite number`, ok: false }
    }

    if (typeof candidate === 'string' && candidate.length > MAX_DESCRIPTION_LENGTH) {
      return { error: `${field} contains text that is too long`, ok: false }
    }

    record[key] = candidate as boolean | null | number | string
  }

  return record
}

function parseMutationPreview(value: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = unsupportedField(value, MUTATION_PREVIEW_KEYS, 'mutation-preview')

  if (unsupported) {
    return unsupported
  }

  const base = parseBase(value)

  if (isFailure(base)) {
    return base
  }

  const canonicalApproval = parseCanonicalApproval(value.canonicalApproval)

  if (isFailure(canonicalApproval)) {
    return canonicalApproval
  }

  if (!Array.isArray(value.changes) || value.changes.length === 0 || value.changes.length > MAX_MUTATION_CHANGES) {
    return { error: 'mutation-preview changes must contain 1 to 10 changes', ok: false }
  }

  const changes: HermesUiMutationPreviewChange[] = []
  const identities = new Set<string>()

  for (const [index, candidate] of value.changes.entries()) {
    if (!isRecord(candidate)) {
      return { error: `changes[${index}] must be an object`, ok: false }
    }

    const unsupportedChange = unsupportedField(candidate, CHANGE_KEYS, `changes[${index}]`)

    if (unsupportedChange) {
      return unsupportedChange
    }

    const taskId = requiredNormalizedText(candidate.taskId, MAX_TASK_ID_LENGTH, `changes[${index}].taskId`)
    const title = requiredNormalizedText(candidate.title, MAX_TITLE_LENGTH, `changes[${index}].title`)

    if (typeof taskId !== 'string') {
      return taskId
    }

    if (typeof title !== 'string') {
      return title
    }

    if (taskId !== canonicalApproval.taskId) {
      return { error: 'canonical mutation-preview changes must match taskId', ok: false }
    }

    if (
      candidate.operation !== 'update' &&
      candidate.operation !== 'create' &&
      candidate.operation !== 'delete'
    ) {
      return { error: `changes[${index}].operation is invalid`, ok: false }
    }

    if (candidate.risk !== undefined && candidate.risk !== 'low' && candidate.risk !== 'medium' && candidate.risk !== 'high') {
      return { error: `changes[${index}].risk is invalid`, ok: false }
    }

    const identity = `${taskId}:${candidate.operation}`

    if (identities.has(identity)) {
      return { error: `Duplicate change: ${identity}`, ok: false }
    }

    identities.add(identity)
    const before = parseVisibleRecord(candidate.before, `changes[${index}].before`)
    const after = parseVisibleRecord(candidate.after, `changes[${index}].after`)

    if (isFailure(before)) {
      return before
    }

    if (isFailure(after)) {
      return after
    }

    let untouched: string[] | undefined

    if (candidate.untouched !== undefined) {
      if (
        !Array.isArray(candidate.untouched) ||
        candidate.untouched.length > MAX_VISIBLE_FIELDS ||
        candidate.untouched.some(item => typeof item !== 'string' || !item.trim() || item.length > 80)
      ) {
        return { error: `changes[${index}].untouched must be a bounded string list`, ok: false }
      }

      untouched = candidate.untouched.map(item => (item as string).trim())
    }

    changes.push({
      after,
      before,
      operation: candidate.operation,
      risk: candidate.risk as HermesUiMutationRisk | undefined,
      taskId,
      title,
      untouched
    })
  }

  return { artifact: { ...base, canonicalApproval, changes, type: 'mutation-preview' }, ok: true }
}

export function parseHermesUiArtifact(source: string): HermesUiArtifactParseResult {
  let value: unknown

  try {
    value = JSON.parse(source)
  } catch {
    return { error: 'Invalid JSON', ok: false }
  }

  if (!isRecord(value)) {
    return { error: 'Artifact must be an object', ok: false }
  }

  if (value.type === 'task-breakdown') {
    return parseTaskBreakdown(value)
  }

  if (value.type === 'mutation-preview') {
    return parseMutationPreview(value)
  }

  return { error: 'Unsupported artifact type', ok: false }
}

function normalizedIdentity(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120)
}

function stableHash(value: string): string {
  let hash = 0x811c9dc5

  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }

  return (hash >>> 0).toString(36)
}

export function stableArtifactStorageKey(artifact: HermesUiArtifact): string {
  if (artifact.type === 'task-breakdown') {
    const taskIdentity = `${normalizedIdentity(artifact.task.id) || 'task'}-${stableHash(artifact.task.id)}`
    const proposalIdentity = `${normalizedIdentity(artifact.proposalId) || 'proposal'}-${stableHash(artifact.proposalId)}`

    return `hermes-ui:task-breakdown:${taskIdentity}:${proposalIdentity}:r${artifact.proposalRevision}:b${artifact.task.baseRevision}`
  }

  const taskIdentity = `${normalizedIdentity(artifact.canonicalApproval.taskId) || 'task'}-${stableHash(artifact.canonicalApproval.taskId)}`
  const proposalIdentity = `${normalizedIdentity(artifact.canonicalApproval.proposalId) || 'proposal'}-${stableHash(artifact.canonicalApproval.proposalId)}`

  return `hermes-ui:mutation-preview:${taskIdentity}:${proposalIdentity}:r${artifact.canonicalApproval.proposalRevision}:b${artifact.canonicalApproval.baseRevision}`
}
