import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Pagination, Select, Typography } from 'antd'
import {
  CaretDownOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  FilterOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import type { AiDecision, AiStatus, DecisionStatus, DashboardData, GateStatus, ProjectSummary } from '../types'
import {
  aiDecisionLabels,
  aiStatusLabels,
  decisionLabels,
  EmptyBlock,
  ErrorBlock,
  formatDate,
  formatFreshness,
  gateLabels,
  kindLabel,
  LoadingBlock,
  sourceLabel,
} from '../components/Common'
import './Dashboard.css'

const { Title } = Typography

interface ProjectQuery {
  q: string
  gate: string
  ai_status: string
  decision: string
  kind: string
  source_id: string
  ai_decision: string
  page: number
  page_size: number
  sort: 'updated' | 'deadline'
  deadline_soon: boolean
}

const initialQuery: ProjectQuery = {
  q: '',
  gate: '',
  ai_status: '',
  decision: '',
  kind: '',
  source_id: '',
  ai_decision: '',
  page: 1,
  page_size: 30,
  sort: 'updated',
  deadline_soon: false,
}

const quickFilterKeys = ['q', 'gate', 'ai_status', 'decision', 'kind', 'source_id', 'ai_decision', 'sort', 'deadline_soon'] as const

type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

const gateTone: Record<GateStatus, StatusTone> = {
  candidate: 'success',
  review: 'warning',
  excluded: 'neutral',
  expired: 'danger',
  closed: 'neutral',
  signal: 'info',
  duplicate: 'neutral',
}

const aiStatusTone: Record<AiStatus, StatusTone> = {
  not_configured: 'neutral',
  idle: 'neutral',
  queued: 'info',
  running: 'info',
  succeeded: 'success',
  failed: 'danger',
  stale: 'warning',
  skipped: 'neutral',
}

const aiDecisionTone: Record<Exclude<AiDecision, null>, StatusTone> = {
  recommend: 'success',
  consider: 'info',
  verify: 'warning',
  not_recommended: 'neutral',
}

const decisionTone: Record<DecisionStatus, StatusTone> = {
  new: 'neutral',
  following: 'info',
  verify: 'warning',
  closed: 'neutral',
}

const kindOptions = [
  { value: '', label: '全部公告类型' },
  { value: 'procurement', label: '采购公告' },
  { value: 'tender', label: '招标公告' },
  { value: 'bid', label: '招标公告' },
  { value: 'award', label: '成交 / 中标' },
  { value: 'result', label: '成交 / 中标' },
  { value: 'news', label: '行业新闻' },
  { value: 'signal', label: '线索' },
  { value: 'mining_rights', label: '矿业权动态' },
]

function count(data: DashboardData | null, field: 'gate_counts' | 'ai_counts' | 'ai_decision_counts' | 'decision_counts', key: string): number {
  return data?.[field]?.[key] ?? 0
}

function StatusPill({ children, tone = 'neutral' }: { children: ReactNode; tone?: StatusTone }) {
  return <span className={`dashboard-pill dashboard-pill-${tone}`}>{children}</span>
}

function GatePill({ status }: { status: GateStatus }) {
  return <StatusPill tone={gateTone[status]}>{gateLabels[status] ?? status}</StatusPill>
}

function AiStatusPill({ status }: { status: AiStatus }) {
  return <StatusPill tone={aiStatusTone[status]}>{aiStatusLabels[status] ?? status}</StatusPill>
}

function AiDecisionPill({ decision }: { decision: AiDecision }) {
  return decision ? <StatusPill tone={aiDecisionTone[decision]}>{aiDecisionLabels[decision]}</StatusPill> : <StatusPill>暂无结论</StatusPill>
}

function DecisionPill({ status }: { status: DecisionStatus }) {
  return <StatusPill tone={decisionTone[status]}>{decisionLabels[status]}</StatusPill>
}

function isExpired(value: string | null): boolean {
  return Boolean(value && new Date(value).getTime() < Date.now())
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState<ProjectQuery>(initialQuery)
  const [draftQ, setDraftQ] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [loadingDashboard, setLoadingDashboard] = useState(true)
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [dashboardError, setDashboardError] = useState<unknown>(null)
  const [projectsError, setProjectsError] = useState<unknown>(null)

  const loadDashboard = useCallback(async () => {
    setDashboardError(null)
    try {
      const result = await api.dashboard()
      setDashboard(result)
    } catch (error) {
      setDashboardError(error)
    } finally {
      setLoadingDashboard(false)
    }
  }, [])

  const loadProjects = useCallback(async (currentQuery: ProjectQuery) => {
    setLoadingProjects(true)
    setProjectsError(null)
    try {
      const result = await api.projects({ ...currentQuery })
      setProjects(result.items)
      setTotal(result.total)
    } catch (error) {
      setProjectsError(error)
    } finally {
      setLoadingProjects(false)
    }
  }, [])

  useEffect(() => {
    void loadDashboard()
    void loadProjects(initialQuery)
    const timer = window.setInterval(() => { void loadDashboard() }, 30000)
    return () => window.clearInterval(timer)
  }, [loadDashboard, loadProjects])

  const submitQuery = (next: Partial<ProjectQuery>) => {
    const nextQuery = { ...query, ...next, page: 1 }
    setQuery(nextQuery)
    void loadProjects(nextQuery)
  }

  const startFromInitial = (overrides: Partial<ProjectQuery> = {}) => {
    const nextQuery = { ...initialQuery, ...overrides }
    setQuery(nextQuery)
    setDraftQ(nextQuery.q)
    void loadProjects(nextQuery)
  }

  const onPageChange = (page: number, pageSize: number) => {
    const nextQuery = { ...query, page, page_size: pageSize }
    setQuery(nextQuery)
    void loadProjects(nextQuery)
  }

  const deadlineProjects = dashboard?.deadline_soon ?? []
  const deadlineCount = dashboard?.deadline_soon_count ?? deadlineProjects.length
  const deadlinePreviewCount = Math.min(4, deadlineProjects.length)
  const sourceNotes = dashboard?.source_notes ?? ''
  const sourceNotesHaveGap = sourceNotes.includes('历史缺失')

  const hasFilters = Boolean(query.q || query.gate || query.ai_status || query.decision || query.kind || query.source_id || query.ai_decision || query.deadline_soon || query.sort !== initialQuery.sort)
  const emptyDescription = hasFilters ? '没有符合筛选条件的项目' : '暂无项目数据'

  const activeFilter = useMemo(() => {
    const matches = (overrides: Partial<ProjectQuery>) => quickFilterKeys.every((key) => query[key] === (overrides[key] ?? initialQuery[key]))
    if (matches({ sort: 'deadline', deadline_soon: true })) return 'deadline'
    if (matches({ ai_decision: 'recommend' })) return 'recommend'
    if (matches({ gate: 'candidate' })) return 'candidate'
    if (matches({ gate: 'review' })) return 'review'
    if (matches({ decision: 'following' })) return 'following'
    if (matches({ ai_status: 'queued' })) return 'queued'
    if (matches({})) return 'all'
    return null
  }, [query])

  const filterTabs = [
    { key: 'all', label: '全部', count: dashboard?.total_projects ?? 0, query: {} },
    { key: 'recommend', label: '建议跟进', count: count(dashboard, 'ai_decision_counts', 'recommend'), query: { ai_decision: 'recommend' } },
    { key: 'candidate', label: '初筛候选', count: count(dashboard, 'gate_counts', 'candidate'), query: { gate: 'candidate' } },
    { key: 'review', label: '待核实', count: count(dashboard, 'gate_counts', 'review'), query: { gate: 'review' } },
    { key: 'deadline', label: '7天截止', count: deadlineCount, query: { sort: 'deadline' as const, deadline_soon: true } },
    { key: 'following', label: '跟进', count: count(dashboard, 'decision_counts', 'following'), query: { decision: 'following' } },
    { key: 'queued', label: '等待分析', count: count(dashboard, 'ai_counts', 'queued'), query: { ai_status: 'queued' } },
  ]

  const refreshAll = () => {
    void loadDashboard()
    void loadProjects(query)
  }

  return (
    <div className="dashboard-page">
      <header className="dashboard-heading">
        <div className="dashboard-heading-copy">
          <Title level={2} className="dashboard-heading-title">机会池</Title>
          <p>把采集公告按规则、AI判断和销售动作排在同一条工作线上。</p>
        </div>
        <div className="dashboard-sync-bar" aria-label="数据同步状态">
          <DatabaseOutlined aria-hidden="true" />
          <span className="dashboard-sync-label">数据同步</span>
          <span className="dashboard-sync-time">{formatFreshness(dashboard?.data_updated_at)}</span>
          <Button className="dashboard-refresh-button" type="text" icon={<ReloadOutlined />} onClick={refreshAll}>刷新</Button>
        </div>
      </header>

      <nav className="dashboard-filter-nav" aria-label="机会池快捷筛选">
        {filterTabs.map((tab) => (
          <button
            type="button"
            key={tab.key}
            className={`dashboard-filter-tab${activeFilter === tab.key ? ' dashboard-filter-tab-active' : ''}`}
            aria-current={activeFilter === tab.key ? 'page' : undefined}
            onClick={() => startFromInitial(tab.query)}
          >
            <span>{tab.label}</span>
            <span className="dashboard-filter-count">{tab.count.toLocaleString('zh-CN')}</span>
          </button>
        ))}
      </nav>

      {activeFilter === null && hasFilters && (
        <div className="dashboard-custom-filter-note" aria-live="polite">
          <span>当前为自定义筛选</span>
          <span>显示 {total.toLocaleString('zh-CN')} 条结果</span>
          <Button type="link" onClick={() => startFromInitial()}>重置筛选</Button>
        </div>
      )}

      {(dashboardError || dashboard?.import_error || dashboard?.file_stale || dashboard?.import_pending) && (
        <section className="dashboard-anomaly-panel" aria-label="数据异常提示">
          <div className="dashboard-anomaly-heading"><WarningOutlined aria-hidden="true" /><span>数据提醒</span></div>
          <div className="dashboard-anomaly-list">
            {dashboardError != null && <div className="dashboard-anomaly-item dashboard-anomaly-danger"><strong>统计暂时无法读取</strong><span>{dashboardError instanceof Error ? dashboardError.message : '请检查后端服务是否运行在 127.0.0.1:8766'}</span><Button size="small" onClick={() => void loadDashboard()}>重试</Button></div>}
            {dashboard?.import_error && <div className="dashboard-anomaly-item dashboard-anomaly-danger"><strong>采集文件存在异常</strong><span>{dashboard.import_error}</span></div>}
            {dashboard?.file_stale && <div className="dashboard-anomaly-item"><strong>采集文件可能已过期</strong><span>平台暂未看到近期的 records.jsonl 更新，请检查采集系统和导入路径。</span></div>}
            {dashboard?.import_pending && <div className="dashboard-anomaly-item dashboard-anomaly-info"><strong>新采集数据尚未导入</strong><span>自动导入可能未开启或仍待处理，请前往处理状态手动导入。</span></div>}
          </div>
        </section>
      )}

      {dashboard && !dashboard.ai_configured && (
        <div className="dashboard-model-note">
          <div className="dashboard-model-note-copy"><RobotOutlined aria-hidden="true" /><div><strong>模型尚未配置</strong><span>当前仅展示规则结果和已有数据，配置后才能生成 AI 判断。</span></div></div>
          <Button type="link" onClick={() => navigate('/settings')}>去配置</Button>
        </div>
      )}

      <div className="dashboard-workbench">
        <section className="dashboard-list-panel" aria-labelledby="dashboard-list-heading">
          <div className="dashboard-list-header">
            <div>
              <h3 id="dashboard-list-heading">项目清单</h3>
              <p>看需求与理由，确认下一步。</p>
            </div>
            <div className="dashboard-list-total"><strong>{total.toLocaleString('zh-CN')}</strong><span>条结果</span></div>
          </div>

          <div className="dashboard-toolbar">
            <Input.Search
              value={draftQ}
              onChange={(event) => setDraftQ(event.target.value)}
              onSearch={(value) => submitQuery({ q: value })}
              allowClear
              placeholder="搜索项目标题、采购方、设备"
              enterButton={<span>搜索</span>}
              prefix={<SearchOutlined aria-hidden="true" />}
              className="dashboard-search"
            />
            <Button
              className={`dashboard-filter-toggle${advancedOpen ? ' dashboard-filter-toggle-open' : ''}`}
              icon={<FilterOutlined />}
              onClick={() => setAdvancedOpen((value) => !value)}
              aria-expanded={advancedOpen}
            >
              高级筛选
            </Button>
            <Button className="dashboard-toolbar-refresh" icon={<ReloadOutlined />} onClick={refreshAll} aria-label="刷新项目列表" />
          </div>

          {advancedOpen && (
            <div className="dashboard-advanced-filters" aria-label="高级筛选">
              <label><span>规则状态</span><Select value={query.gate} onChange={(value) => submitQuery({ gate: value })} options={[{ value: '', label: '全部规则状态' }, { value: 'candidate', label: '候选' }, { value: 'review', label: '待核实' }, { value: 'excluded', label: '已排除' }, { value: 'expired', label: '已截止' }, { value: 'signal', label: '线索' }, { value: 'duplicate', label: '确认重复' }]} /></label>
              <label><span>AI状态</span><Select value={query.ai_status} onChange={(value) => submitQuery({ ai_status: value })} options={[{ value: '', label: '全部 AI 状态' }, { value: 'not_configured', label: '未配置模型' }, { value: 'idle', label: '待分析' }, { value: 'queued', label: '等待分析' }, { value: 'running', label: '分析中' }, { value: 'succeeded', label: '已分析' }, { value: 'failed', label: '分析失败' }, { value: 'stale', label: '结果待复核' }, { value: 'skipped', label: '无需分析' }]} /></label>
              <label><span>人工状态</span><Select value={query.decision} onChange={(value) => submitQuery({ decision: value })} options={[{ value: '', label: '全部人工状态' }, { value: 'new', label: '未处理' }, { value: 'following', label: '跟进中' }, { value: 'verify', label: '待核实' }, { value: 'closed', label: '已关闭' }]} /></label>
              <label><span>AI建议</span><Select value={query.ai_decision} onChange={(value) => submitQuery({ ai_decision: value })} options={[{ value: '', label: '全部 AI 建议' }, { value: 'recommend', label: '建议跟进' }, { value: 'consider', label: '有条件跟进' }, { value: 'verify', label: '待核实' }, { value: 'not_recommended', label: '不建议' }]} /></label>
              <label><span>公告类型</span><Select value={query.kind} onChange={(value) => submitQuery({ kind: value })} options={kindOptions} placeholder="公告类型" /></label>
              <label><span>来源 ID</span><Input value={query.source_id} onChange={(event) => { const value = event.target.value; setQuery((current) => ({ ...current, source_id: value })); if (!value && query.source_id) submitQuery({ source_id: '' }) }} onPressEnter={(event) => submitQuery({ source_id: event.currentTarget.value })} onBlur={() => { if (query.source_id) submitQuery({ source_id: query.source_id }) }} placeholder="来源 ID" allowClear /></label>
              <label><span>排序</span><Select value={query.sort} onChange={(value: ProjectQuery['sort']) => submitQuery({ sort: value })} options={[{ value: 'updated', label: '按更新时间' }, { value: 'deadline', label: '按截止时间' }]} /></label>
              <Button className="dashboard-clear-filters" onClick={() => startFromInitial()}>清除筛选</Button>
            </div>
          )}

          <div className="dashboard-list-scroll">
            <div className="dashboard-list-table" role="table" aria-label="项目清单">
              <div className="dashboard-project-row dashboard-project-row-header" role="row">
                <span role="columnheader">项目 / 采购方</span>
                <span role="columnheader">规则</span>
                <span role="columnheader">AI判断</span>
                <span role="columnheader">人工</span>
                <span role="columnheader">截止 / 更新</span>
                <span role="columnheader" aria-label="打开项目" />
              </div>
              {projectsError ? <div className="dashboard-list-state"><ErrorBlock error={projectsError} onRetry={() => void loadProjects(query)} /></div> : loadingProjects ? <div className="dashboard-list-state"><LoadingBlock text="正在读取项目" /></div> : projects.length === 0 ? <div className="dashboard-list-state"><EmptyBlock description={emptyDescription} /></div> : projects.map((project) => (
                <article className="dashboard-project-row" role="row" key={project.id}>
                  <div className="dashboard-project-identity" role="cell">
                    <Button type="link" className="dashboard-project-title" onClick={() => navigate(`/projects/${project.id}`)}>{project.title || '未命名项目'}</Button>
                    <div className="dashboard-project-meta"><span>{project.buyer || '采购方未标明'}</span><span className="dashboard-meta-separator">·</span><span>{kindLabel(project.kind)}</span><span className="dashboard-source-name">{sourceLabel(project.source_id)}</span></div>
                    {project.ai_summary && <p className="dashboard-ai-summary" title={project.ai_summary}><RobotOutlined aria-hidden="true" />{project.ai_summary}</p>}
                  </div>
                  <div className="dashboard-status-cell" role="cell"><span className="dashboard-status-label">规则</span><GatePill status={project.gate_status} /></div>
                  <div className="dashboard-status-cell dashboard-ai-cell" role="cell"><span className="dashboard-status-label">AI</span><div className="dashboard-status-pair"><AiStatusPill status={project.ai_status} /><AiDecisionPill decision={project.ai_decision} /></div></div>
                  <div className="dashboard-status-cell" role="cell"><span className="dashboard-status-label">人工</span><DecisionPill status={project.decision.status} /></div>
                  <div className="dashboard-project-dates" role="cell"><span className="dashboard-date-label">截止 <time className={isExpired(project.deadline_at) ? 'dashboard-date-expired' : ''} dateTime={project.deadline_at ?? undefined}>{formatDate(project.deadline_at)}</time></span><span className="dashboard-date-label">更新 <time dateTime={project.updated_at}>{formatDate(project.updated_at)}</time></span></div>
                  <div className="dashboard-project-action" role="cell"><Button type="link" onClick={() => navigate(`/projects/${project.id}`)}>查看</Button></div>
                </article>
              ))}
            </div>
          </div>

          {!loadingProjects && !projectsError && projects.length > 0 && <div className="dashboard-pagination"><Pagination current={query.page} pageSize={query.page_size} total={total} showSizeChanger pageSizeOptions={[15, 30, 60]} showTotal={(value) => `共 ${value} 个`} onChange={onPageChange} /></div>}
        </section>

        <aside className="dashboard-deadline-panel" aria-labelledby="dashboard-deadline-heading">
          <div className="dashboard-deadline-header"><div><span className="dashboard-section-label">时间提醒</span><h3 id="dashboard-deadline-heading">7天内截止</h3></div><ClockCircleOutlined aria-hidden="true" /></div>
          {loadingDashboard ? <LoadingBlock text="正在读取提醒" /> : deadlineCount === 0 ? <div className="dashboard-deadline-empty"><CheckCircleOutlined aria-hidden="true" /><p>未来 7 天没有临近截止项目。</p></div> : <>
            <div className="dashboard-deadline-list">
              {deadlineProjects.slice(0, 4).map((project) => <button type="button" key={project.id} className="dashboard-deadline-row" onClick={() => navigate(`/projects/${project.id}`)}><span className="dashboard-deadline-name">{project.title || '未命名项目'}</span><time dateTime={project.deadline_at ?? undefined}>{formatDate(project.deadline_at)}</time><AiDecisionPill decision={project.ai_decision} /></button>)}
            </div>
            {deadlineCount > deadlinePreviewCount && <p className="dashboard-deadline-more">还有 {deadlineCount - deadlinePreviewCount} 条提醒</p>}
            <Button className="dashboard-deadline-cta" type="link" onClick={() => startFromInitial({ sort: 'deadline', deadline_soon: true })}>查看全部截止项目</Button>
          </>}
        </aside>
      </div>

      {dashboard?.source_notes && (
        <details className="dashboard-source-note">
          <summary><span><InfoCircleOutlined aria-hidden="true" /> 来源覆盖说明{sourceNotesHaveGap && <em className="dashboard-source-gap">存在采集缺口</em>}</span><CaretDownOutlined aria-hidden="true" /></summary>
          <p>{dashboard.source_notes}</p>
        </details>
      )}
    </div>
  )
}
