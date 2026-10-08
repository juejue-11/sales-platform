import { ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Empty, Result, Spin, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { AiDecision, AiStatus, DecisionStatus, GateStatus, ProjectSummary } from '../types'

const { Text } = Typography

export const gateLabels: Record<GateStatus, string> = {
  candidate: '候选',
  review: '待核实',
  excluded: '已排除',
  expired: '已截止',
  closed: '已关闭',
  signal: '线索',
  duplicate: '疑似重复',
}

export const aiStatusLabels: Record<AiStatus, string> = {
  not_configured: '未配置模型',
  queued: '等待分析',
  running: '分析中',
  succeeded: '已分析',
  failed: '分析失败',
  stale: '结果待复核',
}

export const aiDecisionLabels: Record<Exclude<AiDecision, null>, string> = {
  recommend: '建议跟进',
  consider: '有条件跟进',
  verify: '待核实',
  not_recommended: '不建议',
}

export const decisionLabels: Record<DecisionStatus, string> = {
  new: '未处理',
  following: '跟进中',
  verify: '待核实',
  closed: '已关闭',
}

export function formatDate(value: string | null | undefined, withTime = false): string {
  if (!value) return '—'
  const date = dayjs(value)
  if (!date.isValid()) return value
  return date.format(withTime ? 'YYYY-MM-DD HH:mm' : 'YYYY-MM-DD')
}

export function formatFreshness(value: string | null | undefined): string {
  if (!value) return '暂无更新时间'
  const date = dayjs(value)
  if (!date.isValid()) return value
  return `${date.format('YYYY-MM-DD HH:mm')} 更新`
}

export function GateTag({ status }: { status: GateStatus }) {
  const color: Record<GateStatus, string> = {
    candidate: 'blue', review: 'orange', excluded: 'default', expired: 'red', closed: 'default', signal: 'cyan', duplicate: 'purple',
  }
  return <Tag color={color[status]}>{gateLabels[status] ?? status}</Tag>
}

export function AiStatusTag({ status }: { status: AiStatus }) {
  const color: Record<AiStatus, string> = {
    not_configured: 'default', queued: 'processing', running: 'processing', succeeded: 'success', failed: 'error', stale: 'warning',
  }
  return <Tag color={color[status]}>{aiStatusLabels[status] ?? status}</Tag>
}

export function AiDecisionTag({ decision }: { decision: AiDecision }) {
  if (!decision) return <Tag>暂无结论</Tag>
  const color: Record<Exclude<AiDecision, null>, string> = { recommend: 'success', consider: 'blue', verify: 'warning', not_recommended: 'default' }
  return <Tag color={color[decision]}>{aiDecisionLabels[decision]}</Tag>
}

export function DecisionTag({ status }: { status: DecisionStatus }) {
  const color: Record<DecisionStatus, string> = { new: 'default', following: 'blue', verify: 'orange', closed: 'default' }
  return <Tag color={color[status]}>{decisionLabels[status]}</Tag>
}

export function LoadingBlock({ text = '正在加载…' }: { text?: string }) {
  return <div className="state-block"><Spin size="small" /><Text type="secondary">{text}</Text></div>
}

export function EmptyBlock({ description = '暂无数据' }: { description?: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}

export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : '读取数据失败，请检查服务是否已启动'
  return (
    <Result
      status="warning"
      icon={<WarningOutlined />}
      title="数据暂时不可用"
      subTitle={message}
      extra={onRetry ? <Button icon={<ReloadOutlined />} onClick={onRetry}>重新加载</Button> : undefined}
    />
  )
}

export function CountCard({ label, value, tone = 'default', onClick }: { label: string; value: number; tone?: 'default' | 'blue' | 'orange' | 'green'; onClick?: () => void }) {
  return (
    <button type="button" className={`count-card count-card-${tone}${onClick ? ' count-card-clickable' : ''}`} onClick={onClick}>
      <span className="count-label">{label}</span>
      <strong>{value.toLocaleString('zh-CN')}</strong>
    </button>
  )
}

export function ProjectTitle({ project, onOpen }: { project: ProjectSummary; onOpen: (id: number) => void }) {
  return (
    <div className="project-title-cell">
      <Button type="link" className="project-title-link" onClick={() => onOpen(project.id)}>{project.title || '未命名项目'}</Button>
      <div className="project-meta">
        <Text type="secondary">{project.buyer || '采购方未标明'}</Text>
        <span className="meta-dot">·</span>
        <Text type="secondary">{project.kind || '未分类'}</Text>
      </div>
    </div>
  )
}

