import { ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Empty, Result, Spin, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { AiDecision, AiStatus, AnalysisMeta, CompanyMatchStatus, DecisionStatus, GateStatus, ProjectSummary } from '../types'

const { Text } = Typography

export const gateLabels: Record<GateStatus, string> = {
  candidate: '候选',
  review: '待核实',
  excluded: '已排除',
  expired: '已截止',
  closed: '已关闭',
  signal: '线索',
  duplicate: '确认重复',
}

export const aiStatusLabels: Record<AiStatus, string> = {
  not_configured: '未配置模型',
  idle: '待分析',
  queued: '等待分析',
  running: '分析中',
  succeeded: '已分析',
  failed: '分析失败',
  stale: '结果待复核',
  skipped: '无需分析',
}

export const aiDecisionLabels: Record<Exclude<AiDecision, null>, string> = {
  recommend: '建议跟进',
  consider: '有条件跟进',
  verify: '待核实',
  not_recommended: '不建议',
}

/** New judgments describe whether a market opportunity merits attention. */
export const marketAiDecisionLabels: Record<Exclude<AiDecision, null>, string> = {
  recommend: '建议关注',
  consider: '有条件关注',
  verify: '待核实',
  not_recommended: '不建议关注',
}

export const companyMatchLabels: Record<CompanyMatchStatus, string> = {
  not_evaluated: '未评估',
  partial: '部分评估',
  assessed: '已评估',
}

export function companyMatchDescription(status: CompanyMatchStatus): string {
  if (status === 'partial') return '仅覆盖企业资料能够支持的部分条件，其余仍需核实。'
  if (status === 'assessed') return '已按现有企业资料对照项目条件；资料为用户自填，不表示资格或供货能力已经核验。'
  return '企业匹配尚未评估；企业资料可选，项目市场分析可以独立开展。'
}

export const decisionLabels: Record<DecisionStatus, string> = {
  new: '未处理',
  following: '跟进中',
  verify: '待核实',
  closed: '已关闭',
}

const kindLabels: Record<string, string> = {
  procurement: '采购公告', tender: '招标公告', bid: '招标公告', award: '成交 / 中标', result: '成交 / 中标', news: '行业新闻', signal: '线索', mining_rights: '矿业权动态',
}

const sourceLabels: Record<string, string> = {
  mnr_mining_rights: '自然资源部采矿权出让公告',
  mnr_exploration_rights: '自然资源部探矿权出让公告',
  ccteg_materials: '中国煤科电子采购平台物资采购',
  ccteg_engineering: '中国煤科电子采购平台工程采购',
  ccgp_local_tender: '中国政府采购网地方招标',
  huang_ansteel: '鞍钢智慧招投标',
  huang_jiugang: '酒钢公共交易平台',
  jiugang_company_news: '酒钢集团新闻',
}

export function kindLabel(kind: string | null | undefined): string {
  if (!kind) return '未分类'
  return kindLabels[kind] ?? kind
}

export function sourceLabel(sourceId: string | null | undefined): string {
  if (!sourceId) return '来源未标明'
  return sourceLabels[sourceId] ?? sourceId
}

export function formatDate(value: string | null | undefined, withTime = false): string {
  if (!value) return '—'
  const date = dayjs(value)
  if (!date.isValid()) return value
  const hasTime = /[T\s]\d{2}:\d{2}/.test(value)
  return date.format(withTime && hasTime ? 'YYYY-MM-DD HH:mm' : 'YYYY-MM-DD')
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
    not_configured: 'default', idle: 'default', queued: 'processing', running: 'processing', succeeded: 'success', failed: 'error', stale: 'warning', skipped: 'default',
  }
  return <Tag color={color[status]}>{aiStatusLabels[status] ?? status}</Tag>
}

export function AiDecisionTag({ decision, analysisMeta }: { decision: AiDecision; analysisMeta?: AnalysisMeta | null }) {
  if (!decision) return <Tag>暂无结论</Tag>
  const color: Record<Exclude<AiDecision, null>, string> = { recommend: 'success', consider: 'blue', verify: 'warning', not_recommended: 'default' }
  const labels = analysisMeta?.decision_scope === 'market' ? marketAiDecisionLabels : aiDecisionLabels
  return <Tag color={color[decision]}>{labels[decision]}</Tag>
}

/** Distinguishes the new market semantic from judgments created before it. */
export function AnalysisScopeTag({ analysisMeta }: { analysisMeta?: AnalysisMeta | null }) {
  return analysisMeta?.decision_scope === 'market'
    ? <Tag color="blue">市场判断</Tag>
    : <Tag>旧语义</Tag>
}

export function CompanyMatchTag({ status }: { status?: CompanyMatchStatus | null }) {
  if (!status) return null
  const color: Record<CompanyMatchStatus, string> = { not_evaluated: 'default', partial: 'default', assessed: 'default' }
  return <Tag color={color[status]}>企业匹配：{companyMatchLabels[status]}</Tag>
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
        <Text type="secondary">{kindLabel(project.kind)}</Text>
      </div>
    </div>
  )
}
