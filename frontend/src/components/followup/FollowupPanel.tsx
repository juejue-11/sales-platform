import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Collapse, Empty, Input, Modal, Select, Tag, Typography, message } from 'antd'
import { CheckOutlined, HistoryOutlined, PlusOutlined } from '@ant-design/icons'
import { ApiError, api } from '../../api'
import type { FollowupData, FollowupDisposition, FollowupMaterial, FollowupStage, FollowupTask } from '../../types'
import { formatDate } from '../Common'
import { followupStageLabels } from '../procurement/reading'
import './FollowupPanel.css'

const { Text } = Typography

const dispositionLabels: Record<FollowupDisposition, string> = { following: '跟进中', paused: '暂缓', abandoned: '放弃' }
const taskStatusLabels: Record<string, string> = { pending: '待办理', in_progress: '办理中', done: '已完成', skipped: '已跳过' }
const materialStatusLabels: Record<string, string> = { not_ready: '未准备', preparing: '准备中', provided: '已提供', checking: '待核对', needs_more: '需补充' }
const planLabels: Record<string, string> = { title: '任务', name: '任务', basis: '依据', source_kind: '来源', due_at: '截止', deadline: '截止', prerequisite: '前置条件', prerequisites: '前置条件', materials: '材料', completeness: '完整性提示', note: '说明', description: '说明' }
const hiddenPlanKeys = new Set(['id', 'task_id', 'material_id', 'hash', 'sha256', 'schema', 'version', 'status'])

interface FollowupPanelProps { projectId: number; initialFollowup?: FollowupData | null }
interface TaskAction { task: FollowupTask; status: string; label: string }

function normalizeData(value: FollowupData): FollowupData {
  return { ...value, tasks: Array.isArray(value.tasks) ? value.tasks : [], materials: Array.isArray(value.materials) ? value.materials : [], events: Array.isArray(value.events) ? value.events : [] }
}

function taskTitle(task: FollowupTask): string { return task.title || task.name || task.description || `任务 ${task.id}` }
function materialTitle(material: FollowupMaterial): string { return material.name || material.title || `材料 ${material.id}` }
function sourceLabel(value: unknown): string {
  if (value === 'announcement' || value === 'requirement') return '来源要求'
  if (value === 'path' || value === 'selected_path') return '参与路径条件'
  if (value === 'ai' || value === 'suggestion') return 'AI 建议'
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

function PlanPreview({ plan }: { plan: unknown }) {
  if (typeof plan === 'string') return <p className="followup-plan-copy">{plan}</p>
  const items = planItems(plan)
  if (!items.length) return <Text type="secondary">暂无可采用的 AI 计划。</Text>
  return <ol className="followup-plan-items">{items.map((item, index) => {
    const title = typeof item.title === 'string' ? item.title : typeof item.name === 'string' ? item.name : `任务 ${index + 1}`
    const basis = item.basis ?? item.source_kind
    const due = item.due_at ?? item.deadline
    const prerequisites = item.prerequisites ?? item.prerequisite
    const materials = item.materials
    const completeness = item.completeness
    return <li key={`${title}-${index}`}><strong>{title}</strong>{basis != null && <span><b>依据</b>：{sourceLabel(basis)}</span>}{due != null && <span><b>截止</b>：{planValue(due)}</span>}{prerequisites != null && <span><b>前置</b>：{planValue(prerequisites)}</span>}{materials != null && <span><b>材料</b>：{planValue(materials)}</span>}{completeness != null && <span><b>完整性</b>：{planValue(completeness)}</span>}{item.description != null && <span>{planValue(item.description)}</span>}</li>
  })}</ol>
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
  const [submittedAt, setSubmittedAt] = useState(initialFollowup?.submitted_at ?? '')
  const [submissionChannel, setSubmissionChannel] = useState(initialFollowup?.submission_channel ?? '')
  const [receipt, setReceipt] = useState(initialFollowup?.receipt ?? '')
  const [taskAction, setTaskAction] = useState<TaskAction | null>(null)
  const [taskNote, setTaskNote] = useState('')
  const [taskReasons, setTaskReasons] = useState<Record<string, string>>({})

  const applyData = useCallback((value: FollowupData | null) => {
    const next = value ? normalizeData(value) : null
    setData(next)
    setOwner(next?.owner ?? '')
    setNextAction(next?.next_action ?? '')
    setOutcome(next?.outcome ?? '')
    setSubmittedAt(next?.submitted_at ?? '')
    setSubmissionChannel(next?.submission_channel ?? '')
    setReceipt(next?.receipt ?? '')
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try { const result = await api.followup(projectId); applyData(result); setMissing(false) } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 404) { applyData(null); setMissing(true) } else setError(loadError instanceof Error ? loadError.message : '跟进读取失败，请稍后重试')
    } finally { setLoading(false) }
  }, [applyData, projectId])

  useEffect(() => { if (!initialFollowup) void load() }, [initialFollowup, load])

  const saveFields = async (fields: Partial<FollowupData>, success = '跟进信息已保存') => {
    setAction('save'); setError('')
    try { const result = await api.updateFollowup(projectId, fields); applyData(result); setMissing(false); message.success(success) } catch (saveError) { const text = saveError instanceof Error ? saveError.message : '跟进保存失败，请稍后重试'; setError(text); message.error(text) } finally { setAction('') }
  }
  const adoptPlan = async () => {
    setAction('adopt'); setError('')
    const planId = data?.plan_id ?? (data?.plan && typeof data.plan === 'object' && 'plan_id' in data.plan ? (data.plan as { plan_id?: string | number }).plan_id : undefined)
    try { const result = await api.adoptFollowupPlan(projectId, planId); applyData(result); setMissing(false); message.success('AI 计划已采用，成为你的跟进清单') } catch (adoptError) { const text = adoptError instanceof Error ? adoptError.message : '采用计划失败，请稍后重试'; setError(text); message.error(text) } finally { setAction('') }
  }
  const patchTask = async (task: FollowupTask, fields: Partial<FollowupTask>, success = '任务已保存') => {
    setAction(`task-${task.id}`)
    try { const result = await api.updateFollowupTask(projectId, task.id, fields); applyData(result); message.success(success) } catch (taskError) { const text = taskError instanceof Error ? taskError.message : '任务更新失败'; setError(text); message.error(text) } finally { setAction('') }
  }
  const chooseTaskStatus = (task: FollowupTask, status: string) => {
    if (status === 'skipped') { setTaskAction({ task, status, label: '跳过任务' }); setTaskNote(String(task.skip_reason ?? '')); return }
    if (status === 'done' && !String(task.completion_note ?? task.note ?? '').trim()) { setTaskAction({ task, status, label: '记录完成说明' }); setTaskNote(''); return }
    void patchTask(task, { status })
  }
  const submitTaskAction = async () => {
    if (!taskAction || !taskNote.trim()) { message.warning(taskAction?.status === 'skipped' ? '跳过任务必须填写理由' : '完成任务必须填写完成说明'); return }
    const fields: Partial<FollowupTask> = { status: taskAction.status, reason: '用户确认任务状态', ...(taskAction.status === 'skipped' ? { skipped_reason: taskNote.trim(), skip_reason: taskNote.trim() } : { completion_note: taskNote.trim(), note: taskNote.trim() }) }
    await patchTask(taskAction.task, fields)
    setTaskAction(null); setTaskNote('')
  }
  const updateTaskField = (task: FollowupTask, key: 'owner' | 'due_at' | 'note', value: string, reason: string) => {
    const previous = task[key] == null ? '' : String(task[key])
    if (value !== previous && reason.trim()) void patchTask(task, { [key]: value || null, reason: reason.trim() }, key === 'note' ? '完成说明已保存' : '任务信息已保存')
    if (value !== previous && !reason.trim()) message.warning('调整任务需要填写理由')
  }
  const updateMaterial = async (material: FollowupMaterial, status: string) => {
    setAction(`material-${material.id}`)
    try { const result = await api.updateFollowupMaterial(projectId, material.id, { status }); applyData(result); message.success('材料状态已保存') } catch (materialError) { const text = materialError instanceof Error ? materialError.message : '材料更新失败'; setError(text); message.error(text) } finally { setAction('') }
  }

  const taskDone = useMemo(() => data?.tasks.filter((task) => task.status === 'done').length ?? 0, [data?.tasks])
  const isFollowing = data ? data.is_following !== false : false
  if (loading) return <div className="followup-panel"><div className="followup-loading">正在读取跟进记录…</div></div>

  return <div className="followup-panel" data-testid="followup-panel">
    <header className="followup-header"><div><div className="followup-kicker">用户实际办理</div><h2>跟进</h2><p>AI 计划只有在你采用后才会进入清单。任务完成不会代替真实提交，提交时间、渠道和凭证由你手工确认。</p></div>{data && <Tag color={isFollowing && data.disposition === 'following' ? 'blue' : isFollowing && data.disposition === 'paused' ? 'warning' : 'default'}>{isFollowing ? dispositionLabels[data.disposition] : '尚未加入跟进'}</Tag>}</header>
    {error && <Alert type="error" showIcon message="跟进操作失败" description={error} />}
    {missing ? <section className="followup-empty"><Empty description="还没有跟进记录" /><Text type="secondary">可以采用计划，也可以先手工建立跟进。</Text><div className="followup-start-actions"><Button type="primary" icon={<PlusOutlined />} loading={action === 'adopt'} onClick={() => void adoptPlan()}>采用 AI 计划并开始</Button><Button loading={action === 'save'} onClick={() => void saveFields({ is_following: true, stage: 'evaluating', disposition: 'following' }, '已手工加入跟进')}>手工开始跟进</Button></div></section> : data && !isFollowing ? <section className="followup-preview"><div><h3>计划预览</h3><Text type="secondary">这份计划来自采购说明，尚未加入你的跟进清单。</Text></div><PlanPreview plan={data.plan} /><div className="followup-start-actions"><Button type="primary" icon={<PlusOutlined />} loading={action === 'adopt'} onClick={() => void adoptPlan()}>加入我的跟进</Button><Button loading={action === 'save'} onClick={() => void saveFields({ is_following: true, stage: 'evaluating', disposition: 'following' }, '已手工加入跟进')}>手工开始，不采用计划</Button></div></section> : data && <>
      {data.needs_review && <Alert type="warning" showIcon message="资料变化，需要复核跟进计划" description="系统会保留已完成动作和提交记录。请确认当前采购说明后再调整计划。" />}
      <section className="followup-overview"><div className="followup-stage-strip" aria-label="跟进阶段">{(Object.keys(followupStageLabels) as FollowupStage[]).map((stage) => <span className={data.stage === stage ? 'active' : ''} key={stage}>{followupStageLabels[stage]}</span>)}</div><div className="followup-field-grid"><label><span>阶段</span><Select value={data.stage} onChange={(value: FollowupStage) => { if (value === 'finished' && !outcome.trim()) { message.warning('结束阶段需要填写结果或原因'); return } void saveFields({ stage: value }) }} options={(Object.keys(followupStageLabels) as FollowupStage[]).map((value) => ({ value, label: followupStageLabels[value] }))} /></label><label><span>处置</span><Select value={data.disposition} onChange={(value: FollowupDisposition) => void saveFields({ disposition: value })} options={(Object.keys(dispositionLabels) as FollowupDisposition[]).map((value) => ({ value, label: dispositionLabels[value] }))} /></label><label><span>负责人</span><Input value={owner} onChange={(event) => setOwner(event.target.value)} onBlur={() => { if (owner !== (data.owner ?? '')) void saveFields({ owner }) }} placeholder="填写负责人" /></label><label><span>下一步</span><Input value={nextAction} onChange={(event) => setNextAction(event.target.value)} onBlur={() => { if (nextAction !== (data.next_action ?? '')) void saveFields({ next_action: nextAction }) }} placeholder="记录下一件要做的事" /></label></div></section>
      <section className="followup-submission"><div className="followup-section-heading"><div><h3>真实提交记录</h3><Text type="secondary">只有人工确认才会进入这里；任务完成不会自动改变阶段。</Text></div>{data.stage === 'submitted' && !submittedAt && <Tag color="warning">待补提交凭证</Tag>}</div><div className="followup-field-grid"><label><span>提交时间</span><Input type="datetime-local" value={submittedAt} onChange={(event) => setSubmittedAt(event.target.value)} onBlur={() => { if (submittedAt !== (data.submitted_at ?? '')) void saveFields({ submitted_at: submittedAt || null }) }} /></label><label><span>提交渠道</span><Input value={submissionChannel} onChange={(event) => setSubmissionChannel(event.target.value)} onBlur={() => { if (submissionChannel !== (data.submission_channel ?? '')) void saveFields({ submission_channel: submissionChannel || null }) }} placeholder="例如：电子采购平台" /></label><label><span>凭证</span><Input value={receipt} onChange={(event) => setReceipt(event.target.value)} onBlur={() => { if (receipt !== (data.receipt ?? '')) void saveFields({ receipt: receipt || null }) }} placeholder="编号或附件说明" /></label><label><span>结果 / 结束原因</span><Input value={outcome} onChange={(event) => setOutcome(event.target.value)} onBlur={() => { if (outcome !== (data.outcome ?? '')) void saveFields({ outcome: outcome || null }) }} placeholder="成交、未成交或其他原因" /></label></div></section>
      <section className="followup-tasks"><div className="followup-section-heading"><div><h3>跟进任务</h3><Text type="secondary">已完成 {taskDone} / {data.tasks.length}；跳过需要理由。</Text></div><Tag>不等于已提交</Tag></div>{data.tasks.length === 0 ? <Text type="secondary">暂无已采用的任务。</Text> : <div className="followup-task-list">{data.tasks.map((task) => { const key = String(task.id); const reason = taskReasons[key] ?? ''; return <article className="followup-task" key={task.id}><div className="followup-task-main"><strong>{taskTitle(task)}</strong><div className="followup-task-meta">{task.required && <Tag color="blue">来源要求</Tag>}{task.source_kind && <Tag>{sourceLabel(task.source_kind)}</Tag>}{task.due_at && <Text type="secondary">原截止 {formatDate(task.due_at, true)}</Text>}</div><div className="followup-task-edit"><label>负责人<Input size="small" defaultValue={task.owner ?? ''} onBlur={(event) => updateTaskField(task, 'owner', event.target.value, reason)} /></label><label>截止<Input size="small" defaultValue={task.due_at ?? ''} onBlur={(event) => updateTaskField(task, 'due_at', event.target.value, reason)} /></label><label>调整理由<Input size="small" value={reason} onChange={(event) => setTaskReasons((current) => ({ ...current, [key]: event.target.value }))} placeholder="调整时填写" /></label></div>{task.status === 'done' && <label className="followup-completion-note">完成说明<Input.TextArea size="small" defaultValue={String(task.completion_note ?? task.note ?? '')} rows={2} onBlur={(event) => updateTaskField(task, 'note', event.target.value, reason)} /></label>}{task.status === 'skipped' && <Text type="warning">跳过理由：{task.skipped_reason || task.skip_reason || '未填写'}</Text>}</div><Select className="followup-status-select" value={task.status || 'pending'} disabled={action === `task-${task.id}`} onChange={(status) => chooseTaskStatus(task, status)} options={Object.entries(taskStatusLabels).map(([value, label]) => ({ value, label }))} /></article> })}</div>}</section>
      <section className="followup-materials"><div className="followup-section-heading"><h3>材料状态</h3><Text type="secondary">材料检查不会表示外部平台已接受。</Text></div>{data.materials.length === 0 ? <Text type="secondary">暂无材料清单。</Text> : <div className="followup-material-list">{data.materials.map((material) => <div className="followup-material" key={material.id}><span>{materialTitle(material)}</span><Select size="small" value={material.status || 'not_ready'} disabled={action === `material-${material.id}`} onChange={(status) => void updateMaterial(material, status)} options={Object.entries(materialStatusLabels).map(([value, label]) => ({ value, label }))} /></div>)}</div>}</section>
      {data.plan != null && <Collapse ghost items={[{ key: 'plan', label: '查看已采用的计划', children: <PlanPreview plan={data.plan} /> }]} />}
      {data.events.length > 0 && <Collapse ghost items={[{ key: 'events', label: <span><HistoryOutlined /> 跟进留痕（{data.events.length}）</span>, children: <ul className="followup-events">{data.events.map((event, index) => <li key={`${event.id ?? 'event'}-${index}`}><Text type="secondary">{event.at ? formatDate(event.at, true) : '时间待标记'}</Text><span>{event.text || event.message || event.type || '跟进记录'}</span></li>)}</ul> }]} />}
    </>}
    {data && <div className="followup-save-note"><CheckOutlined /> 阶段与动作由用户确认保存；{action === 'save' ? '正在保存…' : '修改后离开页面也会保留。'}</div>}
    <Modal title={taskAction?.label} open={Boolean(taskAction)} okText="保存" cancelText="取消" onCancel={() => { setTaskAction(null); setTaskNote('') }} onOk={() => void submitTaskAction()} confirmLoading={action.startsWith('task-')}><Input.TextArea value={taskNote} onChange={(event) => setTaskNote(event.target.value)} rows={4} placeholder={taskAction?.status === 'skipped' ? '说明为什么跳过，以及何时重新办理。' : '记录完成的动作、时间或结果。'} /></Modal>
  </div>
}
