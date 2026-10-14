import type {
  AgentRun,
  DashboardData,
  ProcurementData,
  ProcurementDocumentSnapshot,
  FollowupData,
  FollowupMaterial,
  FollowupTask,
  ProcurementReadingCorrection,
  ProjectDetail,
  ProjectSummary,
  ProjectsResponse,
  ResearchSourceVersionResponse,
  SettingsData,
  StatusData,
} from './types'

const API_PREFIX = '/api'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function errorMessage(value: unknown): string {
  if (value && typeof value === 'object') {
    const candidate = value as { detail?: unknown; message?: unknown }
    if (typeof candidate.detail === 'string') return candidate.detail
    if (candidate.detail && typeof candidate.detail === 'object') {
      const detail = candidate.detail as { message?: unknown }
      if (typeof detail.message === 'string') return detail.message
    }
    if (typeof candidate.message === 'string') return candidate.message
  }
  return '请求失败，请稍后重试'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_PREFIX}${path}`, { ...init, headers })
  const contentType = response.headers.get('content-type') ?? ''
  const payload: unknown = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) throw new ApiError(errorMessage(payload), response.status)
  return payload as T
}

export const api = {
  health: () => request<{ ok: true }>('/health'),
  dashboard: () => request<DashboardData>('/dashboard'),
  projects: (params: Record<string, string | number | boolean | undefined>) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '' && value !== false) search.set(key, String(value))
    })
    return request<ProjectsResponse>(`/projects?${search.toString()}`)
  },
  project: (id: number) => request<ProjectDetail>(`/projects/${id}`),
  researchRuns: (id: number) => request<{ items: AgentRun[]; total: number }>(`/projects/${id}/research/runs`),
  researchRun: (id: number, runId: number) => request<AgentRun>(`/projects/${id}/research/runs/${runId}`),
  researchSourceVersion: (
    id: number,
    sourceRef: string,
    version: string,
    params: { page?: number; offset?: number; limit?: number } = {},
  ) => {
    const search = new URLSearchParams()
    if (params.page !== undefined) search.set('page', String(params.page))
    if (params.offset !== undefined) search.set('offset', String(params.offset))
    if (params.limit !== undefined) search.set('limit', String(params.limit))
    const query = search.toString()
    return request<ResearchSourceVersionResponse>(
      `/projects/${id}/research/sources/${encodeURIComponent(sourceRef)}/versions/${encodeURIComponent(version)}${query ? `?${query}` : ''}`,
    )
  },
  refreshDocuments: (id: number) => request<{ procurement: ProcurementData; message: string }>(`/projects/${id}/documents/refresh`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  documentSnapshot: (projectId: number, documentId: number | string, sha256?: string | null) => {
    const query = sha256 ? `?sha256=${encodeURIComponent(sha256)}` : ''
    return request<ProcurementDocumentSnapshot>(
      `/projects/${projectId}/documents/${encodeURIComponent(String(documentId))}/snapshot${query}`,
    )
  },
  uploadedFileUrl: (projectId: number, documentId: number | string) => `${API_PREFIX}/projects/${projectId}/documents/${encodeURIComponent(String(documentId))}/uploaded-file`,
  analyze: (id: number, force = false) => request<{ job_id: number | null; status: string; message: string }>(`/projects/${id}/analyze`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  }),
  decision: (id: number, body: { status: string; owner: string; next_action: string; note: string; selected_option_id: string | null; judgment_id?: number | null }) => request<ProjectSummary>(`/projects/${id}/decision`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  gate: (id: number, action: 'restore' | 'clear', reason: string) => request<ProjectSummary>(`/projects/${id}/gate`, {
    method: 'POST',
    body: JSON.stringify({ action, reason }),
  }),
  link: (id: number, body: { target_project_id: number; relation: 'same_project' | 'duplicate' | 'dismiss'; note: string }) => request<{ ok: true; message: string }>(`/projects/${id}/link`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  settings: () => request<SettingsData>('/settings'),
  saveSettings: (body: SettingsData) => request<SettingsData>('/settings', {
    method: 'PUT',
    body: JSON.stringify(body),
  }),
  status: () => request<StatusData>('/status'),
  importNow: () => request<{ inserted: number; updated: number; unchanged: number; total: number; message: string }>('/import', {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  analyzePending: () => request<{ queued: number; message: string }>('/analyze-pending', {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  retryFailed: () => request<{ queued: number; message: string }>('/jobs/retry-failed', {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  archiveUrl: (recordId: number) => `${API_PREFIX}/records/${recordId}/archive`,
  followup: (id: number) => request<FollowupData>(`/projects/${id}/followup`),
  updateFollowup: (id: number, body: Partial<FollowupData>) => request<FollowupData>(`/projects/${id}/followup`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }),
  adoptFollowupPlan: (id: number, planId?: string | number | null) => request<FollowupData>(`/projects/${id}/followup/adopt`, {
    method: 'POST',
    body: JSON.stringify(planId == null ? {} : { plan_id: planId }),
  }),
  updateFollowupTask: (id: number, taskId: string | number, body: Partial<FollowupTask>) => request<FollowupData>(`/projects/${id}/followup/tasks/${encodeURIComponent(String(taskId))}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),
  updateFollowupMaterial: (id: number, materialId: string | number, body: Partial<FollowupMaterial>) => request<FollowupData>(`/projects/${id}/followup/materials/${encodeURIComponent(String(materialId))}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),
  supplementDocument: (id: number, body: { record_id: number; name: string; content_base64: string }) => request<{ document: ProcurementDocumentSnapshot; message: string }>(`/projects/${id}/documents/supplement`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  readingCorrections: (id: number) => request<{ corrections: ProcurementReadingCorrection[] }>(`/projects/${id}/reading/corrections`),
  addReadingCorrection: (id: number, body: { requirement_id: string; text: string; reason: string }) => request<{ corrections: ProcurementReadingCorrection[]; message: string }>(`/projects/${id}/reading/corrections`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
}
