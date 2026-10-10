export type GateStatus = 'candidate' | 'review' | 'excluded' | 'expired' | 'closed' | 'signal' | 'duplicate'
export type AiStatus = 'not_configured' | 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'stale' | 'skipped'
export type AiDecision = 'recommend' | 'consider' | 'verify' | 'not_recommended' | null
export type DecisionStatus = 'new' | 'following' | 'verify' | 'closed'
export type AiEngine = 'pi_sdk' | 'single_call'
export type AgentRunStatus = 'running' | 'succeeded' | 'failed'
export type AgentStepStage = 'started' | 'model' | 'tool_start' | 'tool_end' | 'completed'

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
  source_document_id: number | string
  record_id: number
  page: number
  quote: string
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

export interface ProcurementData {
  status: ProcurementStatus
  scope_summary: string
  items: ProcurementItem[]
  delivery_period: string
  delivery_location: string
  missing_fields: string[]
  documents: ProcurementDocument[]
  updated_at: string | null
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
  briefing?: ProjectBriefing | null
  /** Optional while older API responses are still in circulation. */
  procurement?: ProcurementData | null
  demand_summary?: string | null
  ai_next_actions?: string[]
}

export interface Evidence {
  record_id: number
  quote: string
}

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
}

export interface DashboardData {
  total_records: number
  total_projects: number
  gate_counts: Record<string, number>
  ai_counts: Record<string, number>
  ai_decision_counts?: Record<string, number>
  decision_counts: Record<string, number>
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
  ai_configured: boolean
}

export interface ApiErrorShape {
  detail?: string | { message?: string; [key: string]: unknown }
  message?: string
}
