import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { DashboardData, ProjectSummary } from '../types'
import { isDeadlineWithinDays } from '../components/opportunities/text'

export type OpportunityTabKey = 'current' | 'all' | 'ai' | 'review' | 'deadline' | 'following' | 'queued'
export type OpportunitySort = 'updated' | 'deadline'

export interface OpportunityQuery {
  q: string
  gate: string
  ai_status: string
  decision: string
  kind: string
  source_id: string
  ai_decision: string
  deadline_soon: boolean
  active_only: boolean
  page: number
  page_size: number
  sort: OpportunitySort
}

export interface OpportunityTab {
  key: OpportunityTabKey
  label: string
  count: number
}

const initialQuery: OpportunityQuery = {
  q: '',
  gate: '',
  ai_status: '',
  decision: '',
  kind: '',
  source_id: '',
  ai_decision: '',
  deadline_soon: false,
  active_only: true,
  page: 1,
  page_size: 12,
  sort: 'updated',
}

export interface OpportunityDeskNavigationState {
  query: OpportunityQuery
  draftSearch: string
  advancedOpen: boolean
  scrollY: number
  savedAt: number
}

const navigationStorageKey = 'opportunity-desk-navigation-v1'

function isOpportunityQuery(value: unknown): value is OpportunityQuery {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<OpportunityQuery>
  return typeof candidate.q === 'string'
    && typeof candidate.gate === 'string'
    && typeof candidate.ai_status === 'string'
    && typeof candidate.decision === 'string'
    && typeof candidate.kind === 'string'
    && typeof candidate.source_id === 'string'
    && typeof candidate.ai_decision === 'string'
    && typeof candidate.deadline_soon === 'boolean'
    && typeof candidate.active_only === 'boolean'
    && typeof candidate.page === 'number'
    && Number.isInteger(candidate.page)
    && candidate.page > 0
    && typeof candidate.page_size === 'number'
    && Number.isInteger(candidate.page_size)
    && [12, 24, 48].includes(candidate.page_size)
    && (candidate.sort === 'updated' || candidate.sort === 'deadline')
}

function readNavigationState(): OpportunityDeskNavigationState | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(navigationStorageKey)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return null
    const candidate = value as Partial<OpportunityDeskNavigationState>
    if (!isOpportunityQuery(candidate.query)
      || typeof candidate.draftSearch !== 'string'
      || typeof candidate.advancedOpen !== 'boolean'
      || typeof candidate.scrollY !== 'number'
      || !Number.isFinite(candidate.scrollY)
      || typeof candidate.savedAt !== 'number'
      || !Number.isFinite(candidate.savedAt)
      || Date.now() - candidate.savedAt > 30 * 60 * 1000) return null
    return {
      query: candidate.query,
      draftSearch: candidate.draftSearch,
      advancedOpen: candidate.advancedOpen,
      scrollY: Math.max(0, candidate.scrollY),
      savedAt: candidate.savedAt,
    }
  } catch {
    return null
  }
}

export function saveOpportunityDeskNavigation(state: Omit<OpportunityDeskNavigationState, 'savedAt'>): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(navigationStorageKey, JSON.stringify({ ...state, savedAt: Date.now() }))
  } catch {
    // Storage can be unavailable in privacy modes; navigation still works.
  }
}

function clearNavigationState(): void {
  if (typeof window === 'undefined') return
  try { window.sessionStorage.removeItem(navigationStorageKey) } catch { /* ignore storage errors */ }
}

function count(data: DashboardData | null, field: 'gate_counts' | 'ai_counts' | 'ai_decision_counts' | 'decision_counts', key: string): number {
  return data?.[field]?.[key] ?? 0
}

function tabQuery(key: OpportunityTabKey): OpportunityQuery {
  const base = { ...initialQuery }
  if (key === 'all') return { ...base, active_only: false, sort: 'updated' }
  if (key === 'ai') return { ...base, active_only: false, ai_decision: 'recommend', sort: 'updated' }
  if (key === 'review') return { ...base, gate: 'review' }
  if (key === 'deadline') return { ...base, deadline_soon: true, sort: 'deadline' }
  if (key === 'following') return { ...base, active_only: false, decision: 'following', sort: 'updated' }
  if (key === 'queued') return { ...base, active_only: false, ai_status: 'queued', sort: 'updated' }
  return base
}

function asError(value: unknown): Error {
  if (value instanceof Error) return value
  if (typeof value === 'string') return new Error(value)
  return new Error('请求失败，请稍后重试')
}

export function useOpportunityDesk() {
  const [restoredNavigation] = useState<OpportunityDeskNavigationState | null>(readNavigationState)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState<OpportunityQuery>(() => restoredNavigation?.query ?? initialQuery)
  const [draftSearch, setDraftSearch] = useState(() => restoredNavigation?.draftSearch ?? '')
  const [loadingDashboard, setLoadingDashboard] = useState(true)
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [dashboardError, setDashboardError] = useState<unknown>(null)
  const [projectsError, setProjectsError] = useState<unknown>(null)
  const [advancedOpen, setAdvancedOpen] = useState(() => restoredNavigation?.advancedOpen ?? false)
  const disposed = useRef(false)
  const dashboardSequence = useRef(0)
  const projectsSequence = useRef(0)

  useEffect(() => {
    if (restoredNavigation) clearNavigationState()
  }, [restoredNavigation])

  const loadDashboard = useCallback(async () => {
    const sequence = ++dashboardSequence.current
    setDashboardError(null)
    setLoadingDashboard(true)
    try {
      const result = await api.dashboard()
      if (!disposed.current && sequence === dashboardSequence.current) setDashboard(result)
    } catch (error) {
      if (!disposed.current && sequence === dashboardSequence.current) setDashboardError(error)
    } finally {
      if (!disposed.current && sequence === dashboardSequence.current) setLoadingDashboard(false)
    }
  }, [])

  const loadProjects = useCallback(async (nextQuery: OpportunityQuery) => {
    const sequence = ++projectsSequence.current
    setProjectsError(null)
    setLoadingProjects(true)
    try {
      const result = await api.projects({ ...nextQuery })
      if (!disposed.current && sequence === projectsSequence.current) {
        setProjects(result.items)
        setTotal(result.total)
      }
    } catch (error) {
      if (!disposed.current && sequence === projectsSequence.current) setProjectsError(error)
    } finally {
      if (!disposed.current && sequence === projectsSequence.current) setLoadingProjects(false)
    }
  }, [])

  useEffect(() => {
    disposed.current = false
    void loadDashboard()
    void loadProjects(query)
    const timer = window.setInterval(() => { void loadDashboard() }, 30000)
    return () => {
      disposed.current = true
      window.clearInterval(timer)
    }
  }, [loadDashboard, loadProjects])

  const applyQuery = useCallback((next: OpportunityQuery) => {
    setQuery(next)
    void loadProjects(next)
  }, [loadProjects])

  const selectTab = useCallback((tab: OpportunityTabKey) => {
    const next = tabQuery(tab)
    setDraftSearch('')
    applyQuery(next)
  }, [applyQuery])

  const submitSearch = useCallback((value: string) => {
    const next = { ...query, q: value, page: 1 }
    applyQuery(next)
  }, [applyQuery, query])

  const updateFilter = useCallback(<K extends keyof OpportunityQuery>(key: K, value: OpportunityQuery[K]) => {
    const next = { ...query, [key]: value, page: 1 } as OpportunityQuery
    if (key === 'gate' && typeof value === 'string' && ['excluded', 'expired', 'closed', 'duplicate'].includes(value)) next.active_only = false
    applyQuery(next)
  }, [applyQuery, query])

  const resetFilters = useCallback(() => {
    setDraftSearch('')
    applyQuery(initialQuery)
  }, [applyQuery])

  const changePage = useCallback((page: number, pageSize: number) => {
    applyQuery({ ...query, page, page_size: pageSize })
  }, [applyQuery, query])

  const refresh = useCallback(() => {
    void loadDashboard()
    void loadProjects(query)
  }, [loadDashboard, loadProjects, query])

  const activeTab = useMemo<OpportunityTabKey | null>(() => {
    const keys = (Object.keys(initialQuery) as Array<keyof OpportunityQuery>).filter((field) => field !== 'page' && field !== 'page_size')
    const matchingTab = (Object.keys({ current: true, all: true, ai: true, review: true, deadline: true, following: true, queued: true }) as OpportunityTabKey[]).find((key) => {
      const candidate = tabQuery(key)
      return keys.every((field) => query[field] === candidate[field])
    })
    return matchingTab ?? null
  }, [query])

  const tabs = useMemo<OpportunityTab[]>(() => [
    { key: 'current', label: '当前机会与线索', count: count(dashboard, 'gate_counts', 'candidate') + count(dashboard, 'gate_counts', 'review') + count(dashboard, 'gate_counts', 'signal') },
    { key: 'all', label: '全部', count: dashboard?.total_projects ?? 0 },
    { key: 'ai', label: 'AI建议', count: count(dashboard, 'ai_decision_counts', 'recommend') },
    { key: 'review', label: '待核实', count: count(dashboard, 'gate_counts', 'review') },
    { key: 'deadline', label: '7天截止', count: dashboard?.deadline_soon_count ?? dashboard?.deadline_soon.length ?? 0 },
    { key: 'following', label: '跟进', count: count(dashboard, 'decision_counts', 'following') },
    { key: 'queued', label: '等待分析', count: count(dashboard, 'ai_counts', 'queued') },
  ], [dashboard])

  const activeProjects = projects.filter((project) => project.gate_status === 'candidate' || project.gate_status === 'review' || project.gate_status === 'signal')
  const featured = activeProjects
    .filter((project) => isDeadlineWithinDays(project.deadline_at, 7))
    .sort((left, right) => {
      const leftTime = left.deadline_at ? new Date(left.deadline_at).getTime() : Number.POSITIVE_INFINITY
      const rightTime = right.deadline_at ? new Date(right.deadline_at).getTime() : Number.POSITIVE_INFINITY
      return leftTime - rightTime
    })[0] ?? null
  return {
    dashboard,
    projects,
    total,
    query,
    draftSearch,
    activeTab,
    tabs,
    featured,
    restoredScrollY: restoredNavigation?.scrollY ?? null,
    loadingDashboard,
    loadingProjects,
    dashboardError,
    projectsError,
    advancedOpen,
    setDraftSearch,
    setAdvancedOpen,
    selectTab,
    submitSearch,
    updateFilter,
    resetFilters,
    changePage,
    refresh,
    retryProjects: () => void loadProjects(query),
    errorMessage: asError,
  }
}
