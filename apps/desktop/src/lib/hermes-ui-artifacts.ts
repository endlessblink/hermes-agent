export interface HermesUiChecklistAction {
  id: string
  label: string
  copyText?: string
  submitText?: string
}

export interface HermesUiChecklistItem {
  id: string
  label: string
  description?: string
  actions?: HermesUiChecklistAction[]
}

export interface HermesUiChecklistArtifact {
  type: 'checklist'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  items: HermesUiChecklistItem[]
}

export interface HermesUiQuestionnaireArtifact {
  type: 'questionnaire'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  items: HermesUiChecklistItem[]
}

export type HermesUiTaskPriority = 'high' | 'medium' | 'low' | null

export interface HermesUiTaskTriageTask {
  id: string
  title: string
  status?: string
  priority?: HermesUiTaskPriority
  dueDate?: string | null
  projectId?: string | null
}

export interface HermesUiTaskTriageArtifact {
  type: 'task-triage'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  task: HermesUiTaskTriageTask
}

export type HermesUiTriageDecision = 'today' | 'not_today' | 'later' | 'discuss'

export interface HermesUiFlowStateBatchTask extends HermesUiTaskTriageTask {
  recommendation?: HermesUiTriageDecision
  recommendedPriority?: HermesUiTaskPriority
  recommendedDueDate?: string | null
  rationale?: string
}

export interface HermesUiFlowStateBatchArtifact {
  type: 'flowstate-task-batch'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  tasks: HermesUiFlowStateBatchTask[]
}

export interface HermesUiFlowStateNextBlockPreviewSummary {
  duration: number
  scheduledDate: string
  scheduledTime: string
}

export interface HermesUiFlowStateNextBlockArtifact {
  type: 'flowstate-next-block'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  task: {
    id: string
    title: string
    priority?: HermesUiTaskPriority
    dueDate?: string | null
  }
  durationMinutes: number
  proposedStartTime?: string
  doneEnough: string
  rationale: string
  previewSummary: HermesUiFlowStateNextBlockPreviewSummary
  actions: HermesUiChecklistAction[]
}

export type HermesUiArtifact =
  | HermesUiChecklistArtifact
  | HermesUiQuestionnaireArtifact
  | HermesUiFlowStateBatchArtifact
  | HermesUiFlowStateNextBlockArtifact
  | HermesUiTaskTriageArtifact

export interface HermesUiArtifactParseSuccess {
  artifact: HermesUiArtifact
  ok: true
}

export interface HermesUiArtifactParseFailure {
  error: string
  ok: false
}

export type HermesUiArtifactParseResult = HermesUiArtifactParseFailure | HermesUiArtifactParseSuccess

const MAX_TITLE_LENGTH = 160
const MAX_DESCRIPTION_LENGTH = 500
const MAX_ITEMS = 100
const MAX_ITEM_ID_LENGTH = 120
const MAX_LABEL_LENGTH = 800
const MAX_ITEM_DESCRIPTION_LENGTH = 1000
const MAX_ITEM_ACTIONS = 6
const MAX_ACTION_LABEL_LENGTH = 80
const MAX_ACTION_COPY_TEXT_LENGTH = 1200
const MAX_ACTION_SUBMIT_TEXT_LENGTH = 1600
const MAX_STATUS_LENGTH = 80
const MAX_PROJECT_ID_LENGTH = 120
const MAX_BATCH_TASKS = 5
const MAX_RATIONALE_LENGTH = 280
const MAX_NEXT_BLOCK_ACTIONS = 3

const SAFE_NEXT_BLOCK_KEYS = new Set([
  'actions',
  'direction',
  'description',
  'doneEnough',
  'durationMinutes',
  'id',
  'previewSummary',
  'proposedStartTime',
  'rationale',
  'task',
  'title',
  'type'
])

const SAFE_NEXT_BLOCK_TASK_KEYS = new Set(['dueDate', 'id', 'priority', 'title'])
const SAFE_NEXT_BLOCK_PREVIEW_KEYS = new Set(['duration', 'scheduledDate', 'scheduledTime'])
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/
const TIME_ONLY_RE = /^([01]\d|2[0-3]):[0-5]\d$/

function normalizeDirection(value: unknown): HermesUiChecklistArtifact['direction'] | HermesUiArtifactParseFailure {
  if (value === undefined) {
    return undefined
  }

  if (value === 'auto' || value === 'ltr' || value === 'rtl') {
    return value
  }

  return { error: 'direction must be auto, ltr, or rtl', ok: false }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeText(value: unknown, maxLength: number, field: string): string | HermesUiArtifactParseFailure {
  if (typeof value !== 'string') {
    return { error: `${field} must be a string`, ok: false }
  }

  const text = value.replace(/\0/g, '').trim()

  if (text.length > maxLength) {
    return { error: `${field} is too long`, ok: false }
  }

  return text
}

function optionalText(
  value: unknown,
  maxLength: number,
  field: string
): HermesUiArtifactParseFailure | string | undefined {
  if (value === undefined || value === null) {
    return value === null ? undefined : value
  }

  const text = normalizeText(value, maxLength, field)

  return typeof text === 'string' && text.length === 0 ? undefined : text
}

function optionalNullableText(
  value: unknown,
  maxLength: number,
  field: string
): HermesUiArtifactParseFailure | string | null | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === null) {
    return null
  }

  const text = normalizeText(value, maxLength, field)

  return typeof text === 'string' && text.length === 0 ? null : text
}

function normalizeIdentity(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, MAX_ITEM_ID_LENGTH)
}

function isParseFailure(value: HermesUiArtifactParseFailure | unknown): value is HermesUiArtifactParseFailure {
  return isRecord(value) && value.ok === false && typeof value.error === 'string'
}

function parseChecklistAction(rawAction: unknown, field: string): HermesUiArtifactParseFailure | HermesUiChecklistAction {
  if (!isRecord(rawAction)) {
    return { error: `${field} must be an object`, ok: false }
  }

  const actionId = normalizeText(rawAction.id, MAX_ITEM_ID_LENGTH, `${field}.id`)

  if (typeof actionId !== 'string') {
    return actionId
  }

  if (!actionId) {
    return { error: `${field}.id is required`, ok: false }
  }

  const actionLabel = normalizeText(rawAction.label, MAX_ACTION_LABEL_LENGTH, `${field}.label`)

  if (typeof actionLabel !== 'string') {
    return actionLabel
  }

  if (!actionLabel) {
    return { error: `${field}.label is required`, ok: false }
  }

  const copyText = optionalText(rawAction.copyText, MAX_ACTION_COPY_TEXT_LENGTH, `${field}.copyText`)

  if (copyText && typeof copyText !== 'string') {
    return copyText
  }

  const submitText = optionalText(rawAction.submitText, MAX_ACTION_SUBMIT_TEXT_LENGTH, `${field}.submitText`)

  if (submitText && typeof submitText !== 'string') {
    return submitText
  }

  if (!copyText && !submitText) {
    return { error: `${field}.copyText or ${field}.submitText is required`, ok: false }
  }

  return { copyText, id: actionId, label: actionLabel, submitText }
}

function parseChecklistLikeArtifact(parsed: Record<string, unknown>, type: 'checklist' | 'questionnaire'): HermesUiArtifactParseResult {
  const rawItems = parsed.items ?? parsed.questions

  if (!Array.isArray(rawItems) || rawItems.length === 0) {
    return { error: `${type} items are required`, ok: false }
  }

  if (rawItems.length > MAX_ITEMS) {
    return { error: `${type} has too many items`, ok: false }
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  const seenIds = new Set<string>()
  const items: HermesUiChecklistItem[] = []

  for (const [index, rawItem] of rawItems.entries()) {
    if (!isRecord(rawItem)) {
      return { error: `items[${index}] must be an object`, ok: false }
    }

    const rawId = rawItem.id ?? rawItem.name
    const itemId = normalizeText(rawId, MAX_ITEM_ID_LENGTH, `items[${index}].id`)

    if (typeof itemId !== 'string') {
      return itemId
    }

    if (!itemId) {
      return { error: `items[${index}].id is required`, ok: false }
    }

    if (seenIds.has(itemId)) {
      return { error: `Duplicate item id: ${itemId}`, ok: false }
    }

    seenIds.add(itemId)

    const rawLabel = rawItem.label ?? rawItem.question ?? rawItem.prompt
    const label = normalizeText(rawLabel, MAX_LABEL_LENGTH, `items[${index}].label`)

    if (typeof label !== 'string') {
      return label
    }

    if (!label) {
      return { error: `items[${index}].label is required`, ok: false }
    }

    const itemDescription = optionalText(
      rawItem.description ?? rawItem.helpText,
      MAX_ITEM_DESCRIPTION_LENGTH,
      `items[${index}].description`
    )

    if (itemDescription && typeof itemDescription !== 'string') {
      return itemDescription
    }

    let actions: HermesUiChecklistAction[] | undefined

    if (rawItem.actions !== undefined) {
      if (!Array.isArray(rawItem.actions)) {
        return { error: `items[${index}].actions must be an array`, ok: false }
      }

      if (rawItem.actions.length > MAX_ITEM_ACTIONS) {
        return { error: `items[${index}].actions has too many actions`, ok: false }
      }

      actions = []
      const seenActionIds = new Set<string>()

      for (const [actionIndex, rawAction] of rawItem.actions.entries()) {
        const action = parseChecklistAction(rawAction, `items[${index}].actions[${actionIndex}]`)

        if (isParseFailure(action)) {
          return action
        }

        if (seenActionIds.has(action.id)) {
          return { error: `Duplicate action id: ${action.id}`, ok: false }
        }

        seenActionIds.add(action.id)
        actions.push(action)
      }
    }

    items.push({ actions: actions?.length ? actions : undefined, description: itemDescription, id: itemId, label })
  }

  return {
    artifact: { ...base.fields, items, type },
    ok: true
  }
}

function parseChecklistArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  return parseChecklistLikeArtifact(parsed, 'checklist')
}

function parseQuestionnaireArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  return parseChecklistLikeArtifact(parsed, 'questionnaire')
}

function parseBaseFields(parsed: Record<string, unknown>) {
  const id = optionalText(parsed.id, MAX_ITEM_ID_LENGTH, 'id')

  if (id && typeof id !== 'string') {
    return id
  }

  const title = optionalText(parsed.title, MAX_TITLE_LENGTH, 'title')

  if (title && typeof title !== 'string') {
    return title
  }

  const description = optionalText(parsed.description, MAX_DESCRIPTION_LENGTH, 'description')

  if (description && typeof description !== 'string') {
    return description
  }

  const direction = normalizeDirection(parsed.direction)

  if (direction && typeof direction === 'object') {
    return direction
  }

  return { fields: { description, direction, id, title }, ok: true as const }
}

function parseTaskPriority(value: unknown): HermesUiArtifactParseFailure | HermesUiTaskPriority | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === null || value === 'high' || value === 'medium' || value === 'low') {
    return value
  }

  return { error: 'task.priority must be high, medium, low, or null', ok: false }
}


function parseTriageDecision(value: unknown): HermesUiArtifactParseFailure | HermesUiTriageDecision | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === 'today' || value === 'not_today' || value === 'later' || value === 'discuss') {
    return value
  }

  return { error: 'recommendation must be today, not_today, later, or discuss', ok: false }
}

function parseTaskLike(rawTask: Record<string, unknown>, field: string): HermesUiArtifactParseFailure | HermesUiTaskTriageTask {
  const taskId = normalizeText(rawTask.id, MAX_ITEM_ID_LENGTH, `${field}.id`)

  if (typeof taskId !== 'string') {
    return taskId
  }

  if (!taskId) {
    return { error: `${field}.id is required`, ok: false }
  }

  const taskTitle = normalizeText(rawTask.title, MAX_LABEL_LENGTH, `${field}.title`)

  if (typeof taskTitle !== 'string') {
    return taskTitle
  }

  if (!taskTitle) {
    return { error: `${field}.title is required`, ok: false }
  }

  const status = optionalText(rawTask.status, MAX_STATUS_LENGTH, `${field}.status`)

  if (status && typeof status !== 'string') {
    return status
  }

  const dueDate = optionalNullableText(rawTask.dueDate, MAX_ITEM_ID_LENGTH, `${field}.dueDate`)

  if (dueDate && typeof dueDate !== 'string') {
    return dueDate
  }

  const projectId = optionalNullableText(rawTask.projectId, MAX_PROJECT_ID_LENGTH, `${field}.projectId`)

  if (projectId && typeof projectId !== 'string') {
    return projectId
  }

  const priority = parseTaskPriority(rawTask.priority)

  if (priority && typeof priority === 'object') {
    return priority
  }

  return { dueDate, id: taskId, priority: priority ?? null, projectId, status, title: taskTitle }
}

function parseFlowStateBatchArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.tasks) || parsed.tasks.length === 0) {
    return { error: 'flowstate-task-batch tasks are required', ok: false }
  }

  if (parsed.tasks.length > MAX_BATCH_TASKS) {
    return { error: 'flowstate-task-batch has too many tasks', ok: false }
  }

  const seenIds = new Set<string>()
  const tasks: HermesUiFlowStateBatchTask[] = []

  for (const [index, rawTask] of parsed.tasks.entries()) {
    if (!isRecord(rawTask)) {
      return { error: `tasks[${index}] must be an object`, ok: false }
    }

    const task = parseTaskLike(rawTask, `tasks[${index}]`)

    if (isParseFailure(task)) {
      return task
    }

    if (seenIds.has(task.id)) {
      return { error: `Duplicate task id: ${task.id}`, ok: false }
    }

    seenIds.add(task.id)

    const recommendation = parseTriageDecision(rawTask.recommendation)

    if (recommendation && typeof recommendation === 'object') {
      return recommendation
    }

    const recommendedPriority = parseTaskPriority(rawTask.recommendedPriority)

    if (recommendedPriority && typeof recommendedPriority === 'object') {
      return recommendedPriority
    }

    const recommendedDueDate = optionalNullableText(rawTask.recommendedDueDate, MAX_ITEM_ID_LENGTH, `tasks[${index}].recommendedDueDate`)

    if (recommendedDueDate && typeof recommendedDueDate !== 'string') {
      return recommendedDueDate
    }

    const rationale = optionalText(rawTask.rationale, MAX_RATIONALE_LENGTH, `tasks[${index}].rationale`)

    if (rationale && typeof rationale !== 'string') {
      return rationale
    }

    tasks.push({
      ...task,
      rationale,
      recommendation,
      recommendedDueDate,
      recommendedPriority: recommendedPriority ?? undefined
    })
  }

  return { artifact: { ...base.fields, tasks, type: 'flowstate-task-batch' }, ok: true }
}

function parseTaskTriageArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!isRecord(parsed.task)) {
    return { error: 'task-triage task is required', ok: false }
  }

  const task = parseTaskLike(parsed.task, 'task')

  if (isParseFailure(task)) {
    return task
  }

  return {
    artifact: {
      ...base.fields,
      task,
      type: 'task-triage'
    },
    ok: true
  }
}

function hasUnsupportedKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>, field: string): HermesUiArtifactParseFailure | undefined {
  const unsupported = Object.keys(value).filter(key => !allowed.has(key))

  if (unsupported.length > 0) {
    return { error: `Unsupported ${field} field: ${unsupported[0]}`, ok: false }
  }

  return undefined
}

function parsePositiveMinutes(value: unknown, field: string): HermesUiArtifactParseFailure | number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > 1440) {
    return { error: `${field} must be an integer from 1 to 1440`, ok: false }
  }

  return value
}

function parseScheduledDate(value: unknown, field: string): HermesUiArtifactParseFailure | string {
  const text = normalizeText(value, MAX_ITEM_ID_LENGTH, field)

  if (typeof text !== 'string') {
    return text
  }

  if (!DATE_ONLY_RE.test(text)) {
    return { error: `${field} must be YYYY-MM-DD`, ok: false }
  }

  return text
}

function parseScheduledTime(value: unknown, field: string): HermesUiArtifactParseFailure | string {
  const text = normalizeText(value, MAX_ITEM_ID_LENGTH, field)

  if (typeof text !== 'string') {
    return text
  }

  if (!TIME_ONLY_RE.test(text)) {
    return { error: `${field} must be HH:mm`, ok: false }
  }

  return text
}

function parseFlowStateNextBlockArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_NEXT_BLOCK_KEYS, 'flowstate-next-block')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!isRecord(parsed.task)) {
    return { error: 'flowstate-next-block task is required', ok: false }
  }

  const unsupportedTask = hasUnsupportedKeys(parsed.task, SAFE_NEXT_BLOCK_TASK_KEYS, 'task')

  if (unsupportedTask) {
    return unsupportedTask
  }

  const task = parseTaskLike(parsed.task, 'task')

  if (isParseFailure(task)) {
    return task
  }

  if (!isRecord(parsed.previewSummary)) {
    return { error: 'flowstate-next-block previewSummary is required', ok: false }
  }

  const unsupportedPreview = hasUnsupportedKeys(parsed.previewSummary, SAFE_NEXT_BLOCK_PREVIEW_KEYS, 'previewSummary')

  if (unsupportedPreview) {
    return unsupportedPreview
  }

  const scheduledDate = parseScheduledDate(parsed.previewSummary.scheduledDate, 'previewSummary.scheduledDate')

  if (typeof scheduledDate !== 'string') {
    return scheduledDate
  }

  const scheduledTime = parseScheduledTime(parsed.previewSummary.scheduledTime, 'previewSummary.scheduledTime')

  if (typeof scheduledTime !== 'string') {
    return scheduledTime
  }

  const duration = parsePositiveMinutes(parsed.previewSummary.duration, 'previewSummary.duration')

  if (typeof duration !== 'number') {
    return duration
  }

  const durationMinutes = parsePositiveMinutes(parsed.durationMinutes, 'durationMinutes')

  if (typeof durationMinutes !== 'number') {
    return durationMinutes
  }

  const proposedStartTime = optionalText(parsed.proposedStartTime, MAX_ITEM_ID_LENGTH, 'proposedStartTime')

  if (proposedStartTime && typeof proposedStartTime !== 'string') {
    return proposedStartTime
  }

  if (proposedStartTime && !TIME_ONLY_RE.test(proposedStartTime)) {
    return { error: 'proposedStartTime must be HH:mm', ok: false }
  }

  const doneEnough = normalizeText(parsed.doneEnough, MAX_RATIONALE_LENGTH, 'doneEnough')

  if (typeof doneEnough !== 'string') {
    return doneEnough
  }

  if (!doneEnough) {
    return { error: 'doneEnough is required', ok: false }
  }

  const rationale = normalizeText(parsed.rationale, MAX_RATIONALE_LENGTH, 'rationale')

  if (typeof rationale !== 'string') {
    return rationale
  }

  if (!rationale) {
    return { error: 'rationale is required', ok: false }
  }

  if (!Array.isArray(parsed.actions) || parsed.actions.length === 0) {
    return { error: 'flowstate-next-block actions are required', ok: false }
  }

  if (parsed.actions.length > MAX_NEXT_BLOCK_ACTIONS) {
    return { error: 'flowstate-next-block has too many actions', ok: false }
  }

  const actions: HermesUiChecklistAction[] = []
  const seenActionIds = new Set<string>()

  for (const [index, rawAction] of parsed.actions.entries()) {
    const action = parseChecklistAction(rawAction, `actions[${index}]`)

    if (isParseFailure(action)) {
      return action
    }

    if (!action.submitText) {
      return { error: `actions[${index}].submitText is required`, ok: false }
    }

    if (seenActionIds.has(action.id)) {
      return { error: `Duplicate action id: ${action.id}`, ok: false }
    }

    seenActionIds.add(action.id)
    actions.push(action)
  }

  return {
    artifact: {
      ...base.fields,
      actions,
      doneEnough,
      durationMinutes,
      previewSummary: { duration, scheduledDate, scheduledTime },
      proposedStartTime,
      rationale,
      task: {
        dueDate: task.dueDate,
        id: task.id,
        priority: task.priority,
        title: task.title
      },
      type: 'flowstate-next-block'
    },
    ok: true
  }
}

export function parseHermesUiArtifact(source: string): HermesUiArtifactParseResult {
  let parsed: unknown

  try {
    parsed = JSON.parse(source)
  } catch {
    return { error: 'Invalid JSON', ok: false }
  }

  if (!isRecord(parsed)) {
    return { error: 'Artifact must be an object', ok: false }
  }

  if (parsed.type === 'checklist') {
    return parseChecklistArtifact(parsed)
  }

  if (parsed.type === 'questionnaire') {
    return parseQuestionnaireArtifact(parsed)
  }

  if (parsed.type === 'task-triage') {
    return parseTaskTriageArtifact(parsed)
  }

  if (parsed.type === 'flowstate-task-batch') {
    return parseFlowStateBatchArtifact(parsed)
  }

  if (parsed.type === 'flowstate-next-block') {
    return parseFlowStateNextBlockArtifact(parsed)
  }

  return { error: 'Unsupported artifact type', ok: false }
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(',')}]`
  }

  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .filter(key => value[key] !== undefined)
      .map(key => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(',')}}`
  }

  return JSON.stringify(value)
}

function stableHash(value: string): string {
  let hash = 0x811c9dc5

  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }

  return (hash >>> 0).toString(36)
}

export function stableArtifactStorageKey(artifact: HermesUiChecklistArtifact | HermesUiQuestionnaireArtifact): string {
  const identity = artifact.id ? normalizeIdentity(artifact.id) : ''
  const suffix = identity || stableHash(stableStringify(artifact))

  return `hermes-ui:${artifact.type}:${suffix}`
}
