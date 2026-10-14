import type {
  ProcurementDemandType,
  ProcurementDocument,
  ProcurementReadingMilestone,
  ProcurementReading,
  ProcurementReadingStatus,
  ProjectRecord,
} from '../../types'

export const readingStatusMeta: Record<ProcurementReadingStatus, { label: string; color?: string }> = {
  pending: { label: '正在整理', color: 'processing' },
  complete: { label: '资料完整', color: 'success' },
  partial: { label: '资料部分完整', color: 'warning' },
  failed: { label: '阅读待处理', color: 'error' },
  not_configured: { label: '阅读待配置' },
}

export const demandTypeMeta: Record<ProcurementDemandType, { label: string; color?: string }> = {
  confirmed: { label: '明确采购', color: 'blue' },
  lead: { label: '潜在线索', color: 'cyan' },
  unknown: { label: '类型待核对' },
}

export const followupStageLabels: Record<string, string> = {
  evaluating: '待评估',
  working: '办理中',
  submitted: '已提交',
  awaiting_result: '等待结果',
  finished: '已结束',
}

export const procurementStageLabels: Record<string, string> = {
  discovery: '发现线索',
  confirmed: '已确认采购',
  registration: '报名 / 注册',
  document_access: '获取文件',
  question: '答疑 / 澄清',
  bidding: '报价 / 投标',
  opening: '开标 / 评审',
  result: '结果阶段',
  finished: '已结束',
}

export function readingStageLabel(stage: string | null | undefined): string {
  if (!stage) return '阶段待核对'
  return procurementStageLabels[stage] ?? stage
}

export function readingStatusLabel(status: ProcurementReadingStatus | null | undefined): string {
  return status ? (readingStatusMeta[status]?.label ?? status) : '采购说明待核对'
}

export function demandTypeLabel(type: ProcurementDemandType | null | undefined): string {
  return type ? (demandTypeMeta[type]?.label ?? type) : demandTypeMeta.unknown.label
}

export function submissionMilestone(reading: ProcurementReading | null | undefined): ProcurementReadingMilestone | null {
  if (!reading?.milestones?.length || reading.stale || reading.status === 'pending' || reading.status === 'failed' || reading.status === 'not_configured' || (reading.conflicts?.length ?? 0) > 0) return null
  const milestone = reading.milestones.find((item) => item.kind === 'submission')
  if (!milestone?.at || !milestone.sources?.length) return null
  return milestone
}

export function milestoneDate(reading: ProcurementReading | null | undefined, kind: ProcurementReadingMilestone['kind']): string | null {
  return reading?.milestones?.find((milestone) => milestone.kind === kind)?.at ?? null
}

export function readingCompleteness(reading: ProcurementReading | null | undefined): string {
  if (!reading) return '采购说明待核对'
  if (reading.status === 'complete' && !reading.stale) return '已覆盖当前资料'
  if (reading.status === 'partial') return '部分资料已覆盖'
  if (reading.status === 'pending') return '正在整理资料'
  if (reading.status === 'failed') return '资料处理状态待查看'
  if (reading.status === 'not_configured') return '阅读服务待配置'
  return reading.stale ? '资料有更新，待复核' : '完整性待核对'
}

export function sourceLabel(source: { source_kind: string; record_id: number; document_id?: number | string; page?: number | null }, records: ProjectRecord[] = [], documents: ProcurementDocument[] = []): string {
  const kind = source.source_kind === 'attachment' ? '附件' : '公告正文'
  const document = source.document_id == null ? '' : ` · ${documents.find((item) => String(item.id) === String(source.document_id))?.name || '补充文件'}`
  const record = records.find((item) => item.id === source.record_id)
  const recordName = source.document_id == null ? record?.title : undefined
  const page = source.page == null || source.page <= 0 ? '' : ` · 第 ${source.page} 页`
  return `${kind}${recordName ? ` · ${recordName}` : ''}${document}${page}`
}

export function readableCoverage(coverage: unknown[]): string[] {
  return coverage.flatMap((entry) => {
    if (typeof entry === 'string') return [entry]
    if (!entry || typeof entry !== 'object') return []
    const record = entry as Record<string, unknown>
    const label = ['label', 'document_name', 'source_title', 'title', 'name'].map((key) => record[key]).find((value): value is string => typeof value === 'string' && value.trim().length > 0) || (record.document_id != null ? '采购附件' : '资料片段')
    const statusValue = typeof record.status === 'string' ? record.status : ''
    const statusMap: Record<string, string> = { complete: '已阅读', completed: '已阅读', read: '已阅读', not_read: '待阅读', partial: '部分阅读', pending: '待补充', failed: '读取失败', unavailable: '待补充', restricted: '需补充资料' }
    const status = statusValue ? `：${statusMap[statusValue] ?? '待核对'}` : ''
    const detail = typeof record.note === 'string' ? `，${record.note}` : ''
    return [`${label}${status}${detail}`]
  })
}
