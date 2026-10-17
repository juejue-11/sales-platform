import type {
  ProcurementAttachmentStatus,
  ProcurementData,
  ProcurementDocument,
  ProcurementItem,
  ProjectRecord,
} from '../../types'
import { normalizeOpportunityText } from '../opportunities/text'

export function sourceHrefWithPage(href: string, page?: number | null): string {
  if (!href || page == null || page <= 0) return href
  const hashIndex = href.indexOf('#')
  return `${hashIndex >= 0 ? href.slice(0, hashIndex) : href}#page=${page}`
}

export const procurementDocumentStatusMeta: Record<ProcurementDocument['status'], { label: string; color?: string }> = {
  parsed: { label: '已解析', color: 'success' },
  unsupported: { label: '暂不支持解析', color: 'default' },
  auth_required: { label: '需登录', color: 'warning' },
  failed: { label: '下载失败', color: 'error' },
  unreadable: { label: '无法读取', color: 'error' },
}

export const procurementAttachmentStatusMeta: Record<ProcurementAttachmentStatus, { label: string; color?: string }> = {
  not_checked: { label: '尚未检查公开附件', color: 'default' },
  none_public: { label: '未发现公开附件', color: 'default' },
  restricted: { label: '公开附件受限', color: 'warning' },
  available: { label: '已有公开附件', color: 'success' },
  failed: { label: '公开附件检查失败', color: 'error' },
}

const procurementErrorMessages: Record<string, string> = {
  format_not_supported: '暂不支持解析此附件格式',
  converter_unavailable: '自动文档转换组件不可用',
  conversion_timeout: '文档自动转换超时，可稍后重试',
  conversion_failed: '文档自动转换失败，请核对原文件是否损坏或加密',
  converted_document_too_large: '转换后的文档超过解析大小限制',
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

export function procurementValue(value: string | null | undefined): string {
  return normalizeOpportunityText(value) || ''
}

export function procurementDocumentError(value: string | null | undefined): string {
  const raw = procurementValue(value)
  if (!raw) return ''
  const normalized = raw.toLowerCase()
  if (raw.includes('该附件格式暂不能自动解析')) return '暂不支持解析此附件格式'
  const matchedCode = Object.keys(procurementErrorMessages).find((code) => normalized === code || normalized.includes(code))
  if (matchedCode) return procurementErrorMessages[matchedCode]
  return '获取或解析失败，请核对原文件'
}

export function hasProcurementDocumentId(item: ProcurementItem): boolean {
  return item.source_document_id !== null && item.source_document_id !== undefined && String(item.source_document_id).trim() !== ''
}

/**
 * Announcement rows are first-class procurement facts. Older responses do
 * not have source_kind, so an empty document id remains the compatibility
 * signal for a row quoted from the notice body.
 */
export function isAnnouncementProcurementItem(item: ProcurementItem): boolean {
  if (item.source_kind === 'announcement') return true
  if (item.source_kind === 'attachment') return false
  return !hasProcurementDocumentId(item)
}

export function procurementAttachmentStatus(procurement: ProcurementData | null | undefined): ProcurementAttachmentStatus {
  const explicitStatus = procurement?.attachment_status
  if (explicitStatus) return explicitStatus
  if (!procurement || procurement.status === 'not_checked') return 'not_checked'

  const documents = procurement.documents ?? []
  if (documents.some((document) => document.status === 'auth_required')) return 'restricted'
  if (documents.some((document) => document.status === 'failed' || document.status === 'unreadable')) return 'failed'
  if (documents.length > 0) return 'available'
  // `unavailable` is the old API's attempted-but-unsuccessful state. New
  // responses use `none_public` for a clean absence of public attachments.
  if (procurement.status === 'unavailable') return 'failed'
  return 'none_public'
}

export function procurementAttachmentMessage(procurement: ProcurementData | null | undefined, items: ProcurementItem[]): string {
  const status = procurementAttachmentStatus(procurement)
  const note = procurementValue(procurement?.attachment_note)
  if (note) return note
  if (status === 'not_checked') {
    return items.some(isAnnouncementProcurementItem)
      ? '已知采购事实来自公告正文；公开附件尚未检查。'
      : '公开附件尚未检查，当前没有可展示的附件资料。'
  }
  if (status === 'none_public') {
    return items.some(isAnnouncementProcurementItem)
      ? '公告正文已提供采购事实；未发现公开附件。'
      : '公告未提供可公开下载的附件，已知事实仍以公告正文为准。'
  }
  if (status === 'restricted') return '附件获取受限，请按公告说明获取招标文件。'
  if (status === 'failed') {
    const documentError = procurement?.documents?.find((document) => document.error)?.error
    return procurementDocumentError(documentError) || '公开附件检查失败，公告正文事实仍可核对。'
  }
  return '已发现公开附件，物料行会标注各自的来源。'
}

export function procurementTechnicalMissingValue(procurement: ProcurementData | null | undefined): string {
  return procurementTechnicalMissingForItem(procurement)
}

export function procurementTechnicalMissingForItem(procurement: ProcurementData | null | undefined, item?: ProcurementItem): string {
  const items = item ? [item] : (procurement?.items ?? [])
  const status = procurementAttachmentStatus(procurement)
  if (items.some(isAnnouncementProcurementItem)) {
    return status === 'restricted' ? '详细规格需核对招标文件' : '详细规格待核对'
  }

  switch (status) {
    case 'none_public': return '详细规格待核对'
    case 'restricted': return '详细规格需核对招标文件'
    case 'failed': return '附件未能提供规格，需核对原文件'
    case 'not_checked': return '详细规格待核对'
    default: return '详细规格待核对'
  }
}

export function procurementPageLabel(pages: number | null): string {
  return pages != null && pages > 0 ? `${pages} 页` : '页数未提取'
}

function originalNotStated(procurement: Pick<ProcurementData, 'missing_fields'>, field?: ProcurementMissingField): boolean {
  const aliases: Record<ProcurementMissingField, string[]> = {
    scope: ['scope', '范围', '采购范围'],
    delivery: ['delivery', '交付', '交货'],
    items: ['item', 'material', '物料', '明细'],
    qualification: ['qualification', '资格'],
    contact: ['contact', '联系方式'],
  }
  const missingFields = Array.isArray(procurement.missing_fields) ? procurement.missing_fields : []
  return missingFields.some((missing) => {
    const value = procurementValue(missing).toLowerCase()
    if (!/(?:原文未写|原文未说明|原文未提供|not[_ -]?stated|not[_ -]?specified|original[_ -]?missing)/i.test(value)) return false
    if (!field) return true
    return aliases[field].some((alias) => value.includes(alias.toLowerCase())) || /original[_ -]?(?:missing|not[_ -]?stated|not[_ -]?specified)|原文未/.test(value)
  })
}

export function procurementMissingValue(procurement: ProcurementData | null | undefined, value: string | null | undefined, field?: ProcurementMissingField): string {
  if (procurementValue(value)) return procurementValue(value)
  if (field && field !== 'items') {
    const labels: Record<Exclude<ProcurementMissingField, 'items'>, string> = {
      scope: '采购范围',
      delivery: '交付要求',
      qualification: '资格要求',
      contact: '联系方式',
    }
    return `公告尚未提取到${labels[field]}`
  }
  if (!procurement || procurementAttachmentStatus(procurement) === 'not_checked') return '尚未检查公开附件'
  const statuses = procurement.documents?.map((document) => document.status) ?? []
  if (procurementAttachmentStatus(procurement) === 'restricted' || statuses.includes('auth_required')) return '附件获取受限，物料明细待核对'
  if (procurementAttachmentStatus(procurement) === 'none_public') return '物料明细待核对'
  if (statuses.includes('unsupported')) return '待核对附件'
  if (procurementAttachmentStatus(procurement) === 'failed' || statuses.includes('failed') || statuses.includes('unreadable') || procurement.status === 'unavailable') {
    const documentError = procurement.documents?.find((document) => document.error)?.error
    return procurementDocumentError(documentError) || '获取或解析失败，请核对原文件'
  }
  if (originalNotStated(procurement, field)) return '原文未写'
  return '尚未提取到对应字段'
}

export function procurementDocumentFor(item: ProcurementItem, documents: ProcurementDocument[]): ProcurementDocument | undefined {
  if (isAnnouncementProcurementItem(item) || !hasProcurementDocumentId(item)) return undefined
  return documents.find((document) => String(document.id) === String(item.source_document_id))
}

export function uniformProcurementField(items: ProcurementItem[], field: 'delivery_period' | 'delivery_location'): string {
  const values = Array.from(new Set(items.map((item) => procurementValue(item[field])).filter(Boolean)))
  return values.length === 1 ? values[0] : ''
}

export function procurementSourceHref(
  item: ProcurementItem,
  document: ProcurementDocument | undefined,
  records: ProjectRecord[],
  archiveUrl: (recordId: number) => string,
): string {
  if (isAnnouncementProcurementItem(item)) {
    const record = records.find((candidate) => candidate.id === item.record_id)
    return record?.original_url || record?.canonical_url || archiveUrl(item.record_id)
  }
  const baseHref = document?.url || (() => {
    const record = records.find((candidate) => candidate.id === item.record_id)
    return record?.original_url || record?.canonical_url || archiveUrl(item.record_id)
  })()
  if (baseHref) return sourceHrefWithPage(baseHref, item.page)
  const record = records.find((candidate) => candidate.id === item.record_id)
  return record?.original_url || record?.canonical_url || archiveUrl(item.record_id)
}
