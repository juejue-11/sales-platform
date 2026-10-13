import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Collapse, Drawer, Empty, List, Space, Spin, Tag, Typography } from 'antd'
import { ClockCircleOutlined, FileSearchOutlined, LinkOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { AgentRun, Investigation, Judgment, ResearchEvidence, ResearchSource, ResearchSourceRelation } from '../../types'
import { formatDate } from '../Common'
import './ResearchDisplay.css'

const { Text } = Typography

const investigationStatusMeta: Record<Investigation['status'], { label: string; color: string }> = {
  not_started: { label: '未开始', color: 'default' },
  running: { label: '补查中', color: 'processing' },
  completed: { label: '已完成', color: 'success' },
  limited: { label: '受限停止', color: 'warning' },
  failed: { label: '补查失败', color: 'error' },
  not_performed: { label: '未执行主动补查', color: 'default' },
}
const relationLabels: Record<ResearchSourceRelation, string> = {
  same_procurement: '同采购批次',
  related_project: '同项目其他阶段',
  buyer_background: '采购主体背景',
  unconfirmed: '待核实',
  unrelated: '不相关',
  conflict: '存在冲突',
}

const relationColors: Record<ResearchSourceRelation, string> = {
  same_procurement: 'blue',
  related_project: 'cyan',
  buyer_background: 'geekblue',
  unconfirmed: 'default',
  unrelated: 'default',
  conflict: 'error',
}

const sourceStatusLabels: Record<ResearchSource['status'], string> = {
  candidate: '候选',
  read: '已读取',
  failed: '读取失败',
  unsupported: '暂不支持',
  auth_required: '需要登录',
  unreadable: '无法读取',
}

const eventStatusLabels: Record<string, string> = {
  success: '完成',
  read: '已读取',
  verified: '已核实',
  unconfirmed: '关系待核实',
  rejected: '关系未通过',
  blocked: '访问受限',
  auth_required: '需要登录',
  no_results: '已查范围内无结果',
  no_content: '片段没有内容',
  no_configuration: '来源未配置',
  parse_failed: '解析失败',
  unsupported: '暂不支持',
  unreadable: '无法读取',
  limited: '受限停止',
  budget_exhausted: '已达调查限额',
  invalid_arguments: '参数无效',
  started: '开始',
  searching: '搜索中',
  reading: '读取中',
  verifying: '核实关系',
  completed: '完成',
  failed: '失败',
  stopped: '已停止',
}

const eventToolLabels: Record<string, string> = {
  search_project_sources: '搜索项目资料',
  read_research_source: '读取来源正文',
  assess_source_relation: '核实资料关系',
  search_research_content: '定位相关片段',
}

function getStatusMeta(status: Investigation['status']) {
  return investigationStatusMeta[status] ?? investigationStatusMeta.not_started
}

function relationLabel(relation: ResearchSourceRelation): string {
  return relationLabels[relation] ?? relationLabels.unconfirmed
}

function eventToolLabel(tool: string): string {
  return eventToolLabels[tool] ?? '研究工具'
}

function sourceDate(source: ResearchSource): string {
  const published = source.published_at ? `发布于 ${formatDate(source.published_at, true)}` : ''
  const fetched = source.fetched_at ? `读取于 ${formatDate(source.fetched_at, true)}` : ''
  return [published, fetched].filter(Boolean).join('，') || '时间未提供'
}

function sourceForEvidence(evidence: ResearchEvidence, source?: ResearchSource): ResearchSource {
  return source ?? {
    source_ref: evidence.source_ref,
    source_version: evidence.source_version,
    title: evidence.title,
    url: evidence.source_url,
    fetched_at: evidence.fetched_at,
    kind: 'page',
    status: 'read',
    relation: evidence.relation,
    relation_status: 'verified',
  }
}

interface ResearchSnapshotDrawerProps {
  projectId: number
  source: ResearchSource
  open: boolean
  onClose: () => void
  quote?: string
  page?: number | null
}

function ResearchSnapshotDrawer({ projectId, source, open, onClose, quote, page }: ResearchSnapshotDrawerProps) {
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof api.researchSourceVersion>> | null>(null)
  const requestRef = useRef(0)
  const chunkSize = 4000

  const loadSnapshot = useCallback(async (nextOffset: number, nextPage: number | undefined = page ?? undefined) => {
    const requestId = ++requestRef.current
    setLoading(true)
    setError(null)
    try {
      if (!source.source_version) {
        throw new Error('该来源没有保存的内容版本，暂时无法打开快照')
      }
      const result = await api.researchSourceVersion(projectId, source.source_ref, source.source_version, { page: nextPage, offset: nextOffset, limit: chunkSize })
      if (requestId !== requestRef.current) return
      setSnapshot(result)
      setOffset(result.offset)
    } catch (loadError) {
      if (requestId !== requestRef.current) return
      setError(loadError)
      setSnapshot(null)
    } finally {
      if (requestId === requestRef.current) setLoading(false)
    }
  }, [page, projectId, source.source_ref, source.source_version])

  useEffect(() => {
    if (!open) return
    setOffset(0)
    setSnapshot(null)
    void loadSnapshot(0)
    return () => { requestRef.current += 1 }
  }, [loadSnapshot, open])

  const errorText = error instanceof Error ? error.message : '保存快照读取失败，请稍后重试'
  const from = snapshot ? snapshot.offset + 1 : 0
  const to = snapshot ? Math.min(snapshot.offset + snapshot.text.length, snapshot.total_chars) : 0

  return (
    <Drawer
      className="research-snapshot-drawer"
      title={<span>{source.title || '研究来源快照'}</span>}
      placement="right"
      width={Math.min(640, window.innerWidth - 24)}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      <div className="research-snapshot-meta">
        <Space wrap size={[5, 5]}>
          <Tag color={relationColors[source.relation]}>{relationLabel(source.relation)}</Tag>
          <Tag>{sourceStatusLabels[source.status]}</Tag>
          {source.kind === 'document' && <Tag>文档</Tag>}
        </Space>
        <Text type="secondary">保存版本：{source.source_version || '未提供'}</Text>
        <Text type="secondary">读取时间：{source.fetched_at ? formatDate(source.fetched_at, true) : '未提供'}</Text>
      </div>

      {quote && <blockquote className="research-snapshot-quote">“{quote}”</blockquote>}
      {source.url && <div className="research-online-link"><a href={source.url} target="_blank" rel="noreferrer"><LinkOutlined /> 打开在线原文</a><Text type="secondary">在线内容可能更新</Text></div>}

      {loading && <div className="research-snapshot-loading"><Spin size="small" /><Text type="secondary">正在读取保存的快照…</Text></div>}
      {Boolean(error) && !loading && <Alert type="error" showIcon message="快照读取失败" description={errorText} action={<Button type="link" size="small" icon={<ReloadOutlined />} onClick={() => void loadSnapshot(offset)}>重试</Button>} />}
      {snapshot && !loading && (
        <>
          <div className="research-snapshot-range">
            <Text type="secondary">{snapshot.page ? `第 ${snapshot.page} 页` : '保存内容'} · {from}–{to} / {snapshot.total_chars} 字</Text>
          </div>
          <pre className="research-snapshot-text">{snapshot.text || '保存快照没有可显示的正文。'}</pre>
          <div className="research-snapshot-pagination">
            <Button size="small" disabled={offset <= 0} onClick={() => void loadSnapshot(Math.max(0, offset - chunkSize), snapshot.page ?? undefined)}>上一段</Button>
            <Button size="small" type="primary" disabled={!snapshot.has_more} onClick={() => void loadSnapshot(offset + snapshot.text.length, snapshot.page ?? undefined)}>下一段</Button>
            {(snapshot.source.pages ?? 0) > 1 && <>
              <Button size="small" disabled={(snapshot.page ?? 1) <= 1} onClick={() => void loadSnapshot(0, (snapshot.page ?? 1) - 1)}>上一页</Button>
              <Button size="small" disabled={(snapshot.page ?? 1) >= (snapshot.source.pages ?? 1)} onClick={() => void loadSnapshot(0, (snapshot.page ?? 1) + 1)}>下一页</Button>
            </>}
          </div>
        </>
      )}
    </Drawer>
  )
}

export function ResearchEvidenceCitation({ projectId, evidence, source }: { projectId: number; evidence: ResearchEvidence; source?: ResearchSource }) {
  const [open, setOpen] = useState(false)
  const resolvedSource = sourceForEvidence(evidence, source)
  const pageLabel = evidence.page && evidence.page > 0 ? ` · 第 ${evidence.page} 页` : ''

  return (
    <div className="research-evidence-citation">
      <div className="research-evidence-citation-heading">
        <Button type="link" size="small" className="research-evidence-citation-button" onClick={() => setOpen(true)}>
          <FileSearchOutlined /> {evidence.title || '研究来源'}{pageLabel}
        </Button>
        {evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer" aria-label="打开在线原文"><LinkOutlined /></a>}
      </div>
      <q>{evidence.quote}</q>
      <ResearchSnapshotDrawer projectId={projectId} source={resolvedSource} quote={evidence.quote} page={evidence.page} open={open} onClose={() => setOpen(false)} />
    </div>
  )
}

function InvestigationStatusBlock({ investigation, title }: { investigation: Investigation; title: string }) {
  const statusMeta = getStatusMeta(investigation.status)
  const latestEvent = investigation.events[investigation.events.length - 1]
  return (
    <div className="research-status-block">
      <div className="research-status-heading">
        <Text strong>{title}</Text>
        <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
      </div>
      {investigation.stop_reason && <div className="research-stop-reason"><WarningOutlined /><span>停止原因：{investigation.stop_reason}</span></div>}
      <div className="research-status-facts">
        <span><strong>{investigation.sources.length}</strong> 个来源</span>
        <span><strong>{investigation.events.length}</strong> 条调查记录</span>
        <span><strong>{investigation.unresolved.length}</strong> 个待确认事项</span>
      </div>
      {latestEvent && <div className="research-latest-event"><Text type="secondary">最新进展</Text><strong>{latestEvent.purpose || '调查步骤'}</strong><Tag>{eventStatusLabels[latestEvent.status] || latestEvent.status}</Tag></div>}
      {investigation.unresolved.length > 0 && <div className="research-unresolved"><Text strong>仍待确认</Text><ul>{investigation.unresolved.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    </div>
  )
}

function ResearchSourceList({ projectId, sources, title }: { projectId: number; sources: ResearchSource[]; title: string }) {
  const [selected, setSelected] = useState<ResearchSource | null>(null)
  if (sources.length === 0) return null

  return (
    <section className="research-sources" aria-label={title}>
      <div className="research-subheading"><h3>{title}</h3><Text type="secondary">{sources.length} 个来源</Text></div>
      <List
        className="research-source-list"
        dataSource={sources}
        renderItem={(source) => (
          <List.Item key={`${source.source_ref}-${source.source_version || 'current'}`}>
            <div className="research-source-main">
              <div className="research-source-title-row">
                <strong title={source.title}>{source.title || '未命名来源'}</strong>
                <Space wrap size={[4, 4]}>
                  <Tag color={relationColors[source.relation]}>{relationLabel(source.relation)}</Tag>
                  <Tag>{source.relation_status === 'verified' ? '关系已核实' : source.relation_status === 'rejected' ? '关系已排除' : '关系待核实'}</Tag>
                  <Tag>{sourceStatusLabels[source.status]}</Tag>
                </Space>
              </div>
              <div className="research-source-meta"><span>{sourceDate(source)}</span><span>{source.kind === 'document' ? '文档' : '网页'}</span>{source.pages ? <span>{source.pages} 页</span> : null}{source.source_version ? <span>版本 {source.source_version}</span> : null}</div>
              <div className="research-source-actions">
                <Button size="small" type="link" disabled={!source.source_version} onClick={() => setSelected(source)}>查看保存快照</Button>
                {source.url && <a href={source.url} target="_blank" rel="noreferrer">打开在线原文 <LinkOutlined /></a>}
                {!source.source_version && <Text type="secondary">尚无可查看的保存版本</Text>}
              </div>
              {source.error && <div className="research-source-error">{source.error}</div>}
            </div>
          </List.Item>
        )}
      />
      {selected && <ResearchSnapshotDrawer projectId={projectId} source={selected} open onClose={() => setSelected(null)} />}
      <Text className="research-relation-note" type="secondary">来源关系已核实只表示它与项目的关系得到支持，不等于采购事实已经确认。</Text>
    </section>
  )
}

function InvestigationEvents({ investigation }: { investigation: Investigation }) {
  if (investigation.events.length === 0) return null
  return (
    <Collapse
      className="research-events"
      ghost
      items={[{
        key: 'events',
        label: `展开调查记录（${investigation.events.length} 条）`,
        children: <ol className="research-event-list">{investigation.events.map((event, index) => <li key={`${event.at}-${index}`}>
          <div className="research-event-marker" />
          <div className="research-event-content">
            <div className="research-event-heading"><strong>{event.purpose || '调查步骤'}</strong><Tag>{eventStatusLabels[event.status] || event.status}</Tag></div>
            {event.query && <div className="research-event-query">查询：{event.query}</div>}
            <div className="research-event-meta"><span>{eventToolLabel(event.tool)}</span><span>{formatDate(event.at, true)}</span>{event.sources.length > 0 && <span>{event.sources.length} 个来源</span>}</div>
            {event.message && <div className="research-event-message">{event.message}</div>}
          </div>
        </li>)}</ol>,
      }]}
    />
  )
}

function InvestigationTechnicalDetails({ investigation, usage }: { investigation: Investigation; usage?: AgentRun['usage'] }) {
  const tools = Array.from(new Set(investigation.events.map((event) => event.tool).filter(Boolean)))
  if (!investigation.policy_version && tools.length === 0 && !usage) return null
  return (
    <details className="research-technical-details">
      <summary>查看调查策略与技术调用统计</summary>
      <div>
        {investigation.policy_version && <Text type="secondary">策略版本：{investigation.policy_version}</Text>}
        {usage && <Text type="secondary">模型调用 {usage.model_calls} 次，工具调用 {usage.tool_calls} 次</Text>}
        {tools.length > 0 && <Text type="secondary">原始工具名：{tools.join('、')}</Text>}
      </div>
    </details>
  )
}

function RunHistory({ projectId }: { projectId: number }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [details, setDetails] = useState<Record<number, AgentRun>>({})
  const [detailLoading, setDetailLoading] = useState<number | null>(null)
  const projectIdRef = useRef(projectId)
  const requestRef = useRef(0)
  projectIdRef.current = projectId

  const loadRuns = useCallback(async () => {
    const requestedProjectId = projectIdRef.current
    const requestId = ++requestRef.current
    setLoading(true)
    setError(null)
    try {
      const result = await api.researchRuns(requestedProjectId)
      if (requestId !== requestRef.current || projectIdRef.current !== requestedProjectId) return
      setRuns(result.items)
    } catch (loadError) {
      if (requestId === requestRef.current && projectIdRef.current === requestedProjectId) setError(loadError)
    } finally {
      if (requestId === requestRef.current) setLoading(false)
    }
  }, [])

  const loadRun = async (runId: number) => {
    const requestedProjectId = projectIdRef.current
    setDetailLoading(runId)
    try {
      const result = await api.researchRun(requestedProjectId, runId)
      if (projectIdRef.current !== requestedProjectId) return
      setDetails((current) => ({ ...current, [runId]: result }))
    } catch (loadError) {
      if (projectIdRef.current === requestedProjectId) setError(loadError)
    } finally {
      if (projectIdRef.current === requestedProjectId) setDetailLoading(null)
    }
  }

  const toggleHistory = (nextOpen: boolean) => {
    setOpen(nextOpen)
    if (nextOpen && runs.length === 0 && !loading) void loadRuns()
  }

  useEffect(() => {
    setOpen(false)
    setRuns([])
    setDetails({})
    setError(null)
    requestRef.current += 1
  }, [projectId])

  return (
    <section className="research-history">
      <Button type="text" className="research-history-toggle" aria-expanded={open} onClick={() => toggleHistory(!open)}>
        <ClockCircleOutlined /> 调查运行历史 <span>{open && runs.length > 0 ? `（${runs.length} 次）` : ''}</span>
      </Button>
      {open && <div className="research-history-body">
        {loading && <div className="research-inline-loading"><Spin size="small" /> 正在读取历史调查…</div>}
        {Boolean(error) && !loading && <Alert type="error" showIcon message="调查历史读取失败" description={error instanceof Error ? error.message : '请稍后重试'} action={<Button type="link" onClick={() => void loadRuns()}>重试</Button>} />}
        {!loading && !error && runs.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史调查记录" />}
        {!loading && !error && runs.length > 0 && <Collapse accordion items={runs.map((run) => {
          const status = run.investigation?.status
          const statusMeta = status ? getStatusMeta(status) : null
          const detail = details[run.id]
          const historyInvestigation: Investigation = detail?.investigation ?? {
            policy_version: '',
            status: detail?.status === 'failed' ? 'failed' : detail?.status === 'running' ? 'running' : 'not_performed',
            events: [],
            sources: [],
            unresolved: [],
          }
          return {
            key: String(run.id),
            label: <div className="research-history-label"><span>{formatDate(run.started_at, true)}</span>{statusMeta ? <Tag color={statusMeta.color}>{statusMeta.label}</Tag> : <Tag>{run.status === 'running' ? '执行中' : run.status === 'succeeded' ? '已完成' : '失败'}</Tag>}</div>,
            children: detail ? <div className="research-history-detail"><InvestigationStatusBlock investigation={historyInvestigation} title="本次运行调查" />{historyInvestigation.sources.length > 0 && <ResearchSourceList projectId={projectId} sources={historyInvestigation.sources} title="本次运行发现的资料" />}<InvestigationEvents investigation={historyInvestigation} /><InvestigationTechnicalDetails investigation={historyInvestigation} usage={detail.usage} />{detail.error && <div className="research-source-error">{detail.error}</div>}<Text type="secondary">本次报告与其他运行记录分开保存。</Text></div> : <div className="research-history-detail-placeholder">{detailLoading === run.id ? <Spin size="small" /> : <Button type="link" onClick={() => void loadRun(run.id)}>读取本次调查记录</Button>}</div>,
          }
        })} />}
      </div>}
    </section>
  )
}

export interface ResearchDisplayProps {
  projectId: number
  judgment?: Judgment | null
  latestRun?: AgentRun | null
  projectSources?: ResearchSource[]
  researchSummary?: {
    status: Investigation['status']
    source_count: number
    stop_reason?: string
    updated_at?: string
  }
  onRetry?: () => void
}

export function ResearchDisplay({ projectId, judgment, latestRun, projectSources = [], researchSummary, onRetry }: ResearchDisplayProps) {
  const reportInvestigation = judgment?.investigation ?? null
  const latestInvestigation = latestRun?.investigation ?? null
  const reportSources = reportInvestigation?.sources ?? (latestInvestigation?.sources.length ? latestInvestigation.sources : projectSources)
  const hasResearch = Boolean(reportInvestigation || latestInvestigation || researchSummary || projectSources.length)
  const reportRunIsLatest = judgment?.agent_run_id != null && judgment.agent_run_id === latestRun?.id
  const latestRunIsNewer = Boolean(latestRun && (!judgment || (judgment.agent_run_id != null
    ? latestRun.id > judgment.agent_run_id
    : Date.parse(latestRun.started_at) > Date.parse(judgment.created_at))))
  const separateLatestInvestigation = reportInvestigation && latestInvestigation && !reportRunIsLatest && latestRunIsNewer ? latestInvestigation : null
  const summaryStatus = (reportRunIsLatest || latestRunIsNewer ? latestInvestigation?.status ?? researchSummary?.status : undefined) ?? reportInvestigation?.status ?? researchSummary?.status
  const displayedSourceCount = reportRunIsLatest || latestRunIsNewer
    ? researchSummary?.source_count ?? latestInvestigation?.sources.length ?? 0
    : reportSources.length
  const summaryInvestigation: Investigation | null = !reportInvestigation && !latestInvestigation && researchSummary
    ? { policy_version: '', status: researchSummary.status, stop_reason: researchSummary.stop_reason, events: [], sources: projectSources, unresolved: [] }
    : null

  if (!hasResearch) {
    return <section className="research-panel research-panel-empty" id="research" aria-labelledby="research-heading"><div className="research-panel-heading"><h2 id="research-heading">项目调查</h2><Tag>未执行主动补查</Tag></div><p>当前报告没有主动调查记录，结论仍以已有公告正文和采购附件为依据。</p></section>
  }

  const summaryMeta = summaryStatus ? getStatusMeta(summaryStatus) : null
  return (
    <section className="research-panel" id="research" aria-labelledby="research-heading">
      <div className="research-panel-heading">
        <div><h2 id="research-heading">项目调查</h2><p>主动补查的结果、资料关系与仍待确认事项。</p></div>
        <Space wrap size={[6, 6]}>
          {summaryMeta && <Tag color={summaryMeta.color}>{summaryMeta.label}</Tag>}
          {researchSummary && <Text type="secondary">{displayedSourceCount} 个来源</Text>}
          {onRetry && latestRun?.status !== 'running' && <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>重新调查</Button>}
        </Space>
      </div>

      {reportInvestigation && <InvestigationStatusBlock investigation={reportInvestigation} title={reportRunIsLatest ? '当前报告的调查' : '当前报告保留的调查'} />}
      {!reportInvestigation && latestInvestigation && <InvestigationStatusBlock investigation={latestInvestigation} title="本轮运行的调查" />}
      {separateLatestInvestigation && <InvestigationStatusBlock investigation={separateLatestInvestigation} title={latestRun?.status === 'running' ? '本轮补查（当前运行）' : '本轮补查'} />}
      {summaryInvestigation && <InvestigationStatusBlock investigation={summaryInvestigation} title="项目调查状态" />}
      {!reportInvestigation && !latestInvestigation && researchSummary?.stop_reason && <div className="research-stop-reason"><WarningOutlined /><span>停止原因：{researchSummary.stop_reason}</span></div>}
      {separateLatestInvestigation && <div className="research-active-note"><Tag color={separateLatestInvestigation.status === 'failed' ? 'error' : separateLatestInvestigation.status === 'limited' ? 'warning' : 'processing'}>{separateLatestInvestigation.status === 'failed' ? '本轮补查失败' : separateLatestInvestigation.status === 'limited' ? '本轮受限停止' : '本轮补查'}</Tag><span>旧报告继续保留，本轮调查单独记录。</span></div>}
      {reportInvestigation && latestRun && latestRunIsNewer && !reportRunIsLatest && latestRun.status === 'failed' && !latestInvestigation && <div className="research-active-note"><Tag color="error">本轮补查失败</Tag><span>本轮运行未形成新的调查记录，旧报告继续保留。</span></div>}

      {reportSources.length > 0 && <ResearchSourceList projectId={projectId} sources={reportSources} title={reportInvestigation ? '本次调查发现的资料' : '已登记的调查资料'} />}
      {separateLatestInvestigation && separateLatestInvestigation.sources.length > 0 && <ResearchSourceList projectId={projectId} sources={separateLatestInvestigation.sources} title="本轮补查发现的资料" />}
      {reportInvestigation && <InvestigationEvents investigation={reportInvestigation} />}
      {!reportInvestigation && latestInvestigation && <InvestigationEvents investigation={latestInvestigation} />}
      {separateLatestInvestigation && <InvestigationEvents investigation={separateLatestInvestigation} />}
      {reportInvestigation && <InvestigationTechnicalDetails investigation={reportInvestigation} usage={reportRunIsLatest ? latestRun?.usage : undefined} />}
      {separateLatestInvestigation && <InvestigationTechnicalDetails investigation={separateLatestInvestigation} usage={latestRun?.usage} />}
      <RunHistory projectId={projectId} />
    </section>
  )
}
