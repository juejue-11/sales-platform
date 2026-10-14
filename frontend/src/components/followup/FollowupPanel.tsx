import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Collapse, Empty, Input, Modal, Select, Tag, Typography, message } from 'antd'
import { CheckOutlined, HistoryOutlined, PlusOutlined } from '@ant-design/icons'
import { ApiError, api } from '../../api'
import type { FollowupData, FollowupDisposition, FollowupMaterial, FollowupStage, FollowupTask } from '../../types'
import { formatDate } from '../Common'
import { followupStageLabels } from '../procurement/reading'
import './FollowupPanel.css'

const { Text } = Typography

const dispositionLabels: Record<FollowupDisposition, string> = { following: '跟进中', paused: '暂缓', abandoned: '放弃' }
const outcomeOptions = [{ value: 'won', label: '已成交' }, { value: 'lost', label: '未成交' }, { value: 'abandoned', label: '已放弃' }, { value: 'other', label: '其他结果' }]
const taskStatusLabels: Record<string, string> = { todo: '待办理', doing: '办理中', done: '已完成', skipped: '已跳过' }
const materialStatusLabels: Record<string, string> = { not_prepared: '未准备', preparing: '准备中', provided: '已提供', pending_check: '待核对', needs_supplement: '需补充' }
const planLabels: Record<string, string> = { title: '任务', name: '任务', basis: '依据', source_kind: '来源', due_at: '截止', deadline: '截止', prerequisite: '前置条件', prerequisites: '前置条件', materials: '材料', completeness: '完整性提示', note: '说明', description: '说明' }
const hiddenPlanKeys = new Set(['id', 'plan_id', 'task_id', 'material_id', 'record_id', 'document_id', 'source_ref', 'source_id', 'source_hash', 'hash', 'sha256', 'schema', 'version', 'status'])

interface FollowupPanelProps { projectId: number; initialFollowup?: FollowupData | null }
interface TaskAction { task: FollowupTask; status: string; label: string }

function normalizeData(value: FollowupData): FollowupData {
  return { ...value, tasks: Array.isArray(value.tasks) ? value.tasks : [], materials: Array.isArray(value.materials) ? value.materials : [], events: Array.isArray(value.events) ? value.events : [] }
}

function taskTitle(task: FollowupTask): string { return task.title || task.name || task.description || '任务待命名' }
function materialTitle(material: FollowupMaterial): string { return material.name || material.title || '材料待命名' }
function receiptText(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['number', 'code', 'id', 'name', 'text', 'url', 'channel']) {
      if (typeof record[key] === 'string' || typeof record[key] === 'number') return String(record[key])
    }
    try { return JSON.stringify(value) } catch { return '' }
  }
  return ''
}

function toDateTimeLocal(value: string | null | undefined): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
}

function toSubmissionIso(value: string): string | null {
  if (!value.trim()) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

function sourceLabel(value: unknown): string {
  if (value === 'required' || value === 'requirement') return '来源要求'
  if (value === 'announcement') return '公告原文'
  if (value === 'conditional' || value === 'path' || value === 'selected_path') return '参与路径条件'
  if (value === 'suggested' || value === 'ai' || value === 'suggestion') return 'AI 建议'
  if (value === 'attachment') return '附件原文'
  return typeof value === 'string' && value ? value : '待核对'
}
function planValue(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map((item) => planValue(item)).filter(Boolean).join('；')
  if (value && typeof value === 'object') return Object.entries(value as Record<string, unknown>).filter(([key]) => !hiddenPlanKeys.has(key)).map(([key, item]) => `${planLabels[key] ?? key}：${planValue(item)}`).join('；')
  return ''
}
function planItems(plan: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(plan)) return plan.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
  if (plan && typeof plan === 'object') {
    const record = plan as Record<string, unknown>
    for (const key of ['tasks', 'items', 'steps', 'actions']) {
      if (Array.isArray(record[key])) return record[key].filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    }
    return Object.entries(record).filter(([key]) => !hiddenPlanKeys.has(key)).map(([key, value]) => ({ title: planLabels[key] ?? key, description: planValue(value) }))
  }
  return []
}

function planRecord(plan: unknown): Record<string, unknown> | null {
  return plan && typeof plan === 'object' && !Array.isArray(plan) ? plan as Record<string, unknown> : null
}

function planIsEligible(plan: unknown): boolean {
  const record = planRecord(plan)
  return Boolean(record) && record?.eligible !== false
}

function planMaterialNames(plan: unknown): Map<string, string> {
  const result = new Map<string, string>()
  const materials = planRecord(plan)?.materials
  if (!Array.isArray(materials)) return result
  for (const material of materials) {
    if (!material || typeof material !== 'object') continue
    const record = material as Record<string, unknown>
    const id = record.id
    const name = record.name ?? record.title
    if ((typeof id === 'string' || typeof id === 'number') && typeof name === 'string' && name.trim()) result.set(String(id), name)
  }
  return result
}

function planMaterialText(value: unknown, names: Map<string, string>): string {
  if (!Array.isArray(value)) return planValue(value)
  return value.map((item) => {
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      if (typeof record.name === 'string' && record.name.trim()) return record.name
      if (typeof record.title === 'string' && record.title.trim()) return record.title
    }
    if (typeof item === 'string' || typeof item === 'number') return names.get(String(item)) ?? '材料待核对'
    return '材料待核对'
  }).join('、')
}

function PlanPreview({ plan }: { plan: unknown }) {
  if (typeof plan === 'string') return <p className="followup-plan-copy">{plan}</p>
  const record = planRecord(plan)
  const materialNames = planMaterialNames(plan)
  const eligibilityWarning = record?.eligible === false ? <Alert type="warning" showIcon message="当前计划暂不可采用" description={typeof record.reason === 'string' && record.reason.trim() ? record.reason : '采购说明仍需补充或复核。'} /> : null
  const items = planItems(plan)
  return <>{eligibilityWarning}{!items.length ? <Text type="secondary">暂无可采用的 AI 计划。</Text> : <ol className="followup-plan-items">{items.map((item, index) => {
    const title = typeof item.title === 'string' ? item.title : typeof item.name === 'string' ? item.name : `任务 ${index + 1}`
    const basis = item.basis ?? item.source_kind
    const due = item.due_at ?? item.deadline
    const prerequisites = item.prerequisites ?? item.prerequisite
    const materials = item.materials
    const completeness = item.completeness
    return <li key={`${title}-${index}`}><strong>{title}</strong>{basis != null && <span><b>依据</b>：{sourceLabel(basis)}</span>}{due != null && <span><b>截止</b>：{planValue(due)}</span>}{prerequisites != null && <span><b>前置</b>：{planValue(prerequisites)}</span>}{materials != null && <span><b>材料</b>：{planMaterialText(materials, materialNames)}</span>}{completeness != null && <span><b>完整性</b>：{planValue(completeness)}</span>}{item.description != null && <span>{planValue(item.description)}</span>}</li>
  })}</ol>}</>
}

export function FollowupPanel({ projectId, initialFollowup }: FollowupPanelProps) {
  const [data, setData] = useState<FollowupData | null>(initialFollowup ? normalizeData(initialFollowup) : null)
  const [missing, setMissing] = useState(!initialFollowup)
  const [loading, setLoading] = useState(!initialFollowup)
  const [error, setError] = useState('')
  const [action, setAction] = useState('')
  const [owner, setOwner] = useState(initialFollowup?.owner ?? '')
  const [nextAction, setNextAction] = useState(initialFollowup?.next_action ?? '')
  const [outcome, setOutcome] = useState(initialFollowup?.outcome ?? '')
  const [submittedAt, setSubmittedAt] = useState(toDateTimeLocal(initialFollowup?.submitted_at))
  const [submissionChannel, setSubmissionChannel] = useState(initialFollowup?.submission_channel ?? '')
  const [receipt, setReceipt] = useState(receiptText(initialFollowup?.receipt))
  const [taskAction, setTaskAction] = useState<TaskAction | null>(null)
  const [taskNote, setTaskNote] = useState('')
  const [taskReasons, setTaskReasons] = useState<Record<string, string>>({})
  const [finishOpen, setFinishOpen] = useState(false)
  const [finishReason, setFinishReason] = useState('')
  const [dispositionAction, setDispositionAction] = useState<FollowupDisposition | null>(null)
  const [dispositionReason, setDispositionReason] = useState('')
  const [submissionConfirmOpen, setSubmissionConfirmOpen] = useState(false)
  const mountedRef = useRef(false)
  const requestTokenRef = useRef(0)
  const headerDraftDirtyRef = useRef(false)
  const submissionDraftDirtyRef = useRef(false)

  const applyData = useCallback((value: FollowupData | null, options: { preserveHeaderDraft?: boolean; preserveSubmissionDraft?: boolean } = {}) => {
    if (!mountedRef.current) return
    const next = value ? normalizeData(value) : null
    setData(next)
    const preserveHeaderDraft = next != null && (options.preserveHeaderDraft ?? headerDraftDirtyRef.current)
    const preserveSubmissionDraft = next != null && (options.preserveSubmissionDraft ?? submissionDraftDirtyRef.current)
    if (!preserveHeaderDraft) {
      setOwner(next?.owner ?? '')
      setNextAction(next?.next_action ?? '')
    }
    setOutcome(next?.outcome ?? '')
    if (!preserveSubmissionDraft) {
      setSubmittedAt(toDateTimeLocal(next?.submitted_at))
      setSubmissionChannel(next?.submission_channel ?? '')
      setReceipt(receiptText(next?.receipt))
    }
    if (!next) {
      headerDraftDirtyRef.current = false
      submissionDraftDirtyRef.current = false
    }
  }, [])

  const load = useCallback(async () => {
    const token = ++requestTokenRef.current
    if (!mountedRef.current) return
    setLoading(true)
    setError('')
    try {
      const result = await api.followup(projectId)
      if (!mountedRef.current || token !== requestTokenRef.current) return
      applyData(result); setMissing(false)
    } catch (loadError) {
      if (!mountedRef.current || token !== requestTokenRef.current) return
      if (loadError instanceof ApiError && loadError.status === 404) { applyData(null); setMissing(true) } else setError(loadError instanceof Error ? loadError.message : '跟进读取失败，请稍后重试')
    } finally {
      if (mountedRef.current && token === requestTokenRef.current) setLoading(false)
    }
  }, [applyData, projectId])

  useEffect(() => {
    mountedRef.current = true
    if (!initialFollowup) void load()
    return () => { mountedRef.current = false; requestTokenRef.current += 1 }
  }, [initialFollowup, load])

  const saveFields = async (fields: Partial<FollowupData>, success = '跟进信息已保存'): Promise<boolean> => {
    if (fields.stage === 'submitted' && !('submitted_at' in fields) && !('submission_channel' in fields)) {
      setSubmissionConfirmOpen(true)
      return false
    }
    setAction('save'); setError('')
    const savingSubmission = 'submitted_at' in fields || 'submission_channel' in fields || 'receipt' in fields || fields.stage === 'submitted'
    const preserveHeaderDraft = headerDraftDirtyRef.current && !('owner' in fields || 'next_action' in fields)
    const preserveSubmissionDraft = submissionDraftDirtyRef.current && !savingSubmission
    try {
      const result = await api.updateFollowup(projectId, fields)
      applyData(result, { preserveHeaderDraft, preserveSubmissionDraft })
      if ('owner' in fields || 'next_action' in fields) headerDraftDirtyRef.current = false
      if (savingSubmission) submissionDraftDirtyRef.current = false
      setMissing(false)
      message.success(success)
      return true
    } catch (saveError) {
      const text = saveError instanceof Error ? saveError.message : '跟进保存失败，请稍后重试'
      setError(text); message.error(text)
      return false
    } finally { setAction('') }
  }
  const adoptPlan = async () => {
    setAction('adopt'); setError('')
    const planId = data?.plan_id ?? (data?.plan && typeof data.plan === 'object' ? (() => {
      const plan = data.plan as Record<string, unknown>
      return typeof plan.id === 'string' || typeof plan.id === 'number' ? plan.id : typeof plan.plan_id === 'string' || typeof plan.plan_id === 'number' ? plan.plan_id : undefined
    })() : undefined)
    try { const result = await api.adoptFollowupPlan(projectId, planId); applyData(result); setMissing(false); message.success('AI 计划已采用，成为你的跟进清单') } catch (adoptError) { const text = adoptError instanceof Error ? adoptError.message : '采用计划失败，请稍后重试'; setError(text); message.error(text) } finally { setAction('') }
  }
  const patchTask = async (task: FollowupTask, fields: Partial<FollowupTask>, success = '任务已保存') => {
    setAction(`task-${task.id}`)
    try { const result = await api.updateFollowupTask(projectId, task.id, fields); applyData(result, { preserveHeaderDraft: true, preserveSubmissionDraft: true }); message.success(success) } catch (taskError) { const text = taskError instanceof Error ? taskError.message : '任务更新失败'; setError(text); message.error(text) } finally { setAction('') }
  }
  const chooseTaskStatus = (task: FollowupTask, status: string) => {
    if (status === 'skipped') { setTaskAction({ task, status, label: '跳过任务' }); setTaskNote(String(task.skipped_reason ?? '')); return }
    if (status === 'done' && !String(task.completion_note ?? task.note ?? '').trim()) { setTaskAction({ task, status, label: '记录完成说明' }); setTaskNote(''); return }
    void patchTask(task, { status })
  }
  const submitTaskAction = async () => {
    if (!taskAction || !taskNote.trim()) { message.warning(taskAction?.status === 'skipped' ? '跳过任务必须填写理由' : '完成任务必须填写完成说明'); return }
    const fields: Partial<FollowupTask> = taskAction.status === 'skipped'
      ? { status: 'skipped', skipped_reason: taskNote.trim() }
      : { status: 'done', completion_note: taskNote.trim() }
    await patchTask(taskAction.task, fields)
    setTaskAction(null); setTaskNote('')
  }
  const updateTaskField = (task: FollowupTask, key: 'owner' | 'due_at' | 'completion_note', value: string, reason: string) => {
    const responseKey = key === 'completion_note' ? 'completion_note' : key
    const previousValue = key === 'completion_note' ? task.completion_note : task[key]
    const previous = previousValue == null ? '' : String(previousValue)
    if (value === previous) return
    if (key === 'owner' || key === 'completion_note') {
      void patchTask(task, { [responseKey]: value || null }, key === 'completion_note' ? '完成说明已保存' : '负责人已保存')
      return
    }
    if (!reason.trim()) { message.warning('调整截止时间需要填写理由'); return }
    void patchTask(task, { due_at: value || null, reason: reason.trim() }, '任务截止时间已保存')
  }
  const updateMaterial = async (material: FollowupMaterial, status: string) => {
    setAction(`material-${material.id}`)
    try { const result = await api.updateFollowupMaterial(projectId, material.id, { status }); applyData(result, { preserveHeaderDraft: true, preserveSubmissionDraft: true }); message.success('材料状态已保存') } catch (materialError) { const text = materialError instanceof Error ? materialError.message : '材料更新失败'; setError(text); message.error(text) } finally { setAction('') }
  }

  const submitFinished = async () => {
    if (!outcome.trim() || !finishReason.trim()) { message.warning('结束阶段需要填写结果和原因'); return }
    await saveFields({ stage: 'finished', outcome: outcome.trim(), note: finishReason.trim() }, '已保存结束结果')
    setFinishOpen(false)
    setFinishReason('')
  }
  const submitDisposition = async () => {
    if (!dispositionAction || !dispositionReason.trim()) { message.warning('暂缓或放弃需要填写理由'); return }
    await saveFields({ disposition: dispositionAction, reason: dispositionReason.trim() }, '已保存跟进处置')
    setDispositionAction(null)
    setDispositionReason('')
  }

  const hasConfirmedSubmission = Boolean(data?.submitted_at && data.submission_channel?.trim())
  const openSubmissionConfirmation = () => setSubmissionConfirmOpen(true)
  const submitSubmission = async () => {
    const channel = submissionChannel.trim()
    const submittedAtIso = toSubmissionIso(submittedAt)
    if (!submittedAtIso || !channel) {
      message.warning('确认提交前请填写有效的提交时间和提交渠道')
      return
    }
    const fields: Partial<FollowupData> = { stage: 'submitted', submitted_at: submittedAtIso, submission_channel: channel }
    if (receipt.trim()) fields.receipt = receipt.trim()
    const saved = await saveFields(fields, '已确认真实提交')
    if (saved) setSubmissionConfirmOpen(false)
  }

  const taskDone = useMemo(() => data?.tasks.filter((task) => task.status === 'done').length ?? 0, [data?.tasks])
  const isFollowing = data?.is_following === true
  const canAdoptPlan = planIsEligible(data?.plan)
  if (loading) return <div className="followup-panel"><div className="followup-loading">正在读取跟进记录…</div></div>

  return <div className="followup-panel" data-testid="followup-panel">
    <header className="followup-header"><div><div className="followup-kicker">用户实际办理</div><h2>跟进</h2><p>AI 计划只有在你采用后才会进入清单。任务完成不会代替真实提交，提交时间、渠道和凭证由你手工确认。</p></div>{data && <Tag color={isFollowing && data.disposition === 'following' ? 'blue' : isFollowing && data.disposition === 'paused' ? 'warning' : 'default'}>{isFollowing ? dispositionLabels[data.disposition] : '尚未加入跟进'}</Tag>}</header>
    {error && <Alert type="error" showIcon message="跟进操作失败" description={error} />}
    {missing ? <section className="followup-empty"><Empty description="还没有跟进记录" /><Text type="secondary">可以采用计划，也可以先手工建立跟进。</Text><div className="followup-start-actions"><Button type="primary" icon={<PlusOutlined />} loading={action === 'adopt'} disabled={!canAdoptPlan} onClick={() => void adoptPlan()}>采用 AI 计划并开始</Button><Button loading={action === 'save'} onClick={() => void saveFields({ is_following: true, stage: 'evaluating', disposition: 'following' }, '已手工加入跟进')}>手工开始跟进</Button></div></section> : data && !isFollowing ? <section className="followup-preview"><div><h3>计划预览</h3><Text type="secondary">这份计划来自采购说明，尚未加入你的跟进清单。</Text></div><PlanPreview plan={data.plan} /><div className="followup-start-actions"><Button type="primary" icon={<PlusOutlined />} loading={action === 'adopt'} disabled={!canAdoptPlan} onClick={() => void adoptPlan()}>加入我的跟进</Button><Button loading={action === 'save'} onClick={() => void saveFields({ is_following: true, stage: 'evaluating', disposition: 'following' }, '已手工加入跟进')}>手工开始，不采用计划</Button></div></section> : data && <>
      {data.needs_review && <Alert type="warning" showIcon message="资料变化，需要复核跟进计划" description="系统会保留已完成动作和提交记录。请确认当前采购说明后再调整计划。" />}
      <section className="followup-overview"><div className="followup-stage-strip" aria-label="跟进阶段">{(Object.keys(followupStageLabels) as FollowupStage[]).map((stage) => <span className={data.stage === stage ? 'active' : ''} key={stage}>{followupStageLabels[stage]}</span>)}</div><div className="followup-field-grid"><label><span>阶段</span><Select value={data.stage} onChange={(value: FollowupStage) => { if (value === 'finished') { setFinishReason(String(data.note ?? '')); setFinishOpen(true) } else if (value === 'awaiting_result' && !hasConfirmedSubmission) { message.warning('请先确认真实提交，再进入等待结果') } else void saveFields({ stage: value }) }} options={(Object.keys(followupStageLabels) as FollowupStage[]).map((value) => ({ value, label: followupStageLabels[value] }))} /></label><label><span>处置</span><Select value={data.disposition} onChange={(value: FollowupDisposition) => { if (value === 'following') void saveFields({ disposition: value }); else { setDispositionAction(value); setDispositionReason('') } }} options={(Object.keys(dispositionLabels) as FollowupDisposition[]).map((value) => ({ value, label: dispositionLabels[value] }))} /></label><label><span>负责人</span><Input value={owner} onChange={(event) => { headerDraftDirtyRef.current = true; setOwner(event.target.value) }} onBlur={() => { if (owner !== (data.owner ?? '')) void saveFields({ owner }) }} placeholder="填写负责人" /></label><label><span>下一步</span><Input value={nextAction} onChange={(event) => { headerDraftDirtyRef.current = true; setNextAction(event.target.value) }} onBlur={() => { if (nextAction !== (data.next_action ?? '')) void saveFields({ next_action: nextAction }) }} placeholder="记录下一件要做的事" /></label></div></section>
      <section className="followup-submission"><div className="followup-section-heading"><div><h3>真实提交记录</h3><Text type="secondary">只有人工确认才会进入这里；任务完成不会自动改变阶段。</Text></div><div className="followup-submission-actions">{data.stage === 'submitted' && !hasConfirmedSubmission && <Tag color="warning">待补提交凭证</Tag>}<Button size="small" type="primary" disabled={hasConfirmedSubmission || action === 'save'} onClick={openSubmissionConfirmation}>确认已提交</Button></div></div><div className="followup-field-grid"><label><span>提交时间</span><Input type="datetime-local" value={hasConfirmedSubmission ? toDateTimeLocal(data.submitted_at) : submittedAt} disabled={hasConfirmedSubmission} onChange={(event) => { submissionDraftDirtyRef.current = true; setSubmittedAt(event.target.value) }} /></label><label><span>提交渠道</span><Input value={hasConfirmedSubmission ? data.submission_channel ?? '' : submissionChannel} disabled={hasConfirmedSubmission} onChange={(event) => { submissionDraftDirtyRef.current = true; setSubmissionChannel(event.target.value) }} placeholder="例如：电子采购平台" /></label><label><span>凭证</span><Input value={hasConfirmedSubmission ? receiptText(data.receipt) : receipt} disabled={hasConfirmedSubmission} onChange={(event) => { submissionDraftDirtyRef.current = true; setReceipt(event.target.value) }} placeholder="编号或附件说明" /></label><label><span>结果</span><Select value={outcome || undefined} allowClear onChange={(value: string) => { setOutcome(value); if (value !== (data.outcome ?? '')) void saveFields({ outcome: value || null }) }} options={outcomeOptions} placeholder="选择结果" /></label></div></section>
      <section className="followup-tasks"><div className="followup-section-heading"><div><h3>跟进任务</h3><Text type="secondary">已完成 {taskDone} / {data.tasks.length}；跳过需要理由。</Text></div><Tag>不等于已提交</Tag></div>{data.tasks.length === 0 ? <Text type="secondary">暂无已采用的任务。</Text> : <div className="followup-task-list">{data.tasks.map((task) => { const key = String(task.id); const reason = taskReasons[key] ?? ''; const taskBusy = action === `task-${task.id}`; const hasBasis = Boolean(task.basis || task.source_kind); return <article className="followup-task" key={task.id}><div className="followup-task-main"><strong>{taskTitle(task)}</strong><div className="followup-task-meta">{task.required && !hasBasis && <Tag color="blue">来源要求</Tag>}{hasBasis && <Tag>{sourceLabel(task.basis || task.source_kind)}</Tag>}{(task.due_at || task.due) && <Text type="secondary">原截止 {formatDate(task.due_at || task.due || null, true)}</Text>}</div><div className="followup-task-edit"><label>负责人<Input size="small" disabled={taskBusy} defaultValue={task.owner ?? ''} onBlur={(event) => updateTaskField(task, 'owner', event.target.value, reason)} /></label><label>截止<Input size="small" disabled={taskBusy} defaultValue={task.due_at ?? task.due ?? ''} onBlur={(event) => updateTaskField(task, 'due_at', event.target.value, reason)} /></label><label>调整理由<Input size="small" disabled={taskBusy} value={reason} onChange={(event) => setTaskReasons((current) => ({ ...current, [key]: event.target.value }))} placeholder="调整时填写" /></label></div>{task.status === 'done' && <label className="followup-completion-note">完成说明<Input.TextArea size="small" disabled={taskBusy} defaultValue={String(task.completion_note ?? '')} rows={2} onBlur={(event) => updateTaskField(task, 'completion_note', event.target.value, reason)} /></label>}{task.status === 'skipped' && <Text type="warning">跳过理由：{task.skipped_reason || '未填写'}</Text>}</div><Select className="followup-status-select" value={task.status || 'todo'} disabled={taskBusy} onChange={(status) => chooseTaskStatus(task, status)} options={Object.entries(taskStatusLabels).map(([value, label]) => ({ value, label }))} /></article> })}</div>}</section>
      <section className="followup-materials"><div className="followup-section-heading"><h3>材料状态</h3><Text type="secondary">材料检查不会表示外部平台已接受。</Text></div>{data.materials.length === 0 ? <Text type="secondary">暂无材料清单。</Text> : <div className="followup-material-list">{data.materials.map((material) => <div className="followup-material" key={material.id}><span>{materialTitle(material)}</span><Select size="small" value={material.status || 'not_prepared'} disabled={action === `material-${material.id}`} onChange={(status) => void updateMaterial(material, status)} options={Object.entries(materialStatusLabels).map(([value, label]) => ({ value, label }))} /></div>)}</div>}</section>
      {data.plan != null && <Collapse ghost items={[{ key: 'plan', label: '查看已采用的计划', children: <PlanPreview plan={data.plan} /> }]} />}
      {data.events.length > 0 && <Collapse ghost items={[{ key: 'events', label: <span><HistoryOutlined /> 跟进留痕（{data.events.length}）</span>, children: <ul className="followup-events">{data.events.map((event, index) => <li key={`${event.id ?? 'event'}-${index}`}><Text type="secondary">{event.at ? formatDate(event.at, true) : '时间待标记'}</Text><span>{event.text || event.message || event.type || '跟进记录'}</span></li>)}</ul> }]} />}
    </>}
    {data && <div className="followup-save-note"><CheckOutlined /> 阶段与动作由用户确认保存；{action === 'save' ? '正在保存…' : '修改后离开页面也会保留。'}</div>}
    <Modal title="结束跟进" open={finishOpen} okText="保存" cancelText="取消" onCancel={() => { setFinishOpen(false); setFinishReason('') }} onOk={() => void submitFinished()} confirmLoading={action === 'save'}><label className="followup-modal-field"><span>结束结果</span><Select value={outcome || undefined} onChange={(value: string) => setOutcome(value)} options={outcomeOptions} placeholder="选择结果" /></label><label className="followup-modal-field"><span>结束原因</span><Input.TextArea value={finishReason} onChange={(event) => setFinishReason(event.target.value)} rows={3} placeholder="说明为什么结束跟进" /></label></Modal>
    <Modal title={dispositionAction === 'paused' ? '暂缓跟进' : '放弃跟进'} open={Boolean(dispositionAction)} okText="保存" cancelText="取消" onCancel={() => { setDispositionAction(null); setDispositionReason('') }} onOk={() => void submitDisposition()} confirmLoading={action === 'save'}><label className="followup-modal-field"><span>理由</span><Input.TextArea value={dispositionReason} onChange={(event) => setDispositionReason(event.target.value)} rows={3} placeholder="说明暂缓或放弃的原因" /></label></Modal>
    <Modal title={taskAction?.label} open={Boolean(taskAction)} okText="保存" cancelText="取消" onCancel={() => { setTaskAction(null); setTaskNote('') }} onOk={() => void submitTaskAction()} confirmLoading={action.startsWith('task-')}><Input.TextArea value={taskNote} onChange={(event) => setTaskNote(event.target.value)} rows={4} placeholder={taskAction?.status === 'skipped' ? '说明为什么跳过，以及何时重新办理。' : '记录完成的动作、时间或结果。'} /></Modal>
    <Modal title="确认真实提交" open={submissionConfirmOpen} okText="确认已提交" cancelText="返回修改" onCancel={() => setSubmissionConfirmOpen(false)} onOk={() => void submitSubmission()} confirmLoading={action === 'save'}>
      <Text type="secondary">提交时间、渠道和凭证会作为一次人工确认保存；任务或材料状态不会替代真实提交。</Text>
      <label className="followup-modal-field"><span>提交时间</span><Input aria-label="确认窗口提交时间" type="datetime-local" value={submittedAt} onChange={(event) => { submissionDraftDirtyRef.current = true; setSubmittedAt(event.target.value) }} /></label>
      <label className="followup-modal-field"><span>提交渠道</span><Input aria-label="确认窗口提交渠道" value={submissionChannel} onChange={(event) => { submissionDraftDirtyRef.current = true; setSubmissionChannel(event.target.value) }} placeholder="例如：电子采购平台" /></label>
      <label className="followup-modal-field"><span>凭证（可选）</span><Input aria-label="确认窗口提交凭证" value={receipt} onChange={(event) => { submissionDraftDirtyRef.current = true; setReceipt(event.target.value) }} placeholder="编号或附件说明" /></label>
    </Modal>
  </div>
}
