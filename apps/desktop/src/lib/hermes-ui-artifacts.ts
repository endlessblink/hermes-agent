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

export type HermesUiFormFieldType =
  | 'short-text'
  | 'long-text'
  | 'single-choice'
  | 'multi-choice'
  | 'boolean'
  | 'number'
  | 'date'
  | 'time'

const CANONICAL_24_HOUR_TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/

export function isCanonical24HourTime(value: string): boolean {
  return CANONICAL_24_HOUR_TIME.test(value)
}

export interface HermesUiFormOption {
  label: string
  value: string
}

export interface HermesUiFormField {
  id: string
  label: string
  type: HermesUiFormFieldType
  defaultValue?: HermesUiFormValue
  description?: string
  placeholder?: string
  required?: boolean
  options?: HermesUiFormOption[]
}

export interface HermesUiFormArtifact {
  type: 'form'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  fields: HermesUiFormField[]
  submitLabel?: string
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


export type HermesUiFlowStatePlanningMode = 'day-start' | 'overload-relief' | 'end-of-day' | 'quick-triage'
export type HermesUiFlowStatePlanningTone = 'risk' | 'health' | 'pet' | 'work' | 'money' | 'life' | 'creative' | 'maintenance'

export interface HermesUiFlowStatePlanningCategoryExample {
  id: string
  title: string
  dueDate?: string | null
  priority?: HermesUiTaskPriority
}

export interface HermesUiFlowStatePlanningCategory {
  id: string
  label: string
  tone: HermesUiFlowStatePlanningTone
  count: number
  recommendation: string
  examples: HermesUiFlowStatePlanningCategoryExample[]
}

export interface HermesUiFlowStatePlanningNextBlock {
  id: string
  title: string
  durationMinutes: number
  taskIds: string[]
  doneEnough: string
  rationale: string
}

export interface HermesUiFlowStatePlanningSessionArtifact {
  type: 'flowstate-planning-session'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  mode: HermesUiFlowStatePlanningMode
  categories: HermesUiFlowStatePlanningCategory[]
  nextBlock?: HermesUiFlowStatePlanningNextBlock
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

export type HermesUiPlanningFunnelStepStatus = 'pending' | 'current' | 'done' | 'blocked'

export interface HermesUiPlanningFunnelStep {
  id: string
  label: string
  description?: string
  status?: HermesUiPlanningFunnelStepStatus
}

export interface HermesUiPlanningFunnelArtifact {
  type: 'planning-funnel'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  steps: HermesUiPlanningFunnelStep[]
}

export type HermesUiTaskBreakdownScope = 'next-move' | 'working-session' | 'full-delivery'

export interface HermesUiTaskBreakdownStep {
  id: string
  title: string
  doneEnough: string
  estimateMinutes?: number
  optional?: boolean
}

export interface HermesUiTaskBreakdownArtifact {
  type: 'task-breakdown'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  task: {
    id: string
    title: string
  }
  scope: HermesUiTaskBreakdownScope
  targetOutcome?: string
  stoppingRule?: string
  steps: HermesUiTaskBreakdownStep[]
  submitLabel?: string
}

export const HERMES_UI_TASK_BREAKDOWN_LIMITS = {
  doneEnoughLength: 1000,
  stepCount: 12,
  titleLength: 800
} as const

export interface HermesUiTaskContextArtifact {
  type: 'task-context'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  task: HermesUiTaskTriageTask
  meaning?: string
  connections?: string[]
  waitingOn?: string[]
  progress?: string
  unknowns?: string[]
  actions?: HermesUiChecklistAction[]
}

export type HermesUiTaskTableColumn =
  | 'task'
  | 'context'
  | 'timeSize'
  | 'energy'
  | 'urgency'
  | 'externality'
  | 'nextStep'
  | 'confidence'

export type HermesUiTaskSize = 'tiny' | 'small' | 'medium' | 'large' | 'unknown'
export type HermesUiPlanningLevel = 'low' | 'medium' | 'high' | 'unknown'
export type HermesUiTaskExternality = 'internal' | 'external' | 'waiting' | 'unknown'
export type HermesUiConfidence = 'low' | 'medium' | 'high'

export interface HermesUiPlanningTaskRow {
  id: string
  title: string
  dueDate?: string | null
  priority?: HermesUiTaskPriority
  context?: string
  timeSize?: HermesUiTaskSize
  energy?: HermesUiPlanningLevel
  urgency?: HermesUiPlanningLevel
  externality?: HermesUiTaskExternality
  nextStep?: string
  confidence?: HermesUiConfidence
  actions?: HermesUiChecklistAction[]
}

export interface HermesUiTaskTableArtifact {
  type: 'task-table'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  columns: HermesUiTaskTableColumn[]
  rows: HermesUiPlanningTaskRow[]
}

export interface HermesUiMiniKanbanTask {
  id: string
  title: string
  dueDate?: string | null
  priority?: HermesUiTaskPriority
  note?: string
  confidence?: HermesUiConfidence
  actions?: HermesUiChecklistAction[]
}

export interface HermesUiMiniKanbanLane {
  id: string
  title: string
  description?: string
  tasks: HermesUiMiniKanbanTask[]
}

export interface HermesUiMiniKanbanArtifact {
  type: 'mini-kanban'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  lanes: HermesUiMiniKanbanLane[]
}

export type HermesUiTimelineBlockKind = 'fixed' | 'focus' | 'short-task' | 'buffer' | 'break' | 'floating'
export type HermesUiTimelineStatus = 'planned' | 'doing' | 'done' | 'dropped' | 'candidate'

export interface HermesUiDayTimelineBlock {
  id: string
  label: string
  startTime?: string
  endTime?: string
  durationMinutes?: number
  kind?: HermesUiTimelineBlockKind
  taskId?: string
  status?: HermesUiTimelineStatus
  doneEnough?: string
  confidence?: HermesUiConfidence
  actions?: HermesUiChecklistAction[]
}

export interface HermesUiDayTimelineArtifact {
  type: 'day-timeline'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  date: string
  currentTime?: string
  blocks: HermesUiDayTimelineBlock[]
}

export type HermesUiMutationOperation = 'update' | 'schedule-instance' | 'complete' | 'create' | 'delete'
export type HermesUiMutationRisk = 'low' | 'medium' | 'high'
export type HermesUiVisibleRecord = Record<string, string | number | boolean | null>

export interface HermesUiMutationPreviewChange {
  taskId: string
  title: string
  operation: HermesUiMutationOperation
  before?: HermesUiVisibleRecord
  after?: HermesUiVisibleRecord
  untouched?: string[]
  risk?: HermesUiMutationRisk
}

export interface HermesUiMutationPreviewArtifact {
  type: 'mutation-preview'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  changes: HermesUiMutationPreviewChange[]
  actions: HermesUiChecklistAction[]
}

export type HermesUiMatrixAxis = 'energy' | 'effort' | 'urgency' | 'impact'
export type HermesUiMatrixPoint = 'low' | 'medium' | 'high'

export interface HermesUiTaskChip {
  id: string
  title: string
  dueDate?: string | null
  priority?: HermesUiTaskPriority
  confidence?: HermesUiConfidence
  actions?: HermesUiChecklistAction[]
}

export interface HermesUiUrgencyEnergyCell {
  x: HermesUiMatrixPoint
  y: HermesUiMatrixPoint
  label?: string
  tasks: HermesUiTaskChip[]
}

export interface HermesUiUrgencyEnergyMatrixArtifact {
  type: 'urgency-energy-matrix'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  xAxis: 'energy' | 'effort'
  yAxis: 'urgency' | 'impact'
  cells: HermesUiUrgencyEnergyCell[]
}

export type HermesUiWorkloadBarTone = 'neutral' | 'warning' | 'danger' | 'success'

export interface HermesUiWorkloadBar {
  id: string
  label: string
  value: number
  max?: number
  tone?: HermesUiWorkloadBarTone
  note?: string
}

export interface HermesUiWorkloadBarsArtifact {
  type: 'workload-bars'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  bars: HermesUiWorkloadBar[]
}

export type HermesUiTaskGraphNodeKind = 'task' | 'project' | 'person' | 'money' | 'health' | 'creative' | 'home' | 'unknown'

export interface HermesUiTaskGraphNode {
  id: string
  label: string
  kind?: HermesUiTaskGraphNodeKind
}

export interface HermesUiTaskGraphEdge {
  source: string
  target: string
  label?: string
}

export interface HermesUiTaskGraphArtifact {
  type: 'task-graph'
  direction?: 'auto' | 'ltr' | 'rtl'
  id?: string
  title?: string
  description?: string
  nodes: HermesUiTaskGraphNode[]
  edges: HermesUiTaskGraphEdge[]
}

export type HermesUiDailyPlanningSectionKind = 'calendar' | 'due-today' | 'overdue' | 'suggestions' | 'more-suggestions'
export type HermesUiDailyPlanningSourceKind = 'calendar' | 'flowstate' | 'notion' | 'obsidian' | 'hermes'
export type HermesUiDailyPlanningSourceStatus = 'open' | 'in-progress' | 'done' | 'cancelled' | 'unknown'
export type HermesUiDailyPlanningPlanPlacement = 'unassigned' | 'core' | 'optional' | 'not-today'
export type HermesUiDailyPlanningContextConfidence = 'verified' | 'partial' | 'unknown' | 'conflict'
export type HermesUiDailyPlanningEnergy = 'low' | 'medium' | 'high' | 'unknown'
export type HermesUiDailyPlanningQuickActionId =
  | 'mark-done'
  | 'start'
  | 'defer'
  | 'open-source'
  | 'add-to-plan'
  | 'remove-from-plan'
export type HermesUiDailyPlanningClaimScopeKind =
  | 'exact-day'
  | 'exact-task'
  | 'series'
  | 'project'
  | 'life-domain'
  | 'workflow'
  | 'global'

export interface HermesUiDailyPlanningSourceIdentity {
  kind: HermesUiDailyPlanningSourceKind
  recordId: string
  revision?: string
}

export interface HermesUiDailyPlanningTemporal {
  allDay?: boolean
  endTime?: string
  startTime?: string
}

export interface HermesUiDailyPlanningContextEntry {
  id: string
  text: string
}

export interface HermesUiDailyPlanningProvenanceEntry {
  capturedAt: string
  id: string
  sourceKind: HermesUiDailyPlanningSourceKind
  sourceRecordId?: string
  text?: string
}

export interface HermesUiDailyPlanningClaimScope {
  kind: HermesUiDailyPlanningClaimScopeKind
  referenceId?: string
}

export interface HermesUiDailyPlanningLearnedClaim {
  id: string
  provenance: HermesUiDailyPlanningProvenanceEntry[]
  scope: HermesUiDailyPlanningClaimScope
  state: 'active' | 'superseded'
  supersedesClaimId?: string
  text: string
}

export interface HermesUiDailyPlanningLearningConflict {
  id: string
  newClaimId: string
  priorClaimId: string
}

export interface HermesUiDailyPlanningGeneralizationProposal {
  claimId: string
  id: string
  rationale: string
  scope: HermesUiDailyPlanningClaimScope
}

export interface HermesUiDailyPlanningQuickAction {
  id: HermesUiDailyPlanningQuickActionId
  label: string
}

export interface HermesUiDailyPlanningRow {
  context?: HermesUiDailyPlanningContextEntry[]
  contextConfidence: HermesUiDailyPlanningContextConfidence
  doneEnough?: string
  dueDate?: string | null
  durationMinutes?: number
  energy?: HermesUiDailyPlanningEnergy
  expectedImpact?: string
  generalizationProposals?: HermesUiDailyPlanningGeneralizationProposal[]
  id: string
  learnedClaims?: HermesUiDailyPlanningLearnedClaim[]
  learningConflicts?: HermesUiDailyPlanningLearningConflict[]
  nextStep?: string
  planPlacement: HermesUiDailyPlanningPlanPlacement
  priority?: HermesUiTaskPriority
  provenance?: HermesUiDailyPlanningProvenanceEntry[]
  quickActions?: HermesUiDailyPlanningQuickAction[]
  source: HermesUiDailyPlanningSourceIdentity
  sourceMutationAllowed: boolean
  sourceStatus: HermesUiDailyPlanningSourceStatus
  suggestionConfidence?: HermesUiConfidence
  suggestionRationale?: string
  temporal?: HermesUiDailyPlanningTemporal
  title: string
}

export interface HermesUiDailyPlanningSection {
  id: string
  kind: HermesUiDailyPlanningSectionKind
  rows: HermesUiDailyPlanningRow[]
  title: string
}

export interface HermesUiDailyPlanningListArtifact {
  baselineId: string
  date: string
  description?: string
  direction?: 'auto' | 'ltr' | 'rtl'
  id: string
  schemaVersion: 1
  sections: [HermesUiDailyPlanningSection, HermesUiDailyPlanningSection, HermesUiDailyPlanningSection, HermesUiDailyPlanningSection, HermesUiDailyPlanningSection]
  timezone: string
  title: string
  type: 'daily-planning-list'
}

export type HermesUiArtifact =
  | HermesUiChecklistArtifact
  | HermesUiDailyPlanningListArtifact
  | HermesUiDayTimelineArtifact
  | HermesUiQuestionnaireArtifact
  | HermesUiFlowStateBatchArtifact
  | HermesUiFlowStatePlanningSessionArtifact
  | HermesUiFlowStateNextBlockArtifact
  | HermesUiMiniKanbanArtifact
  | HermesUiMutationPreviewArtifact
  | HermesUiPlanningFunnelArtifact
  | HermesUiTaskBreakdownArtifact
  | HermesUiTaskContextArtifact
  | HermesUiTaskGraphArtifact
  | HermesUiTaskTableArtifact
  | HermesUiTaskTriageArtifact
  | HermesUiUrgencyEnergyMatrixArtifact
  | HermesUiWorkloadBarsArtifact
  | HermesUiFormArtifact

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
const MAX_PLANNING_CATEGORIES = 5
const MAX_PLANNING_CATEGORY_EXAMPLES = 2
const MAX_PLANNING_TASK_IDS = 5
const MAX_RATIONALE_LENGTH = 280
const MAX_NEXT_BLOCK_ACTIONS = 3
const MAX_FUNNEL_STEPS = 6
const MAX_BREAKDOWN_STEPS = HERMES_UI_TASK_BREAKDOWN_LIMITS.stepCount
const MAX_CONTEXT_ITEMS = 6
const MAX_TASK_TABLE_COLUMNS = 8
const MAX_TASK_TABLE_ROWS = 7
const MIN_TASK_TABLE_ROWS = 3
const MAX_MINI_KANBAN_LANES = 5
const MAX_MINI_KANBAN_TASKS = 8
const MAX_TIMELINE_BLOCKS = 12
const MAX_MUTATION_CHANGES = 10
const MAX_MUTATION_RECORD_FIELDS = 12
const MAX_MATRIX_CELLS = 9
const MAX_MATRIX_TASKS = 5
const MAX_WORKLOAD_BARS = 8
const MAX_GRAPH_NODES = 12
const MAX_GRAPH_EDGES = 16
const MAX_FORM_FIELDS = 12
const MAX_FORM_OPTIONS = 12
const SAFE_FORM_KEYS = new Set(['description', 'direction', 'fields', 'id', 'submitLabel', 'title', 'type'])
const SAFE_FORM_FIELD_KEYS = new Set(['default', 'description', 'id', 'label', 'options', 'placeholder', 'required', 'type'])
const SAFE_FORM_OPTION_KEYS = new Set(['label', 'value'])

const SAFE_PLANNING_SESSION_KEYS = new Set(['categories', 'description', 'direction', 'id', 'mode', 'nextBlock', 'tasks', 'title', 'type'])
const SAFE_PLANNING_CATEGORY_KEYS = new Set(['count', 'examples', 'id', 'label', 'recommendation', 'tone'])
const SAFE_PLANNING_CATEGORY_EXAMPLE_KEYS = new Set(['dueDate', 'id', 'priority', 'title'])
const SAFE_PLANNING_NEXT_BLOCK_KEYS = new Set(['doneEnough', 'durationMinutes', 'id', 'rationale', 'taskIds', 'title'])

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
const SAFE_TASK_TABLE_KEYS = new Set(['columns', 'description', 'direction', 'id', 'rows', 'title', 'type'])

const SAFE_TASK_TABLE_ROW_KEYS = new Set([
  'actions',
  'confidence',
  'context',
  'dueDate',
  'energy',
  'externality',
  'id',
  'nextStep',
  'priority',
  'timeSize',
  'title',
  'urgency'
])

const SAFE_MINI_KANBAN_KEYS = new Set(['description', 'direction', 'id', 'lanes', 'title', 'type'])
const SAFE_MINI_KANBAN_LANE_KEYS = new Set(['description', 'id', 'tasks', 'title'])
const SAFE_MINI_KANBAN_TASK_KEYS = new Set(['actions', 'confidence', 'dueDate', 'id', 'note', 'priority', 'title'])
const SAFE_DAY_TIMELINE_KEYS = new Set(['blocks', 'currentTime', 'date', 'description', 'direction', 'id', 'title', 'type'])

const SAFE_DAY_TIMELINE_BLOCK_KEYS = new Set([
  'actions',
  'confidence',
  'doneEnough',
  'durationMinutes',
  'endTime',
  'id',
  'kind',
  'label',
  'startTime',
  'status',
  'taskId'
])

const SAFE_MUTATION_PREVIEW_KEYS = new Set(['actions', 'changes', 'description', 'direction', 'id', 'title', 'type'])
const SAFE_MUTATION_CHANGE_KEYS = new Set(['after', 'before', 'operation', 'risk', 'taskId', 'title', 'untouched'])
const SAFE_MATRIX_KEYS = new Set(['cells', 'description', 'direction', 'id', 'title', 'type', 'xAxis', 'yAxis'])
const SAFE_MATRIX_CELL_KEYS = new Set(['label', 'tasks', 'x', 'y'])
const SAFE_TASK_CHIP_KEYS = new Set(['actions', 'confidence', 'dueDate', 'id', 'priority', 'title'])
const SAFE_WORKLOAD_BARS_KEYS = new Set(['bars', 'description', 'direction', 'id', 'title', 'type'])
const SAFE_WORKLOAD_BAR_KEYS = new Set(['id', 'label', 'max', 'note', 'tone', 'value'])
const SAFE_TASK_GRAPH_KEYS = new Set(['description', 'direction', 'edges', 'id', 'nodes', 'title', 'type'])
const SAFE_TASK_GRAPH_NODE_KEYS = new Set(['id', 'kind', 'label'])
const SAFE_TASK_GRAPH_EDGE_KEYS = new Set(['label', 'source', 'target'])

const SAFE_TASK_BREAKDOWN_KEYS = new Set([
  'description',
  'direction',
  'id',
  'scope',
  'steps',
  'stoppingRule',
  'submitLabel',
  'targetOutcome',
  'task',
  'title',
  'type'
])

const SAFE_TASK_BREAKDOWN_TASK_KEYS = new Set(['id', 'title'])
const SAFE_TASK_BREAKDOWN_STEP_KEYS = new Set(['doneEnough', 'estimateMinutes', 'id', 'optional', 'title'])
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

function parseFormArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  if (Object.keys(parsed).some(key => !SAFE_FORM_KEYS.has(key))) {
    return { error: 'form contains unsupported properties', ok: false }
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.fields) || parsed.fields.length === 0) {
    return { error: 'form fields are required', ok: false }
  }

  if (parsed.fields.length > MAX_FORM_FIELDS) {
    return { error: 'form has too many fields', ok: false }
  }

  const allowedTypes = new Set<HermesUiFormFieldType>([
    'short-text', 'long-text', 'single-choice', 'multi-choice', 'boolean', 'number', 'date', 'time'
  ])

  const seenIds = new Set<string>()
  const fields: HermesUiFormField[] = []

  for (const [index, raw] of parsed.fields.entries()) {
    if (!isRecord(raw)) {
      return { error: `fields[${index}] must be an object`, ok: false }
    }

    if (Object.keys(raw).some(key => !SAFE_FORM_FIELD_KEYS.has(key))) {
      return { error: `fields[${index}] contains unsupported properties`, ok: false }
    }

    const id = normalizeText(raw.id, MAX_ITEM_ID_LENGTH, `fields[${index}].id`)

    if (typeof id !== 'string') {
      return id
    }

    if (!id) {
      return { error: `fields[${index}].id is required`, ok: false }
    }

    if (seenIds.has(id)) {
      return { error: `Duplicate field id: ${id}`, ok: false }
    }

    seenIds.add(id)
    const label = normalizeText(raw.label, MAX_LABEL_LENGTH, `fields[${index}].label`)

    if (typeof label !== 'string') {
      return label
    }

    if (!label) {
      return { error: `fields[${index}].label is required`, ok: false }
    }

    if (typeof raw.type !== 'string' || !allowedTypes.has(raw.type as HermesUiFormFieldType)) {
      return { error: `fields[${index}].type is unsupported`, ok: false }
    }

    if (raw.required !== undefined && typeof raw.required !== 'boolean') {
      return { error: `fields[${index}].required must be a boolean`, ok: false }
    }

    const description = optionalText(raw.description, MAX_ITEM_DESCRIPTION_LENGTH, `fields[${index}].description`)

    if (description && typeof description !== 'string') {
      return description
    }

    const placeholder = optionalText(raw.placeholder, MAX_LABEL_LENGTH, `fields[${index}].placeholder`)

    if (placeholder && typeof placeholder !== 'string') {
      return placeholder
    }

    let options: HermesUiFormOption[] | undefined
    const optionValues = new Set<string>()

    if (raw.type === 'single-choice' || raw.type === 'multi-choice') {
      if (!Array.isArray(raw.options) || raw.options.length === 0 || raw.options.length > MAX_FORM_OPTIONS) {
        return { error: `fields[${index}].options must contain 1-${MAX_FORM_OPTIONS} options`, ok: false }
      }

      options = []

      for (const [optionIndex, rawOption] of raw.options.entries()) {
        if (typeof rawOption === 'string') {
          const optionText = normalizeText(
            rawOption,
            MAX_ITEM_ID_LENGTH,
            `fields[${index}].options[${optionIndex}]`
          )

          if (typeof optionText !== 'string') {
            return optionText
          }

          if (!optionText || optionValues.has(optionText)) {
            return { error: `fields[${index}] has invalid or duplicate options`, ok: false }
          }

          optionValues.add(optionText)
          options.push({ label: optionText, value: optionText })

          continue
        }

        if (!isRecord(rawOption)) {
          return { error: `fields[${index}].options[${optionIndex}] must be an object`, ok: false }
        }

        if (Object.keys(rawOption).some(key => !SAFE_FORM_OPTION_KEYS.has(key))) {
          return { error: `fields[${index}].options[${optionIndex}] contains unsupported properties`, ok: false }
        }

        const value = normalizeText(rawOption.value, MAX_ITEM_ID_LENGTH, `fields[${index}].options[${optionIndex}].value`)
        const optionLabel = normalizeText(rawOption.label, MAX_ACTION_COPY_TEXT_LENGTH, `fields[${index}].options[${optionIndex}].label`)

        if (typeof value !== 'string') {
          return value
        }

        if (typeof optionLabel !== 'string') {
          return optionLabel
        }

        if (!value || !optionLabel || optionValues.has(value)) {
          return { error: `fields[${index}] has invalid or duplicate options`, ok: false }
        }

        optionValues.add(value)
        options.push({ label: optionLabel, value })
      }
    } else if (raw.options !== undefined) {
      return { error: `fields[${index}].options is only valid for choice fields`, ok: false }
    }

    let defaultValue: HermesUiFormValue | undefined

    if (raw.default !== undefined) {
      if (raw.type === 'boolean' && typeof raw.default === 'boolean') {
        defaultValue = raw.default
      } else if (raw.type === 'number' && typeof raw.default === 'number' && Number.isFinite(raw.default)) {
        defaultValue = String(raw.default)
      } else if (raw.type === 'multi-choice' && Array.isArray(raw.default)) {
        const selected = raw.default.filter(value => typeof value === 'string')

        if (selected.length !== raw.default.length || selected.some(value => !optionValues.has(value))) {
          return { error: `fields[${index}].default contains an unsupported option`, ok: false }
        }

        defaultValue = selected
      } else if (typeof raw.default === 'string') {
        const normalizedDefault = normalizeText(raw.default, MAX_ACTION_COPY_TEXT_LENGTH, `fields[${index}].default`)

        if (typeof normalizedDefault !== 'string') {
          return normalizedDefault
        }

        if (raw.type === 'single-choice' && !optionValues.has(normalizedDefault)) {
          return { error: `fields[${index}].default contains an unsupported option`, ok: false }
        }

        if (raw.type === 'time' && !isCanonical24HourTime(normalizedDefault)) {
          return { error: `fields[${index}].default must use 24-hour HH:mm format`, ok: false }
        }

        defaultValue = normalizedDefault
      } else {
        return { error: `fields[${index}].default does not match its field type`, ok: false }
      }
    }

    fields.push({
      ...(defaultValue !== undefined ? { defaultValue } : {}),
      description,
      id,
      label,
      options,
      placeholder,
      required: raw.required === true,
      type: raw.type as HermesUiFormFieldType
    })
  }

  const submitLabel = optionalText(parsed.submitLabel, MAX_ACTION_LABEL_LENGTH, 'submitLabel')

  if (submitLabel && typeof submitLabel !== 'string') {
    return submitLabel
  }

  const hasBooleanApproval = fields.some(field => field.type === 'boolean')
  const hasWritableContext = fields.some(field => field.type === 'short-text' || field.type === 'long-text')

  if (hasBooleanApproval && !hasWritableContext) {
    const revisionId = seenIds.has('revision') ? 'revision_context' : 'revision'
    fields.push({
      id: revisionId,
      label: base.fields.direction === 'rtl' ? 'תיקון או הקשר (אופציונלי)' : 'Changes or context (optional)',
      required: false,
      type: 'long-text'
    })
  }

  return { artifact: { ...base.fields, fields, submitLabel, type: 'form' }, ok: true }
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


function parsePlanningMode(value: unknown): HermesUiArtifactParseFailure | HermesUiFlowStatePlanningMode {
  if (value === 'day-start' || value === 'overload-relief' || value === 'end-of-day' || value === 'quick-triage') {
    return value
  }

  return { error: 'mode must be day-start, overload-relief, end-of-day, or quick-triage', ok: false }
}

function parsePlanningTone(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiFlowStatePlanningTone {
  if (
    value === 'risk' ||
    value === 'health' ||
    value === 'pet' ||
    value === 'work' ||
    value === 'money' ||
    value === 'life' ||
    value === 'creative' ||
    value === 'maintenance'
  ) {
    return value
  }

  return { error: `${field} must be a supported planning category tone`, ok: false }
}

function parseFlowStatePlanningSessionArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_PLANNING_SESSION_KEYS, 'flowstate-planning-session')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  const mode = parsePlanningMode(parsed.mode)

  if (typeof mode !== 'string') {
    return mode
  }

  if (!Array.isArray(parsed.categories) || parsed.categories.length === 0) {
    return { error: 'flowstate-planning-session categories are required', ok: false }
  }

  if (parsed.categories.length > MAX_PLANNING_CATEGORIES) {
    return { error: 'flowstate-planning-session has too many categories', ok: false }
  }

  const categories: HermesUiFlowStatePlanningCategory[] = []
  const seenCategoryIds = new Set<string>()

  for (const [index, rawCategory] of parsed.categories.entries()) {
    if (!isRecord(rawCategory)) {
      return { error: `categories[${index}] must be an object`, ok: false }
    }

    const categoryUnsupported = hasUnsupportedKeys(rawCategory, SAFE_PLANNING_CATEGORY_KEYS, `categories[${index}]`)

    if (categoryUnsupported) {
      return categoryUnsupported
    }

    const categoryId = normalizeText(rawCategory.id, MAX_ITEM_ID_LENGTH, `categories[${index}].id`)

    if (typeof categoryId !== 'string') {
      return categoryId
    }

    if (!categoryId) {
      return { error: `categories[${index}].id is required`, ok: false }
    }

    if (seenCategoryIds.has(categoryId)) {
      return { error: `Duplicate category id: ${categoryId}`, ok: false }
    }

    seenCategoryIds.add(categoryId)

    const label = normalizeText(rawCategory.label, MAX_LABEL_LENGTH, `categories[${index}].label`)

    if (typeof label !== 'string') {
      return label
    }

    if (!label) {
      return { error: `categories[${index}].label is required`, ok: false }
    }

    const tone = parsePlanningTone(rawCategory.tone, `categories[${index}].tone`)

    if (typeof tone !== 'string') {
      return tone
    }

    if (typeof rawCategory.count !== 'number' || !Number.isInteger(rawCategory.count) || rawCategory.count < 0 || rawCategory.count > 999) {
      return { error: `categories[${index}].count must be an integer from 0 to 999`, ok: false }
    }

    const recommendation = normalizeText(rawCategory.recommendation, MAX_ITEM_DESCRIPTION_LENGTH, `categories[${index}].recommendation`)

    if (typeof recommendation !== 'string') {
      return recommendation
    }

    if (!Array.isArray(rawCategory.examples)) {
      return { error: `categories[${index}].examples must be an array`, ok: false }
    }

    if (rawCategory.examples.length > MAX_PLANNING_CATEGORY_EXAMPLES) {
      return { error: `categories[${index}].examples has too many items`, ok: false }
    }

    const examples: HermesUiFlowStatePlanningCategoryExample[] = []
    const seenExampleIds = new Set<string>()

    for (const [exampleIndex, rawExample] of rawCategory.examples.entries()) {
      if (!isRecord(rawExample)) {
        return { error: `categories[${index}].examples[${exampleIndex}] must be an object`, ok: false }
      }

      const exampleUnsupported = hasUnsupportedKeys(
        rawExample,
        SAFE_PLANNING_CATEGORY_EXAMPLE_KEYS,
        `categories[${index}].examples[${exampleIndex}]`
      )

      if (exampleUnsupported) {
        return exampleUnsupported
      }

      const example = parseTaskLike(rawExample, `categories[${index}].examples[${exampleIndex}]`)

      if (isParseFailure(example)) {
        return example
      }

      if (seenExampleIds.has(example.id)) {
        return { error: `Duplicate category example id: ${example.id}`, ok: false }
      }

      seenExampleIds.add(example.id)
      examples.push({ dueDate: example.dueDate, id: example.id, priority: example.priority, title: example.title })
    }

    categories.push({ count: rawCategory.count, examples, id: categoryId, label, recommendation, tone })
  }

  let nextBlock: HermesUiFlowStatePlanningNextBlock | undefined

  if (parsed.nextBlock !== undefined) {
    if (!isRecord(parsed.nextBlock)) {
      return { error: 'nextBlock must be an object', ok: false }
    }

    const nextBlockUnsupported = hasUnsupportedKeys(parsed.nextBlock, SAFE_PLANNING_NEXT_BLOCK_KEYS, 'nextBlock')

    if (nextBlockUnsupported) {
      return nextBlockUnsupported
    }

    const id = normalizeText(parsed.nextBlock.id, MAX_ITEM_ID_LENGTH, 'nextBlock.id')

    if (typeof id !== 'string') {
      return id
    }

    const title = normalizeText(parsed.nextBlock.title, MAX_LABEL_LENGTH, 'nextBlock.title')

    if (typeof title !== 'string') {
      return title
    }

    const durationMinutes = parsePositiveMinutes(parsed.nextBlock.durationMinutes, 'nextBlock.durationMinutes')

    if (typeof durationMinutes !== 'number') {
      return durationMinutes
    }

    if (!Array.isArray(parsed.nextBlock.taskIds) || parsed.nextBlock.taskIds.length === 0) {
      return { error: 'nextBlock.taskIds are required', ok: false }
    }

    if (parsed.nextBlock.taskIds.length > MAX_PLANNING_TASK_IDS) {
      return { error: 'nextBlock.taskIds has too many items', ok: false }
    }

    const taskIds: string[] = []
    const seenTaskIds = new Set<string>()

    for (const [index, rawTaskId] of parsed.nextBlock.taskIds.entries()) {
      const taskId = normalizeText(rawTaskId, MAX_ITEM_ID_LENGTH, `nextBlock.taskIds[${index}]`)

      if (typeof taskId !== 'string') {
        return taskId
      }

      if (!taskId) {
        return { error: `nextBlock.taskIds[${index}] is required`, ok: false }
      }

      if (seenTaskIds.has(taskId)) {
        return { error: `Duplicate nextBlock task id: ${taskId}`, ok: false }
      }

      seenTaskIds.add(taskId)
      taskIds.push(taskId)
    }

    const doneEnough = normalizeText(parsed.nextBlock.doneEnough, MAX_ITEM_DESCRIPTION_LENGTH, 'nextBlock.doneEnough')

    if (typeof doneEnough !== 'string') {
      return doneEnough
    }

    const rationale = normalizeText(parsed.nextBlock.rationale, MAX_ITEM_DESCRIPTION_LENGTH, 'nextBlock.rationale')

    if (typeof rationale !== 'string') {
      return rationale
    }

    nextBlock = { doneEnough, durationMinutes, id, rationale, taskIds, title }
  }

  if (!Array.isArray(parsed.tasks) || parsed.tasks.length === 0) {
    return { error: 'flowstate-planning-session tasks are required', ok: false }
  }

  if (parsed.tasks.length > MAX_BATCH_TASKS) {
    return { error: 'flowstate-planning-session has too many tasks', ok: false }
  }

  const seenTaskIds = new Set<string>()
  const tasks: HermesUiFlowStateBatchTask[] = []

  for (const [index, rawTask] of parsed.tasks.entries()) {
    if (!isRecord(rawTask)) {
      return { error: `tasks[${index}] must be an object`, ok: false }
    }

    const task = parseTaskLike(rawTask, `tasks[${index}]`)

    if (isParseFailure(task)) {
      return task
    }

    if (seenTaskIds.has(task.id)) {
      return { error: `Duplicate task id: ${task.id}`, ok: false }
    }

    seenTaskIds.add(task.id)

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

  return { artifact: { ...base.fields, categories, mode, nextBlock, tasks, type: 'flowstate-planning-session' }, ok: true }
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

function parseTaskBreakdownSteps(
  value: unknown,
  allowEmptyEditableFields: boolean
): HermesUiArtifactParseFailure | HermesUiTaskBreakdownStep[] {
  if (!Array.isArray(value) || value.length === 0) {
    return { error: 'task-breakdown steps are required', ok: false }
  }

  if (value.length > MAX_BREAKDOWN_STEPS) {
    return { error: 'task-breakdown has too many steps', ok: false }
  }

  const seenIds = new Set<string>()
  const steps: HermesUiTaskBreakdownStep[] = []

  for (const [index, rawStep] of value.entries()) {
    if (!isRecord(rawStep)) {return { error: `steps[${index}] must be an object`, ok: false }}

    const unsupportedStep = hasUnsupportedKeys(rawStep, SAFE_TASK_BREAKDOWN_STEP_KEYS, `steps[${index}]`)

    if (unsupportedStep) {return unsupportedStep}

    const id = normalizeText(rawStep.id, MAX_ITEM_ID_LENGTH, `steps[${index}].id`)

    if (typeof id !== 'string') {return id}

    if (!id) {return { error: `steps[${index}].id is required`, ok: false }}

    if (seenIds.has(id)) {return { error: `Duplicate step id: ${id}`, ok: false }}
    seenIds.add(id)

    const title = normalizeText(
      rawStep.title,
      HERMES_UI_TASK_BREAKDOWN_LIMITS.titleLength,
      `steps[${index}].title`
    )

    if (typeof title !== 'string') {return title}

    if (!title && !allowEmptyEditableFields) {
      return { error: `steps[${index}].title is required`, ok: false }
    }

    if (rawStep.doneEnough === undefined || rawStep.doneEnough === null) {
      return { error: `steps[${index}].doneEnough is required`, ok: false }
    }

    const doneEnough = normalizeText(
      rawStep.doneEnough,
      HERMES_UI_TASK_BREAKDOWN_LIMITS.doneEnoughLength,
      `steps[${index}].doneEnough`
    )

    if (typeof doneEnough !== 'string') {return doneEnough}

    if (!doneEnough && !allowEmptyEditableFields) {
      return { error: `steps[${index}].doneEnough is required`, ok: false }
    }

    let estimateMinutes: number | undefined

    if (rawStep.estimateMinutes !== undefined) {
      if (typeof rawStep.estimateMinutes !== 'number' || !Number.isInteger(rawStep.estimateMinutes) || rawStep.estimateMinutes < 1 || rawStep.estimateMinutes > 480) {
        return { error: `steps[${index}].estimateMinutes must be an integer from 1 to 480`, ok: false }
      }

      estimateMinutes = rawStep.estimateMinutes
    }

    if (rawStep.optional !== undefined && typeof rawStep.optional !== 'boolean') {
      return { error: `steps[${index}].optional must be a boolean`, ok: false }
    }

    steps.push({ doneEnough, estimateMinutes, id, optional: rawStep.optional as boolean | undefined, title })
  }

  return steps
}

export function parseHermesUiTaskBreakdownDraftSteps(value: unknown): HermesUiTaskBreakdownStep[] | null {
  const result = parseTaskBreakdownSteps(value, true)

  return Array.isArray(result) ? result : null
}

function parseTaskBreakdownArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_TASK_BREAKDOWN_KEYS, 'task-breakdown')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!isRecord(parsed.task)) {
    return { error: 'task-breakdown task is required', ok: false }
  }

  const unsupportedTask = hasUnsupportedKeys(parsed.task, SAFE_TASK_BREAKDOWN_TASK_KEYS, 'task')

  if (unsupportedTask) {
    return unsupportedTask
  }

  const taskId = normalizeText(parsed.task.id, MAX_ITEM_ID_LENGTH, 'task.id')

  if (typeof taskId !== 'string') {return taskId}

  if (!taskId) {return { error: 'task.id is required', ok: false }}

  const taskTitle = normalizeText(parsed.task.title, MAX_LABEL_LENGTH, 'task.title')

  if (typeof taskTitle !== 'string') {return taskTitle}

  if (!taskTitle) {return { error: 'task.title is required', ok: false }}

  if (parsed.scope !== 'next-move' && parsed.scope !== 'working-session' && parsed.scope !== 'full-delivery') {
    return { error: 'scope must be next-move, working-session, or full-delivery', ok: false }
  }

  const steps = parseTaskBreakdownSteps(parsed.steps, false)

  if (!Array.isArray(steps)) {
    return steps
  }

  const targetOutcome = optionalText(parsed.targetOutcome, MAX_ITEM_DESCRIPTION_LENGTH, 'targetOutcome')

  if (targetOutcome && typeof targetOutcome !== 'string') {return targetOutcome}
  const stoppingRule = optionalText(parsed.stoppingRule, MAX_ITEM_DESCRIPTION_LENGTH, 'stoppingRule')

  if (stoppingRule && typeof stoppingRule !== 'string') {return stoppingRule}
  const submitLabel = optionalText(parsed.submitLabel, MAX_ACTION_LABEL_LENGTH, 'submitLabel')

  if (submitLabel && typeof submitLabel !== 'string') {return submitLabel}

  return {
    artifact: {
      ...base.fields,
      scope: parsed.scope,
      steps,
      stoppingRule,
      submitLabel,
      targetOutcome,
      task: { id: taskId, title: taskTitle },
      type: 'task-breakdown'
    },
    ok: true
  }
}

function parsePlanningFunnelStepStatus(value: unknown): HermesUiArtifactParseFailure | HermesUiPlanningFunnelStepStatus | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === 'pending' || value === 'current' || value === 'done' || value === 'blocked') {
    return value
  }

  return { error: 'step.status must be pending, current, done, or blocked', ok: false }
}

function parsePlanningFunnelArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.steps) || parsed.steps.length === 0) {
    return { error: 'planning-funnel steps are required', ok: false }
  }

  if (parsed.steps.length > MAX_FUNNEL_STEPS) {
    return { error: 'planning-funnel has too many steps', ok: false }
  }

  const seenIds = new Set<string>()
  const steps: HermesUiPlanningFunnelStep[] = []

  for (const [index, rawStep] of parsed.steps.entries()) {
    if (!isRecord(rawStep)) {
      return { error: `steps[${index}] must be an object`, ok: false }
    }

    const stepId = normalizeText(rawStep.id, MAX_ITEM_ID_LENGTH, `steps[${index}].id`)

    if (typeof stepId !== 'string') {
      return stepId
    }

    if (!stepId) {
      return { error: `steps[${index}].id is required`, ok: false }
    }

    if (seenIds.has(stepId)) {
      return { error: `Duplicate step id: ${stepId}`, ok: false }
    }

    seenIds.add(stepId)

    const label = normalizeText(rawStep.label, MAX_LABEL_LENGTH, `steps[${index}].label`)

    if (typeof label !== 'string') {
      return label
    }

    if (!label) {
      return { error: `steps[${index}].label is required`, ok: false }
    }

    const description = optionalText(rawStep.description, MAX_ITEM_DESCRIPTION_LENGTH, `steps[${index}].description`)

    if (description && typeof description !== 'string') {
      return description
    }

    const status = parsePlanningFunnelStepStatus(rawStep.status)

    if (status && typeof status === 'object') {
      return status
    }

    steps.push({ description, id: stepId, label, status })
  }

  return { artifact: { ...base.fields, steps, type: 'planning-funnel' }, ok: true }
}

function parseOptionalStringList(value: unknown, field: string): HermesUiArtifactParseFailure | string[] | undefined {
  if (value === undefined) {
    return undefined
  }

  if (!Array.isArray(value)) {
    return { error: `${field} must be an array`, ok: false }
  }

  if (value.length > MAX_CONTEXT_ITEMS) {
    return { error: `${field} has too many items`, ok: false }
  }

  const items: string[] = []

  for (const [index, rawItem] of value.entries()) {
    const item = normalizeText(rawItem, MAX_ITEM_DESCRIPTION_LENGTH, `${field}[${index}]`)

    if (typeof item !== 'string') {
      return item
    }

    if (item) {
      items.push(item)
    }
  }

  return items
}

function parseSubmitActions(
  value: unknown,
  field: string,
  maxActions = MAX_NEXT_BLOCK_ACTIONS,
  required = false
): HermesUiArtifactParseFailure | HermesUiChecklistAction[] | undefined {
  if (value === undefined) {
    return required ? { error: `${field} are required`, ok: false } : undefined
  }

  if (!Array.isArray(value)) {
    return { error: `${field} must be an array`, ok: false }
  }

  if (required && value.length === 0) {
    return { error: `${field} are required`, ok: false }
  }

  if (value.length > maxActions) {
    return { error: `${field} has too many actions`, ok: false }
  }

  const actions: HermesUiChecklistAction[] = []
  const seenActionIds = new Set<string>()

  for (const [index, rawAction] of value.entries()) {
    const action = parseChecklistAction(rawAction, `${field}[${index}]`)

    if (isParseFailure(action)) {
      return action
    }

    if (!action.submitText) {
      return { error: `${field}[${index}].submitText is required`, ok: false }
    }

    if (seenActionIds.has(action.id)) {
      return { error: `Duplicate action id: ${action.id}`, ok: false }
    }

    seenActionIds.add(action.id)
    actions.push(action)
  }

  return actions.length ? actions : undefined
}

function parseTaskSize(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiTaskSize | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === 'tiny' || value === 'small' || value === 'medium' || value === 'large' || value === 'unknown') {
    return value
  }

  return { error: `${field} must be tiny, small, medium, large, or unknown`, ok: false }
}

function parsePlanningLevel(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiPlanningLevel | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === 'low' || value === 'medium' || value === 'high' || value === 'unknown') {
    return value
  }

  return { error: `${field} must be low, medium, high, or unknown`, ok: false }
}

function parseConfidence(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiConfidence | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === 'low' || value === 'medium' || value === 'high') {
    return value
  }

  return { error: `${field} must be low, medium, or high`, ok: false }
}

function parseExternality(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiTaskExternality | undefined {
  if (value === undefined) {
    return undefined
  }

  if (value === 'internal' || value === 'external' || value === 'waiting' || value === 'unknown') {
    return value
  }

  return { error: `${field} must be internal, external, waiting, or unknown`, ok: false }
}

function parseOptionalDate(value: unknown, field: string): HermesUiArtifactParseFailure | string | null | undefined {
  const text = optionalNullableText(value, MAX_ITEM_ID_LENGTH, field)

  if (text && typeof text !== 'string') {
    return text
  }

  if (typeof text === 'string' && !DATE_ONLY_RE.test(text)) {
    return { error: `${field} must be YYYY-MM-DD`, ok: false }
  }

  return text
}

function parseOptionalTime(value: unknown, field: string): HermesUiArtifactParseFailure | string | undefined {
  const text = optionalText(value, MAX_ITEM_ID_LENGTH, field)

  if (text && typeof text !== 'string') {
    return text
  }

  if (typeof text === 'string' && !TIME_ONLY_RE.test(text)) {
    return { error: `${field} must be HH:mm`, ok: false }
  }

  return text
}

function parseTaskChip(
  rawTask: Record<string, unknown>,
  field: string,
  allowedKeys: ReadonlySet<string> = SAFE_TASK_CHIP_KEYS
): HermesUiArtifactParseFailure | HermesUiTaskChip {
  const unsupported = hasUnsupportedKeys(rawTask, allowedKeys, field)

  if (unsupported) {
    return unsupported
  }

  const task = parseTaskLike(rawTask, field)

  if (isParseFailure(task)) {
    return task
  }

  const dueDate = parseOptionalDate(rawTask.dueDate, `${field}.dueDate`)

  if (dueDate && typeof dueDate !== 'string') {
    return dueDate
  }

  const confidence = parseConfidence(rawTask.confidence, `${field}.confidence`)

  if (confidence && typeof confidence === 'object') {
    return confidence
  }

  const actions = parseSubmitActions(rawTask.actions, `${field}.actions`)

  if (isParseFailure(actions)) {
    return actions
  }

  return {
    actions,
    confidence,
    dueDate,
    id: task.id,
    priority: task.priority,
    title: task.title
  }
}

function parseVisibleRecord(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiVisibleRecord | undefined {
  if (value === undefined) {
    return undefined
  }

  if (!isRecord(value)) {
    return { error: `${field} must be an object`, ok: false }
  }

  const entries = Object.entries(value)

  if (entries.length > MAX_MUTATION_RECORD_FIELDS) {
    return { error: `${field} has too many fields`, ok: false }
  }

  const parsed: HermesUiVisibleRecord = {}

  for (const [key, rawRecordValue] of entries) {
    if (key.length > MAX_ITEM_ID_LENGTH) {
      return { error: `${field}.${key} key is too long`, ok: false }
    }

    if (
      rawRecordValue !== null &&
      typeof rawRecordValue !== 'string' &&
      typeof rawRecordValue !== 'number' &&
      typeof rawRecordValue !== 'boolean'
    ) {
      return { error: `${field}.${key} must be a safe visible value`, ok: false }
    }

    if (typeof rawRecordValue === 'string') {
      const text = normalizeText(rawRecordValue, MAX_ITEM_DESCRIPTION_LENGTH, `${field}.${key}`)

      if (typeof text !== 'string') {
        return text
      }

      parsed[key] = text
    } else {
      parsed[key] = rawRecordValue
    }
  }

  return parsed
}

function parseTaskContextArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!isRecord(parsed.task)) {
    return { error: 'task-context task is required', ok: false }
  }

  const task = parseTaskLike(parsed.task, 'task')

  if (isParseFailure(task)) {
    return task
  }

  const meaning = optionalText(parsed.meaning, MAX_ITEM_DESCRIPTION_LENGTH, 'meaning')

  if (meaning && typeof meaning !== 'string') {
    return meaning
  }

  const progress = optionalText(parsed.progress, MAX_ITEM_DESCRIPTION_LENGTH, 'progress')

  if (progress && typeof progress !== 'string') {
    return progress
  }

  const connections = parseOptionalStringList(parsed.connections, 'connections')

  if (isParseFailure(connections)) {
    return connections
  }

  const waitingOn = parseOptionalStringList(parsed.waitingOn, 'waitingOn')

  if (isParseFailure(waitingOn)) {
    return waitingOn
  }

  const unknowns = parseOptionalStringList(parsed.unknowns, 'unknowns')

  if (isParseFailure(unknowns)) {
    return unknowns
  }

  let actions: HermesUiChecklistAction[] | undefined

  if (parsed.actions !== undefined) {
    if (!Array.isArray(parsed.actions)) {
      return { error: 'task-context actions must be an array', ok: false }
    }

    if (parsed.actions.length > MAX_NEXT_BLOCK_ACTIONS) {
      return { error: 'task-context has too many actions', ok: false }
    }

    actions = []
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
  }

  return {
    artifact: {
      ...base.fields,
      actions: actions?.length ? actions : undefined,
      connections,
      meaning,
      progress,
      task,
      type: 'task-context',
      unknowns,
      waitingOn
    },
    ok: true
  }
}

function parseTaskTableColumn(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiTaskTableColumn {
  if (
    value === 'task' ||
    value === 'context' ||
    value === 'timeSize' ||
    value === 'energy' ||
    value === 'urgency' ||
    value === 'externality' ||
    value === 'nextStep' ||
    value === 'confidence'
  ) {
    return value
  }

  return { error: `${field} is not a supported task-table column`, ok: false }
}

function parseTaskTableArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_TASK_TABLE_KEYS, 'task-table')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.columns) || parsed.columns.length === 0) {
    return { error: 'task-table columns are required', ok: false }
  }

  if (parsed.columns.length > MAX_TASK_TABLE_COLUMNS) {
    return { error: 'task-table has too many columns', ok: false }
  }

  const seenColumns = new Set<string>()
  const columns: HermesUiTaskTableColumn[] = []

  for (const [index, rawColumn] of parsed.columns.entries()) {
    const column = parseTaskTableColumn(rawColumn, `columns[${index}]`)

    if (isParseFailure(column)) {
      return column
    }

    if (seenColumns.has(column)) {
      return { error: `Duplicate column: ${column}`, ok: false }
    }

    seenColumns.add(column)
    columns.push(column)
  }

  if (!Array.isArray(parsed.rows) || parsed.rows.length < MIN_TASK_TABLE_ROWS) {
    return { error: 'task-table requires at least 3 rows', ok: false }
  }

  if (parsed.rows.length > MAX_TASK_TABLE_ROWS) {
    return { error: 'task-table has too many rows', ok: false }
  }

  const seenRowIds = new Set<string>()
  const rows: HermesUiPlanningTaskRow[] = []

  for (const [index, rawRow] of parsed.rows.entries()) {
    if (!isRecord(rawRow)) {
      return { error: `rows[${index}] must be an object`, ok: false }
    }

    const unsupportedRow = hasUnsupportedKeys(rawRow, SAFE_TASK_TABLE_ROW_KEYS, `rows[${index}]`)

    if (unsupportedRow) {
      return unsupportedRow
    }

    const row = parseTaskLike(rawRow, `rows[${index}]`)

    if (isParseFailure(row)) {
      return row
    }

    if (seenRowIds.has(row.id)) {
      return { error: `Duplicate row id: ${row.id}`, ok: false }
    }

    seenRowIds.add(row.id)

    const dueDate = parseOptionalDate(rawRow.dueDate, `rows[${index}].dueDate`)

    if (dueDate && typeof dueDate !== 'string') {
      return dueDate
    }

    const context = optionalText(rawRow.context, MAX_RATIONALE_LENGTH, `rows[${index}].context`)

    if (context && typeof context !== 'string') {
      return context
    }

    const nextStep = optionalText(rawRow.nextStep, MAX_RATIONALE_LENGTH, `rows[${index}].nextStep`)

    if (nextStep && typeof nextStep !== 'string') {
      return nextStep
    }

    const timeSize = parseTaskSize(rawRow.timeSize, `rows[${index}].timeSize`)
    const energy = parsePlanningLevel(rawRow.energy, `rows[${index}].energy`)
    const urgency = parsePlanningLevel(rawRow.urgency, `rows[${index}].urgency`)
    const externality = parseExternality(rawRow.externality, `rows[${index}].externality`)
    const confidence = parseConfidence(rawRow.confidence, `rows[${index}].confidence`)
    const enumValues = [timeSize, energy, urgency, externality, confidence]

    for (const enumValue of enumValues) {
      if (enumValue && typeof enumValue === 'object') {
        return enumValue
      }
    }

    const actions = parseSubmitActions(rawRow.actions, `rows[${index}].actions`)

    if (isParseFailure(actions)) {
      return actions
    }

    rows.push({
      actions,
      confidence: confidence as HermesUiConfidence | undefined,
      context,
      dueDate,
      energy: energy as HermesUiPlanningLevel | undefined,
      externality: externality as HermesUiTaskExternality | undefined,
      id: row.id,
      nextStep,
      priority: row.priority,
      timeSize: timeSize as HermesUiTaskSize | undefined,
      title: row.title,
      urgency: urgency as HermesUiPlanningLevel | undefined
    })
  }

  return { artifact: { ...base.fields, columns, rows, type: 'task-table' }, ok: true }
}

function parseMiniKanbanArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_MINI_KANBAN_KEYS, 'mini-kanban')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.lanes) || parsed.lanes.length === 0) {
    return { error: 'mini-kanban lanes are required', ok: false }
  }

  if (parsed.lanes.length > MAX_MINI_KANBAN_LANES) {
    return { error: 'mini-kanban has too many lanes', ok: false }
  }

  const lanes: HermesUiMiniKanbanLane[] = []
  const seenLaneIds = new Set<string>()
  const seenTaskIds = new Set<string>()

  for (const [laneIndex, rawLane] of parsed.lanes.entries()) {
    if (!isRecord(rawLane)) {
      return { error: `lanes[${laneIndex}] must be an object`, ok: false }
    }

    const unsupportedLane = hasUnsupportedKeys(rawLane, SAFE_MINI_KANBAN_LANE_KEYS, `lanes[${laneIndex}]`)

    if (unsupportedLane) {
      return unsupportedLane
    }

    const laneId = normalizeText(rawLane.id, MAX_ITEM_ID_LENGTH, `lanes[${laneIndex}].id`)
    const laneTitle = normalizeText(rawLane.title, MAX_LABEL_LENGTH, `lanes[${laneIndex}].title`)

    if (typeof laneId !== 'string') {
      return laneId
    }

    if (typeof laneTitle !== 'string') {
      return laneTitle
    }

    if (!laneId || !laneTitle) {
      return { error: `lanes[${laneIndex}].id and title are required`, ok: false }
    }

    if (seenLaneIds.has(laneId)) {
      return { error: `Duplicate lane id: ${laneId}`, ok: false }
    }

    seenLaneIds.add(laneId)

    const laneDescription = optionalText(rawLane.description, MAX_ITEM_DESCRIPTION_LENGTH, `lanes[${laneIndex}].description`)

    if (laneDescription && typeof laneDescription !== 'string') {
      return laneDescription
    }

    if (!Array.isArray(rawLane.tasks)) {
      return { error: `lanes[${laneIndex}].tasks must be an array`, ok: false }
    }

    if (rawLane.tasks.length > MAX_MINI_KANBAN_TASKS) {
      return { error: `lanes[${laneIndex}].tasks has too many tasks`, ok: false }
    }

    const tasks: HermesUiMiniKanbanTask[] = []

    for (const [taskIndex, rawTask] of rawLane.tasks.entries()) {
      if (!isRecord(rawTask)) {
        return { error: `lanes[${laneIndex}].tasks[${taskIndex}] must be an object`, ok: false }
      }

      const unsupportedTask = hasUnsupportedKeys(rawTask, SAFE_MINI_KANBAN_TASK_KEYS, `lanes[${laneIndex}].tasks[${taskIndex}]`)

      if (unsupportedTask) {
        return unsupportedTask
      }

      const task = parseTaskChip(rawTask, `lanes[${laneIndex}].tasks[${taskIndex}]`, SAFE_MINI_KANBAN_TASK_KEYS)

      if (isParseFailure(task)) {
        return task
      }

      if (seenTaskIds.has(task.id)) {
        return { error: `Duplicate task id: ${task.id}`, ok: false }
      }

      seenTaskIds.add(task.id)

      const note = optionalText(rawTask.note, MAX_RATIONALE_LENGTH, `lanes[${laneIndex}].tasks[${taskIndex}].note`)

      if (note && typeof note !== 'string') {
        return note
      }

      tasks.push({ ...task, note })
    }

    lanes.push({ description: laneDescription, id: laneId, tasks, title: laneTitle })
  }

  return { artifact: { ...base.fields, lanes, type: 'mini-kanban' }, ok: true }
}

function parseDayTimelineArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_DAY_TIMELINE_KEYS, 'day-timeline')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  const date = parseScheduledDate(parsed.date, 'date')

  if (typeof date !== 'string') {
    return date
  }

  const currentTime = parseOptionalTime(parsed.currentTime, 'currentTime')

  if (currentTime && typeof currentTime !== 'string') {
    return currentTime
  }

  if (!Array.isArray(parsed.blocks) || parsed.blocks.length === 0) {
    return { error: 'day-timeline blocks are required', ok: false }
  }

  if (parsed.blocks.length > MAX_TIMELINE_BLOCKS) {
    return { error: 'day-timeline has too many blocks', ok: false }
  }

  const blocks: HermesUiDayTimelineBlock[] = []
  const seenBlockIds = new Set<string>()

  for (const [index, rawBlock] of parsed.blocks.entries()) {
    if (!isRecord(rawBlock)) {
      return { error: `blocks[${index}] must be an object`, ok: false }
    }

    const unsupportedBlock = hasUnsupportedKeys(rawBlock, SAFE_DAY_TIMELINE_BLOCK_KEYS, `blocks[${index}]`)

    if (unsupportedBlock) {
      return unsupportedBlock
    }

    const blockId = normalizeText(rawBlock.id, MAX_ITEM_ID_LENGTH, `blocks[${index}].id`)
    const label = normalizeText(rawBlock.label, MAX_LABEL_LENGTH, `blocks[${index}].label`)

    if (typeof blockId !== 'string') {
      return blockId
    }

    if (typeof label !== 'string') {
      return label
    }

    if (!blockId || !label) {
      return { error: `blocks[${index}].id and label are required`, ok: false }
    }

    if (seenBlockIds.has(blockId)) {
      return { error: `Duplicate block id: ${blockId}`, ok: false }
    }

    seenBlockIds.add(blockId)

    const startTime = parseOptionalTime(rawBlock.startTime, `blocks[${index}].startTime`)
    const endTime = parseOptionalTime(rawBlock.endTime, `blocks[${index}].endTime`)

    if (startTime && typeof startTime !== 'string') {
      return startTime
    }

    if (endTime && typeof endTime !== 'string') {
      return endTime
    }

    let durationMinutes: number | undefined

    if (rawBlock.durationMinutes !== undefined) {
      const duration = parsePositiveMinutes(rawBlock.durationMinutes, `blocks[${index}].durationMinutes`)

      if (typeof duration !== 'number') {
        return duration
      }

      durationMinutes = duration
    }

    const kind = rawBlock.kind

    if (
      kind !== undefined &&
      kind !== 'fixed' &&
      kind !== 'focus' &&
      kind !== 'short-task' &&
      kind !== 'buffer' &&
      kind !== 'break' &&
      kind !== 'floating'
    ) {
      return { error: `blocks[${index}].kind is invalid`, ok: false }
    }

    const status = rawBlock.status

    if (
      status !== undefined &&
      status !== 'planned' &&
      status !== 'doing' &&
      status !== 'done' &&
      status !== 'dropped' &&
      status !== 'candidate'
    ) {
      return { error: `blocks[${index}].status is invalid`, ok: false }
    }

    const taskId = optionalText(rawBlock.taskId, MAX_ITEM_ID_LENGTH, `blocks[${index}].taskId`)
    const doneEnough = optionalText(rawBlock.doneEnough, MAX_RATIONALE_LENGTH, `blocks[${index}].doneEnough`)

    if (taskId && typeof taskId !== 'string') {
      return taskId
    }

    if (doneEnough && typeof doneEnough !== 'string') {
      return doneEnough
    }

    const confidence = parseConfidence(rawBlock.confidence, `blocks[${index}].confidence`)

    if (confidence && typeof confidence === 'object') {
      return confidence
    }

    const actions = parseSubmitActions(rawBlock.actions, `blocks[${index}].actions`)

    if (isParseFailure(actions)) {
      return actions
    }

    blocks.push({
      actions,
      confidence,
      doneEnough,
      durationMinutes,
      endTime,
      id: blockId,
      kind: kind as HermesUiTimelineBlockKind | undefined,
      label,
      startTime,
      status: status as HermesUiTimelineStatus | undefined,
      taskId
    })
  }

  return { artifact: { ...base.fields, blocks, currentTime, date, type: 'day-timeline' }, ok: true }
}

function parseMutationPreviewArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_MUTATION_PREVIEW_KEYS, 'mutation-preview')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.changes) || parsed.changes.length === 0) {
    return { error: 'mutation-preview changes are required', ok: false }
  }

  if (parsed.changes.length > MAX_MUTATION_CHANGES) {
    return { error: 'mutation-preview has too many changes', ok: false }
  }

  const changes: HermesUiMutationPreviewChange[] = []
  const seenChangeIds = new Set<string>()

  for (const [index, rawChange] of parsed.changes.entries()) {
    if (!isRecord(rawChange)) {
      return { error: `changes[${index}] must be an object`, ok: false }
    }

    const unsupportedChange = hasUnsupportedKeys(rawChange, SAFE_MUTATION_CHANGE_KEYS, `changes[${index}]`)

    if (unsupportedChange) {
      return unsupportedChange
    }

    const taskId = normalizeText(rawChange.taskId, MAX_ITEM_ID_LENGTH, `changes[${index}].taskId`)
    const title = normalizeText(rawChange.title, MAX_LABEL_LENGTH, `changes[${index}].title`)

    if (typeof taskId !== 'string') {
      return taskId
    }

    if (typeof title !== 'string') {
      return title
    }

    if (!taskId || !title) {
      return { error: `changes[${index}].taskId and title are required`, ok: false }
    }

    const operation = rawChange.operation

    if (
      operation !== 'update' &&
      operation !== 'schedule-instance' &&
      operation !== 'complete' &&
      operation !== 'create' &&
      operation !== 'delete'
    ) {
      return { error: `changes[${index}].operation is invalid`, ok: false }
    }

    const risk = rawChange.risk

    if (risk !== undefined && risk !== 'low' && risk !== 'medium' && risk !== 'high') {
      return { error: `changes[${index}].risk is invalid`, ok: false }
    }

    const changeKey = `${taskId}:${operation}`

    if (seenChangeIds.has(changeKey)) {
      return { error: `Duplicate change: ${changeKey}`, ok: false }
    }

    seenChangeIds.add(changeKey)

    const before = parseVisibleRecord(rawChange.before, `changes[${index}].before`)
    const after = parseVisibleRecord(rawChange.after, `changes[${index}].after`)

    if (isParseFailure(before)) {
      return before
    }

    if (isParseFailure(after)) {
      return after
    }

    const untouched = parseOptionalStringList(rawChange.untouched, `changes[${index}].untouched`)

    if (isParseFailure(untouched)) {
      return untouched
    }

    changes.push({
      after,
      before,
      operation,
      risk: risk as HermesUiMutationRisk | undefined,
      taskId,
      title,
      untouched
    })
  }

  const actions = parseSubmitActions(parsed.actions, 'actions', MAX_NEXT_BLOCK_ACTIONS, true)

  if (isParseFailure(actions)) {
    return actions
  }

  return { artifact: { ...base.fields, actions: actions || [], changes, type: 'mutation-preview' }, ok: true }
}

function parseUrgencyEnergyMatrixArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_MATRIX_KEYS, 'urgency-energy-matrix')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (parsed.xAxis !== 'energy' && parsed.xAxis !== 'effort') {
    return { error: 'xAxis must be energy or effort', ok: false }
  }

  if (parsed.yAxis !== 'urgency' && parsed.yAxis !== 'impact') {
    return { error: 'yAxis must be urgency or impact', ok: false }
  }

  if (!Array.isArray(parsed.cells) || parsed.cells.length === 0) {
    return { error: 'urgency-energy-matrix cells are required', ok: false }
  }

  if (parsed.cells.length > MAX_MATRIX_CELLS) {
    return { error: 'urgency-energy-matrix has too many cells', ok: false }
  }

  const cells: HermesUiUrgencyEnergyCell[] = []
  const seenCells = new Set<string>()

  for (const [cellIndex, rawCell] of parsed.cells.entries()) {
    if (!isRecord(rawCell)) {
      return { error: `cells[${cellIndex}] must be an object`, ok: false }
    }

    const unsupportedCell = hasUnsupportedKeys(rawCell, SAFE_MATRIX_CELL_KEYS, `cells[${cellIndex}]`)

    if (unsupportedCell) {
      return unsupportedCell
    }

    if (rawCell.x !== 'low' && rawCell.x !== 'medium' && rawCell.x !== 'high') {
      return { error: `cells[${cellIndex}].x is invalid`, ok: false }
    }

    if (rawCell.y !== 'low' && rawCell.y !== 'medium' && rawCell.y !== 'high') {
      return { error: `cells[${cellIndex}].y is invalid`, ok: false }
    }

    const cellKey = `${rawCell.x}:${rawCell.y}`

    if (seenCells.has(cellKey)) {
      return { error: `Duplicate matrix cell: ${cellKey}`, ok: false }
    }

    seenCells.add(cellKey)

    const label = optionalText(rawCell.label, MAX_LABEL_LENGTH, `cells[${cellIndex}].label`)

    if (label && typeof label !== 'string') {
      return label
    }

    if (!Array.isArray(rawCell.tasks)) {
      return { error: `cells[${cellIndex}].tasks must be an array`, ok: false }
    }

    if (rawCell.tasks.length > MAX_MATRIX_TASKS) {
      return { error: `cells[${cellIndex}].tasks has too many tasks`, ok: false }
    }

    const tasks: HermesUiTaskChip[] = []
    const seenTaskIds = new Set<string>()

    for (const [taskIndex, rawTask] of rawCell.tasks.entries()) {
      if (!isRecord(rawTask)) {
        return { error: `cells[${cellIndex}].tasks[${taskIndex}] must be an object`, ok: false }
      }

      const task = parseTaskChip(rawTask, `cells[${cellIndex}].tasks[${taskIndex}]`)

      if (isParseFailure(task)) {
        return task
      }

      if (seenTaskIds.has(task.id)) {
        return { error: `Duplicate task id: ${task.id}`, ok: false }
      }

      seenTaskIds.add(task.id)
      tasks.push(task)
    }

    cells.push({ label, tasks, x: rawCell.x, y: rawCell.y })
  }

  return {
    artifact: {
      ...base.fields,
      cells,
      type: 'urgency-energy-matrix',
      xAxis: parsed.xAxis,
      yAxis: parsed.yAxis
    },
    ok: true
  }
}

function parseWorkloadBarsArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_WORKLOAD_BARS_KEYS, 'workload-bars')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.bars) || parsed.bars.length === 0) {
    return { error: 'workload-bars bars are required', ok: false }
  }

  if (parsed.bars.length > MAX_WORKLOAD_BARS) {
    return { error: 'workload-bars has too many bars', ok: false }
  }

  const bars: HermesUiWorkloadBar[] = []
  const seenBarIds = new Set<string>()

  for (const [index, rawBar] of parsed.bars.entries()) {
    if (!isRecord(rawBar)) {
      return { error: `bars[${index}] must be an object`, ok: false }
    }

    const unsupportedBar = hasUnsupportedKeys(rawBar, SAFE_WORKLOAD_BAR_KEYS, `bars[${index}]`)

    if (unsupportedBar) {
      return unsupportedBar
    }

    const id = normalizeText(rawBar.id, MAX_ITEM_ID_LENGTH, `bars[${index}].id`)
    const label = normalizeText(rawBar.label, MAX_LABEL_LENGTH, `bars[${index}].label`)

    if (typeof id !== 'string') {
      return id
    }

    if (typeof label !== 'string') {
      return label
    }

    if (!id || !label) {
      return { error: `bars[${index}].id and label are required`, ok: false }
    }

    if (seenBarIds.has(id)) {
      return { error: `Duplicate bar id: ${id}`, ok: false }
    }

    seenBarIds.add(id)

    if (typeof rawBar.value !== 'number' || !Number.isFinite(rawBar.value) || rawBar.value < 0) {
      return { error: `bars[${index}].value must be a non-negative number`, ok: false }
    }

    let max: number | undefined

    if (rawBar.max !== undefined) {
      if (typeof rawBar.max !== 'number' || !Number.isFinite(rawBar.max) || rawBar.max <= 0) {
        return { error: `bars[${index}].max must be a positive number`, ok: false }
      }

      max = rawBar.max
    }

    const tone = rawBar.tone

    if (tone !== undefined && tone !== 'neutral' && tone !== 'warning' && tone !== 'danger' && tone !== 'success') {
      return { error: `bars[${index}].tone is invalid`, ok: false }
    }

    const note = optionalText(rawBar.note, MAX_RATIONALE_LENGTH, `bars[${index}].note`)

    if (note && typeof note !== 'string') {
      return note
    }

    bars.push({ id, label, max, note, tone: tone as HermesUiWorkloadBarTone | undefined, value: rawBar.value })
  }

  return { artifact: { ...base.fields, bars, type: 'workload-bars' }, ok: true }
}

function parseTaskGraphArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  const unsupported = hasUnsupportedKeys(parsed, SAFE_TASK_GRAPH_KEYS, 'task-graph')

  if (unsupported) {
    return unsupported
  }

  const base = parseBaseFields(parsed)

  if (!base.ok) {
    return base
  }

  if (!Array.isArray(parsed.nodes) || parsed.nodes.length === 0) {
    return { error: 'task-graph nodes are required', ok: false }
  }

  if (parsed.nodes.length > MAX_GRAPH_NODES) {
    return { error: 'task-graph has too many nodes', ok: false }
  }

  if (!Array.isArray(parsed.edges)) {
    return { error: 'task-graph edges are required', ok: false }
  }

  if (parsed.edges.length > MAX_GRAPH_EDGES) {
    return { error: 'task-graph has too many edges', ok: false }
  }

  const nodes: HermesUiTaskGraphNode[] = []
  const nodeIds = new Set<string>()

  for (const [index, rawNode] of parsed.nodes.entries()) {
    if (!isRecord(rawNode)) {
      return { error: `nodes[${index}] must be an object`, ok: false }
    }

    const unsupportedNode = hasUnsupportedKeys(rawNode, SAFE_TASK_GRAPH_NODE_KEYS, `nodes[${index}]`)

    if (unsupportedNode) {
      return unsupportedNode
    }

    const id = normalizeText(rawNode.id, MAX_ITEM_ID_LENGTH, `nodes[${index}].id`)
    const label = normalizeText(rawNode.label, MAX_LABEL_LENGTH, `nodes[${index}].label`)

    if (typeof id !== 'string') {
      return id
    }

    if (typeof label !== 'string') {
      return label
    }

    if (!id || !label) {
      return { error: `nodes[${index}].id and label are required`, ok: false }
    }

    if (nodeIds.has(id)) {
      return { error: `Duplicate node id: ${id}`, ok: false }
    }

    nodeIds.add(id)

    const kind = rawNode.kind

    if (
      kind !== undefined &&
      kind !== 'task' &&
      kind !== 'project' &&
      kind !== 'person' &&
      kind !== 'money' &&
      kind !== 'health' &&
      kind !== 'creative' &&
      kind !== 'home' &&
      kind !== 'unknown'
    ) {
      return { error: `nodes[${index}].kind is invalid`, ok: false }
    }

    nodes.push({ id, kind: kind as HermesUiTaskGraphNodeKind | undefined, label })
  }

  const edges: HermesUiTaskGraphEdge[] = []
  const seenEdges = new Set<string>()

  for (const [index, rawEdge] of parsed.edges.entries()) {
    if (!isRecord(rawEdge)) {
      return { error: `edges[${index}] must be an object`, ok: false }
    }

    const unsupportedEdge = hasUnsupportedKeys(rawEdge, SAFE_TASK_GRAPH_EDGE_KEYS, `edges[${index}]`)

    if (unsupportedEdge) {
      return unsupportedEdge
    }

    const source = normalizeText(rawEdge.source, MAX_ITEM_ID_LENGTH, `edges[${index}].source`)
    const target = normalizeText(rawEdge.target, MAX_ITEM_ID_LENGTH, `edges[${index}].target`)

    if (typeof source !== 'string') {
      return source
    }

    if (typeof target !== 'string') {
      return target
    }

    if (!nodeIds.has(source) || !nodeIds.has(target)) {
      return { error: `edges[${index}] must reference existing nodes`, ok: false }
    }

    const edgeKey = `${source}:${target}`

    if (seenEdges.has(edgeKey)) {
      return { error: `Duplicate edge: ${edgeKey}`, ok: false }
    }

    seenEdges.add(edgeKey)

    const label = optionalText(rawEdge.label, MAX_RATIONALE_LENGTH, `edges[${index}].label`)

    if (label && typeof label !== 'string') {
      return label
    }

    edges.push({ label, source, target })
  }

  return { artifact: { ...base.fields, edges, nodes, type: 'task-graph' }, ok: true }
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

const DAILY_SECTION_ORDER: readonly HermesUiDailyPlanningSectionKind[] = ['calendar', 'due-today', 'overdue', 'suggestions', 'more-suggestions']
const DAILY_MAX_CALENDAR_ROWS = 8
const DAILY_MAX_UNRESOLVED_CHOICES = 3
const DAILY_MAX_CONTEXT_ENTRIES = 20
const DAILY_MAX_NESTED_ITEMS = 20
const DAILY_MAX_TEXT = 2000
const DAILY_ARTIFACT_KEYS = new Set(['baselineId', 'date', 'description', 'direction', 'id', 'schemaVersion', 'sections', 'timezone', 'title', 'type'])
const DAILY_SECTION_KEYS = new Set(['id', 'kind', 'rows', 'title'])

const DAILY_ROW_KEYS = new Set([
  'context', 'contextConfidence', 'doneEnough', 'dueDate', 'durationMinutes', 'energy', 'expectedImpact',
  'generalizationProposals', 'id', 'learnedClaims', 'learningConflicts', 'nextStep', 'planPlacement', 'priority',
  'provenance', 'quickActions', 'source', 'sourceMutationAllowed', 'sourceStatus', 'suggestionConfidence',
  'suggestionRationale', 'temporal', 'title'
])

function dailyHasUnsupportedKeys(value: Record<string, unknown>, safeKeys: ReadonlySet<string>): boolean {
  return Object.keys(value).some(key => !safeKeys.has(key))
}

function parseDailyIdentity(value: unknown, field: string): HermesUiArtifactParseFailure | string {
  const identity = normalizeText(value, MAX_ITEM_ID_LENGTH, field)

  if (typeof identity !== 'string') {return identity}

  return identity && /^[A-Za-z0-9][A-Za-z0-9._:@/-]*$/.test(identity)
    ? identity
    : { error: `${field} must be a stable identifier`, ok: false }
}

function parseDailyExternalIdentity(value: unknown, field: string): HermesUiArtifactParseFailure | string {
  const identity = normalizeText(value, MAX_ITEM_ID_LENGTH, field)

  if (typeof identity !== 'string') {return identity}

  return identity || { error: `${field} must be a stable external identifier`, ok: false }
}

function parseDailyRevision(value: unknown, field: string): HermesUiArtifactParseFailure | string {
  const hasControlCharacters = typeof value === 'string' && Array.from(value).some(character => {
    const codePoint = character.codePointAt(0) ?? 0

    return codePoint < 32 || codePoint === 127
  })

  if (typeof value !== 'string' || !value || value.length > MAX_ITEM_ID_LENGTH || hasControlCharacters) {
    return { error: `${field} must be safe bounded revision evidence`, ok: false }
  }

  return value
}

function isRealDate(value: string): boolean {
  if (!DATE_ONLY_RE.test(value)) {return false}
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))

  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

function parseDailyDate(value: unknown, field: string, nullable = false): HermesUiArtifactParseFailure | string | null {
  if (nullable && value === null) {return null}

  if (typeof value !== 'string' || !isRealDate(value)) {return { error: `${field} must be a valid YYYY-MM-DD date`, ok: false }}

  return value
}

function parseDailyTimezone(value: unknown): HermesUiArtifactParseFailure | string {
  if (typeof value !== 'string' || !value || value.length > 120) {return { error: 'timezone must be a valid IANA timezone', ok: false }}

  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value }).format()

    return value
  } catch {
    return { error: 'timezone must be a valid IANA timezone', ok: false }
  }
}

function parseDailyEnum<T extends string>(value: unknown, values: readonly T[], field: string): HermesUiArtifactParseFailure | T {
  return typeof value === 'string' && values.includes(value as T)
    ? value as T
    : { error: `${field} has an invalid value`, ok: false }
}

function parseDailyProvenance(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiDailyPlanningProvenanceEntry[] | undefined {
  if (value === undefined) {return undefined}

  if (!Array.isArray(value) || value.length > DAILY_MAX_NESTED_ITEMS) {return { error: `${field} must be a bounded array`, ok: false }}
  const entries: HermesUiDailyPlanningProvenanceEntry[] = []

  for (const [index, raw] of value.entries()) {
    if (!isRecord(raw) || dailyHasUnsupportedKeys(raw, new Set(['capturedAt', 'id', 'sourceKind', 'sourceRecordId', 'text']))) {return { error: `${field}[${index}] is invalid`, ok: false }}
    const id = parseDailyIdentity(raw.id, `${field}[${index}].id`)
    const sourceKind = parseDailyEnum(raw.sourceKind, ['calendar', 'flowstate', 'notion', 'obsidian', 'hermes'] as const, `${field}[${index}].sourceKind`)
    const capturedAt = raw.capturedAt

    if (isParseFailure(id) || isParseFailure(sourceKind)) {return isParseFailure(id) ? id : sourceKind as HermesUiArtifactParseFailure}

    if (typeof capturedAt !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(capturedAt) || Number.isNaN(Date.parse(capturedAt))) {return { error: `${field}[${index}].capturedAt must be ISO-8601`, ok: false }}
    const sourceRecordId = raw.sourceRecordId === undefined ? undefined : parseDailyExternalIdentity(raw.sourceRecordId, `${field}[${index}].sourceRecordId`)
    const text = optionalText(raw.text, DAILY_MAX_TEXT, `${field}[${index}].text`)

    if (isParseFailure(sourceRecordId) || isParseFailure(text)) {return isParseFailure(sourceRecordId) ? sourceRecordId : text as HermesUiArtifactParseFailure}
    entries.push({ capturedAt, id, sourceKind, sourceRecordId, text })
  }

  return entries
}

function parseDailyScope(value: unknown, field: string): HermesUiArtifactParseFailure | HermesUiDailyPlanningClaimScope {
  if (!isRecord(value) || dailyHasUnsupportedKeys(value, new Set(['kind', 'referenceId']))) {return { error: `${field} is invalid`, ok: false }}
  const kind = parseDailyEnum(value.kind, ['exact-day', 'exact-task', 'series', 'project', 'life-domain', 'workflow', 'global'] as const, `${field}.kind`)

  if (isParseFailure(kind)) {return kind}
  const referenceId = value.referenceId === undefined ? undefined : parseDailyIdentity(value.referenceId, `${field}.referenceId`)

  if (isParseFailure(referenceId)) {return referenceId}

  if (kind !== 'global' && !referenceId) {return { error: `${field}.referenceId is required`, ok: false }}

  return { kind, referenceId }
}

function parseDailyRow(raw: unknown, field: string): HermesUiArtifactParseFailure | HermesUiDailyPlanningRow {
  if (!isRecord(raw) || dailyHasUnsupportedKeys(raw, DAILY_ROW_KEYS)) {return { error: `${field} contains unsupported properties`, ok: false }}
  const id = parseDailyIdentity(raw.id, `${field}.id`)
  const title = normalizeText(raw.title, MAX_LABEL_LENGTH, `${field}.title`)

  if (isParseFailure(id) || typeof title !== 'string' || !title) {return isParseFailure(id) ? id : { error: `${field}.title is required`, ok: false }}

  if (!isRecord(raw.source) || dailyHasUnsupportedKeys(raw.source, new Set(['kind', 'recordId', 'revision']))) {return { error: `${field}.source is invalid`, ok: false }}
  const sourceKind = parseDailyEnum(raw.source.kind, ['calendar', 'flowstate', 'notion', 'obsidian', 'hermes'] as const, `${field}.source.kind`)
  const recordId = parseDailyExternalIdentity(raw.source.recordId, `${field}.source.recordId`)
  const revision = raw.source.revision === undefined ? undefined : parseDailyRevision(raw.source.revision, `${field}.source.revision`)
  const sourceStatus = parseDailyEnum(raw.sourceStatus, ['open', 'in-progress', 'done', 'cancelled', 'unknown'] as const, `${field}.sourceStatus`)
  const planPlacement = parseDailyEnum(raw.planPlacement, ['unassigned', 'core', 'optional', 'not-today'] as const, `${field}.planPlacement`)
  const contextConfidence = parseDailyEnum(raw.contextConfidence, ['verified', 'partial', 'unknown', 'conflict'] as const, `${field}.contextConfidence`)

  for (const parsed of [sourceKind, recordId, revision, sourceStatus, planPlacement, contextConfidence]) {if (isParseFailure(parsed)) {return parsed}}

  if (typeof raw.sourceMutationAllowed !== 'boolean') {return { error: `${field}.sourceMutationAllowed must be boolean`, ok: false }}

  if (sourceKind === 'calendar' && raw.sourceMutationAllowed) {return { error: `${field} Calendar source mutation must be false`, ok: false }}

  const priority = parseTaskPriority(raw.priority)

  if (isParseFailure(priority)) {return priority}
  const dueDate = raw.dueDate === undefined ? undefined : parseDailyDate(raw.dueDate, `${field}.dueDate`, true)

  if (isParseFailure(dueDate)) {return dueDate}

  if (raw.durationMinutes !== undefined && (!Number.isInteger(raw.durationMinutes) || (raw.durationMinutes as number) < 1 || (raw.durationMinutes as number) > 1440)) {return { error: `${field}.durationMinutes is invalid`, ok: false }}
  const energy = raw.energy === undefined ? undefined : parseDailyEnum(raw.energy, ['low', 'medium', 'high', 'unknown'] as const, `${field}.energy`)
  const suggestionConfidence = raw.suggestionConfidence === undefined ? undefined : parseDailyEnum(raw.suggestionConfidence, ['low', 'medium', 'high'] as const, `${field}.suggestionConfidence`)

  if (isParseFailure(energy) || isParseFailure(suggestionConfidence)) {return isParseFailure(energy) ? energy : suggestionConfidence as HermesUiArtifactParseFailure}

  let temporal: HermesUiDailyPlanningTemporal | undefined

  if (raw.temporal !== undefined) {
    if (!isRecord(raw.temporal) || dailyHasUnsupportedKeys(raw.temporal, new Set(['allDay', 'endTime', 'startTime']))) {return { error: `${field}.temporal is invalid`, ok: false }}
    const { allDay, endTime, startTime } = raw.temporal

    if (allDay !== undefined && typeof allDay !== 'boolean') {return { error: `${field}.temporal.allDay must be boolean`, ok: false }}

    if (startTime !== undefined && (typeof startTime !== 'string' || !TIME_ONLY_RE.test(startTime))) {return { error: `${field}.temporal.startTime is invalid`, ok: false }}

    if (endTime !== undefined && (typeof endTime !== 'string' || !TIME_ONLY_RE.test(endTime))) {return { error: `${field}.temporal.endTime is invalid`, ok: false }}
    temporal = { allDay, endTime: endTime as string | undefined, startTime: startTime as string | undefined }
  }

  let context: HermesUiDailyPlanningContextEntry[] | undefined

  if (raw.context !== undefined) {
    if (!Array.isArray(raw.context) || raw.context.length > DAILY_MAX_CONTEXT_ENTRIES) {return { error: `${field}.context must be a bounded array`, ok: false }}
    context = []

    for (const [index, entry] of raw.context.entries()) {
      if (!isRecord(entry) || dailyHasUnsupportedKeys(entry, new Set(['id', 'text']))) {return { error: `${field}.context[${index}] is invalid`, ok: false }}
      const entryId = parseDailyIdentity(entry.id, `${field}.context[${index}].id`)
      const entryText = normalizeText(entry.text, DAILY_MAX_TEXT, `${field}.context[${index}].text`)

      if (isParseFailure(entryId) || typeof entryText !== 'string' || !entryText) {return isParseFailure(entryId) ? entryId : { error: `${field}.context[${index}].text is required`, ok: false }}
      context.push({ id: entryId, text: entryText })
    }
  }

  const provenance = parseDailyProvenance(raw.provenance, `${field}.provenance`)

  if (isParseFailure(provenance)) {return provenance}
  const learnedClaims: HermesUiDailyPlanningLearnedClaim[] = []

  if (raw.learnedClaims !== undefined) {
    if (!Array.isArray(raw.learnedClaims) || raw.learnedClaims.length > DAILY_MAX_NESTED_ITEMS) {return { error: `${field}.learnedClaims must be a bounded array`, ok: false }}

    for (const [index, claim] of raw.learnedClaims.entries()) {
      if (!isRecord(claim) || dailyHasUnsupportedKeys(claim, new Set(['id', 'provenance', 'scope', 'state', 'supersedesClaimId', 'text']))) {return { error: `${field}.learnedClaims[${index}] is invalid`, ok: false }}
      const claimId = parseDailyIdentity(claim.id, `${field}.learnedClaims[${index}].id`)
      const text = normalizeText(claim.text, DAILY_MAX_TEXT, `${field}.learnedClaims[${index}].text`)
      const scope = parseDailyScope(claim.scope, `${field}.learnedClaims[${index}].scope`)
      const claimProvenance = parseDailyProvenance(claim.provenance, `${field}.learnedClaims[${index}].provenance`)
      const state = parseDailyEnum(claim.state, ['active', 'superseded'] as const, `${field}.learnedClaims[${index}].state`)
      const supersedesClaimId = claim.supersedesClaimId === undefined ? undefined : parseDailyIdentity(claim.supersedesClaimId, `${field}.learnedClaims[${index}].supersedesClaimId`)

      for (const parsed of [claimId, scope, claimProvenance, state, supersedesClaimId]) {if (isParseFailure(parsed)) {return parsed}}
      const validProvenance = claimProvenance as HermesUiDailyPlanningProvenanceEntry[] | undefined

      if (typeof text !== 'string' || !text || !validProvenance?.length) {return { error: `${field}.learnedClaims[${index}] is incomplete`, ok: false }}
      learnedClaims.push({
        id: claimId as string,
        provenance: validProvenance,
        scope: scope as HermesUiDailyPlanningClaimScope,
        state: state as 'active' | 'superseded',
        supersedesClaimId: supersedesClaimId as string | undefined,
        text
      })
    }
  }

  const learningConflicts: HermesUiDailyPlanningLearningConflict[] = []

  if (raw.learningConflicts !== undefined) {
    if (!Array.isArray(raw.learningConflicts) || raw.learningConflicts.length > DAILY_MAX_NESTED_ITEMS) {return { error: `${field}.learningConflicts must be a bounded array`, ok: false }}

    for (const [index, conflict] of raw.learningConflicts.entries()) {
      if (!isRecord(conflict) || dailyHasUnsupportedKeys(conflict, new Set(['id', 'newClaimId', 'priorClaimId']))) {return { error: `${field}.learningConflicts[${index}] is invalid`, ok: false }}
      const conflictId = parseDailyIdentity(conflict.id, `${field}.learningConflicts[${index}].id`)
      const newClaimId = parseDailyIdentity(conflict.newClaimId, `${field}.learningConflicts[${index}].newClaimId`)
      const priorClaimId = parseDailyIdentity(conflict.priorClaimId, `${field}.learningConflicts[${index}].priorClaimId`)

      for (const parsed of [conflictId, newClaimId, priorClaimId]) {if (isParseFailure(parsed)) {return parsed}}
      learningConflicts.push({ id: conflictId as string, newClaimId: newClaimId as string, priorClaimId: priorClaimId as string })
    }
  }

  const generalizationProposals: HermesUiDailyPlanningGeneralizationProposal[] = []

  if (raw.generalizationProposals !== undefined) {
    if (!Array.isArray(raw.generalizationProposals) || raw.generalizationProposals.length > DAILY_MAX_NESTED_ITEMS) {return { error: `${field}.generalizationProposals must be a bounded array`, ok: false }}

    for (const [index, proposal] of raw.generalizationProposals.entries()) {
      if (!isRecord(proposal) || dailyHasUnsupportedKeys(proposal, new Set(['claimId', 'id', 'rationale', 'scope']))) {return { error: `${field}.generalizationProposals[${index}] is invalid`, ok: false }}
      const proposalId = parseDailyIdentity(proposal.id, `${field}.generalizationProposals[${index}].id`)
      const claimId = parseDailyIdentity(proposal.claimId, `${field}.generalizationProposals[${index}].claimId`)
      const scope = parseDailyScope(proposal.scope, `${field}.generalizationProposals[${index}].scope`)
      const rationale = normalizeText(proposal.rationale, MAX_RATIONALE_LENGTH, `${field}.generalizationProposals[${index}].rationale`)

      for (const parsed of [proposalId, claimId, scope]) {if (isParseFailure(parsed)) {return parsed}}

      if (typeof rationale !== 'string' || !rationale) {return { error: `${field}.generalizationProposals[${index}].rationale is required`, ok: false }}
      generalizationProposals.push({ claimId: claimId as string, id: proposalId as string, rationale, scope: scope as HermesUiDailyPlanningClaimScope })
    }
  }

  let quickActions: HermesUiDailyPlanningQuickAction[] | undefined

  if (raw.quickActions !== undefined) {
    if (!Array.isArray(raw.quickActions) || raw.quickActions.length > 6) {return { error: `${field}.quickActions must be a bounded array`, ok: false }}
    quickActions = []

    for (const [index, action] of raw.quickActions.entries()) {
      if (!isRecord(action) || dailyHasUnsupportedKeys(action, new Set(['id', 'label']))) {return { error: `${field}.quickActions[${index}] is invalid`, ok: false }}
      const actionId = parseDailyEnum(action.id, ['mark-done', 'start', 'defer', 'open-source', 'add-to-plan', 'remove-from-plan'] as const, `${field}.quickActions[${index}].id`)
      const label = normalizeText(action.label, MAX_ACTION_LABEL_LENGTH, `${field}.quickActions[${index}].label`)

      if (isParseFailure(actionId) || typeof label !== 'string' || !label) {return isParseFailure(actionId) ? actionId : { error: `${field}.quickActions[${index}].label is required`, ok: false }}
      quickActions.push({ id: actionId, label })
    }
  }

  const optionalFields = ['doneEnough', 'expectedImpact', 'nextStep', 'suggestionRationale'] as const
  const texts: Partial<Record<typeof optionalFields[number], string>> = {}

  for (const key of optionalFields) {
    const text = optionalText(raw[key], DAILY_MAX_TEXT, `${field}.${key}`)

    if (isParseFailure(text)) {return text}

    if (text) {texts[key] = text}
  }

  return {
    ...texts, context, contextConfidence: contextConfidence as HermesUiDailyPlanningContextConfidence,
    dueDate: dueDate as string | null | undefined, durationMinutes: raw.durationMinutes as number | undefined,
    energy: energy as HermesUiDailyPlanningEnergy | undefined,
    generalizationProposals: generalizationProposals.length ? generalizationProposals : undefined, id: id as string,
    learnedClaims: learnedClaims.length ? learnedClaims : undefined,
    learningConflicts: learningConflicts.length ? learningConflicts : undefined,
    planPlacement: planPlacement as HermesUiDailyPlanningPlanPlacement, priority,
    provenance, quickActions,
    source: {
      kind: sourceKind as HermesUiDailyPlanningSourceKind,
      recordId: recordId as string,
      revision: revision as string | undefined
    },
    sourceMutationAllowed: raw.sourceMutationAllowed,
    sourceStatus: sourceStatus as HermesUiDailyPlanningSourceStatus,
    suggestionConfidence: suggestionConfidence as HermesUiConfidence | undefined, temporal, title
  }
}

function parseDailyPlanningListArtifact(parsed: Record<string, unknown>): HermesUiArtifactParseResult {
  if (dailyHasUnsupportedKeys(parsed, DAILY_ARTIFACT_KEYS)) {return { error: 'daily-planning-list contains unsupported properties', ok: false }}

  if (parsed.schemaVersion !== 1) {return { error: 'schemaVersion must be 1', ok: false }}
  const id = parseDailyIdentity(parsed.id, 'id')
  const baselineId = parseDailyIdentity(parsed.baselineId, 'baselineId')
  const title = normalizeText(parsed.title, MAX_TITLE_LENGTH, 'title')
  const description = optionalText(parsed.description, MAX_DESCRIPTION_LENGTH, 'description')
  const date = parseDailyDate(parsed.date, 'date')
  const timezone = parseDailyTimezone(parsed.timezone)
  const direction = normalizeDirection(parsed.direction)

  for (const value of [id, baselineId, description, date, timezone, direction]) {if (isParseFailure(value)) {return value}}

  if (typeof title !== 'string' || !title) {return { error: 'title is required', ok: false }}

  if (!Array.isArray(parsed.sections) || parsed.sections.length !== 5) {return { error: 'daily-planning-list requires exactly five sections', ok: false }}
  const seenSectionIds = new Set<string>()
  const seenRowIds = new Set<string>()
  const sections: HermesUiDailyPlanningSection[] = []
  let unresolvedChoiceCount = 0

  for (const [index, section] of parsed.sections.entries()) {
    if (!isRecord(section) || dailyHasUnsupportedKeys(section, DAILY_SECTION_KEYS)) {return { error: `sections[${index}] contains unsupported properties`, ok: false }}
    const sectionId = parseDailyIdentity(section.id, `sections[${index}].id`)
    const sectionTitle = normalizeText(section.title, MAX_TITLE_LENGTH, `sections[${index}].title`)

    if (isParseFailure(sectionId) || typeof sectionTitle !== 'string' || !sectionTitle) {return isParseFailure(sectionId) ? sectionId : { error: `sections[${index}].title is required`, ok: false }}

    if (seenSectionIds.has(sectionId)) {return { error: `Duplicate section id: ${sectionId}`, ok: false }}
    seenSectionIds.add(sectionId)

    if (section.kind !== DAILY_SECTION_ORDER[index]) {return { error: `sections[${index}].kind must be ${DAILY_SECTION_ORDER[index]}`, ok: false }}

    if (!Array.isArray(section.rows)) {return { error: `sections[${index}].rows must be an array`, ok: false }}

    if (section.kind === 'suggestions' && section.rows.length > 3) {return { error: 'suggestions may contain at most three rows', ok: false }}

    if (section.kind === 'calendar' && section.rows.length > DAILY_MAX_CALENDAR_ROWS) {return { error: 'calendar may contain at most eight relevant constraints', ok: false }}

    if (section.kind !== 'calendar') {unresolvedChoiceCount += section.rows.length}

    if (unresolvedChoiceCount > DAILY_MAX_UNRESOLVED_CHOICES) {return { error: 'daily-planning-list may contain at most three unresolved choices', ok: false }}

    const rows: HermesUiDailyPlanningRow[] = []

    for (const [rowIndex, rawRow] of section.rows.entries()) {
      const row = parseDailyRow(rawRow, `sections[${index}].rows[${rowIndex}]`)

      if (isParseFailure(row)) {return row}

      if (seenRowIds.has(row.id)) {return { error: `Duplicate row id: ${row.id}`, ok: false }}
      seenRowIds.add(row.id)

      if (section.kind === 'suggestions' && (!row.suggestionRationale || !row.suggestionConfidence || !row.expectedImpact)) {return { error: 'suggestions rows require rationale, confidence, and expected impact', ok: false }}

      if (section.kind === 'calendar' && row.source.kind !== 'calendar') {return { error: 'calendar rows must use Calendar source', ok: false }}
      rows.push(row)
    }

    sections.push({ id: sectionId as string, kind: section.kind as HermesUiDailyPlanningSectionKind, rows, title: sectionTitle })
  }

  return {
    artifact: {
      baselineId: baselineId as string,
      date: date as string,
      description: description as string | undefined,
      direction: direction as HermesUiDailyPlanningListArtifact['direction'],
      id: id as string,
      schemaVersion: 1,
      sections: sections as HermesUiDailyPlanningListArtifact['sections'],
      timezone: timezone as string,
      title,
      type: 'daily-planning-list'
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

  if (parsed.type === 'daily-planning-list') {
    if (source.length > 2 * 1024 * 1024) {return { error: 'daily-planning-list is too large', ok: false }}

    return parseDailyPlanningListArtifact(parsed)
  }

  if (parsed.type === 'checklist') {
    return parseChecklistArtifact(parsed)
  }

  if (parsed.type === 'questionnaire') {
    return parseQuestionnaireArtifact(parsed)
  }

  if (parsed.type === 'form') {
    return parseFormArtifact(parsed)
  }

  if (parsed.type === 'task-triage') {
    return parseTaskTriageArtifact(parsed)
  }

  if (parsed.type === 'flowstate-task-batch') {
    return parseFlowStateBatchArtifact(parsed)
  }

  if (parsed.type === 'flowstate-planning-session') {
    return parseFlowStatePlanningSessionArtifact(parsed)
  }

  if (parsed.type === 'flowstate-next-block') {
    return parseFlowStateNextBlockArtifact(parsed)
  }

  if (parsed.type === 'planning-funnel') {
    return parsePlanningFunnelArtifact(parsed)
  }

  if (parsed.type === 'task-breakdown') {
    return parseTaskBreakdownArtifact(parsed)
  }

  if (parsed.type === 'task-context') {
    return parseTaskContextArtifact(parsed)
  }

  if (parsed.type === 'task-table') {
    return parseTaskTableArtifact(parsed)
  }

  if (parsed.type === 'mini-kanban') {
    return parseMiniKanbanArtifact(parsed)
  }

  if (parsed.type === 'day-timeline') {
    return parseDayTimelineArtifact(parsed)
  }

  if (parsed.type === 'mutation-preview') {
    return parseMutationPreviewArtifact(parsed)
  }

  if (parsed.type === 'urgency-energy-matrix') {
    return parseUrgencyEnergyMatrixArtifact(parsed)
  }

  if (parsed.type === 'workload-bars') {
    return parseWorkloadBarsArtifact(parsed)
  }

  if (parsed.type === 'task-graph') {
    return parseTaskGraphArtifact(parsed)
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

export type HermesUiDailyPlanningConflictDecision = 'keep-prior' | 'activate-new' | 'keep-both'

export interface HermesUiDailyPlanningDraftRow {
  approvedGeneralizationIds: string[]
  conflictResolutions: Record<string, HermesUiDailyPlanningConflictDecision>
  context: HermesUiDailyPlanningContextEntry[]
  contextConfidence: HermesUiDailyPlanningContextConfidence
  doneEnough?: string
  dueDate?: string | null
  durationMinutes?: number
  energy?: HermesUiDailyPlanningEnergy
  nextStep?: string
  planPlacement: HermesUiDailyPlanningPlanPlacement
  priority?: HermesUiTaskPriority
  proposedLearningClaims: HermesUiDailyPlanningLearnedClaim[]
  sourceStatus: HermesUiDailyPlanningSourceStatus
  suggestionSelected: boolean
}

export interface HermesUiDailyPlanningDraft {
  rows: Record<string, HermesUiDailyPlanningDraftRow>
}

export interface HermesUiDailyPlanningFieldDiff<T = unknown> {
  after: T
  before: T
}

export interface HermesUiDailyPlanningSourceChange {
  changes: Partial<Record<'dueDate' | 'priority' | 'sourceStatus', HermesUiDailyPlanningFieldDiff>>
  rowId: string
  source: HermesUiDailyPlanningSourceIdentity
}

export interface HermesUiDailyPlanningDayPlanChange {
  changes: Partial<Record<'context' | 'contextConfidence' | 'doneEnough' | 'durationMinutes' | 'energy' | 'nextStep' | 'planPlacement' | 'suggestionSelected', HermesUiDailyPlanningFieldDiff>>
  rowId: string
  source: HermesUiDailyPlanningSourceIdentity
}

export interface HermesUiDailyPlanningLearningChange {
  claims: HermesUiDailyPlanningLearnedClaim[]
  rowId: string
  source: HermesUiDailyPlanningSourceIdentity
}

export interface HermesUiDailyPlanningConflictResolution {
  conflictId: string
  decision: HermesUiDailyPlanningConflictDecision
  rowId: string
}

export interface HermesUiDailyPlanningApprovedGeneralization {
  proposal: HermesUiDailyPlanningGeneralizationProposal
  rowId: string
}

export interface HermesUiDailyPlanningListResponse {
  action: 'request-preview'
  artifactId: string
  baselineId: string
  conflictResolutions: HermesUiDailyPlanningConflictResolution[]
  continuationInstruction: string
  dayPlanChanges: HermesUiDailyPlanningDayPlanChange[]
  generalizationProposals: HermesUiDailyPlanningApprovedGeneralization[]
  idempotencyKey: string
  learningChanges: HermesUiDailyPlanningLearningChange[]
  schemaVersion: 1
  sourceChanges: HermesUiDailyPlanningSourceChange[]
  sourceRevisions: Array<{ revision: string; source: HermesUiDailyPlanningSourceIdentity }>
  type: 'daily-planning-list-response'
}

function dailyPlanningRows(artifact: HermesUiDailyPlanningListArtifact): Array<{ row: HermesUiDailyPlanningRow; suggestion: boolean }> {
  return artifact.sections.flatMap(section => section.rows.map(row => ({
    row,
    suggestion: section.kind === 'suggestions' || section.kind === 'more-suggestions'
  })))
}

export function createHermesUiDailyPlanningDraft(artifact: HermesUiDailyPlanningListArtifact): HermesUiDailyPlanningDraft {
  return {
    rows: Object.fromEntries(dailyPlanningRows(artifact).map(({ row, suggestion }) => [row.id, {
      approvedGeneralizationIds: [],
      conflictResolutions: {},
      context: row.context ? structuredClone(row.context) : [],
      contextConfidence: row.contextConfidence,
      doneEnough: row.doneEnough,
      dueDate: row.dueDate,
      durationMinutes: row.durationMinutes,
      energy: row.energy,
      nextStep: row.nextStep,
      planPlacement: row.planPlacement,
      priority: row.priority,
      proposedLearningClaims: [],
      sourceStatus: row.sourceStatus,
      suggestionSelected: !suggestion
    }]))
  }
}

function addDailyDiff(
  target: Record<string, HermesUiDailyPlanningFieldDiff>,
  field: string,
  before: unknown,
  after: unknown
): void {
  if (stableStringify(before) !== stableStringify(after)) {target[field] = { after: after ?? null, before: before ?? null }}
}

export function buildHermesUiDailyPlanningListResponse(
  artifact: HermesUiDailyPlanningListArtifact,
  draft: HermesUiDailyPlanningDraft
): HermesUiDailyPlanningListResponse {
  const sourceChanges: HermesUiDailyPlanningSourceChange[] = []
  const dayPlanChanges: HermesUiDailyPlanningDayPlanChange[] = []
  const learningChanges: HermesUiDailyPlanningLearningChange[] = []
  const conflictResolutions: HermesUiDailyPlanningConflictResolution[] = []
  const generalizationProposals: HermesUiDailyPlanningApprovedGeneralization[] = []

  for (const { row, suggestion } of dailyPlanningRows(artifact)) {
    const current = draft.rows[row.id]

    if (!current) {throw new Error(`Missing daily planning draft row: ${row.id}`)}
    const sourceDiffs: Record<string, HermesUiDailyPlanningFieldDiff> = {}
    addDailyDiff(sourceDiffs, 'sourceStatus', row.sourceStatus, current.sourceStatus)
    addDailyDiff(sourceDiffs, 'priority', row.priority, current.priority)
    addDailyDiff(sourceDiffs, 'dueDate', row.dueDate, current.dueDate)

    if (Object.keys(sourceDiffs).length) {
      if (!row.sourceMutationAllowed || row.source.kind === 'calendar') {throw new Error(`Source row ${row.id} is read-only`)}
      sourceChanges.push({ changes: sourceDiffs, rowId: row.id, source: row.source })
    }

    const dayPlanDiffs: Record<string, HermesUiDailyPlanningFieldDiff> = {}
    addDailyDiff(dayPlanDiffs, 'planPlacement', row.planPlacement, current.planPlacement)
    addDailyDiff(dayPlanDiffs, 'contextConfidence', row.contextConfidence, current.contextConfidence)
    addDailyDiff(dayPlanDiffs, 'durationMinutes', row.durationMinutes, current.durationMinutes)
    addDailyDiff(dayPlanDiffs, 'energy', row.energy, current.energy)
    addDailyDiff(dayPlanDiffs, 'context', row.context ?? [], current.context)
    addDailyDiff(dayPlanDiffs, 'nextStep', row.nextStep, current.nextStep)
    addDailyDiff(dayPlanDiffs, 'doneEnough', row.doneEnough, current.doneEnough)

    if (suggestion) {addDailyDiff(dayPlanDiffs, 'suggestionSelected', false, current.suggestionSelected)}

    if (Object.keys(dayPlanDiffs).length) {dayPlanChanges.push({ changes: dayPlanDiffs, rowId: row.id, source: row.source })}

    if (current.proposedLearningClaims.length) {
      for (const conflict of row.learningConflicts ?? []) {
        const decision = current.conflictResolutions[conflict.id]

        if (!decision) {throw new Error(`Learning conflict ${conflict.id} requires a resolution`)}
        conflictResolutions.push({ conflictId: conflict.id, decision, rowId: row.id })
      }

      learningChanges.push({ claims: structuredClone(current.proposedLearningClaims), rowId: row.id, source: row.source })
    }

    for (const proposalId of [...new Set(current.approvedGeneralizationIds)].sort()) {
      const proposal = row.generalizationProposals?.find(candidate => candidate.id === proposalId)

      if (!proposal) {throw new Error(`Unknown generalization proposal: ${proposalId}`)}
      generalizationProposals.push({ proposal, rowId: row.id })
    }
  }

  const sourceRevisions = dailyPlanningRows(artifact)
    .map(({ row }) => row.source)
    .filter((source): source is HermesUiDailyPlanningSourceIdentity & { revision: string } => Boolean(source.revision))
    .sort((left, right) => `${left.kind}:${left.recordId}`.localeCompare(`${right.kind}:${right.recordId}`))
    .map(source => ({ revision: source.revision, source }))

  const responseCore = {
    action: 'request-preview' as const,
    artifactId: artifact.id,
    baselineId: artifact.baselineId,
    conflictResolutions,
    continuationInstruction: 'Validate IDs and revisions, re-read affected records, then produce typed source previews and a separate learning preview with append-only claims. Do not apply any change until the user explicitly approves each preview.',
    dayPlanChanges,
    generalizationProposals,
    learningChanges,
    schemaVersion: 1 as const,
    sourceChanges,
    sourceRevisions,
    type: 'daily-planning-list-response' as const
  }

  return {
    ...responseCore,
    idempotencyKey: `daily-planning-list:${stableHash(stableStringify(responseCore))}`
  }
}

export type HermesUiFormValue = boolean | string | string[]

export interface HermesUiFormResponse {
  actionId: 'submit'
  artifactId: string
  continuationInstruction: string
  idempotencyKey: string
  schemaVersion: 1
  type: 'form-response'
  values: Record<string, HermesUiFormValue>
}

export function buildHermesUiFormResponse(
  artifact: HermesUiFormArtifact,
  sourceValues: Readonly<Record<string, HermesUiFormValue>>
): HermesUiFormResponse {
  const artifactId = artifact.id || stableArtifactStorageKey(artifact)

  const values = Object.fromEntries(
    artifact.fields.map(field => [
      field.id,
      sourceValues[field.id] ?? (field.type === 'multi-choice' ? [] : field.type === 'boolean' ? false : '')
    ])
  )

  const idempotencyKey = `form:${stableHash(`${artifactId}:${stableStringify(values)}`)}`

  return {
    actionId: 'submit',
    artifactId,
    continuationInstruction:
      'Continue the active workflow after processing this response. Supporting tool results are not completion; stop only when the workflow is complete or another user answer is required.',
    idempotencyKey,
    schemaVersion: 1,
    type: 'form-response',
    values
  }
}

export function stableArtifactStorageKey(artifact: HermesUiArtifact): string {
  const identity = artifact.id ? normalizeIdentity(artifact.id) : ''
  const suffix = identity || stableHash(stableStringify(artifact))

  return `hermes-ui:${artifact.type}:${suffix}`
}
