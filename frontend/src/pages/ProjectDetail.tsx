import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Collapse, Descriptions, Divider, Empty, Form, Input, InputNumber, List, Modal, Radio, Select, Space, Table, Tag, Typography, message } from 'antd'
import { ArrowLeftOutlined, CheckCircleOutlined, FileSearchOutlined, FileTextOutlined, LinkOutlined, ReloadOutlined, RobotOutlined, SaveOutlined, SearchOutlined, WarningOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { DecisionStatus, Evidence, JudgmentOption, ProcurementData, ProcurementDocument, ProcurementItem, ProjectDetail as ProjectDetailData, ProjectSummary } from '../types'
import { AiDecisionTag, AiStatusTag, DecisionTag, EmptyBlock, ErrorBlock, formatDate, formatFreshness, GateTag, LoadingBlock, ProjectTitle, sourceLabel } from '../components/Common'
import { DecisionHistoryList, StructuredFields, VersionHistory } from '../components/ReadableFields'
import { cleanQualificationExcerpt, cleanScopeExcerpt, normalizeOpportunityText } from '../components/opportunities/text'
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
  projectIdRef.current = projectId

  const markDecisionDirty = (value: boolean) => {
    decisionDirtyRef.current = value
    setDecisionDirty(value)
  }

  const loadDetail = useCallback(async (showLoading = true, syncDecision = false) => {
    const requestedProjectId = projectIdRef.current
    const requestSequence = ++requestSequenceRef.current
    if (!Number.isInteger(requestedProjectId) || requestedProjectId <= 0) {
      setError(new Error('项目编号无效'))
      setLoading(false)
      return
    }
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const result = await api.project(requestedProjectId)
      if (requestSequence !== requestSequenceRef.current || projectIdRef.current !== requestedProjectId) return
      setDetail(result)
      const blockedGate = ['expired', 'closed', 'duplicate', 'excluded'].includes(result.gate_status)
      if (syncDecision || !decisionDirtyRef.current) setDecisionForm(decisionFromProject(result, result.judgment?.id ?? null, Boolean(result.judgment?.stale), blockedGate))
    } catch (loadError) {
      if (requestSequence !== requestSequenceRef.current || projectIdRef.current !== requestedProjectId) return
      setError(loadError)
    } finally {
      if (showLoading && requestSequence === requestSequenceRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    requestSequenceRef.current += 1
    markDecisionDirty(false)
    setDetail(null)
    setDecisionForm(emptyDecision)
    setError(null)
    setLoading(true)
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
      message.warning('当前方案已经过期或待复核，请重新分析后选择方案')
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
    setProcurementLoading(true)
    setProcurementError(null)
    try {
      const result = await api.refreshDocuments(projectId)
      setDetail((current) => current ? { ...current, procurement: result.procurement } : current)
      message.success(result.message || '采购资料已更新')
      await loadDetail(false)
    } catch (refreshError) {
      setProcurementError(refreshError)
      message.error(refreshError instanceof Error ? refreshError.message : '采购资料核对失败，请稍后重试')
    } finally {
      setProcurementLoading(false)
    }
  }

  const options = detail?.judgment?.options ?? []
  const optionBlockedByGate = Boolean(detail && ['expired', 'closed', 'duplicate', 'excluded'].includes(detail.gate_status))
  const optionAvailable = Boolean(detail?.judgment && !detail.judgment.stale && !optionBlockedByGate && options.length > 0)
  const historicalSelectionId = detail?.decision.selected_option_id ?? null
  const hasOldJudgmentSelection = Boolean(detail && historicalSelectionId && (detail.judgment?.stale || (detail.decision.judgment_id != null && (detail.judgment == null || detail.decision.judgment_id !== detail.judgment.id))))
  const hasHistoricalSelection = Boolean(detail && historicalSelectionId && (hasOldJudgmentSelection || optionBlockedByGate))
  const historicalJudgment = detail && detail.decision.judgment_id != null ? detail.judgment_history.find((history) => history.id === detail.decision.judgment_id) : undefined
  const historicalOption = hasHistoricalSelection && detail && historicalSelectionId
    ? historicalJudgment?.options.find((option) => option.id === historicalSelectionId) ?? (hasOldJudgmentSelection ? detail.judgment_history.flatMap((history) => history.options).find((option) => option.id === historicalSelectionId) : detail.judgment?.options.find((option) => option.id === historicalSelectionId))
    : undefined
  const optionDescription = detail?.judgment?.stale ? '这份判断来自旧输入，方案已锁定。请先重判，系统会保留旧判断与人工决定。' : hasOldJudgmentSelection ? '已有新的判断版本，旧人工选择需要重新确认。请选择当前方案后保存，旧决定会继续保留在历史中。' : optionBlockedByGate ? '项目当前处于不可选状态，不能选择现有方案。' : !optionAvailable ? '可以先记录负责人、下一步和待核实事项，后续再确认方案。' : detail?.needs_review ? '资料或规则需要复核；当前判断仍可选，请核对后保存人工决定。' : '选择一个方案后填写负责人和下一步，保存将追加一条人工决定记录。'
  const noOptionsMessage = detail?.ai_status === 'not_configured' ? '配置模型后生成可比较的方案。' : !detail?.judgment ? '尚未生成方案，可先记录人工推进动作。' : options.length ? '当前判断不可作为方案选择依据' : '当前判断没有可选方案，请先确认待核实事项。'
  const gateRestored = detail?.gate_reasons.some((reason) => reason === '人工恢复规则排除') ?? false

  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取项目详情" /></div>
  if (error || !detail) return <div className="page-stack"><Button type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回机会池</Button><ErrorBlock error={error} onRetry={() => void loadDetail(true, true)} /></div>

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

      <div className="detail-workspace">
        <main className="detail-main-column">
          <ProcurementBriefing briefing={detail.briefing} procurement={detail.procurement} records={detail.records} title={detail.title} onRefresh={() => void refreshProcurement()} refreshing={procurementLoading} error={procurementError} />

          <section className="detail-summary" id="ai-judgment" aria-labelledby="detail-summary-heading">
            <div className="detail-summary-header">
              <div>
                <h2 className="detail-section-title" id="detail-summary-heading"><FileSearchOutlined /> AI研判</h2>
                <div className="detail-summary-status"><AiStatusTag status={detail.ai_status} /><AiDecisionTag decision={detail.ai_decision} /></div>
              </div>
              {detail.judgment?.stale && <Tag color="warning">输入已变化，待重新分析</Tag>}
            </div>
            <p className="detail-summary-conclusion">{detail.judgment?.summary || detail.ai_summary || (detail.ai_status === 'not_configured' ? '配置模型后生成判断' : '暂未形成一句话结论')}</p>
            <div className="detail-summary-grid">
              <div className="detail-summary-block detail-summary-rule"><Text className="detail-label">规则先筛</Text>{detail.gate_reasons.length ? <ul className="detail-compact-list">{detail.gate_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <Text>没有额外排除或复核说明</Text>}</div>
              <div className="detail-summary-block"><Text className="detail-label">需求摘要</Text><Paragraph>{detail.judgment?.demand_summary || '暂无已验证的需求摘要，先以公告正文为准。'}</Paragraph></div>
              <div className="detail-summary-block"><Text className="detail-label">资料时间</Text><Paragraph>{formatDate(detail.updated_at, true)}</Paragraph><Text type="secondary">{detail.records.length} 条源记录 · {detail.versions.length || 0} 个版本</Text></div>
            </div>
          </section>

          {detail.needs_review && <div className="detail-inline-note detail-inline-note-warning"><WarningOutlined /><div><Text strong>人工决定需要复核</Text><Text>{hasOldJudgmentSelection ? '项目已有新的 AI 判断。旧人工决定和历史方案仍会保留，请在下方重新确认当前方案或修改下一步。' : '规则或资料发生变化，请核对当前判断后保存人工决定。'}</Text></div></div>}
          {detail.ai_status === 'failed' && <Alert className="detail-failure-note" type="error" showIcon message="AI 分析失败" description="请查看处理状态中的失败原因，并在修复配置后重试。" action={<Button type="link" onClick={() => navigate('/operations')}>查看任务</Button>} />}

          <div className="detail-columns">
            <div className="detail-main-column">
              <section className="detail-panel detail-record-card" id="records" aria-labelledby="records-heading">
                <header className="detail-section-header"><div className="detail-section-heading"><FileSearchOutlined /><h2 id="records-heading">公告正文与事实</h2></div><Text type="secondary">{detail.records.length} 条源记录</Text></header>
                <div className="detail-panel-intro">原文是判断依据。展开记录查看正文、来源链接以及已抽取的事实。</div>
                <div className="detail-demand-strip"><Text className="detail-label">采购提示</Text><div>{detail.procurement?.items?.length ? <Text type="secondary">物料明细以采购资料为准；标题中的设备词仅表示项目背景。</Text> : detail.equipment.length ? detail.equipment.map((equipment) => <Tag key={equipment}>{equipment}</Tag>) : <Text type="secondary">设备信息待从正文确认</Text>}</div></div>
                {detail.records.length === 0 ? <EmptyBlock description="暂无公告正文" /> : <Collapse className="detail-record-list" items={detail.records.map((record) => ({ key: String(record.id), label: <div className="detail-record-label"><span>{record.title || `记录 #${record.id}`}</span><Text type="secondary">记录 #{record.id} · {record.parse_status || '已接入'}</Text></div>, children: <RecordPanel record={record} /> }))} />}
              </section>
            </div>

            <div className="detail-side-column">
              <section className="detail-panel detail-analysis-card" id="ai-evidence" aria-labelledby="ai-evidence-heading">
                <header className="detail-section-header"><div className="detail-section-heading"><RobotOutlined /><h2 id="ai-evidence-heading">AI 理由与证据</h2></div><AiStatusTag status={detail.ai_status} /></header>
                {detail.judgment ? <div className="detail-judgment-body"><div className="detail-judgment-meta"><Text type="secondary">模型：{detail.judgment.model || '未标明'}</Text><Text type="secondary">生成于 {formatDate(detail.judgment.created_at, true)}</Text>{detail.judgment.stale && <Tag color="warning">待复核</Tag>}</div><List className="detail-reason-list" size="small" dataSource={detail.judgment.reasons} locale={{ emptyText: '暂无结构化理由' }} renderItem={(reason) => <List.Item><div><Text strong>{reason.text}</Text>{reason.evidence.length > 0 && <div className="detail-evidence-list">{reason.evidence.map((evidence, index) => <EvidenceItem evidence={evidence} procurement={detail.procurement} records={detail.records} key={`${evidence.document_id ?? 'record'}-${evidence.record_id}-${evidence.page ?? ''}-${evidence.quote}-${index}`} />)}</div>}</div></List.Item>} /></div> : <Empty description={detail.ai_status === 'not_configured' ? '完成模型配置后生成判断' : '暂无 AI 判断'} image={Empty.PRESENTED_IMAGE_SIMPLE} />}
                <AgentExecutionActivity run={detail.agent_run} retrying={actionLoading === 'analyze'} onRetry={() => void runAction('analyze', () => api.analyze(projectId, true), '已提交重新分析任务')} />
                {detail.judgment?.unknowns && detail.judgment.unknowns.length > 0 && <div className="detail-unknown-panel"><Text strong><WarningOutlined /> 待确认信息</Text><ul className="detail-compact-list">{detail.judgment.unknowns.map((unknown) => <li key={unknown}>{unknown}</li>)}</ul></div>}
              </section>
            </div>
          </div>

          <section className="detail-decision-card" id="decision" aria-labelledby="decision-heading">
            <header className="detail-section-header"><div className="detail-section-heading"><CheckCircleOutlined /><h2 id="decision-heading">方案比较与人工决定</h2></div><DecisionTag status={decisionForm.status} /></header>
            <Paragraph className="detail-decision-intro" type="secondary">{optionDescription}</Paragraph>
            {hasHistoricalSelection && <Alert className="detail-historical-alert" type="warning" showIcon message={hasOldJudgmentSelection ? '当前人工选择来自旧判断版本' : '当前人工选择暂不可修改'} description={hasOldJudgmentSelection ? `历史选择：${historicalOption?.title ?? `方案 ID ${historicalSelectionId}`}。系统不会把旧方案自动当作当前方案；请重新选择当前方案。` : `当前选择：${historicalOption?.title ?? `方案 ID ${historicalSelectionId}`}。项目当前处于不可选状态，人工决定历史仍会保留。`} />}
            {detail.judgment?.next_actions && detail.judgment.next_actions.length > 0 && <div className="detail-next-actions"><Text strong>AI 建议下一步</Text><ul className="detail-compact-list">{detail.judgment.next_actions.map((next) => <li key={next}>{next}</li>)}</ul></div>}
            {!optionAvailable ? <div className="detail-option-unavailable"><WarningOutlined /><Text>{noOptionsMessage}</Text></div> : <Radio.Group className="detail-option-grid" value={decisionForm.selected_option_id} onChange={(event) => setDecision('selected_option_id', event.target.value)}>{options.map((option) => <OptionCard key={option.id} option={option} />)}</Radio.Group>}
            <Divider />
            <Form layout="vertical" className="detail-decision-form">
              <div className="detail-decision-form-grid"><Form.Item label={<label htmlFor="decision-status">人工状态</label>}><Select id="decision-status" value={decisionForm.status} onChange={(value: DecisionStatus) => setDecision('status', value)} options={[{ value: 'new', label: '未处理' }, { value: 'following', label: '跟进中' }, { value: 'verify', label: '待核实' }, { value: 'closed', label: '已关闭' }]} /></Form.Item><Form.Item label={<label htmlFor="decision-owner">负责人</label>}><Input id="decision-owner" value={decisionForm.owner} onChange={(event) => setDecision('owner', event.target.value)} placeholder="填写负责同事" /></Form.Item><Form.Item label={<label htmlFor="decision-next-action">下一步</label>}><Input id="decision-next-action" value={decisionForm.next_action} onChange={(event) => setDecision('next_action', event.target.value)} placeholder="例如：取得技术清单并核对件号" /></Form.Item></div>
              <Form.Item label={<label htmlFor="decision-note">备注</label>}><Input.TextArea id="decision-note" value={decisionForm.note} onChange={(event) => setDecision('note', event.target.value)} rows={3} placeholder="记录人工判断、客户沟通或待确认信息" /></Form.Item>
              <div className="detail-decision-actions"><Text type={decisionDirty ? 'warning' : 'secondary'}>{decisionDirty ? '有未保存修改' : `最近保存：${formatDate(detail.decision.updated_at, true)}`}</Text><Button type="primary" icon={<SaveOutlined />} loading={actionLoading === 'decision'} disabled={!decisionDirty} onClick={() => void saveDecision()}>保存人工决定</Button></div>
            </Form>
          </section>

          <section className="detail-secondary" id="history" aria-labelledby="history-heading">
            <div className="detail-secondary-heading-row"><h2 id="history-heading">历史、版本与关联</h2><Text type="secondary">{detail.judgment_history.length} 条判断 · {detail.decision_history.length} 次决定 · {detail.related_projects.length} 个关联</Text></div>
            <Collapse ghost items={[{ key: 'secondary', label: <span className="detail-secondary-label">展开历史与关联</span>, children: <div className="detail-secondary-grid">
              <div className="detail-secondary-block"><div className="detail-secondary-heading"><Text strong>公告版本</Text><Text type="secondary">输入变化会触发复核</Text></div><Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[{ key: 'published', label: '发布时间', children: formatDate(detail.published_at, true) }, { key: 'deadline', label: '截止时间', children: formatDate(detail.deadline_at, true) }, { key: 'records', label: '源记录 ID', children: detail.record_ids.join('、') || '—' }, { key: 'versions', label: '版本数', children: detail.versions.length || '—' }]} />{detail.versions.length > 0 && <Collapse ghost items={[{ key: 'versions', label: '展开版本变更', children: <VersionHistory versions={detail.versions} /> }]} />}</div>
              <div className="detail-secondary-block"><div className="detail-secondary-heading"><Text strong><LinkOutlined /> 关联项目</Text><Button size="small" icon={<LinkOutlined />} onClick={() => setLinkOpen(true)}>记录关联</Button></div><Text type="secondary">疑似同项目、重复或排除提示不会自动合并，双方原文与决定都会保留。</Text>{detail.related_projects.length === 0 ? <EmptyBlock description="暂无关联项目" /> : <List className="detail-related-list" dataSource={detail.related_projects} renderItem={(related) => <List.Item actions={[<Button key="open" type="link" onClick={() => navigate(`/projects/${related.id}`)}>查看</Button>]}><List.Item.Meta title={<ProjectTitle project={related} onOpen={(relatedId) => navigate(`/projects/${relatedId}`)} />} description={<Space><GateTag status={related.gate_status} /><Text type="secondary">项目 #{related.id}</Text></Space>} /></List.Item>} />}</div>
              <div className="detail-secondary-block"><div className="detail-secondary-heading"><Text strong>历史判断与人工决定</Text><Text type="secondary">可追溯</Text></div><Collapse ghost items={[{ key: 'judgments', label: `历史判断（${detail.judgment_history.length}）`, children: detail.judgment_history.length === 0 ? <Text type="secondary">暂无历史判断</Text> : <List size="small" dataSource={detail.judgment_history} renderItem={(history) => <List.Item><div className="detail-history-item"><Space><AiDecisionTag decision={history.decision} /><Text type="secondary">{formatDate(history.created_at, true)}</Text>{history.stale && <Tag color="warning">待复核</Tag>}</Space><div>{history.summary || '暂无判断摘要'}</div></div></List.Item>} /> }, { key: 'decisions', label: `人工决定（${detail.decision_history.length}）`, children: <DecisionHistoryList history={detail.decision_history} /> }]} /></div>
            </div> }]} />
          </section>
        </main>

        <aside className="detail-info-rail" aria-label="项目关键信息">
          <section className="detail-rail-section">
            <h2 className="detail-rail-heading">项目属性</h2>
            <div className="detail-meta-item"><Text>截止日期</Text><strong>{formatDate(detail.deadline_at, true)}</strong></div>
            <div className="detail-meta-item"><Text>涉及设备</Text><span>{detail.equipment.length ? detail.equipment.join('、') : '待从原文确认'}</span></div>
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
          <nav className="detail-rail-section detail-contents-nav" aria-label="页面内容">
            <h2 className="detail-rail-heading">本页内容</h2>
            <a href="#procurement-briefing-heading">采购信息</a>
            <a href="#ai-judgment">AI研判</a>
            <a href="#decision">方案与决定</a>
            <a href="#history">历史与关联</a>
          </nav>
        </aside>
      </div>

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

function RecordPanel({ record }: { record: ProjectDetailData['records'][number] }) {
  return <div className="detail-record-panel"><Space wrap><Tag>记录 #{record.id}</Tag>{record.parse_status && <Tag>{record.parse_status}</Tag>}{record.original_url && <a href={record.original_url} target="_blank" rel="noreferrer">打开原文</a>}<a href={api.archiveUrl(record.id)} target="_blank" rel="noreferrer">下载归档</a></Space><Paragraph className="detail-record-body">{record.body_text || '这条记录没有正文内容。'}</Paragraph><Collapse ghost items={[{ key: 'facts', label: '已抽取事实', children: <StructuredFields value={record.facts} emptyText="暂无已抽取事实" /> }, { key: 'inferences', label: '推测与待核实线索', children: <StructuredFields value={record.inferences} emptyText="暂无推测；请以公告正文为准" tone="warning" /> }]} /></div>
}

function sourceHrefWithPage(href: string, page?: number | null): string {
  if (!href || page == null || page <= 0) return href
  const hashIndex = href.indexOf('#')
  return `${hashIndex >= 0 ? href.slice(0, hashIndex) : href}#page=${page}`
}

function EvidenceItem({ evidence, procurement, records }: { evidence: Evidence; procurement: ProjectDetailData['procurement']; records: ProjectDetailData['records'] }) {
  const hasDocumentId = evidence.document_id !== null && evidence.document_id !== undefined && String(evidence.document_id).trim() !== ''
  const document = hasDocumentId ? procurement?.documents.find((candidate) => String(candidate.id) === String(evidence.document_id)) : undefined
  const record = records.find((candidate) => candidate.id === evidence.record_id)
  const attachmentName = evidence.document_name || document?.name || '采购附件'
  const page = evidence.page != null && evidence.page > 0 ? evidence.page : null
  const label = hasDocumentId
    ? `${attachmentName}${page ? ` · 第 ${page} 页` : ''} · 记录 #${evidence.record_id}`
    : `公告正文 · 记录 #${evidence.record_id}`
  const rawHref = hasDocumentId
    ? evidence.source_url || document?.url || ''
    : record?.original_url || record?.canonical_url || api.archiveUrl(evidence.record_id)
  const href = sourceHrefWithPage(rawHref, page)
  return <div className="detail-evidence-item"><span className="detail-evidence-source">{href ? <a href={href} target="_blank" rel="noreferrer">{label}</a> : label}</span><q>{evidence.quote}</q></div>
}

const procurementStatusMeta: Record<ProcurementData['status'], { label: string; description: string }> = {
  not_checked: { label: '尚未检查附件', description: '当前只有公告正文，附件资料还没有被读取。' },
  partial: { label: '已解析部分资料', description: '已读取部分采购资料，仍有字段需要人工核对。' },
  complete: { label: '资料已核对', description: '采购明细与交付信息来自已解析的采购资料。' },
  unavailable: { label: '资料暂不可用', description: '附件下载或解析没有完成，缺少部分采购依据。' },
}

const procurementDocumentStatusMeta: Record<ProcurementDocument['status'], { label: string; color?: string }> = {
  parsed: { label: '已解析', color: 'success' },
  unsupported: { label: '暂不支持解析', color: 'default' },
  auth_required: { label: '需登录', color: 'warning' },
  failed: { label: '下载失败', color: 'error' },
  unreadable: { label: '无法读取', color: 'error' },
}

const procurementErrorMessages: Record<string, string> = {
  source_unreachable: '来源链接不可达，请核对公告链接',
  fetch_failed: '附件下载失败，请核对原文件',
  auth_required: '附件需要登录后获取',
  unsupported: '暂不支持解析此附件',
  unreadable: '附件无法读取，请核对原文件',
  parse_failed: '附件解析失败，请核对原文件',
  empty: '附件内容为空，无法提取',
  timeout: '附件获取超时，请稍后重试',
}

type ProcurementMissingField = 'scope' | 'delivery' | 'items' | 'qualification' | 'contact'

function procurementValue(value: string | null | undefined): string {
  return normalizeOpportunityText(value) || ''
}

function procurementDocumentError(value: string | null | undefined): string {
  const raw = procurementValue(value)
  if (!raw) return ''
  const normalized = raw.toLowerCase()
  const matchedCode = Object.keys(procurementErrorMessages).find((code) => normalized === code || normalized.includes(code))
  if (matchedCode) return procurementErrorMessages[matchedCode]
  return '获取或解析失败，请核对原文件'
}

function procurementPageLabel(pages: number | null): string {
  return pages != null && pages > 0 ? `${pages} 页` : '页数未提取'
}

function originalNotStated(procurement: ProcurementData, field?: ProcurementMissingField): boolean {
  const aliases: Record<ProcurementMissingField, string[]> = {
    scope: ['scope', '范围', '采购范围'],
    delivery: ['delivery', '交付', '交货'],
    items: ['item', 'material', '物料', '明细'],
    qualification: ['qualification', '资格'],
    contact: ['contact', '联系方式'],
  }
  return procurement.missing_fields.some((missing) => {
    const value = procurementValue(missing).toLowerCase()
    if (!/(?:原文未写|原文未说明|原文未提供|not[_ -]?stated|not[_ -]?specified|original[_ -]?missing)/i.test(value)) return false
    if (!field) return true
    return aliases[field].some((alias) => value.includes(alias.toLowerCase())) || /original[_ -]?(?:missing|not[_ -]?stated|not[_ -]?specified)|原文未/.test(value)
  })
}

function procurementMissingValue(procurement: ProcurementData | null | undefined, value: string | null | undefined, field?: ProcurementMissingField): string {
  if (procurementValue(value)) return procurementValue(value)
  if (!procurement || procurement.status === 'not_checked') return '未检查附件'
  const statuses = procurement.documents.map((document) => document.status)
  if (statuses.includes('auth_required')) return '需登录'
  if (statuses.includes('unsupported')) return '待核对附件'
  if (statuses.includes('failed') || statuses.includes('unreadable') || procurement.status === 'unavailable') {
    const documentError = procurement.documents.find((document) => document.error)?.error
    return procurementDocumentError(documentError) || '获取或解析失败，请核对原文件'
  }
  if (originalNotStated(procurement, field)) return '原文未写'
  return '尚未提取到对应字段'
}

function procurementDocumentFor(item: ProcurementItem, documents: ProcurementDocument[]): ProcurementDocument | undefined {
  return documents.find((document) => String(document.id) === String(item.source_document_id))
}

function uniformProcurementField(items: ProcurementItem[], field: 'delivery_period' | 'delivery_location'): string {
  const values = Array.from(new Set(items.map((item) => procurementValue(item[field])).filter(Boolean)))
  return values.length === 1 ? values[0] : ''
}

function procurementSourceHref(item: ProcurementItem, document: ProcurementDocument | undefined, records: ProjectDetailData['records']): string {
  const baseHref = document?.url || (() => {
    const record = records.find((candidate) => candidate.id === item.record_id)
    return record?.original_url || record?.canonical_url || api.archiveUrl(item.record_id)
  })()
  if (baseHref) return sourceHrefWithPage(baseHref, item.page)
  const record = records.find((candidate) => candidate.id === item.record_id)
  return record?.original_url || record?.canonical_url || api.archiveUrl(item.record_id)
}

function ProcurementSource({ item, document, href }: { item: ProcurementItem; document?: ProcurementDocument; href: string }) {
  const label = `${document?.name || `记录 #${item.record_id}`}${item.page ? ` · 第 ${item.page} 页` : ''}`
  const quote = procurementValue(item.quote)
  return (
    <div className="detail-procurement-source-cell">
      {href ? <a href={href} target="_blank" rel="noreferrer">{label}</a> : <span>{label}</span>}
      {quote && <span className="detail-procurement-quote" title={quote}>“{quote}”</span>}
    </div>
  )
}

function ProcurementBriefing({
  briefing,
  procurement,
  records,
  title,
  onRefresh,
  refreshing,
  error,
}: {
  briefing: ProjectDetailData['briefing']
  procurement: ProjectDetailData['procurement']
  records: ProjectDetailData['records']
  title: string
  onRefresh: () => void
  refreshing: boolean
  error: unknown
}) {
  const fallbackRecord = records.find((record) => record.original_url || record.canonical_url)
  const sourceUrl = briefing?.original_url || fallbackRecord?.original_url || fallbackRecord?.canonical_url || ''
  const recordId = briefing?.record_id ?? records[0]?.id ?? null
  const status = procurement?.status ?? 'not_checked'
  const statusMeta = procurementStatusMeta[status]
  const parsedScope = cleanScopeExcerpt(procurement?.scope_summary, title)
  const scope = parsedScope || cleanScopeExcerpt(briefing?.scope_excerpt, title)
  const items = procurement?.items ?? []
  const documents = procurement?.documents ?? []
  const itemDeliveryPeriod = uniformProcurementField(items, 'delivery_period')
  const itemDeliveryLocation = uniformProcurementField(items, 'delivery_location')
  const deliveryPeriod = procurementValue(procurement?.delivery_period) || itemDeliveryPeriod
  const deliveryLocation = procurementValue(procurement?.delivery_location) || itemDeliveryLocation
  const delivery = [deliveryPeriod, deliveryLocation].filter(Boolean).join('；') || procurementValue(briefing?.delivery_excerpt)
  const uniformDelivery = Boolean(items.length && itemDeliveryPeriod && itemDeliveryLocation && items.every((item) => procurementValue(item.delivery_period) === itemDeliveryPeriod && procurementValue(item.delivery_location) === itemDeliveryLocation))
  const itemMissing = procurementMissingValue(procurement, '', 'items')
  const qualification = cleanQualificationExcerpt(briefing?.qualification_excerpt)
  const contact = procurementValue(briefing?.contact_excerpt)
  const [itemSearch, setItemSearch] = useState('')
  const filteredItems = useMemo(() => {
    const query = itemSearch.trim().toLocaleLowerCase()
    if (!query) return items
    return items.filter((item) => [item.name, item.specification, item.material, item.code, item.standard, item.delivery_period, item.delivery_location]
      .some((value) => procurementValue(value).toLocaleLowerCase().includes(query)))
  }, [itemSearch, items])
  const columns = useMemo(() => [
    {
      title: '名称', key: 'name', width: 160,
      render: (_value: unknown, item: ProcurementItem) => <span className="detail-procurement-item-name">{procurementValue(item.name) || itemMissing}</span>,
    },
    {
      title: '规格', key: 'specification', width: 170,
      render: (_value: unknown, item: ProcurementItem) => <span>{procurementValue(item.specification) || itemMissing}</span>,
    },
    {
      title: '材质', key: 'material', width: 120,
      render: (_value: unknown, item: ProcurementItem) => <span>{procurementValue(item.material) || itemMissing}</span>,
    },
    {
      title: '数量 / 单位', key: 'quantity', width: 115,
      render: (_value: unknown, item: ProcurementItem) => <span className="detail-procurement-quantity">{procurementValue(item.quantity) || itemMissing}{procurementValue(item.unit) ? ` ${item.unit}` : ''}</span>,
    },
    ...(uniformDelivery ? [] : [{
      title: '交付', key: 'delivery', width: 210,
      render: (_value: unknown, item: ProcurementItem) => <div className="detail-procurement-delivery-cell"><span>{procurementValue(item.delivery_location) || itemMissing}</span><span>{procurementValue(item.delivery_period) || itemMissing}</span></div>,
    }]),
    {
      title: '来源', key: 'source', width: 190,
      render: (_value: unknown, item: ProcurementItem) => {
        const document = procurementDocumentFor(item, documents)
        return <ProcurementSource item={item} document={document} href={procurementSourceHref(item, document, records)} />
      },
    },
  ], [documents, itemMissing, records, uniformDelivery])
  const errorText = error instanceof Error ? error.message : '采购资料核对失败，请稍后重试。'

  return (
    <section className="detail-briefing detail-procurement" aria-labelledby="procurement-briefing-heading">
      <div className="detail-briefing-heading-row">
        <div className="detail-briefing-heading">
          <div className="detail-procurement-title-line"><h2 id="procurement-briefing-heading">采购信息</h2><Tag color={statusMeta.label === '资料已核对' ? 'success' : status === 'unavailable' ? 'error' : 'warning'}>{statusMeta.label}</Tag></div>
          <span className="detail-briefing-note" role="note">范围与交付优先 · 明细按来源核对</span>
        </div>
        <div className="detail-briefing-source detail-procurement-actions">
          {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer"><LinkOutlined /> 查看公告原文</a> : <Text type="secondary">原文链接：未提供</Text>}
          {recordId != null && <Text type="secondary">记录 #{recordId}</Text>}
          <Button size="small" icon={status === 'not_checked' || status === 'unavailable' ? <FileTextOutlined /> : <ReloadOutlined />} loading={refreshing} onClick={onRefresh}>{status === 'not_checked' || status === 'unavailable' ? '补充采购资料' : '重新核对资料'}</Button>
        </div>
      </div>

      <div className="detail-procurement-status-line"><FileTextOutlined /><span>{statusMeta.description}</span>{procurement?.updated_at && <Text type="secondary">更新于 {formatDate(procurement.updated_at, true)}</Text>}</div>
      {Boolean(error) && <Alert className="detail-procurement-error" type="error" showIcon message="采购资料核对失败" description={errorText} />}

      <div className="detail-briefing-primary">
        <article className="detail-briefing-field detail-briefing-scope">
          <div className="detail-briefing-field-heading"><Text className="detail-briefing-label">采购范围</Text></div>
          <p className={!scope ? 'detail-procurement-missing' : undefined}>{scope || procurementMissingValue(procurement, '')}</p>
        </article>
        <article className="detail-briefing-field detail-briefing-delivery">
          <div className="detail-briefing-field-heading"><Text className="detail-briefing-label">交付要求</Text></div>
          <p className={!delivery ? 'detail-procurement-missing' : undefined}>{delivery || procurementMissingValue(procurement, '')}</p>
        </article>
      </div>

      <section className="detail-procurement-items" aria-labelledby="procurement-items-heading">
        <div className="detail-procurement-section-heading">
          <div><h3 id="procurement-items-heading">物料明细</h3><Text type="secondary">{items.length ? `${items.length} 项；不同单位不合并${uniformDelivery ? ' · 统一交付已收在上方摘要' : ''}` : itemMissing}</Text></div>
          {items.length > 0 && <Input className="detail-procurement-search" allowClear prefix={<SearchOutlined />} value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="搜索名称、规格、材质或编号" aria-label="搜索物料明细" />}
        </div>
        {items.length > 0 ? <div className={`detail-procurement-table-wrap${uniformDelivery ? ' detail-procurement-table-wrap-compact' : ''}`}><Table<ProcurementItem> size="small" rowKey={(item, index) => `${item.source_document_id}-${item.record_id}-${item.page}-${item.code || item.name}-${index}`} columns={columns} dataSource={filteredItems} scroll={{ x: uniformDelivery ? 755 : 965 }} pagination={{ pageSize: 8, showSizeChanger: true, pageSizeOptions: [8, 16, 32], showTotal: (total, range) => `${range[0]}–${range[1]} / ${total} 项` }} locale={{ emptyText: '当前筛选没有匹配的物料' }} /></div> : <div className="detail-procurement-empty">采购附件尚未形成可展示的物料行。</div>}
      </section>

      <section className="detail-procurement-documents" aria-labelledby="procurement-documents-heading">
        <div className="detail-procurement-section-heading"><div><h3 id="procurement-documents-heading">附件与来源</h3><Text type="secondary">附件名、页码和链接用于回到原文核对</Text></div><Text type="secondary">{documents.length ? `${documents.length} 份资料` : procurementMissingValue(procurement, '')}</Text></div>
        {documents.length > 0 ? <ul className="detail-procurement-document-list">{documents.map((document) => { const documentMeta = procurementDocumentStatusMeta[document.status]; const documentError = procurementDocumentError(document.error); return <li key={String(document.id)}><div className="detail-procurement-document-main"><FileTextOutlined /><div><div className="detail-procurement-document-title">{document.url ? <a href={document.url} target="_blank" rel="noreferrer">{document.name}</a> : <span>{document.name}</span>}<Tag color={documentMeta.color}>{documentMeta.label}</Tag></div><Text type="secondary">{procurementPageLabel(document.pages)} · 记录 #{document.record_id}{document.mime_type ? ` · ${document.mime_type}` : ''}</Text>{documentError && <div className="detail-procurement-document-error">{documentError}</div>}</div></div></li> })}</ul> : <div className="detail-procurement-empty">尚未检查附件；点击“补充采购资料”开始核对。</div>}
      </section>

      <details className="detail-briefing-qualifications">
        <summary><span className="detail-briefing-summary-title">投标人资格要求</span><span className="detail-briefing-summary-meta">{qualification ? '展开完整摘录核对' : procurementMissingValue(procurement, '', 'qualification')}</span></summary>
        <p className="detail-briefing-qualification-body">{qualification || procurementMissingValue(procurement, '', 'qualification')}</p>
      </details>
      <article className="detail-briefing-contact">
        <div className="detail-briefing-field-heading"><Text className="detail-briefing-label">联系方式</Text></div>
        <p>{contact || procurementMissingValue(procurement, '', 'contact')}</p>
      </article>
    </section>
  )
}

function OptionCard({ option }: { option: JudgmentOption }) {
  return <Radio value={option.id} className="detail-option-radio"><div className="detail-option-card"><div className="detail-option-title-row"><div><Text className="detail-option-kicker">条件性方案</Text><h3>{option.title}</h3></div><span className="detail-option-state">可比较</span></div><Paragraph>{option.description || '暂无说明'}</Paragraph><div className="detail-option-detail-grid"><div><Text className="detail-label">适用条件</Text><Paragraph>{option.suitable_for || '未说明'}</Paragraph></div><div><Text className="detail-label">下一步</Text><Paragraph>{option.next_action || '未说明'}</Paragraph></div></div>{option.tradeoffs.length > 0 && <div className="detail-option-list-block"><Text className="detail-label">主要取舍</Text><ul className="detail-compact-list">{option.tradeoffs.map((item) => <li key={item}>{item}</li>)}</ul></div>}{option.unknowns.length > 0 && <div className="detail-option-list-block"><Text className="detail-label">仍需确认</Text><ul className="detail-compact-list">{option.unknowns.map((item) => <li key={item}>{item}</li>)}</ul></div>}</div></Radio>
}
