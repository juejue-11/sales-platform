export type GateStatus = 'candidate' | 'review' | 'excluded' | 'expired' | 'closed' | 'signal' | 'duplicate'
export type AiStatus = 'not_configured' | 'queued' | 'running' | 'succeeded' | 'failed' | 'stale'
export type AiDecision = 'recommend' | 'consider' | 'verify' | 'not_recommended' | null
export type DecisionStatus = 'new' | 'following' | 'verify' | 'closed'

export interface Decision {
  status: DecisionStatus
  owner: string
  next_action: string
  note: string
  selected_option_id: string | null
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
}

export interface DashboardData {
  total_records: number
  total_projects: number
  gate_counts: Record<string, number>
  ai_counts: Record<string, number>
  decision_counts: Record<string, number>
  deadline_soon: ProjectSummary[]
  data_updated_at: string | null
  import_error: string | null
  ai_configured: boolean
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
