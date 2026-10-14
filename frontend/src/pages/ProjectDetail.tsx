import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Button, Collapse, Descriptions, Divider, Empty, Form, Input, InputNumber, List, Modal, Radio, Select, Space, Tabs, Tag, Typography, message } from 'antd'
import { ArrowLeftOutlined, CheckCircleOutlined, FileSearchOutlined, LinkOutlined, ReloadOutlined, RobotOutlined, SaveOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { DecisionStatus, Evidence, JudgmentOption, ProjectDetail as ProjectDetailData, ProjectSummary } from '../types'
import { ProcurementBriefing } from '../components/procurement'
import { AiDecisionTag, AiStatusTag, AnalysisScopeTag, CompanyMatchTag, companyMatchDescription, companyMatchLabels, DecisionTag, EmptyBlock, ErrorBlock, formatDate, formatFreshness, GateTag, LoadingBlock, ProjectTitle, sourceLabel } from '../components/Common'
import { DecisionHistoryList, VersionHistory } from '../components/ReadableFields'
import { normalizeOpportunityText } from '../components/opportunities/text'
import { ResearchDisplay } from '../components/research/ResearchDisplay'
import { ExpandableText } from '../components/ExpandableText'
import { AgentReport, LegacyJudgmentReport } from '../components/report/AgentReport'
import { EvidenceCitation } from '../components/report/EvidenceCitation'
import './ProjectDetail.css'

const { Title, Text, Paragraph } = Typography

interface DecisionFormState {
  status: DecisionStatus
  owner: string
  next_action: string
  note: string
  selected_option_id: string | null
}

const emptyDecision: DecisionFormState = { status: 'new', owner: '', next_action: '', note: '', selected_option_id: null }

const detailTabs = [
  { key: 'procurement', label: '采购信息' },
  { key: 'analysis', label: 'AI 研判' },
  { key: 'research', label: '项目调查' },
  { key: 'decision', label: '行动与决定' },
  { key: 'history', label: '历史与关联' },
]

function decisionFromProject(project: ProjectSummary, currentJudgmentId: number | null, judgmentStale: boolean, blockedGate: boolean): DecisionFormState {
  const selectionFromOldJudgment = project.decision.judgment_id != null && project.decision.judgment_id !== currentJudgmentId
  return {
    status: project.decision.status,
    owner: project.decision.owner ?? '',
    next_action: project.decision.next_action ?? '',
    note: project.decision.note ?? '',
    selected_option_id: selectionFromOldJudgment || judgmentStale || blockedGate ? null : project.decision.selected_option_id,
  }
}

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const activeTab = detailTabs.find((tab) => tab.key === requestedTab)?.key ?? 'procurement'
  const tabBarRef = useRef<HTMLDivElement>(null)
  const changeTab = (key: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('tab', key)
      return next
    }, { preventScrollReset: true })
    // A short panel can leave the reader below its content after switching.
    // Keep the panel entrance in view when switching from a scrolled position.
    const tabBar = tabBarRef.current
    if (tabBar?.parentElement && tabBar.parentElement.getBoundingClientRect().top < tabBar.getBoundingClientRect().top) {
      tabBar.scrollIntoView({ block: 'start' })
    }
  }
  const [detail, setDetail] = useState<ProjectDetailData | null>(null)
  const [decisionForm, setDecisionForm] = useState<DecisionFormState>(emptyDecision)
  const [decisionDirty, setDecisionDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [actionLoading, setActionLoading] = useState('')
  const [procurementLoading, setProcurementLoading] = useState(false)
  const [procurementError, setProcurementError] = useState<unknown>(null)
  const [linkTarget, setLinkTarget] = useState<number | null>(null)
  const [linkRelation, setLinkRelation] = useState<'same_project' | 'duplicate' | 'dismiss'>('same_project')
  const [linkNote, setLinkNote] = useState('')
  const [linkOpen, setLinkOpen] = useState(false)
  const projectIdRef = useRef(projectId)
  const decisionDirtyRef = useRef(false)
  const requestSequenceRef = useRef(0)
  const procurementRequestSequenceRef = useRef(0)
  projectIdRef.current = projectId

  const markDecisionDirty = (value: boolean) => {
    decisionDirtyRef.current = value
    setDecisionDirty(value)
  }

  const loadDetail = useCallback(async (showLoading = true, syncDecision = false) => {
    const requestedProjectId = projectIdRef.current
    const requestSequence = ++requestSequenceRef.current
    const isCurrentRequest = () => requestSequence === requestSequenceRef.current && projectIdRef.current === requestedProjectId
    if (!Number.isInteger(requestedProjectId) || requestedProjectId <= 0) {
      setError(new Error('项目编号无效'))
      setLoading(false)
      return
    }
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const result = await api.project(requestedProjectId)
      if (!isCurrentRequest()) return
      setDetail(result)
      const blockedGate = ['expired', 'closed', 'duplicate', 'excluded'].includes(result.gate_status)
      if (syncDecision || !decisionDirtyRef.current) setDecisionForm(decisionFromProject(result, result.judgment?.id ?? null, Boolean(result.judgment?.stale), blockedGate))
    } catch (loadError) {
      if (!isCurrentRequest()) return
      setError(loadError)
    } finally {
      if (showLoading && isCurrentRequest()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    requestSequenceRef.current += 1
    procurementRequestSequenceRef.current += 1
    markDecisionDirty(false)
    setDetail(null)
    setDecisionForm(emptyDecision)
    setError(null)
    setLoading(true)
    setProcurementLoading(false)
    setProcurementError(null)
    void loadDetail(true, true)
  }, [loadDetail, projectId])

  useEffect(() => {
    if (!detail || !['queued', 'running'].includes(detail.ai_status)) return
    const interval = detail.ai_status === 'running' || detail.agent_run?.status === 'running' ? 3000 : 8000
    const timer = window.setInterval(() => { void loadDetail(false) }, interval)
    return () => window.clearInterval(timer)
  }, [detail?.ai_status, detail?.agent_run?.status, loadDetail])

  const setDecision = <K extends keyof DecisionFormState>(key: K, value: DecisionFormState[K]) => {
    markDecisionDirty(true)
    setDecisionForm((current) => ({ ...current, [key]: value }))
  }

  const runAction = async (key: string, action: () => Promise<unknown>, success: string) => {
    setActionLoading(key)
    try {
      await action()
      message.success(success)
      await loadDetail(false)
    } catch (actionError) {
      message.error(actionError instanceof Error ? actionError.message : '操作失败，请稍后重试')
    } finally {
      setActionLoading('')
    }
  }

  const saveDecision = async () => {
    if (!detail) return
    const selected = detail.judgment?.options.find((option) => option.id === decisionForm.selected_option_id)
    const blockedGate = ['expired', 'closed', 'duplicate', 'excluded'].includes(detail.gate_status)
    if (decisionForm.selected_option_id && (!selected || detail.judgment?.stale || blockedGate)) {
      message.warning('当前行动方向已经过期或待复核，请重新分析后选择行动')
      return
    }
    setActionLoading('decision')
    try {
      await api.decision(projectId, { ...decisionForm, ...(decisionForm.selected_option_id && detail.judgment ? { judgment_id: detail.judgment.id } : {}) })
      markDecisionDirty(false)
      await loadDetail(true, true)
      message.success('人工决定已保存，历史记录已保留')
    } catch (saveError) {
      message.error(saveError instanceof Error ? saveError.message : '保存失败，请稍后重试')
    } finally {
      setActionLoading('')
    }
  }

  const submitLink = async () => {
    if (!linkTarget || linkTarget === projectId) {
      message.warning('请输入另一个项目编号')
      return
    }
    setActionLoading('link')
    try {
      const result = await api.link(projectId, { target_project_id: linkTarget, relation: linkRelation, note: linkNote })
      message.success(result.message || '关联提示已记录')
      setLinkOpen(false)
      setLinkTarget(null)
      setLinkNote('')
      await loadDetail()
    } catch (linkError) {
      message.error(linkError instanceof Error ? linkError.message : '关联操作失败')
    } finally {
      setActionLoading('')
    }
  }

  const refreshProcurement = async () => {
    const requestedProjectId = projectIdRef.current
    const requestSequence = ++procurementRequestSequenceRef.current
    const isCurrentRequest = () => requestSequence === procurementRequestSequenceRef.current && projectIdRef.current === requestedProjectId
    setProcurementLoading(true)
    setProcurementError(null)
    try {
      const result = await api.refreshDocuments(requestedProjectId)
      if (!isCurrentRequest()) return
      setDetail((current) => current && current.id === requestedProjectId ? { ...current, procurement: result.procurement } : current)
      message.success(result.message || '采购资料已更新')
      await loadDetail(false)
    } catch (refreshError) {
      if (!isCurrentRequest()) return
      setProcurementError(refreshError)
      message.error(refreshError instanceof Error ? refreshError.message : '采购资料核对失败，请稍后重试')
    } finally {
      if (isCurrentRequest()) setProcurementLoading(false)
    }
  }

  const options = detail?.judgment?.options ?? []
  const optionBlockedByGate = Boolean(detail && ['expired', 'closed', 'duplicate', 'excluded'].includes(detail.gate_status))
  const optionAvailable = Boolean(detail?.judgment && !detail.judgment.stale && !optionBlockedByGate && options.length > 0)
  const analysisMeta = detail?.judgment?.analysis_meta ?? detail?.analysis_meta ?? null
  const displayedDecision = detail?.ai_decision ?? detail?.judgment?.decision ?? null
  const historicalSelectionId = detail?.decision.selected_option_id ?? null
  const hasOldJudgmentSelection = Boolean(detail && historicalSelectionId && (detail.judgment?.stale || (detail.decision.judgment_id != null && (detail.judgment == null || detail.decision.judgment_id !== detail.judgment.id))))
  const hasHistoricalSelection = Boolean(detail && historicalSelectionId && (hasOldJudgmentSelection || optionBlockedByGate))
  const historicalJudgment = detail && detail.decision.judgment_id != null ? detail.judgment_history.find((history) => history.id === detail.decision.judgment_id) : undefined
  const historicalOption = hasHistoricalSelection && detail && historicalSelectionId
    ? historicalJudgment?.options.find((option) => option.id === historicalSelectionId) ?? (hasOldJudgmentSelection ? detail.judgment_history.flatMap((history) => history.options).find((option) => option.id === historicalSelectionId) : detail.judgment?.options.find((option) => option.id === historicalSelectionId))
    : undefined
  const optionDescription = detail?.judgment?.stale ? '这份判断来自旧输入，行动方向已锁定。请先重判，系统会保留旧判断与人工决定。' : hasOldJudgmentSelection ? '已有新的判断版本，旧人工选择需要重新确认。请选择当前行动方向后保存，旧决定会继续保留在历史中。' : optionBlockedByGate ? '项目当前处于不可选状态，不能选择现有行动方向。' : !optionAvailable ? '可以先记录负责人、下一步和待核实事项，后续再确认行动方向。' : detail?.needs_review ? '资料或规则需要复核；当前行动方向仍可选，请核对后保存人工决定。' : '可选择一个关注或核实方向，也可以直接填写人工下一步；保存将追加一条人工决定记录。'
  const noOptionsMessage = detail?.ai_status === 'not_configured' ? '配置模型后生成可选行动。' : !detail?.judgment ? '尚未生成行动建议，可先记录人工推进动作。' : options.length ? '当前判断不可作为行动选择依据' : '当前判断没有可选行动，请先确认待核实事项。'
  const gateRestored = detail?.gate_reasons.some((reason) => reason === '人工恢复规则排除') ?? false

  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取项目详情" /></div>
  if (error || !detail) return <div className="page-stack"><Button type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回机会池</Button><ErrorBlock error={error} onRetry={() => void loadDetail(true, true)} /></div>

  const equipmentFromProcurement = Array.from(new Set((detail.procurement?.items ?? []).map((item) => normalizeOpportunityText(item.name)).filter(Boolean)))
  const equipmentSummary = detail.equipment.length ? detail.equipment : equipmentFromProcurement
  const renderEvidence = (evidence: Evidence, key: string) => <EvidenceCitation key={key} projectId={projectId} evidence={evidence} procurement={detail.procurement} records={detail.records} sources={detail.research_sources ?? detail.judgment?.investigation?.sources ?? []} />

  const tabPanels = {
    procurement: (
      <div className="detail-tab-panel">
        <ProcurementBriefing projectId={projectId} briefing={detail.briefing} procurement={detail.procurement} records={detail.records} title={detail.title} onRefresh={() => void refreshProcurement()} refreshing={procurementLoading} error={procurementError} />

      </div>
    ),
    analysis: (
      <div className="detail-tab-panel">
        <section className="detail-summary" id="ai-judgment" aria-labelledby="detail-summary-heading">
          <div className="detail-summary-header">
            <div>
              <h2 className="detail-section-title" id="detail-summary-heading"><FileSearchOutlined /> 分析结论</h2>
              <div className="detail-summary-status"><AiStatusTag status={detail.ai_status} /><AiDecisionTag decision={displayedDecision} analysisMeta={analysisMeta} />{(analysisMeta || detail.judgment) && <AnalysisScopeTag analysisMeta={analysisMeta} />}<CompanyMatchTag status={analysisMeta?.company_match} /></div>
            </div>
            {detail.judgment?.stale && <Tag color="warning">输入已变化，待重新分析</Tag>}
          </div>
          <ExpandableText className="detail-summary-conclusion" text={detail.judgment?.summary || detail.ai_summary || (detail.ai_status === 'not_configured' ? '配置模型后生成判断' : '暂未形成一句话结论')} />
          {analysisMeta?.decision_scope === 'market' && <div className="detail-company-match-note"><Text strong>企业匹配：{companyMatchLabels[analysisMeta.company_match]}</Text><Text>{companyMatchDescription(analysisMeta.company_match)}</Text></div>}
          <div className="detail-summary-grid">
            <div className="detail-summary-block detail-summary-rule"><Text className="detail-label">规则先筛</Text>{detail.gate_reasons.length ? <ul className="detail-compact-list">{detail.gate_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <Text>没有额外排除或复核说明</Text>}</div>
            <div className="detail-summary-block"><Text className="detail-label">资料时间</Text><Paragraph>{formatDate(detail.updated_at, true)}</Paragraph><Text type="secondary">{detail.records.length} 条源记录 · {detail.versions.length || 0} 个版本</Text></div>
          </div>
        </section>

        {detail.needs_review && <div className="detail-inline-note detail-inline-note-warning"><WarningOutlined /><div><Text strong>人工决定需要复核</Text><Text>{hasOldJudgmentSelection ? '项目已有新的 AI 判断。旧人工决定和历史行动仍会保留，请在“行动与决定”中重新确认当前方向或修改下一步。' : '规则或资料发生变化，请核对当前判断后保存人工决定。'}</Text></div></div>}
        {detail.ai_status === 'failed' && <Alert className="detail-failure-note" type="error" showIcon message="AI 分析失败" description="请查看处理状态中的失败原因，并在修复配置后重试。" action={<Button type="link" onClick={() => navigate('/operations')}>查看任务</Button>} />}

        {!detail.judgment?.report && detail.judgment?.unknowns && detail.judgment.unknowns.length > 0 && <div className="detail-unknown-panel"><Text strong><WarningOutlined /> 待确认信息</Text><ul className="detail-compact-list">{detail.judgment.unknowns.map((unknown) => <li key={unknown}>{unknown}</li>)}</ul></div>}
        {!detail.judgment?.report && detail.judgment?.next_actions && detail.judgment.next_actions.length > 0 && <div className="detail-next-actions"><Text strong>AI 建议下一步</Text><ul className="detail-compact-list">{detail.judgment.next_actions.map((next) => <li key={next}>{next}</li>)}</ul></div>}
        {detail.judgment?.report && <AgentReport report={detail.judgment.report} overviewFirst renderEvidence={renderEvidence} />}
        {(!detail.judgment?.report || detail.agent_run) && <section className="detail-panel detail-analysis-card" id="ai-evidence" aria-labelledby="ai-evidence-heading">
          <header className="detail-section-header"><div className="detail-section-heading"><RobotOutlined /><h2 id="ai-evidence-heading">{detail.judgment?.report ? 'Agent 执行活动' : 'AI 理由与证据'}</h2></div><AiStatusTag status={detail.ai_status} /></header>
          {!detail.judgment?.report && detail.judgment?.demand_summary && <div className="detail-demand-strip"><Text className="detail-label">需求摘要</Text><Paragraph>{detail.judgment.demand_summary}</Paragraph></div>}
          {!detail.judgment?.report && (detail.judgment ? <div className="detail-judgment-body"><div className="detail-judgment-meta"><Text type="secondary">模型：{detail.judgment.model || '未标明'}</Text><Text type="secondary">生成于 {formatDate(detail.judgment.created_at, true)}</Text>{detail.judgment.stale && <Tag color="warning">待复核</Tag>}</div><List className="detail-reason-list" size="small" dataSource={detail.judgment.reasons} locale={{ emptyText: '暂无结构化理由' }} renderItem={(reason) => <List.Item><div><Text strong>{reason.text}</Text>{reason.evidence.length > 0 && <div className="detail-evidence-list">{reason.evidence.map((evidence, index) => <EvidenceCitation projectId={projectId} evidence={evidence} procurement={detail.procurement} records={detail.records} sources={detail.research_sources ?? detail.judgment?.investigation?.sources ?? []} key={evidenceKey(evidence, index)} />)}</div>}</div></List.Item>} /></div> : <Empty description={detail.ai_status === 'not_configured' ? '完成模型配置后生成判断' : '暂无 AI 判断'} image={Empty.PRESENTED_IMAGE_SIMPLE} />)}
          <AgentExecutionActivity run={detail.agent_run} retrying={actionLoading === 'analyze'} onRetry={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新分析任务')} />

        </section>}
      </div>
    ),
    research: (
      <div className="detail-tab-panel">
        <ResearchDisplay
          projectId={projectId}
          judgment={detail.judgment}
          latestRun={detail.agent_run}
          projectSources={detail.research_sources}
          researchSummary={detail.research_summary}
          onRetry={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新分析任务')}
        />

      </div>
    ),
    decision: (
      <div className="detail-tab-panel">
        {detail.needs_review && <div className="detail-inline-note detail-inline-note-warning"><WarningOutlined /><div><Text strong>人工决定需要复核</Text><Text>{hasOldJudgmentSelection ? '项目已有新的 AI 判断。旧人工决定和历史行动仍会保留，请重新确认当前方向或修改下一步。' : '规则或资料发生变化，请核对当前判断后保存人工决定。'}</Text></div></div>}
        <section className="detail-decision-card" id="decision" aria-labelledby="decision-heading">
          <header className="detail-section-header"><div className="detail-section-heading"><CheckCircleOutlined /><h2 id="decision-heading">可选行动与人工决定</h2></div><DecisionTag status={decisionForm.status} /></header>
          <Paragraph className="detail-decision-intro" type="secondary">{optionDescription}</Paragraph>
          {hasHistoricalSelection && <Alert className="detail-historical-alert" type="warning" showIcon message={hasOldJudgmentSelection ? '当前人工选择来自旧判断版本' : '当前人工选择暂不可修改'} description={hasOldJudgmentSelection ? `历史选择：${historicalOption?.title ?? `行动 ID ${historicalSelectionId}`}。系统不会把旧行动自动当作当前方向；请重新选择当前行动。` : `当前选择：${historicalOption?.title ?? `行动 ID ${historicalSelectionId}`}。项目当前处于不可选状态，人工决定历史仍会保留。`} />}
          {detail.judgment?.next_actions && detail.judgment.next_actions.length > 0 && <div className="detail-next-actions"><Text strong>AI 建议下一步</Text><ul className="detail-compact-list">{detail.judgment.next_actions.map((next) => <li key={next}>{next}</li>)}</ul></div>}

          {!optionAvailable ? <div className="detail-option-unavailable"><WarningOutlined /><Text>{noOptionsMessage}</Text></div> : <Radio.Group className="detail-option-grid" value={decisionForm.selected_option_id} onChange={(event) => setDecision('selected_option_id', event.target.value)}>{options.map((option) => <OptionCard key={option.id} option={option} />)}</Radio.Group>}
          <Divider />
          <Form layout="vertical" className="detail-decision-form">
            <div className="detail-decision-form-grid"><Form.Item label={<label htmlFor="decision-status">人工状态</label>}><Select id="decision-status" value={decisionForm.status} onChange={(value: DecisionStatus) => setDecision('status', value)} options={[{ value: 'new', label: '未处理' }, { value: 'following', label: '跟进中' }, { value: 'verify', label: '待核实' }, { value: 'closed', label: '已关闭' }]} /></Form.Item><Form.Item label={<label htmlFor="decision-owner">负责人</label>}><Input id="decision-owner" value={decisionForm.owner} onChange={(event) => setDecision('owner', event.target.value)} placeholder="填写负责同事" /></Form.Item><Form.Item label={<label htmlFor="decision-next-action">下一步</label>}><Input id="decision-next-action" value={decisionForm.next_action} onChange={(event) => setDecision('next_action', event.target.value)} placeholder="例如：取得技术清单并核对件号" /></Form.Item></div>
            <Form.Item label={<label htmlFor="decision-note">备注</label>}><Input.TextArea id="decision-note" value={decisionForm.note} onChange={(event) => setDecision('note', event.target.value)} rows={3} placeholder="记录人工判断、客户沟通或待确认信息" /></Form.Item>
            <div className="detail-decision-actions"><Text type={decisionDirty ? 'warning' : 'secondary'}>{decisionDirty ? '有未保存修改' : `最近保存：${formatDate(detail.decision.updated_at, true)}`}</Text><Button type="primary" icon={<SaveOutlined />} loading={actionLoading === 'decision'} disabled={!decisionDirty} onClick={() => void saveDecision()}>保存人工决定</Button></div>
          </Form>
        </section>

      </div>
    ),
    history: (
      <div className="detail-tab-panel">
        <section className="detail-secondary" id="history" aria-labelledby="history-heading">
          <div className="detail-secondary-heading-row"><h2 id="history-heading">历史、版本与关联</h2><Text type="secondary">{detail.judgment_history.length} 条判断 · {detail.decision_history.length} 次决定 · {detail.related_projects.length} 个关联</Text></div>
          <div className="detail-secondary-grid">
            <div className="detail-secondary-block"><div className="detail-secondary-heading"><Text strong>公告版本</Text><Text type="secondary">输入变化会触发复核</Text></div><Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[{ key: 'published', label: '发布时间', children: formatDate(detail.published_at, true) }, { key: 'deadline', label: '截止时间', children: formatDate(detail.deadline_at, true) }, { key: 'records', label: '源记录 ID', children: detail.record_ids.join('、') || '—' }, { key: 'versions', label: '版本数', children: detail.versions.length || '—' }]} />{detail.versions.length > 0 && <Collapse ghost items={[{ key: 'versions', label: '展开版本变更', children: <VersionHistory versions={detail.versions} /> }]} />}</div>
            <div className="detail-secondary-block"><div className="detail-secondary-heading"><Text strong><LinkOutlined /> 关联项目</Text><Button size="small" icon={<LinkOutlined />} onClick={() => setLinkOpen(true)}>记录关联</Button></div><Text type="secondary">疑似同项目、重复或排除提示不会自动合并，双方原文与决定都会保留。</Text>{detail.related_projects.length === 0 ? <EmptyBlock description="暂无关联项目" /> : <List className="detail-related-list" dataSource={detail.related_projects} renderItem={(related) => <List.Item actions={[<Button key="open" type="link" onClick={() => navigate(`/projects/${related.id}`)}>查看</Button>]}><List.Item.Meta title={<ProjectTitle project={related} onOpen={(relatedId) => navigate(`/projects/${relatedId}`)} />} description={<Space><GateTag status={related.gate_status} /><Text type="secondary">项目 #{related.id}</Text></Space>} /></List.Item>} />}</div>
            <div className="detail-secondary-block"><div className="detail-secondary-heading"><Text strong>历史判断与人工决定</Text><Text type="secondary">可追溯</Text></div><Collapse ghost items={[{ key: 'judgments', label: `历史判断（${detail.judgment_history.length}）`, children: detail.judgment_history.length === 0 ? <Text type="secondary">暂无历史判断</Text> : <List size="small" dataSource={detail.judgment_history} renderItem={(history) => <List.Item><div className="detail-history-item"><Space><AiDecisionTag decision={history.decision} analysisMeta={history.analysis_meta} /><AnalysisScopeTag analysisMeta={history.analysis_meta} /><Text type="secondary">{formatDate(history.created_at, true)}</Text>{history.stale && <Tag color="warning">待复核</Tag>}</Space><div>{history.summary || '暂无判断摘要'}</div>{(history.report || history.reasons.length > 0 || history.unknowns.length > 0 || history.next_actions.length > 0) && <Collapse ghost items={[{ key: `history-report-${history.id}`, label: history.report ? '展开 Agent 报告全文' : '展开旧报告全文', children: history.report ? <AgentReport report={history.report} renderEvidence={(evidence, key) => <EvidenceCitation key={key} projectId={projectId} evidence={evidence} procurement={detail.procurement} records={detail.records} sources={detail.research_sources ?? history.investigation?.sources ?? []} />} /> : <LegacyJudgmentReport judgment={history} renderEvidence={(evidence, key) => <EvidenceCitation key={key} projectId={projectId} evidence={evidence} procurement={detail.procurement} records={detail.records} sources={detail.research_sources ?? history.investigation?.sources ?? []} />} /> }]} />}</div></List.Item>} /> }, { key: 'decisions', label: `人工决定（${detail.decision_history.length}）`, children: <DecisionHistoryList history={detail.decision_history} /> }]} /></div>
          </div>
        </section>

      </div>
    ),
  }

  return (
    <div className="page-stack detail-page">
      <div className="detail-toolbar">
        <Button className="detail-back-button" type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回机会池</Button>
        <Text className="detail-refresh">项目 #{detail.id} · {formatFreshness(detail.updated_at)}</Text>
      </div>

      <header className="detail-hero">
        <Title level={2}>{detail.title || '未命名项目'}</Title>
        <div className="detail-subline">
          <Text className="detail-buyer">{detail.buyer || '采购方未标明'}</Text>
          <Space className="detail-status-tags" wrap>
            <GateTag status={detail.gate_status} />
            <DecisionTag status={detail.decision.status} />
            {detail.needs_review && <Tag color="warning">需要复核</Tag>}
          </Space>
        </div>
      </header>

      <aside className="detail-context-bar" aria-label="项目关键信息">
        <section className="detail-rail-section">
          <h2 className="detail-rail-heading">项目属性</h2>
          <div className="detail-meta-item"><Text>截止日期</Text><strong>{formatDate(detail.deadline_at, true)}</strong></div>
          <div className="detail-meta-item"><Text>设备线索</Text><span>{equipmentSummary.length ? equipmentSummary.join('、') : detail.judgment?.report ? '详见研判报告' : '详见采购信息'}</span></div>
          <div className="detail-meta-item"><Text>来源</Text><span>{sourceLabel(detail.source_id)}</span></div>
        </section>
        <section className="detail-rail-section detail-rail-actions">
          <h2 className="detail-rail-heading">项目操作</h2>
          <div className="detail-actions" aria-label="项目操作">
            {(detail.ai_status === 'failed' || detail.ai_status === 'stale' || detail.ai_status === 'succeeded') && <Button icon={<ReloadOutlined />} loading={actionLoading === 'analyze'} onClick={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新分析任务')}>重新分析</Button>}
            {detail.ai_status === 'not_configured' && <Button type="primary" icon={<RobotOutlined />} onClick={() => navigate('/settings')}>去配置模型</Button>}
            {detail.ai_status === 'idle' && <Button type="primary" icon={<RobotOutlined />} loading={actionLoading === 'analyze'} onClick={() => void runAction('analyze', () => api.analyze(projectId, false), '已提交分析任务')}>开始分析</Button>}
            {(detail.ai_status === 'queued' || detail.ai_status === 'running') && <Button type="primary" icon={<RobotOutlined />} disabled>分析中</Button>}
            {detail.gate_status === 'excluded' && <Button loading={actionLoading === 'gate'} onClick={() => void runAction('gate', () => api.gate(projectId, 'restore', '人工恢复机会'), '项目已恢复到机会池')}>恢复机会</Button>}
            {gateRestored && <Button loading={actionLoading === 'gate'} onClick={() => void runAction('gate', () => api.gate(projectId, 'clear', '清除人工恢复规则'), '已清除人工恢复规则')}>清除恢复</Button>}
          </div>
        </section>
      </aside>

      <main className="detail-workspace">
        <Tabs
          className="detail-tabs"
          activeKey={activeTab}
          onChange={changeTab}
          size="small"
          destroyOnHidden={false}
          renderTabBar={(props, DefaultTabBar) => (
            <div className="detail-tab-bar" ref={tabBarRef}>
              <DefaultTabBar {...props} />
            </div>
          )}
          items={detailTabs.map((tab) => ({ ...tab, forceRender: true, children: tabPanels[tab.key as keyof typeof tabPanels] }))}
        />
      </main>

      <Modal title="记录关联提示" open={linkOpen} onCancel={() => setLinkOpen(false)} onOk={() => void submitLink()} okText="保存关系" confirmLoading={actionLoading === 'link'}>
        <Form layout="vertical"><Form.Item label="对方项目编号" required><InputNumber min={1} value={linkTarget} onChange={(value) => setLinkTarget(value)} placeholder="输入项目 ID" style={{ width: '100%' }} /></Form.Item><Form.Item label="关系" required><Select value={linkRelation} onChange={setLinkRelation} options={[{ value: 'same_project', label: '同项目' }, { value: 'duplicate', label: '重复项目' }, { value: 'dismiss', label: '排除提示' }]} /></Form.Item><Form.Item label="备注"><Input.TextArea rows={3} value={linkNote} onChange={(event) => setLinkNote(event.target.value)} placeholder="说明人工确认依据" /></Form.Item></Form>
      </Modal>
    </div>
  )
}

function AgentExecutionActivity({ run, retrying, onRetry }: { run?: ProjectDetailData['agent_run']; retrying: boolean; onRetry: () => void }) {
  if (!run) return null

  const steps = Array.isArray(run.steps)
    ? run.steps.filter((step) => typeof step?.label === 'string' && step.label.trim().length > 0)
    : []
  const latestLabel = steps[steps.length - 1]?.label.trim()
  const statusLabel = run.status === 'running' ? '执行中' : run.status === 'succeeded' ? '已完成' : '失败'
  const statusColor = run.status === 'running' ? 'processing' : run.status === 'succeeded' ? 'success' : 'error'
  const usageItems = run.usage
    ? [
        ['模型调用', run.usage.model_calls],
        ['工具调用', run.usage.tool_calls],
        ['输入 token', run.usage.input_tokens],
        ['输出 token', run.usage.output_tokens],
      ].filter(([, value]) => typeof value === 'number')
    : []

  return (
    <section className="detail-agent-activity" aria-labelledby="detail-agent-activity-heading">
      <div className="detail-agent-activity-heading">
        <Text strong id="detail-agent-activity-heading"><RobotOutlined /> Agent 执行记录</Text>
        <Tag color={statusColor}>{statusLabel}</Tag>
      </div>
      {run.status === 'running' && <div className="detail-agent-latest"><Text type="secondary">最新进展</Text><Text>{latestLabel || '正在执行'}</Text></div>}
      {run.status === 'failed' && <Alert className="detail-agent-failure" type="error" showIcon message="分析失败" description={run.error || '分析任务失败，未返回具体原因'} action={<Button type="link" size="small" loading={retrying} onClick={onRetry}>重新分析</Button>} />}
      {run.status === 'succeeded' && <Text className="detail-agent-complete" type="secondary">分析已完成，执行步骤已记录。</Text>}
      {steps.length > 0 && <Collapse ghost items={[{ key: 'agent-steps', label: `查看执行步骤（${steps.length}）`, children: <ol className="detail-agent-step-list">{steps.map((step, index) => <li key={`${step.at}-${index}`}><span>{step.label}</span><Text type="secondary">{formatDate(step.at, true)}</Text></li>)}</ol> }]} />}
      {usageItems.length > 0 && <div className="detail-agent-usage" aria-label="执行统计">{usageItems.map(([label, value]) => <span key={label}><Text type="secondary">{label}</Text><strong>{value}</strong></span>)}</div>}
    </section>
  )
}

function OptionCard({ option }: { option: JudgmentOption }) {
  return <Radio value={option.id} className="detail-option-radio"><div className="detail-option-card"><div className="detail-option-title-row"><div><Text className="detail-option-kicker">可选推进方向</Text><h3>{option.title}</h3></div><span className="detail-option-state">可供选择</span></div><Paragraph>{option.description || '暂无说明'}</Paragraph><div className="detail-option-detail-grid"><div><Text className="detail-label">适用条件</Text><Paragraph>{option.suitable_for || '未说明'}</Paragraph></div><div><Text className="detail-label">建议下一步</Text><Paragraph>{option.next_action || '未说明'}</Paragraph></div></div>{option.tradeoffs.length > 0 && <div className="detail-option-list-block"><Text className="detail-label">主要取舍</Text><ul className="detail-compact-list">{option.tradeoffs.map((item) => <li key={item}>{item}</li>)}</ul></div>}{option.unknowns.length > 0 && <div className="detail-option-list-block"><Text className="detail-label">仍需确认</Text><ul className="detail-compact-list">{option.unknowns.map((item) => <li key={item}>{item}</li>)}</ul></div>}</div></Radio>
}

function evidenceKey(evidence: Evidence, index: number): string {
  if ('source_ref' in evidence) return `research-${evidence.source_ref}-${evidence.source_version}-${evidence.page ?? ''}-${index}`
  return `${evidence.document_id ?? 'record'}-${evidence.record_id}-${evidence.page ?? ''}-${evidence.quote}-${index}`
}
