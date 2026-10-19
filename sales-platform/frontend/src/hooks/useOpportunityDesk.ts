import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'
import { api } from '../api'
import type { DashboardData, ProjectSummary } from '../types'
import { isDeadlineWithinDays } from '../components/opportunities/text'

export type OpportunityTabKey = 'current' | 'all' | 'procurement' | 'leads' | 'analyzed' | 'ai' | 'review' | 'deadline' | 'following' | 'queued'
export type OpportunitySort = 'published' | 'updated' | 'deadline'

export interface OpportunityQuery {
  q: string
  gate: string
  ai_status: string
  decision: string
  kind: string
  source_id: string
  category: string
  region: string
  buyer: string
  procurement_stage: string
  demand_type: string
  key_time: string
  published_days: number
  is_following: boolean
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
  category: '',
  region: '',
  buyer: '',
  procurement_stage: '',
  demand_type: '',
  key_time: '',
  published_days: 0,
  is_following: false,
  ai_decision: '',
  deadline_soon: false,
  active_only: true,
  page: 1,
  page_size: 12,
  sort: 'published',
}

export interface OpportunityDeskNavigationState {
  query: OpportunityQuery
  draftSearch: string
  advancedOpen: boolean
  scrollY: number
  savedAt: number
  search?: string
  viewMode?: 'cards' | 'list'
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
    && typeof candidate.category === 'string'
    && typeof candidate.region === 'string'
    && typeof candidate.buyer === 'string'
    && typeof candidate.procurement_stage === 'string'
    && typeof candidate.demand_type === 'string'
    && typeof candidate.key_time === 'string'
    && (candidate.published_days === undefined || (typeof candidate.published_days === 'number' && Number.isInteger(candidate.published_days) && candidate.published_days >= 0 && candidate.published_days <= 365))
    && typeof candidate.is_following === 'boolean'
    && typeof candidate.ai_decision === 'string'
    && typeof candidate.deadline_soon === 'boolean'
    && typeof candidate.active_only === 'boolean'
    && typeof candidate.page === 'number'
    && Number.isInteger(candidate.page)
    && candidate.page > 0
    && typeof candidate.page_size === 'number'
    && Number.isInteger(candidate.page_size)
    && [12, 24, 48].includes(candidate.page_size)
    && (candidate.sort === 'published' || candidate.sort === 'updated' || candidate.sort === 'deadline')
}

function readNavigationState(): OpportunityDeskNavigationState | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(navigationStorageKey)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return null
    const candidate = value as Partial<OpportunityDeskNavigationState>
    if (candidate.search !== window.location.search) return null
    if (!isOpportunityQuery(candidate.query)
      || typeof candidate.draftSearch !== 'string'
      || typeof candidate.advancedOpen !== 'boolean'
      || typeof candidate.scrollY !== 'number'
      || !Number.isFinite(candidate.scrollY)
      || typeof candidate.savedAt !== 'number'
      || !Number.isFinite(candidate.savedAt)
      || Date.now() - candidate.savedAt > 30 * 60 * 1000) return null
    return {
      query: { ...candidate.query, published_days: candidate.query.published_days ?? 0 },
      draftSearch: candidate.draftSearch,
      advancedOpen: candidate.advancedOpen,
      scrollY: Math.max(0, candidate.scrollY),
      savedAt: candidate.savedAt,
      search: candidate.search,
      viewMode: candidate.viewMode === 'list' ? 'list' : 'cards',
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
  if (key === 'all') return { ...base, active_only: false }
  if (key === 'analyzed') return { ...base, active_only: false, ai_status: 'succeeded' }
  if (key === 'procurement') return { ...base, demand_type: 'confirmed' }
  if (key === 'leads') return { ...base, demand_type: 'lead' }
  if (key === 'ai') return { ...base, active_only: false, ai_decision: 'recommend', sort: 'updated' }
  if (key === 'review') return { ...base, gate: 'review' }
  if (key === 'deadline') return { ...base, key_time: '7d', sort: 'deadline' }
  if (key === 'following') return { ...base, active_only: false, decision: '', is_following: true, sort: 'updated' }
  if (key === 'queued') return { ...base, active_only: false, ai_status: 'queued', sort: 'updated' }
  return base
}

function asError(value: unknown): Error {
  if (value instanceof Error) return value
  if (typeof value === 'string') return new Error(value)
  return new Error('请求失败，请稍后重试')
}

export function useOpportunityDesk() {
  const location = useLocation()
  const navigationType = useNavigationType()
  const [restoredNavigation] = useState<OpportunityDeskNavigationState | null>(() => navigationType === 'POP' || location.state?.restoreDesk ? readNavigationState() : null)
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
  const dashboardInFlight = useRef(false)
  const projectsInFlight = useRef(false)
  const queryRef = useRef(query)
  const dashboardReady = useRef(false)
  const projectsReady = useRef(false)

  useEffect(() => {
    if (restoredNavigation) clearNavigationState()
  }, [restoredNavigation])

  const loadDashboard = useCallback(async (silent = false) => {
    if (silent && dashboardInFlight.current) return
    const sequence = ++dashboardSequence.current
    dashboardInFlight.current = true
    if (!silent) {
      setDashboardError(null)
      setLoadingDashboard(true)
    }
    try {
      const result = await api.dashboard()
      if (!disposed.current && sequence === dashboardSequence.current) {
        dashboardReady.current = true
        setDashboard(result)
        setDashboardError(null)
      }
    } catch (error) {
      if (!disposed.current && sequence === dashboardSequence.current && (!silent || !dashboardReady.current)) setDashboardError(error)
    } finally {
      if (sequence === dashboardSequence.current) {
        dashboardInFlight.current = false
        if (!disposed.current) setLoadingDashboard(false)
      }
    }
  }, [])

  const loadProjects = useCallback(async (nextQuery: OpportunityQuery, silent = false) => {
    if (silent && projectsInFlight.current) return
    const sequence = ++projectsSequence.current
    projectsInFlight.current = true
    if (!silent) {
      setProjectsError(null)
      setLoadingProjects(true)
    }
    try {
      const result = await api.projects({ ...nextQuery })
      if (!disposed.current && sequence === projectsSequence.current) {
        projectsReady.current = true
        setProjects(result.items)
        setTotal(result.total)
        setProjectsError(null)
      }
    } catch (error) {
      if (!disposed.current && sequence === projectsSequence.current && (!silent || !projectsReady.current)) setProjectsError(error)
    } finally {
      if (sequence === projectsSequence.current) {
        projectsInFlight.current = false
        if (!disposed.current) setLoadingProjects(false)
      }
    }
  }, [])

  useEffect(() => {
    disposed.current = false
    void loadDashboard()
    void loadProjects(query)
    const timer = window.setInterval(() => {
      void loadDashboard(true)
      void loadProjects(queryRef.current, true)
    }, 4000)
    return () => {
      disposed.current = true
      window.clearInterval(timer)
    }
  }, [loadDashboard, loadProjects])

  const applyQuery = useCallback((next: OpportunityQuery) => {
    queryRef.current = next
    setQuery(next)
    void loadProjects(next)
  }, [loadProjects])

  const selectTab = useCallback((tab: OpportunityTabKey, overrides: Partial<OpportunityQuery> = {}) => {
    const next = { ...tabQuery(tab), ...overrides }
    setDraftSearch(next.q)
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
    applyQuery(tabQuery('all'))
  }, [applyQuery])

  const changePage = useCallback((page: number, pageSize: number) => {
    applyQuery({ ...query, page, page_size: pageSize })
  }, [applyQuery, query])

  const refresh = useCallback(() => {
    void loadDashboard()
    void loadProjects(query)
  }, [loadDashboard, loadProjects, query])

  const activeTab = useMemo<OpportunityTabKey | null>(() => {
    if (query.is_following) return 'following'
    if (query.ai_status === 'succeeded') return 'analyzed'
    if (query.ai_status === 'queued') return 'queued'
    if (query.ai_decision === 'recommend') return 'ai'
    if (query.demand_type === 'confirmed') return 'procurement'
    if (query.demand_type === 'lead') return 'leads'
    if (query.gate === 'review') return 'review'
    if (query.key_time === '7d') return 'deadline'
    return query.active_only ? 'current' : 'all'
  }, [query])

  const tabs = useMemo<OpportunityTab[]>(() => [
    { key: 'current', label: '当前机会与线索', count: count(dashboard, 'gate_counts', 'candidate') + count(dashboard, 'gate_counts', 'review') + count(dashboard, 'gate_counts', 'signal') },
    { key: 'procurement', label: '明确采购', count: dashboard?.active_demand_counts?.confirmed ?? count(dashboard, 'gate_counts', 'candidate') + count(dashboard, 'gate_counts', 'review') },
    { key: 'leads', label: '潜在线索', count: dashboard?.active_demand_counts?.lead ?? count(dashboard, 'gate_counts', 'signal') },
    { key: 'all', label: '全部', count: dashboard?.total_projects ?? 0 },
    { key: 'analyzed', label: '已研判', count: count(dashboard, 'ai_counts', 'succeeded') },
    { key: 'ai', label: 'AI建议', count: count(dashboard, 'ai_decision_counts', 'recommend') },
    { key: 'review', label: '待核实', count: count(dashboard, 'gate_counts', 'review') },
    { key: 'deadline', label: '7天截止', count: dashboard?.deadline_soon_count ?? dashboard?.deadline_soon.length ?? 0 },
    { key: 'following', label: '跟进', count: dashboard?.followup_count ?? count(dashboard, 'decision_counts', 'following') },
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
    restoredViewMode: restoredNavigation?.viewMode,
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
    applyFilters: (filters: Partial<OpportunityQuery>) => {
      const next = { ...query, ...filters, page: 1 }
      if (['excluded', 'expired', 'closed', 'duplicate'].includes(next.gate)) next.active_only = false
      setDraftSearch(next.q)
      applyQuery(next)
    },
    resetFilters,
    changePage,
    refresh,
    retryProjects: () => void loadProjects(query),
    errorMessage: asError,
  }
}
