import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { Alert, Button, Input, Modal, Pagination, Select, message } from 'antd'
import { CheckCircleOutlined, ClockCircleOutlined, FolderOpenOutlined, PlusOutlined, ReloadOutlined, RightOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { ProjectSummary } from '../types'
import { ErrorBlock, LoadingBlock } from '../components/Common'
import { submissionMilestone } from '../components/procurement/reading'
import './FollowupPage.css'

const columns = [
  { key: 'pending', label: '待处理', hint: '评估项目，明确参与方向' },
  { key: 'working', label: '跟进中', hint: '推进任务，准备所需材料' },
  { key: 'submitted', label: '待结果', hint: '已确认提交，持续关注结果' },
  { key: 'finished', label: '已完成', hint: '记录结果，沉淀跟进经验' },
  { key: 'abandoned', label: '已放弃', hint: '保留记录，方便后续回溯' },
] as const
type ColumnKey = typeof columns[number]['key']
type StatusKey = 'all' | ColumnKey
type SortKey = 'updated' | 'deadline'
const outcomeLabels: Record<string, string> = { won: '已成交', lost: '未成交', other: '已结束', abandoned: '已放弃' }

function columnOf(project: ProjectSummary): ColumnKey {
  const followup = project.followup_summary
  if (followup?.disposition === 'abandoned' || followup?.outcome === 'abandoned') return 'abandoned'
  const stage = followup?.stage ?? project.followup_stage
  if (stage === 'finished') return 'finished'
  if (stage === 'submitted' || stage === 'awaiting_result') return 'submitted'
  return stage === 'working' ? 'working' : 'pending'
}

function deadlineOf(project: ProjectSummary): string | null {
  // Only the verified submission milestone can represent a submission deadline.
  return submissionMilestone(project.procurement_reading)?.at ?? null
}

function dateText(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(parsed).replaceAll('/', '-')
}

function remainingDays(value: string, now: number): number | null {
  const day = dateText(value)
  const today = dateText(new Date(now).toISOString())
  if (day === '—') return null
  return Math.round((Date.parse(`${day}T00:00:00+08:00`) - Date.parse(`${today}T00:00:00+08:00`)) / 86400000)
}

function DeadlineBadge({ project, now }: { project: ProjectSummary; now: number }) {
  const column = columnOf(project)
  const followup = project.followup_summary
  if (column === 'finished') return <span className="followup-badge is-success"><CheckCircleOutlined />{outcomeLabels[followup?.outcome ?? ''] ?? '已结束'}</span>
  if (column === 'abandoned') return <span className="followup-badge is-muted">已放弃</span>
  if (column === 'submitted') return <span className="followup-badge is-submitted"><CheckCircleOutlined />{followup?.stage === 'awaiting_result' ? '等待结果' : '已提交'}</span>
  const deadline = deadlineOf(project)
  const days = deadline ? remainingDays(deadline, now) : null
  if (days == null) return <span className="followup-badge is-muted"><ClockCircleOutlined />提交截止待核对</span>
  const hasTime = /[T\s]\d{2}:\d{2}/.test(deadline!)
  const overdue = days < 0 || (hasTime && new Date(deadline!).getTime() <= now)
  const label = overdue ? `已截止${days < 0 ? ` ${Math.abs(days)} 天` : ''}` : days === 0 ? '今天截止' : `剩余 ${days} 天`
  return <span className={`followup-badge ${overdue || days <= 7 ? 'is-urgent' : days <= 14 ? 'is-soon' : 'is-muted'}`} title={`提交截止：${dateText(deadline)}`}><time dateTime={deadline!}>{dateText(deadline).slice(5)}</time><span>{label}</span></span>
}

function FollowupCard({ project, now, from }: { project: ProjectSummary; now: number; from: string }) {
  const followup = project.followup_summary
  const owner = followup?.owner ?? project.decision?.owner
  const nextAction = followup?.next_action ?? project.decision?.next_action
  const column = columnOf(project)
  return <Link className="followup-project-card" to={`/projects/${project.id}?tab=followup`} state={{ from }} aria-label={`查看跟进：${project.title}`}>
    <h3 title={project.title}>{project.title}</h3>
    <div className="followup-card-badges"><DeadlineBadge project={project} now={now} />{followup?.disposition === 'paused' && <span className="followup-paused">暂缓</span>}</div>
    <dl>
      <div><dt>负责人</dt><dd className={!owner ? 'is-unassigned' : ''}>{owner || '待分配'}</dd></div>
      {column === 'finished' ? <div><dt>最近更新</dt><dd><time>{dateText(followup?.updated_at ?? project.updated_at)}</time></dd></div> : column === 'submitted' ? <div><dt>提交时间</dt><dd><time>{dateText(followup?.submitted_at)}</time></dd></div> : <div><dt>下一步</dt><dd title={nextAction || undefined}>{nextAction || (column === 'abandoned' ? '查看历史记录' : '待补充跟进动作')}</dd></div>}
    </dl>
    <span className="followup-card-arrow" aria-hidden="true"><RightOutlined /></span>
  </Link>
}

function AddProjectModal({ open, onClose, onAdded, followedIds }: { open: boolean; onClose: () => void; onAdded: () => void; followedIds: Set<number> }) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<ProjectSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [adding, setAdding] = useState<number | null>(null)
  const [added, setAdded] = useState<Set<number>>(new Set())
  const [retry, setRetry] = useState(0)
  const addLock = useRef(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setError(null)
    const timer = window.setTimeout(() => {
      void api.projects({ q: query.trim(), page, page_size: 8, sort: 'updated' }).then(result => {
        if (!cancelled) { setItems(result.items); setTotal(result.total) }
      }).catch(reason => { if (!cancelled) setError(reason) }).finally(() => { if (!cancelled) setLoading(false) })
    }, 250)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [open, query, page, retry])

  const addProject = async (project: ProjectSummary) => {
    if (addLock.current) return
    addLock.current = true
    setAdding(project.id)
    try {
      await api.updateFollowup(project.id, { is_following: true })
      setAdded(current => new Set([...current, project.id]))
      message.success('已加入项目跟进')
      onAdded()
    } catch (reason) { message.error(reason instanceof Error ? reason.message : '添加失败，请重试') }
    finally { addLock.current = false; setAdding(null) }
  }

  return <Modal title="添加项目" open={open} onCancel={onClose} footer={<Button onClick={onClose}>完成</Button>} width={660} className="followup-add-modal">
    <p className="followup-add-description">从已有采购需求中选择项目，加入跟进后即可记录负责人、任务与进展。</p>
    <Input allowClear prefix={<SearchOutlined />} value={query} onChange={event => { setQuery(event.target.value); setPage(1) }} placeholder="搜索项目名称、采购方或设备" aria-label="搜索可添加的项目" />
    <div className="followup-add-results" aria-busy={loading}>
      {loading ? <LoadingBlock text="正在读取项目" /> : error ? <ErrorBlock error={error} onRetry={() => setRetry(value => value + 1)} /> : items.length === 0 ? <p className="followup-add-empty">没有找到项目，试试其他关键词。</p> : items.map(project => {
        const alreadyAdded = project.is_following || followedIds.has(project.id) || added.has(project.id)
        return <div className="followup-add-row" key={project.id}><div><strong>{project.title}</strong><span>{project.buyer || '采购方待核对'}</span></div><Button aria-label={alreadyAdded ? '已添加' : '加入跟进'} size="small" type={alreadyAdded ? 'default' : 'primary'} icon={alreadyAdded ? <CheckCircleOutlined /> : <PlusOutlined />} loading={adding === project.id} disabled={alreadyAdded || adding !== null} onClick={() => void addProject(project)}>{alreadyAdded ? '已添加' : '加入跟进'}</Button></div>
      })}
    </div>
    {!loading && !error && total > 8 && <Pagination size="small" current={page} total={total} pageSize={8} showSizeChanger={false} onChange={setPage} />}
  </Modal>
}

export function FollowupPage() {
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [revision, setRevision] = useState(0)
  const [addOpen, setAddOpen] = useState(false)
  const [now, setNow] = useState(Date.now)
  const query = params.get('q')?.trim() ?? ''
  const requestedStatus = params.get('status')
  const activeStatus: StatusKey = columns.some(column => column.key === requestedStatus) ? requestedStatus as ColumnKey : 'all'
  const sort: SortKey = params.get('sort') === 'deadline' ? 'deadline' : 'updated'

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const load = async () => {
      const all: ProjectSummary[] = []
      let page = 1
      let total = 0
      do {
        const result = await api.projects({ is_following: true, page, page_size: 200, sort: 'updated' })
        if (cancelled) return
        all.push(...result.items)
        total = result.total
        if (result.items.length === 0) break
        page += 1
      } while (all.length < total)
      if (!cancelled) setProjects([...new Map(all.map(project => [project.id, project])).values()])
    }
    void load().catch(reason => { if (!cancelled) setError(reason) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [revision])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60000)
    return () => window.clearInterval(timer)
  }, [])

  const filtered = useMemo(() => {
    const needle = query.toLocaleLowerCase()
    const result = projects.filter(project => !needle || [project.title, project.buyer, ...(project.equipment ?? []), project.followup_summary?.owner, project.followup_summary?.next_action, project.decision?.owner, project.decision?.next_action].filter(Boolean).join(' ').toLocaleLowerCase().includes(needle))
    const timestamp = (value: string | null | undefined, fallback: number) => value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : fallback
    return result.sort((a, b) => sort === 'deadline'
      ? timestamp(deadlineOf(a), Infinity) - timestamp(deadlineOf(b), Infinity) || b.id - a.id
      : timestamp(b.followup_summary?.updated_at ?? b.updated_at, 0) - timestamp(a.followup_summary?.updated_at ?? a.updated_at, 0) || b.id - a.id)
  }, [projects, query, sort])
  const grouped = useMemo(() => Object.fromEntries(columns.map(column => [column.key, filtered.filter(project => columnOf(project) === column.key)])) as Record<ColumnKey, ProjectSummary[]>, [filtered])
  const followedIds = useMemo(() => new Set(projects.map(project => project.id)), [projects])
  const visibleColumns = activeStatus === 'all' ? columns : columns.filter(column => column.key === activeStatus)
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value === 'all' || value === 'updated') next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: true })
  }
  const from = location.pathname + location.search

  return <main className="followup-page">
    <header className="followup-page-heading"><div><h1>项目跟进</h1><p>管理我关注的采购项目，跟踪进展、记录动作，确保不错过重要节点。</p></div><Button aria-label="添加项目" type="primary" icon={<PlusOutlined />} className="followup-add-button" onClick={() => setAddOpen(true)}>添加项目</Button></header>
    <div className="followup-board-toolbar">
      <nav className="followup-status-tabs" aria-label="跟进状态筛选">{[{ key: 'all', label: '全部' }, ...columns].map(tab => <button key={tab.key} type="button" aria-pressed={activeStatus === tab.key} className={activeStatus === tab.key ? 'is-active' : ''} onClick={() => setFilter('status', tab.key)}>{tab.label}<span>{loading || error ? '—' : tab.key === 'all' ? filtered.length : grouped[tab.key as ColumnKey].length}</span></button>)}</nav>
      <div className="followup-board-actions"><Select aria-label="项目排序" value={sort} onChange={value => setFilter('sort', value)} variant="borderless" options={[{ value: 'updated', label: '最近跟进' }, { value: 'deadline', label: '截止时间' }]} /><Button type="text" icon={<ReloadOutlined spin={loading} />} aria-label="刷新跟进项目" disabled={loading} onClick={() => setRevision(value => value + 1)} /></div>
    </div>
    {query && <div className="followup-search-summary"><span>搜索“{query}”{!loading && !error && `，找到 ${filtered.length} 个跟进项目`}</span><Button type="link" size="small" onClick={() => { const next = new URLSearchParams(params); next.delete('q'); setParams(next, { replace: true }) }}>清除搜索</Button></div>}
    <section className="followup-board-region" aria-label="项目跟进看板" aria-busy={loading}>
      {error ? <ErrorBlock error={error} onRetry={() => setRevision(value => value + 1)} /> : loading ? <LoadingBlock text="正在读取跟进项目" /> : <>
        {projects.some(project => !project.followup_summary) && <Alert className="followup-compatibility-note" type="info" showIcon message="部分项目的跟进摘要尚未同步，请进入跟进记录核对负责人及进展。" />}
        <div className={`followup-board${activeStatus !== 'all' ? ' is-filtered' : ''}`} tabIndex={0} aria-label="按状态分组的项目，可横向滚动">
          {visibleColumns.map(column => <section className={`followup-column tone-${column.key}`} key={column.key} aria-labelledby={`followup-${column.key}`}>
            <header className="followup-column-heading"><h2 id={`followup-${column.key}`}>{column.label}<span>{grouped[column.key].length}</span></h2>{activeStatus === 'all' ? <button type="button" aria-label={`只看${column.label}项目`} onClick={() => setFilter('status', column.key)}><RightOutlined /></button> : <button type="button" aria-label="返回全部状态" onClick={() => setFilter('status', 'all')}><FolderOpenOutlined /></button>}</header>
            <div className="followup-column-cards">{grouped[column.key].map(project => <FollowupCard key={project.id} project={project} now={now} from={from} />)}
              {grouped[column.key].length === 0 && <div className="followup-column-empty"><FolderOpenOutlined /><p>{query ? '暂无匹配项目' : `暂无${column.label}项目`}</p><span>{column.hint}</span>{column.key === 'pending' && !query && <Button aria-label="添加项目" type="text" size="small" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>添加项目</Button>}</div>}
            </div>
          </section>)}
        </div>
        <footer className="followup-board-footer"><span>共 {filtered.length} 个跟进项目{activeStatus !== 'all' && ` · 当前显示 ${grouped[activeStatus].length} 个`}</span><span><ClockCircleOutlined /> 截止时间以采购说明中的已核对提交节点为准</span></footer>
      </>}
    </section>
    <AddProjectModal open={addOpen} onClose={() => setAddOpen(false)} onAdded={() => setRevision(value => value + 1)} followedIds={followedIds} />
  </main>
}
