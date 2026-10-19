import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Button, Tooltip } from 'antd'
import { BarChartOutlined, CheckCircleFilled, ClockCircleOutlined, EnvironmentFilled, FileTextFilled, FolderOpenOutlined, ReloadOutlined, RightOutlined, ThunderboltFilled, UnorderedListOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { DashboardData, ProjectSummary } from '../types'
import { EmptyBlock, ErrorBlock, LoadingBlock, formatFreshness } from '../components/Common'
import { followupStageLabels, readingStageLabel, submissionMilestone } from '../components/procurement/reading'
import chinaRegions from './chinaRegions.json'
import './HomeDashboard.css'

const dayMs = 86_400_000
const todayKey = () => dateKey(new Date())
function dateKey(value: string | Date): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}
function dateText(value: string | null | undefined): string {
  return value ? dateKey(value) || '待核对' : '待核对'
}
function deadlineDays(project: ProjectSummary): number | null {
  const at = submissionMilestone(project.procurement_reading)?.at
  const key = at ? dateKey(at) : ''
  return key ? Math.round((new Date(`${key}T00:00:00+08:00`).getTime() - new Date(`${todayKey()}T00:00:00+08:00`).getTime()) / dayMs) : null
}
function isFollowing(project: ProjectSummary) { return project.is_following === true }
function PanelTitle({ icon, title, link, children }: { icon: ReactNode; title: string; link?: string; children?: ReactNode }) {
  return <div className="home-panel-heading"><h2><span aria-hidden="true">{icon}</span>{title}</h2>{link ? <Link className="home-view-all" to={link}>查看全部 <RightOutlined /></Link> : children}</div>
}

export function HomeDashboard() {
  const [data, setData] = useState<{ dashboard: DashboardData; projects: ProjectSummary[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const disposed = useRef(false)
  const requestSequence = useRef(0)
  const requestInFlight = useRef(false)
  const hasData = useRef(false)
  const load = useCallback(async (silent = false) => {
    if (requestInFlight.current) return
    const sequence = ++requestSequence.current
    requestInFlight.current = true
    if (!silent) {
      setLoading(true)
      setError(null)
    }
    try {
      const [dashboard, first] = await Promise.all([api.dashboard(), api.projects({ page: 1, page_size: 200, sort: 'updated' })])
      const projects = [...first.items]
      for (let page = 2; projects.length < first.total; page++) {
        if (disposed.current || sequence !== requestSequence.current) return
        const next = await api.projects({ page, page_size: 200, sort: 'updated' })
        if (!next.items.length) break
        projects.push(...next.items)
      }
      if (!disposed.current && sequence === requestSequence.current) {
        hasData.current = true
        setData({ dashboard, projects: [...new Map(projects.map(project => [project.id, project])).values()] })
        setError(null)
      }
    } catch (cause) {
      if (!disposed.current && sequence === requestSequence.current && (!silent || !hasData.current)) setError(cause)
    } finally {
      if (!disposed.current && sequence === requestSequence.current) setLoading(false)
      requestInFlight.current = false
    }
  }, [])
  const refresh = useCallback(() => { void load(hasData.current) }, [load])
  useEffect(() => {
    disposed.current = false
    void load()
    let timer: number | undefined
    const poll = async () => {
      if (disposed.current) return
      if (document.visibilityState !== 'hidden') await load(true)
      if (!disposed.current) timer = window.setTimeout(() => { void poll() }, 5000)
    }
    timer = window.setTimeout(() => { void poll() }, 5000)
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void load(true)
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      disposed.current = true
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [load])

  const projects = data?.projects ?? []
  const dashboard = data?.dashboard
  const today = projects.filter(project => project.published_at && dateKey(project.published_at) === todayKey()).length
  const deadlines = projects.filter(project => {
    const days = deadlineDays(project)
    return days !== null && days >= 0 && days <= 7 && !['closed', 'expired', 'excluded', 'duplicate'].includes(project.gate_status)
  }).sort((a, b) => deadlineDays(a)! - deadlineDays(b)!)
  const pending = dashboard?.ai_counts.queued ?? 0
  const following = projects.filter(isFollowing).sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  const latest = [...projects].sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? '')).slice(0, 6)
  const reviews = projects.filter(project => project.needs_review || project.procurement_reading?.stale || project.gate_status === 'review')
  const todos = [...deadlines.map(project => ({ project, label: '提交截止', urgent: true })), ...reviews.filter(project => !deadlines.some(item => item.id === project.id)).map(project => ({ project, label: '待核实', urgent: false }))].slice(0, 5)
  const stats = [
    { label: '今日发布', value: today, note: '按公告发布日期', icon: <FileTextFilled />, tone: 'blue', link: '/?view=all' },
    { label: '采购项目总数', value: dashboard?.total_projects ?? 0, note: '已接入的全部项目', icon: <FolderOpenOutlined />, tone: 'green', link: '/?view=all' },
    { label: '即将截止', value: deadlines.length, note: '未来 7 天内提交', icon: <ClockCircleOutlined />, tone: 'orange', link: '/?view=deadline' },
    { label: '待研判', value: pending, note: '排队等待 AI 分析', icon: <ThunderboltFilled />, tone: 'purple', link: '/?view=queued' },
    { label: '正在跟进', value: dashboard?.followup_count ?? following.length, note: '持续关注项目进展', icon: <CheckCircleFilled />, tone: 'blue', link: '/?view=following' },
  ]
  const unavailable = data == null && (loading || error != null)

  return <main className="home-dashboard" aria-label="采购工作台首页">
    <header className="home-welcome">
      <div className="home-welcome-copy"><p className="home-greeting">你好，欢迎回到采购工作台 <span aria-hidden="true">👋</span></p>
        <h1>{unavailable ? '掌握采购动态，把握每一个机会' : `今天有 ${today} 个项目发布，${deadlines.length} 个项目即将截止`}</h1>
        <p className="home-welcome-description">汇集采购信息，结合 AI 研判，帮你发现项目机会并高效跟进。</p>
      </div><HomeIllustration />
    </header>
    <nav className="home-stats" aria-label="项目统计">
      {stats.map(stat => <Link className="home-stat" key={stat.label} to={stat.link}><span className={`home-stat-icon ${stat.tone}`} aria-hidden="true">{stat.icon}</span><span className="home-stat-copy"><strong>{unavailable ? '—' : stat.value.toLocaleString('zh-CN')}</strong><span>{stat.label}</span><small>{stat.note}</small></span></Link>)}
    </nav>
    {error != null && data == null ? <section className="home-panel"><ErrorBlock error={error} onRetry={refresh} /></section> : data == null ? <section className="home-panel"><LoadingBlock text="正在读取采购工作台" /></section> : <>
      {(dashboard?.import_error || dashboard?.file_stale || dashboard?.import_pending) && <div className="home-data-notice"><WarningOutlined /><span>{dashboard.import_error || (dashboard.import_pending ? '有新采集数据等待导入' : '采集来源需要更新')}</span><Link to="/operations">查看处理状态 <RightOutlined /></Link></div>}
      <div className="home-workspace">
        <div className="home-main-column">
          <div className="home-chart-grid"><TrendChart projects={projects} /><RegionChart projects={projects} /></div>
          <section className="home-panel home-latest"><PanelTitle icon={<FileTextFilled />} title="最新采购项目" link="/?view=all" />
            {latest.length === 0 ? <EmptyBlock description="暂无采购项目，导入数据后将在这里显示" /> : <div className="home-project-list">{latest.map((project, index) => <ProjectRow key={project.id} project={project} index={index} />)}</div>}
          </section>
        </div>
        <aside className="home-side-column">
          <section className="home-panel home-todos"><PanelTitle icon={<ClockCircleOutlined />} title="待办事项"><Link className="home-view-all" to="/?view=all">查看项目 <RightOutlined /></Link></PanelTitle>
            {todos.length === 0 ? <div className="home-quiet-state"><span className="home-quiet-icon"><CheckCircleFilled /></span><strong>暂时没有待办事项</strong><p>临近提交和待核实项目会显示在这里</p></div> : <div className="home-todo-list">{todos.map(({ project, label, urgent }) => <Link to={`/projects/${project.id}`} className={`home-todo ${urgent ? 'urgent' : ''}`} key={project.id}><span className="home-todo-mark" /><span className="home-todo-title" title={project.title}>{project.title}</span><span className="home-status-tag">{label}</span><span className="home-todo-time">{urgent ? deadlineDays(project) === 0 ? '今天' : `${deadlineDays(project)} 天` : '待处理'}</span></Link>)}</div>}
          </section>
          <section className="home-panel home-following"><PanelTitle icon={<UnorderedListOutlined />} title="最近跟进项目" link="/?view=following" />
            {following.length === 0 ? <div className="home-quiet-state"><span className="home-quiet-icon"><FolderOpenOutlined /></span><strong>还没有跟进中的项目</strong><p>在项目详情中加入跟进，持续记录进展</p><Link to="/?view=procurement">浏览采购需求 <RightOutlined /></Link></div> : <div className="home-following-list">{following.slice(0, 4).map((project, index) => <Link className="home-following-item" to={`/projects/${project.id}`} key={project.id}><span className={`home-event-icon tone-${index % 3}`}><FileTextFilled /></span><span className="home-following-copy"><span><strong>{followupStageLabels[project.followup_stage ?? 'evaluating'] ?? '跟进中'}</strong><time>{dateText(project.updated_at)}</time></span><p title={project.title}>{project.title}</p></span></Link>)}</div>}
          </section>
          <div className="home-sync-note"><span className="home-sync-dot" /><span>{formatFreshness(dashboard?.data_updated_at)}</span><Tooltip title="刷新首页数据"><Button type="text" size="small" icon={<ReloadOutlined spin={loading} />} onClick={refresh} aria-label="刷新首页数据" /></Tooltip></div>
        </aside>
      </div>
    </>}
    {dashboard?.source_notes && <details className="home-source-notes"><summary>数据来源与覆盖说明</summary><p>{dashboard.source_notes}</p></details>}
  </main>
}

function TrendChart({ projects }: { projects: ProjectSummary[] }) {
  const [range, setRange] = useState(7)
  const buckets = useMemo(() => {
    const end = new Date(`${todayKey()}T00:00:00+08:00`).getTime()
    const count = range === 7 ? 7 : 6
    const size = range / count
    return Array.from({ length: count }, (_, i) => {
      const start = end - (range - 1) * dayMs + i * size * dayMs
      const finish = start + size * dayMs
      const items = projects.filter(project => {
        const date = project.published_at ? dateKey(project.published_at) : ''
        const time = date ? new Date(`${date}T00:00:00+08:00`).getTime() : NaN
        return time >= start && time < finish
      })
      return { label: dateKey(new Date(start)).slice(5), all: items.length, procurement: items.filter(project => (project.demand_type ?? project.procurement_reading?.demand_type) === 'confirmed').length }
    })
  }, [projects, range])
  const max = Math.max(3, ...buckets.map(bucket => bucket.all))
  const step = Math.ceil(max / 3)
  const ceiling = step * 3
  return <section className="home-panel home-trend"><PanelTitle icon={<BarChartOutlined />} title="采购趋势"><div className="home-period" aria-label="趋势时间范围">{[7, 30, 90].map(value => <button type="button" aria-pressed={range === value} className={range === value ? 'active' : ''} key={value} onClick={() => setRange(value)}>{value === 90 ? '近3个月' : `近${value}天`}</button>)}</div></PanelTitle>
    <div className="home-chart-legend"><span><i className="legend-all" />发布项目</span><span><i className="legend-procurement" />明确采购</span></div>
    <div className="home-trend-plot" role="img" aria-label={`近${range}天项目发布趋势：${buckets.map(bucket => `${bucket.label}发布${bucket.all}项，明确采购${bucket.procurement}项`).join('；')}`}>
      <div className="home-chart-gridlines">{[3, 2, 1, 0].map(tick => <div key={tick}><span>{tick * step}</span></div>)}</div>
      <div className="home-bars">{buckets.map(bucket => <Tooltip key={bucket.label} title={`${bucket.label}：发布 ${bucket.all} 项，明确采购 ${bucket.procurement} 项`}><div className="home-bar-column"><div className="home-bar-track"><div className="home-bar-total" style={{ height: `${bucket.all / ceiling * 100}%` }} /><div className="home-bar-confirmed" style={{ height: `${bucket.procurement / ceiling * 100}%` }} /></div><span>{bucket.label}</span></div></Tooltip>)}</div>
    </div><p className="home-chart-caption">按公告发布日期统计</p>
  </section>
}

function RegionChart({ projects }: { projects: ProjectSummary[] }) {
  const counts = new Map<string, number>()
  let unknown = 0
  for (const project of projects) {
    const region = project.region ?? project.procurement_reading?.region
    const province = region && chinaRegions.find(item => item.name && region.includes(item.name.replace(/省|市|壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区/g, '')))
    if (province) counts.set(province.name, (counts.get(province.name) ?? 0) + 1)
    else unknown++
  }
  const ranking = [...counts].sort((a, b) => b[1] - a[1])
  const max = Math.max(1, ...counts.values())
  return <section className="home-panel home-region"><PanelTitle icon={<EnvironmentFilled />} title="采购地域分布" />
    <div className="home-region-content"><div className="home-map"><svg viewBox="0 0 350 255" role="img" aria-label="各地区采购项目分布"><g stroke="#fff" strokeWidth=".65" strokeLinejoin="round">{chinaRegions.filter(region => region.name).map(region => <path key={region.name} d={region.path} fill={counts.has(region.name) ? `hsl(215 95% ${83 - counts.get(region.name)! / max * 27}%)` : '#eaf1fb'}><title>{region.name}：{counts.get(region.name) ?? 0} 个项目</title></path>)}</g></svg><span className="home-map-legend"><i />少<span className="home-map-gradient" />多</span></div>
      <div className="home-region-table"><div className="home-region-table-head"><span>地区</span><span>数量</span></div>{ranking.slice(0, 5).map(([name, count]) => <Link to={`/?view=all&region=${encodeURIComponent(name)}`} key={name}><span>{name}</span><strong>{count}</strong></Link>)}{ranking.length > 5 && <div><span>其他地区</span><strong>{ranking.slice(5).reduce((sum, item) => sum + item[1], 0)}</strong></div>}<div className="home-region-unknown"><span>地区待核对</span><strong>{unknown}</strong></div></div>
    </div>
  </section>
}

function ProjectRow({ project, index }: { project: ProjectSummary; index: number }) {
  const reading = project.procurement_reading
  const categories = project.category?.length ? project.category : reading?.category?.length ? reading.category : project.equipment
  const submission = submissionMilestone(reading)
  const days = deadlineDays(project)
  return <article className="home-project-row"><span className={`home-project-art art-${index % 3}`} aria-hidden="true"><svg viewBox="0 0 64 64" fill="none"><path d="m0 48 18-17 13 9 14-22 19 17v29H0Z" fill="currentColor" opacity=".13" /><path d="M14 43h26v8H14z" fill="#26496a" /><rect x="12" y="49" width="31" height="8" rx="4" fill="#183956" /><circle cx="19" cy="53" r="2" fill="#a8c4d5" /><circle cx="36" cy="53" r="2" fill="#a8c4d5" /><path d="M19 44V30h12l5 14H19Z" fill="#ffb236" /><path d="M22 32h7l3 8H22Z" fill="#245878" /><path d="m33 32 7-16 7 2 6 17-4 2-6-14-5 12Z" fill="#ffb236" /><path d="m47 34 9 1 3 8-9-2Z" fill="#31516a" /></svg></span>
    <div className="home-project-copy"><Link className="home-project-title" title={project.title} to={`/projects/${project.id}`}>{project.title || '未命名项目'}</Link><p title={project.buyer ?? reading?.buyer ?? ''}>{project.buyer ?? reading?.buyer ?? '采购方待核对'}<span className="home-project-location">{project.region ?? reading?.region ?? '地区待核对'}</span></p><div className="home-project-tags">{(categories?.length ? categories.slice(0, 2) : ['设备 / 服务待核对']).map(category => <span key={category}>{category}</span>)}<span>{readingStageLabel(project.procurement_stage ?? reading?.stage)}</span></div></div>
    <div className="home-project-date"><small>发布</small><time>{dateText(project.published_at)}</time></div><div className={`home-project-deadline${days !== null && days >= 0 && days <= 7 ? ' soon' : ''}`}><small>提交截止</small><time>{submission?.at ? dateText(submission.at) : '待核对'}{days !== null && days >= 0 && days <= 7 && <span>{days === 0 ? '今天' : `${days} 天`}</span>}</time></div><Link className="home-detail-button" to={`/projects/${project.id}`}>查看详情</Link>
  </article>
}

function HomeIllustration() {
  return <svg className="home-welcome-art" viewBox="0 0 500 190" fill="none" aria-hidden="true"><defs><linearGradient id="home-sheet" x1="220" y1="10" x2="390" y2="210" gradientUnits="userSpaceOnUse"><stop stopColor="#fff" /><stop offset="1" stopColor="#d3e8ff" /></linearGradient><linearGradient id="home-folder" x1="310" y1="20" x2="400" y2="180" gradientUnits="userSpaceOnUse"><stop stopColor="#84bdff" /><stop offset="1" stopColor="#2784fb" /></linearGradient></defs><path d="m0 180 250-180h250v190H0Z" fill="#bedcff" opacity=".3" /><path d="m120 170 190-100 190 64M180 190 370 90M270 190l175-90M150 110l200 80" stroke="#fff" opacity=".4" /><path d="m327 11 94 26q17 4 17 22v123l-111-35Z" fill="url(#home-folder)" /><path d="m314 19 94 27q17 4 17 20v123l-111-35Z" fill="#acd2ff" /><path d="m269 22 104 30q14 4 14 17v124l-132-39V33q0-15 14-11Z" fill="url(#home-sheet)" stroke="#fff" strokeWidth="2" /><path d="m277 48 35 10m-35 9 83 24m-83-5 83 24m-83-4 54 16m-54 3 83 24m-83-4 54 16" stroke="#acd2ff" strokeWidth="7" /><path d="m189 65 63 19q11 3 11 16v90l-84-25V76q0-14 10-11Z" fill="#e7f3ff" stroke="#fff" strokeWidth="2" /><path d="m192 91 40 12m-40 4 40 12m-40 4 29 9" stroke="#b3d5ff" strokeWidth="6" /><path d="m130 164 43-26 27 10v42h-70Z" fill="#a9d0ff" opacity=".65" /><circle cx="386" cy="139" r="25" fill="#a2d4ff" fillOpacity=".5" stroke="#1478f9" strokeWidth="7" /><path d="m404 159 23 25" stroke="#1478f9" strokeWidth="10" strokeLinecap="round" /></svg>
}
