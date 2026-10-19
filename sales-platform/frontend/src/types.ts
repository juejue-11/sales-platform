export type GateStatus = 'candidate' | 'review' | 'excluded' | 'expired' | 'closed' | 'signal' | 'duplicate'
export type AiStatus = 'not_configured' | 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'stale' | 'skipped'
export type AiDecision = 'recommend' | 'consider' | 'verify' | 'not_recommended' | null
export type DecisionStatus = 'new' | 'following' | 'verify' | 'closed'
export type AiEngine = 'pi_sdk' | 'single_call'
export type AgentRunStatus = 'running' | 'succeeded' | 'failed'
export type AgentStepStage = 'started' | 'model' | 'tool_start' | 'tool_end' | 'completed'
export type CompanyMatchStatus = 'not_evaluated' | 'partial' | 'assessed'

/** Semantic metadata for the judgment that is currently being displayed. */
export interface AnalysisMeta {
  semantic_version: string
  decision_scope: 'market'
  company_match: CompanyMatchStatus
}

export interface AgentRunStep {
  stage: AgentStepStage
  label: string
  tool?: string
  turn?: number
  at: string
}

export interface AgentRunUsage {
  model_calls: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
}

export interface AgentRunLimits {
  max_turns: number
  max_tool_calls: number
  timeout_ms: number
  max_output_tokens: number
}

export type InvestigationStatus = 'not_started' | 'running' | 'completed' | 'limited' | 'failed' | 'not_performed'

export interface InvestigationEvent {
  at: string
  tool: string
  purpose: string
  query?: string
  status: string
  sources: string[]
  message?: string
}

export type ResearchSourceKind = 'page' | 'document'
export type ResearchSourceStatus = 'candidate' | 'read' | 'failed' | 'unsupported' | 'auth_required' | 'unreadable'
export type ResearchSourceRelation = 'unconfirmed' | 'same_procurement' | 'related_project' | 'buyer_background' | 'unrelated' | 'conflict'
export type ResearchRelationStatus = 'unconfirmed' | 'verified' | 'rejected'

export interface ResearchSource {
  source_ref: string
  source_version?: string
  title: string
  url: string
  published_at?: string | null
  fetched_at?: string | null
  kind: ResearchSourceKind
  status: ResearchSourceStatus
  relation: ResearchSourceRelation
  relation_status: ResearchRelationStatus
  pages?: number
  parent_source_ref?: string | null
  error?: string | null
}

export interface Investigation {
  policy_version: string
  status: InvestigationStatus
  stop_reason?: string
  events: InvestigationEvent[]
  sources: ResearchSource[]
  unresolved: string[]
}

export interface ResearchSourceVersionResponse {
  source: ResearchSource
  text: string
  page?: number | null
  offset: number
  total_chars: number
  has_more: boolean
}

/** Evidence attached to a user-facing, Agent-selected report block. */
export type AgentReportBlockEvidence = Evidence

export interface AgentReportParagraphBlock {
  type: 'paragraph'
  text: string
  evidence: AgentReportBlockEvidence[]
}

export interface AgentReportListItem {
  text: string
  evidence: AgentReportBlockEvidence[]
}

export interface AgentReportListBlock {
  type: 'list'
  items: AgentReportListItem[]
}

export interface AgentReportTableRow {
  cells: string[]
  evidence: AgentReportBlockEvidence[]
}

export interface AgentReportTableBlock {
  type: 'table'
  columns: string[]
  rows: AgentReportTableRow[]
}

export type AgentReportBlock = AgentReportParagraphBlock | AgentReportListBlock | AgentReportTableBlock

export interface AgentReportSection {
  id: string
  title: string
  blocks: AgentReportBlock[]
}

export type AgentOpportunityKind = 'explicit_demand' | 'potential_opportunity'

export interface AgentOpportunity {
  id: string
  kind: AgentOpportunityKind
  title: string
  description: string
  basis: string
  uncertainties: string[]
  next_actions: string[]
  evidence: AgentReportBlockEvidence[]
}

export type AgentReportGapStatus = 'not_stated' | 'not_read' | 'read_failed' | 'not_applicable'

export interface AgentReportGap {
  id: string
  description: string
  status: AgentReportGapStatus
  impact: string
  next_action: string
  worth_researching: boolean
}

/** The current Agent report is intentionally open-ended: sections and blocks are selected per project. */
export interface AgentReport {
  version: 1
  sections: AgentReportSection[]
  opportunities: AgentOpportunity[]
  gaps: AgentReportGap[]
}

export interface AgentRun {
  id: number
  job_id: number | null
  engine: 'pi_sdk'
  status: AgentRunStatus
  started_at: string
  finished_at: string | null
  error: string | null
  steps: AgentRunStep[]
  usage: AgentRunUsage
  limits: AgentRunLimits
  /** Optional while older API responses are still in circulation. */
  investigation?: Investigation | null
}

export interface Decision {
  status: DecisionStatus
  owner: string
  next_action: string
  note: string
  selected_option_id: string | null
  judgment_id?: number | null
  updated_at: string | null
}

/** Procurement facts surfaced on the opportunity desk without opening a detail page. */
export interface ProjectBriefing {
  record_id: number | null
  scope_excerpt: string
  delivery_excerpt: string
  qualification_excerpt: string
  contact_excerpt: string
  original_url: string | null
}

export type ProcurementStatus = 'not_checked' | 'partial' | 'complete' | 'unavailable'
export type ProcurementDocumentStatus = 'parsed' | 'unsupported' | 'auth_required' | 'failed' | 'unreadable'
export type ProcurementSourceKind = 'announcement' | 'attachment'
export type ProcurementAttachmentStatus = 'not_checked' | 'none_public' | 'restricted' | 'available' | 'failed'
export type ProcurementFactField = 'scope' | 'delivery_period' | 'delivery_location' | 'qualification' | 'contact'
export type ProcurementFactStatus = 'extracted' | 'uncertain' | 'conflict'

/** A bounded, immutable citation supplied by the procurement API. */
export interface ProcurementFactSource {
  source_kind: ProcurementSourceKind
  record_id: number
  document_id?: number | string
  page?: number | null
  quote: string
  sha256?: string | null
}

export interface ProcurementFact {
  id: string
  field: ProcurementFactField
  value: string
  status: ProcurementFactStatus
  note?: string | null
  sources: ProcurementFactSource[]
}

export interface ProcurementItem {
  name: string
  specification: string
  material: string
  quantity: string
  unit: string
  delivery_period: string
  delivery_location: string
  code?: string
  standard?: string
  note?: string
  /** Announcement sourced rows have no attachment id and use page 0. */
  source_kind?: ProcurementSourceKind | null
  source_document_id?: number | string | null
  record_id: number
  page?: number | null
  quote?: string | null
  status?: ProcurementFactStatus
  sources?: ProcurementFactSource[]
}

export interface ProcurementDocument {
  id: number | string
  record_id: number
  name: string
  url: string
  status: ProcurementDocumentStatus
  pages: number | null
  sha256: string | null
  fetched_at: string | null
  error?: string | null
  mime_type?: string | null
  byte_size?: number | null
}

export interface ProcurementDocumentSnapshot {
  id?: number | string
  record_id?: number
  name?: string
  url?: string
  status?: ProcurementDocumentStatus
  pages?: number | null
  sha256?: string | null
  fetched_at?: string | null
  mime_type?: string | null
  byte_size?: number | null
  error?: string | null
  text?: string | null
  page_texts?: Array<{ page?: number | null; text?: string | null }>
}

export interface ProcurementData {
  status: ProcurementStatus
  scope_summary: string
  items: ProcurementItem[]
  delivery_period: string
  delivery_location: string
  missing_fields: string[]
  documents: ProcurementDocument[]
  updated_at: string | null
  /** Structured facts are optional while older API responses are still in circulation. */
  facts?: ProcurementFact[]
  /** Optional while older API responses are still in circulation. */
  attachment_status?: ProcurementAttachmentStatus | null
  /** Human-readable access caveat, for example a registration or purchase requirement. */
  attachment_note?: string | null
}

/**
 * The canonical, versioned procurement reading shared by list cards and the
 * project detail.  The API deliberately keeps this separate from the older
 * `procurement` extraction so a stale or failed reading cannot be presented as
 * a completed business summary.
 */
export type ProcurementReadingStatus = 'pending' | 'complete' | 'partial' | 'failed' | 'not_configured'
export type ProcurementDemandType = 'confirmed' | 'lead' | 'unknown'
export type ProcurementMilestoneKind = 'document_access' | 'registration' | 'question' | 'submission' | 'opening' | 'other'

export interface ProcurementReadingSource {
  source_kind: ProcurementSourceKind
  record_id: number
  document_id?: number | string
  page?: number | null
  quote: string
  sha256?: string | null
}

export interface ProcurementReadingRequirement {
  id: string
  text: string
  explanation?: string | null
  sources: ProcurementReadingSource[]
  lot?: string | number | null
  status?: string | null
}

export interface ProcurementReadingSection {
  id: string
  title: string
  requirements: ProcurementReadingRequirement[]
}

export interface ProcurementReadingMilestone {
  id: string
  kind: ProcurementMilestoneKind
  label: string
  at?: string | null
  sources: ProcurementReadingSource[]
}

export interface ProcurementReadingCoverage {
  [key: string]: unknown
}

export interface ProcurementReading {
  version: 1
  material_version: string | number | null
  status: ProcurementReadingStatus
  stale: boolean
  summary: string | null
  demand_type: ProcurementDemandType
  stage: string | null
  buyer: string | null
  region: string | null
  category: string[]
  sections: ProcurementReadingSection[]
  milestones: ProcurementReadingMilestone[]
  coverage: ProcurementReadingCoverage[]
  conflicts: string[]
  unresolved: string[]
}

export interface ProcurementReadingCorrection {
  id: string | number
  requirement_id: string
  text: string
  reason: string
  created_at: string
  material_version: string | number | null
}

export type FollowupStage = 'evaluating' | 'working' | 'submitted' | 'awaiting_result' | 'finished'
export type FollowupDisposition = 'following' | 'paused' | 'abandoned'

export interface FollowupTask {
  id: string | number
  title?: string
  name?: string
  description?: string | null
  status?: string
  required?: boolean
  basis?: 'required' | 'conditional' | 'suggested' | string | null
  source_kind?: string | null
  due_at?: string | null
  due?: string | null
  owner?: string | null
  skip_reason?: string | null
  skipped_reason?: string | null
  completion_note?: string | null
  reason?: string | null
  note?: string | null
  [key: string]: unknown
}

export interface FollowupMaterial {
  id: string | number
  name?: string
  title?: string
  status?: string
  note?: string | null
  [key: string]: unknown
}

export interface FollowupEvent {
  id?: string | number
  at?: string
  created_at?: string
  payload?: Record<string, unknown>
  type?: string
  text?: string
  message?: string
  [key: string]: unknown
}

export interface FollowupData {
  stage: FollowupStage
  disposition: FollowupDisposition
  owner: string | null
  next_action: string | null
  outcome: string | null
  submitted_at: string | null
  submission_channel: string | null
  receipt: unknown
  tasks: FollowupTask[]
  materials: FollowupMaterial[]
  events: FollowupEvent[]
  plan: unknown
  needs_review: boolean
  is_following?: boolean
  plan_id?: string | number | null
  note?: string | null
  reason?: string | null
}

export interface ProjectSummary {
  id: number
  title: string
  buyer: string | null
  equipment: string[]
  kind: string
  source_id: string
  record_ids: number[]
  published_at: string | null
  deadline_at: string | null
  updated_at: string
  gate_status: GateStatus
  gate_reasons: string[]
  ai_status: AiStatus
  ai_decision: AiDecision
  ai_summary: string | null
  needs_review: boolean
  decision: Decision
  /** Optional while older API responses are still in circulation. */
  analysis_meta?: AnalysisMeta | null
  /** Optional while older API responses are still in circulation. */
  briefing?: ProjectBriefing | null
  /** Optional while older API responses are still in circulation. */
  procurement?: ProcurementData | null
  demand_summary?: string | null
  ai_next_actions?: string[]
  research_summary?: {
    status: InvestigationStatus
    source_count: number
    stop_reason?: string
    updated_at?: string
  }
  /** Opportunities from the current valid Agent report; legacy summaries may omit this field. */
  opportunity_signals?: AgentOpportunity[]
  /** Canonical procurement facets returned by the reading worker. */
  category?: string[]
  region?: string | null
  procurement_stage?: string | null
  demand_type?: ProcurementDemandType
  followup_stage?: FollowupStage | null
  followup_summary?: (Pick<FollowupData, 'stage' | 'disposition' | 'owner' | 'next_action' | 'outcome' | 'submitted_at'> & { updated_at: string }) | null
  is_following?: boolean
  procurement_reading?: ProcurementReading | null
}

export interface ProcurementEvidence {
  record_id: number
  quote: string
  document_id?: number | string | null
  page?: number | null
  document_name?: string | null
  source_url?: string | null
  document_sha256?: string | null
  /** API URL for the immutable attachment text/page snapshot used by this report. */
  snapshot_url?: string | null
}

export interface ResearchEvidence {
  source_ref: string
  source_version: string
  quote: string
  page?: number | null
  title: string
  source_url: string
  fetched_at: string | null
  relation: ResearchSourceRelation
  snapshot_url?: string | null
}

export type Evidence = ProcurementEvidence | ResearchEvidence

export interface JudgmentReason {
  text: string
  evidence: Evidence[]
}

export interface JudgmentOption {
  id: string
  title: string
  description: string
  suitable_for: string
  tradeoffs: string[]
  unknowns: string[]
  next_action: string
}

export interface Judgment {
  id: number
  created_at: string
  model: string
  input_fingerprint: string
  stale: boolean
  decision: Exclude<AiDecision, null>
  summary: string
  demand_summary: string
  reasons: JudgmentReason[]
  unknowns: string[]
  next_actions: string[]
  options: JudgmentOption[]
  /** Missing metadata means the judgment uses the historical semantic. */
  analysis_meta?: AnalysisMeta | null
  /** The investigation that supplied this report, kept separate from later runs. */
  agent_run_id?: number | null
  investigation?: Investigation | null
  /** New reports use Agent-selected sections; older judgments keep reasons/unknowns below. */
  report?: AgentReport | null
}

export interface ProjectRecord {
  id: number
  title: string
  body_text: string
  facts?: Record<string, unknown> | null
  inferences?: Record<string, unknown> | null
  original_url?: string | null
  canonical_url?: string | null
  archive_path?: string | null
  parse_status?: string | null
  possible_duplicate_of?: number | null
  related_record_id?: number | null
  [key: string]: unknown
}

export interface ProjectVersion {
  [key: string]: unknown
}

export interface ProjectDetail extends ProjectSummary {
  records: ProjectRecord[]
  judgment: Judgment | null
  judgment_history: Judgment[]
  decision_history: Array<Record<string, unknown>>
  related_projects: ProjectSummary[]
  versions: ProjectVersion[]
  /** Optional while older detail responses are still in circulation. */
  agent_run?: AgentRun | null
  /** Optional while older detail responses are still in circulation. */
  research_sources?: ResearchSource[]
  followup?: FollowupData | null
}

export interface DashboardData {
  total_records: number
  total_projects: number
  gate_counts: Record<string, number>
  active_demand_counts?: Record<string, number>
  ai_counts: Record<string, number>
  ai_decision_counts?: Record<string, number>
  decision_counts: Record<string, number>
  followup_count?: number
  deadline_soon: ProjectSummary[]
  data_updated_at: string | null
  import_error: string | null
  ai_configured: boolean
  source_notes?: string | null
  file_stale?: boolean
  import_pending?: boolean
  deadline_soon_count?: number
}

export interface ProjectsResponse {
  items: ProjectSummary[]
  total: number
  page: number
  page_size: number
}

export interface BusinessSettings {
  company_name: string
  scope_description: string
  /** Customer focus preferences, separate from self-reported company capability material. */
  customer_role: string
  equipment_categories: string
  mineral_types: string
  demand_focus: string
  target_keywords: string[]
  exclude_keywords: string[]
  supported_products: string
  capabilities: string
  solution_examples: string
  region_preferences: string
}

export interface AiSettings {
  base_url: string
  model: string
  enabled: boolean
  has_api_key: boolean
  /** Read-only runtime information returned by newer APIs. */
  engine?: AiEngine
  sdk_ready?: boolean
  api_key?: string
  clear_api_key?: boolean
}

export interface ImportSettings {
  path: string
  auto_import: boolean
  poll_seconds: number
}

export interface SettingsData {
  business: BusinessSettings
  ai: AiSettings
  import: ImportSettings
  version: number
  /** Read-only analysis policy returned by newer APIs. */
  analysis?: {
    goal: string
    semantic_version: string
    company_profile_present: boolean
  }
}

export interface StatusImport {
  [key: string]: unknown
}

export interface StatusJob {
  id: number
  project_id: number | null
  status: string
  attempts: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface ReadingJob {
  id: number
  project_id: number
  status: 'queued' | 'running' | 'succeeded' | 'failed' | string
  attempts: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface StatusData {
  data_updated_at: string | null
  last_import_at: string | null
  import_error: string | null
  file_stale: boolean
  import_pending?: boolean
  source_notes: string
  imports: StatusImport[]
  jobs: StatusJob[]
  job_counts: Record<string, number>
  reading_jobs?: ReadingJob[]
  reading_job_counts?: Record<string, number>
  ai_configured: boolean
}

/** Source and run contracts for the unified collection control surface. */
export interface CollectionSource {
  id: string
  name: string
  category: string
  enabled: boolean
  access_status: string
  access_note: string | null
  entry_url: string | null
  last_success_at: string | null
  last_failure_at: string | null
  last_error: string | null
  last_run: CollectionSourceRun | null
}

export interface CollectionSourceRun {
  status: string
  started_at: string | null
  finished_at: string | null
  found_count: number | null
  fetched_count: number | null
  error: string | null
}

export interface CollectionRun {
  id: string | number
  started_at: string | null
  finished_at: string | null
  status: string
  stats: Record<string, number>
}

export interface CollectionSchedule {
  available: boolean
  registered: boolean
  enabled: boolean
  at: string | null
  state: string
  message: string
}

export type CollectionTaskStatus = 'running' | 'succeeded' | 'partial' | 'failed'

export interface CollectionTask {
  id: string | number
  status: CollectionTaskStatus
  source_id: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  log_tail: string
  import_result: Record<string, unknown> | null
}

export interface CollectionData {
  available: boolean
  import_connected?: boolean
  message: string
  running: boolean
  sources: CollectionSource[]
  runs: CollectionRun[]
  schedule: CollectionSchedule
  task: CollectionTask | null
}

export interface ApiErrorShape {
  detail?: string | { message?: string; [key: string]: unknown }
  message?: string
}
