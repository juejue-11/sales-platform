import type {
  DashboardData,
  ProcurementData,
  ProjectDetail,
  ProjectSummary,
  ProjectsResponse,
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
  refreshDocuments: (id: number) => request<{ procurement: ProcurementData; message: string }>(`/projects/${id}/documents/refresh`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
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
}
