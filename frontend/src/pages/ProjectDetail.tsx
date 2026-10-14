import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Button, Collapse, Space, Tabs, Tag, Typography, message } from 'antd'
import { ArrowLeftOutlined, FileSearchOutlined, ReloadOutlined, RobotOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { Evidence, ProjectDetail as ProjectDetailData } from '../types'
import { ErrorBlock, LoadingBlock, AiDecisionTag, AiStatusTag, AnalysisScopeTag, CompanyMatchTag, companyMatchDescription, companyMatchLabels, GateTag, formatDate, formatFreshness, kindLabel } from '../components/Common'
import { ProcurementReadingPanel, ProcurementSupplement, ReadingCorrections } from '../components/procurement'
import { FollowupPanel } from '../components/followup/FollowupPanel'
import { AgentReport, LegacyJudgmentReport } from '../components/report/AgentReport'
import { EvidenceCitation } from '../components/report/EvidenceCitation'
import { ResearchDisplay } from '../components/research/ResearchDisplay'
import './ProjectDetail.css'

const { Text, Paragraph, Title } = Typography

const detailTabs = [
  { key: 'procurement', label: '采购信息' },
  { key: 'analysis', label: 'AI 研判' },
  { key: 'followup', label: '跟进' },
] as const

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const activeTab = detailTabs.some((tab) => tab.key === requestedTab) ? requestedTab! : 'procurement'
  const tabBarRef = useRef<HTMLDivElement>(null)
  const [detail, setDetail] = useState<ProjectDetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [actionLoading, setActionLoading] = useState('')
  const [procurementLoading, setProcurementLoading] = useState(false)
  const [procurementError, setProcurementError] = useState<unknown>(null)
  const projectIdRef = useRef(projectId)
  const requestSequenceRef = useRef(0)
  projectIdRef.current = projectId

  const loadDetail = useCallback(async (showLoading = true) => {
    const requestedProjectId = projectIdRef.current
    const sequence = ++requestSequenceRef.current
    const current = () => sequence === requestSequenceRef.current && projectIdRef.current === requestedProjectId
    if (!Number.isInteger(requestedProjectId) || requestedProjectId <= 0) {
      setError(new Error('项目编号无效'))
      setLoading(false)
      return
    }
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const result = await api.project(requestedProjectId)
      if (current()) setDetail(result)
    } catch (loadError) {
      if (current()) setError(loadError)
    } finally {
      if (showLoading && current()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    requestSequenceRef.current += 1
    setDetail(null)
    setError(null)
    void loadDetail(true)
  }, [loadDetail, projectId])

  useEffect(() => {
    if (!detail || !['queued', 'running'].includes(detail.ai_status)) return
    const timer = window.setInterval(() => { void loadDetail(false) }, detail.ai_status === 'running' ? 3000 : 8000)
    return () => window.clearInterval(timer)
  }, [detail?.ai_status, loadDetail])

  const changeTab = (key: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('tab', key)
      return next
    }, { preventScrollReset: true })
    const tabBar = tabBarRef.current
    if (tabBar && tabBar.getBoundingClientRect().top < 0) tabBar.scrollIntoView({ block: 'start' })
  }

  const runAction = async (key: string, action: () => Promise<unknown>, success: string) => {
    setActionLoading(key)
    try {
      await action()
      message.success(success)
      await loadDetail(false)
    } catch (actionError) {
      message.error(actionError instanceof Error ? actionError.message : '操作失败，请稍后重试')
    } finally { setActionLoading('') }
  }

  const refreshProcurement = async () => {
    setProcurementLoading(true)
    setProcurementError(null)
    try {
      const result = await api.refreshDocuments(projectId)
      setDetail((current) => current ? { ...current, procurement: result.procurement } : current)
      message.success(result.message || '采购资料已更新')
      await loadDetail(false)
    } catch (refreshError) {
      setProcurementError(refreshError)
      message.error(refreshError instanceof Error ? refreshError.message : '采购资料更新失败，请稍后重试')
    } finally { setProcurementLoading(false) }
  }

  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取项目详情" /></div>
  if (error || !detail) return <div className="page-stack"><Button type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回机会池</Button><ErrorBlock error={error} onRetry={() => void loadDetail(true)} /></div>

  const reading = detail.procurement_reading ?? null
  const analysisMeta = detail.judgment?.analysis_meta ?? detail.analysis_meta ?? null
  const displayedDecision = detail.ai_decision ?? detail.judgment?.decision ?? null
  const evidenceSources = detail.research_sources ?? detail.judgment?.investigation?.sources ?? []
  const renderEvidence = (evidence: Evidence, key: string) => <EvidenceCitation key={key} projectId={projectId} evidence={evidence} procurement={detail.procurement} records={detail.records} sources={evidenceSources} />
  const gateRestored = detail.gate_reasons.some((reason) => reason === '人工恢复规则排除')
  const tabPanels = {
    procurement: <div className="detail-tab-panel detail-procurement-panel"><ProcurementReadingPanel reading={reading} legacyProcurement={detail.procurement} records={detail.records} title={detail.title} onRefresh={() => void refreshProcurement()} refreshing={procurementLoading} error={procurementError} onOpenOperations={() => navigate('/operations')} /><ProcurementSupplement projectId={projectId} records={detail.records} onUploaded={() => void loadDetail(false)} /><ReadingCorrections projectId={projectId} reading={reading} onCorrected={() => void loadDetail(false)} /><Collapse className="detail-history-drawer" ghost items={[{ key: 'history', label: '历史版本、原始引用与关联项目', children: <div className="detail-history-content"><div><Text strong>版本与记录</Text><DescriptionsFallback detail={detail} /></div><div><Text strong>历史 AI 研判</Text>{detail.judgment_history.length === 0 ? <Text type="secondary">暂无历史研判</Text> : <div className="detail-history-list">{detail.judgment_history.map((history) => <div key={history.id}><Space wrap><AiDecisionTag decision={history.decision} analysisMeta={history.analysis_meta} /><AnalysisScopeTag analysisMeta={history.analysis_meta} /><Text type="secondary">{formatDate(history.created_at, true)}</Text>{history.stale && <Tag color="warning">待复核</Tag>}</Space><p>{history.summary || '暂无摘要'}</p>{history.report ? <Collapse ghost items={[{ key: `report-${history.id}`, label: '展开历史报告', children: <AgentReport report={history.report} renderEvidence={renderEvidence} /> }]} /> : history.reasons.length > 0 ? <Collapse ghost items={[{ key: `reasons-${history.id}`, label: '展开历史理由', children: <LegacyJudgmentReport judgment={history} renderEvidence={renderEvidence} /> }]} /> : null}</div>)}</div>}</div><div><Text strong>关联项目</Text>{detail.related_projects.length === 0 ? <Text type="secondary">暂无关联项目</Text> : <ul className="detail-related-list">{detail.related_projects.map((related) => <li key={related.id}><Button type="link" onClick={() => navigate(`/projects/${related.id}`)}>{related.title || `项目 #${related.id}`}</Button><GateTag status={related.gate_status} /></li>)}</ul>}</div></div> }]} /></div>,
    analysis: <div className="detail-tab-panel"><section className="detail-analysis-overview"><div className="detail-summary-header"><div><h2 className="detail-section-title" id="detail-summary-heading"><FileSearchOutlined /> AI 研判</h2><div className="detail-summary-status"><AiStatusTag status={detail.ai_status} /><AiDecisionTag decision={displayedDecision} analysisMeta={analysisMeta} />{(analysisMeta || detail.judgment) && <AnalysisScopeTag analysisMeta={analysisMeta} />}<CompanyMatchTag status={analysisMeta?.company_match} /></div></div>{detail.judgment?.stale && <Tag color="warning">输入已变化，待复核</Tag>}</div><Paragraph className="detail-summary-conclusion">{detail.judgment?.summary || detail.ai_summary || (detail.ai_status === 'not_configured' ? '配置模型后生成研判' : '暂未形成一句话结论')}</Paragraph>{analysisMeta?.decision_scope === 'market' && <div className="detail-company-match-note"><Text strong>企业匹配：{companyMatchLabels[analysisMeta.company_match]}</Text><Text>{companyMatchDescription(analysisMeta.company_match)}</Text></div>}<div className="detail-summary-grid"><div className="detail-summary-block"><Text className="detail-label">研判范围</Text><p>基于采购说明回答机会价值、参与路径、风险与下一步。</p></div><div className="detail-summary-block"><Text className="detail-label">资料版本</Text><p>{reading?.material_version ?? '待标记'} · {formatFreshness(detail.updated_at)}</p></div></div></section>{detail.ai_status === 'failed' && <Alert className="detail-failure-note" type="error" showIcon message="AI 研判失败" description="失败原因和重试入口在处理状态中保留。" action={<Button type="link" onClick={() => navigate('/operations')}>查看处理状态</Button>} />}{detail.judgment?.unknowns && detail.judgment.unknowns.length > 0 && <section className="detail-unknown-panel"><Text strong><WarningOutlined /> 待确认信息</Text><ul>{detail.judgment.unknowns.map((unknown) => <li key={unknown}>{unknown}</li>)}</ul></section>}{detail.judgment?.next_actions && detail.judgment.next_actions.length > 0 && <section className="detail-next-actions"><Text strong>AI 建议下一步</Text><ul>{detail.judgment.next_actions.map((next) => <li key={next}>{next}</li>)}</ul></section>}{detail.judgment?.report && <AgentReport report={detail.judgment.report} overviewFirst renderEvidence={renderEvidence} />}{detail.judgment && !detail.judgment.report && <section className="detail-panel detail-analysis-card"><header className="detail-section-header"><h2>AI 理由与证据</h2><AiStatusTag status={detail.ai_status} /></header><div className="detail-judgment-meta"><Text type="secondary">模型：{detail.judgment.model || '未标明'}</Text><Text type="secondary">生成于 {formatDate(detail.judgment.created_at, true)}</Text></div><ul className="detail-reason-list">{detail.judgment.reasons.map((reason, index) => <li key={`${reason.text}-${index}`}><Text strong>{reason.text}</Text>{reason.evidence.length > 0 && <div className="detail-evidence-list">{reason.evidence.map((evidence, evidenceIndex) => <EvidenceCitation key={`${index}-${evidenceIndex}`} projectId={projectId} evidence={evidence} procurement={detail.procurement} records={detail.records} sources={evidenceSources} />)}</div>}</li>)}</ul></section>}{detail.agent_run && <AgentExecutionActivity run={detail.agent_run} retrying={actionLoading === 'analyze'} onRetry={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新研判任务')} />}<Collapse ghost items={[{ key: 'investigation', label: '查看调查范围与来源（内部资料）', children: <ResearchDisplay projectId={projectId} judgment={detail.judgment} latestRun={detail.agent_run} projectSources={detail.research_sources} researchSummary={detail.research_summary} onRetry={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新研判任务')} /> }]} /></div>,
    followup: <div className="detail-tab-panel"><FollowupPanel projectId={projectId} initialFollowup={detail.followup} /></div>,
  } as const

  return <div className="page-stack detail-page">
    <div className="detail-toolbar"><Button className="detail-back-button" type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回采购需求</Button><Text className="detail-refresh">项目 #{detail.id} · {formatFreshness(detail.updated_at)}</Text></div>
    <header className="detail-hero"><Title level={2}>{detail.title || '未命名项目'}</Title><div className="detail-subline"><Text className="detail-buyer">{detail.buyer || reading?.buyer || '采购方待核对'}</Text><Space className="detail-status-tags" wrap><Tag color={detail.demand_type === 'lead' || reading?.demand_type === 'lead' ? 'cyan' : 'blue'}>{detail.demand_type === 'lead' || reading?.demand_type === 'lead' ? '潜在线索' : '明确采购'}</Tag>{reading && <Tag>{reading.stage ? kindLabel(reading.stage) : '阶段待核对'}</Tag>}<GateTag status={detail.gate_status} />{detail.is_following && <Tag color="blue">已加入跟进</Tag>}</Space></div></header>
    <aside className="detail-context-bar" aria-label="项目关键信息"><section className="detail-rail-section"><h2 className="detail-rail-heading">采购属性</h2><div className="detail-meta-item"><Text>设备 / 服务</Text><span>{(detail.category?.length ? detail.category : reading?.category ?? detail.equipment).join('、') || '待核对'}</span></div><div className="detail-meta-item"><Text>地区</Text><span>{detail.region || reading?.region || '待核对'}</span></div><div className="detail-meta-item"><Text>采购阶段</Text><span>{reading?.stage || detail.procurement_stage || '待核对'}</span></div></section><section className="detail-rail-section detail-rail-actions"><h2 className="detail-rail-heading">项目操作</h2><div className="detail-actions" aria-label="项目操作">{(detail.ai_status === 'failed' || detail.ai_status === 'stale' || detail.ai_status === 'succeeded') && <Button icon={<ReloadOutlined />} loading={actionLoading === 'analyze'} onClick={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新研判任务')}>重新研判</Button>}{detail.ai_status === 'not_configured' && <Button type="primary" icon={<RobotOutlined />} onClick={() => navigate('/settings')}>去业务配置</Button>}{detail.ai_status === 'idle' && <Button type="primary" icon={<RobotOutlined />} loading={actionLoading === 'analyze'} onClick={() => void runAction('analyze', () => api.analyze(projectId, false), '已提交研判任务')}>开始研判</Button>}{detail.gate_status === 'excluded' && <Button loading={actionLoading === 'gate'} onClick={() => void runAction('gate', () => api.gate(projectId, 'restore', '人工恢复采购需求'), '采购需求已恢复')}>恢复需求</Button>}{gateRestored && <Button loading={actionLoading === 'gate'} onClick={() => void runAction('gate', () => api.gate(projectId, 'clear', '清除人工恢复规则'), '已清除恢复规则')}>清除恢复</Button>}</div></section></aside>
    <main className="detail-workspace"><Tabs className="detail-tabs" activeKey={activeTab} onChange={changeTab} size="small" destroyOnHidden={false} renderTabBar={(props, DefaultTabBar) => <div className="detail-tab-bar" ref={tabBarRef}><DefaultTabBar {...props} /></div>} items={detailTabs.map((tab) => ({ ...tab, forceRender: true, children: tabPanels[tab.key] }))} /></main>
  </div>
}

function DescriptionsFallback({ detail }: { detail: ProjectDetailData }) {
  return <dl className="detail-history-facts"><div><dt>发布时间</dt><dd>{formatDate(detail.published_at, true)}</dd></div><div><dt>资料更新时间</dt><dd>{formatDate(detail.updated_at, true)}</dd></div><div><dt>源记录</dt><dd>{detail.record_ids.join('、') || '—'}</dd></div><div><dt>版本数</dt><dd>{detail.versions.length || '—'}</dd></div></dl>
}

function AgentExecutionActivity({ run, retrying, onRetry }: { run: ProjectDetailData['agent_run']; retrying: boolean; onRetry: () => void }) {
  if (!run) return null
  const steps = Array.isArray(run.steps) ? run.steps.filter((step) => typeof step?.label === 'string' && step.label.trim()) : []
  return <section className="detail-agent-activity"><div className="detail-agent-activity-heading"><Text strong><RobotOutlined /> 研判执行记录</Text><Tag color={run.status === 'succeeded' ? 'success' : run.status === 'running' ? 'processing' : 'error'}>{run.status === 'succeeded' ? '已完成' : run.status === 'running' ? '执行中' : '失败'}</Tag></div>{run.status === 'failed' && <Alert type="error" showIcon message="研判失败" description={run.error || '任务失败，未返回具体原因'} action={<Button type="link" onClick={onRetry} loading={retrying}>重新研判</Button>} />}{steps.length > 0 && <Collapse ghost items={[{ key: 'steps', label: `查看执行步骤（${steps.length}）`, children: <ol className="detail-agent-step-list">{steps.map((step, index) => <li key={`${step.at}-${index}`}><span>{step.label}</span><Text type="secondary">{formatDate(step.at, true)}</Text></li>)}</ol> }]} />}</section>
}
