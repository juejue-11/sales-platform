import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { DashboardData, ProjectDetail, ProjectSummary } from '../types'

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

export interface LoadedProjectDetail {
  status: 'loading' | 'success' | 'error'
  updatedAt: string
  detail?: ProjectDetail
  error?: unknown
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
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState<OpportunityQuery>(initialQuery)
  const [draftSearch, setDraftSearch] = useState('')
  const [expandedSourceId, setExpandedSourceId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, LoadedProjectDetail>>({})
  const [loadingDashboard, setLoadingDashboard] = useState(true)
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [dashboardError, setDashboardError] = useState<unknown>(null)
  const [projectsError, setProjectsError] = useState<unknown>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const disposed = useRef(false)
  const dashboardSequence = useRef(0)
  const projectsSequence = useRef(0)
  const detailSequences = useRef<Record<number, number>>({})

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
        setExpandedSourceId(null)
      }
    } catch (error) {
      if (!disposed.current && sequence === projectsSequence.current) setProjectsError(error)
    } finally {
      if (!disposed.current && sequence === projectsSequence.current) setLoadingProjects(false)
    }
  }, [])

  const loadProjectDetail = useCallback(async (id: number, updatedAt: string) => {
    const sequence = (detailSequences.current[id] ?? 0) + 1
    detailSequences.current[id] = sequence
    setDetails((current) => ({ ...current, [id]: { status: 'loading', updatedAt } }))
    try {
      const detail = await api.project(id)
      if (!disposed.current && detailSequences.current[id] === sequence) setDetails((current) => ({ ...current, [id]: { status: 'success', updatedAt, detail } }))
    } catch (error) {
      if (!disposed.current && detailSequences.current[id] === sequence) setDetails((current) => ({ ...current, [id]: { status: 'error', updatedAt, error } }))
    }
  }, [])

  useEffect(() => {
    disposed.current = false
    void loadDashboard()
    void loadProjects(initialQuery)
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
    if (key === 'gate' && typeof value === 'string' && ['excluded', 'expired', 'closed', 'signal', 'duplicate'].includes(value)) next.active_only = false
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

  const toggleOriginal = useCallback((id: number, updatedAt: string) => {
    if (expandedSourceId === id) {
      setExpandedSourceId(null)
      return
    }
    setExpandedSourceId(id)
    const detail = details[id]
    if (!detail || detail.updatedAt !== updatedAt || detail.status === 'error') void loadProjectDetail(id, updatedAt)
  }, [details, expandedSourceId, loadProjectDetail])

  const retryOriginal = useCallback((id: number, updatedAt: string) => {
    setExpandedSourceId(id)
    void loadProjectDetail(id, updatedAt)
  }, [loadProjectDetail])

  const activeTab = useMemo<OpportunityTabKey | null>(() => {
    const keys = (Object.keys(initialQuery) as Array<keyof OpportunityQuery>).filter((field) => field !== 'page' && field !== 'page_size')
    const matchingTab = (Object.keys({ current: true, all: true, ai: true, review: true, deadline: true, following: true, queued: true }) as OpportunityTabKey[]).find((key) => {
      const candidate = tabQuery(key)
      return keys.every((field) => query[field] === candidate[field])
    })
    return matchingTab ?? null
  }, [query])

  const tabs = useMemo<OpportunityTab[]>(() => [
    { key: 'current', label: '当前机会', count: count(dashboard, 'gate_counts', 'candidate') + count(dashboard, 'gate_counts', 'review') },
    { key: 'all', label: '全部', count: dashboard?.total_projects ?? 0 },
    { key: 'ai', label: 'AI建议', count: count(dashboard, 'ai_decision_counts', 'recommend') },
    { key: 'review', label: '待核实', count: count(dashboard, 'gate_counts', 'review') },
    { key: 'deadline', label: '7天截止', count: dashboard?.deadline_soon_count ?? dashboard?.deadline_soon.length ?? 0 },
    { key: 'following', label: '跟进', count: count(dashboard, 'decision_counts', 'following') },
    { key: 'queued', label: '等待分析', count: count(dashboard, 'ai_counts', 'queued') },
  ], [dashboard])

  const activeProjects = projects.filter((project) => project.gate_status === 'candidate' || project.gate_status === 'review')
  const featured = activeProjects[0] ?? null
  const featuredBasis = featured ? '按资料更新时间展示' : '等待采购数据'

  return {
    dashboard,
    projects,
    total,
    query,
    draftSearch,
    activeTab,
    tabs,
    featured,
    featuredBasis,
    details,
    expandedSourceId,
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
    toggleOriginal,
    retryOriginal,
    errorMessage: asError,
  }
}
