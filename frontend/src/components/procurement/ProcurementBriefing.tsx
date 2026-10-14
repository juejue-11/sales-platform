import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Pagination, Tag, Typography } from 'antd'
import { FileSearchOutlined, LinkOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type {
  ProcurementData,
  ProcurementDocument,
  ProcurementFact,
  ProcurementFactField,
  ProcurementFactSource,
  ProcurementItem,
  ProcurementSourceKind,
  ProjectBriefing,
  ProjectRecord,
} from '../../types'
import { formatDate } from '../Common'
import { cleanQualificationExcerpt, cleanScopeExcerpt } from '../opportunities/text'
import { groupProcurementItems, type ProcurementGroup } from '../../utils/procurementGroups'
import {
  procurementAttachmentMessage,
  procurementAttachmentStatus,
  procurementAttachmentStatusMeta,
  procurementDocumentError,
  procurementDocumentFor,
  procurementDocumentStatusMeta,
  hasProcurementDocumentId,
  isAnnouncementProcurementItem,
  procurementMissingValue,
  procurementPageLabel,
  procurementTechnicalMissingForItem,
  procurementValue,
} from './presentation'
import { ProcurementSourceReader, type ProcurementReaderSource } from './ProcurementSourceReader'
import './ProcurementBriefing.css'

const { Text, Paragraph } = Typography

export interface ProcurementBriefingProps {
  projectId: number
  briefing: ProjectBriefing | null | undefined
  procurement: ProcurementData | null | undefined
  records: ProjectRecord[]
  title: string
  onRefresh: () => void
  refreshing: boolean
  error: unknown
}

const factLabels: Record<ProcurementFactField, string> = {
  scope: '采购范围',
  delivery_period: '交付时间',
  delivery_location: '交付地点',
  qualification: '资格要求',
  contact: '联系方式',
}

const factOrder: ProcurementFactField[] = ['delivery_period', 'delivery_location', 'scope', 'qualification', 'contact']
const factMarkerLabels: Record<ProcurementFactField, string[]> = {
  scope: ['采购范围', '招标范围', '采购内容', '采购标的', '货物名称', '物料名称', '需求范围'],
  delivery_period: ['交付时间', '交货期', '供货期', '交付期限', '交货期限', '工期'],
  delivery_location: ['交付地点', '交货地点', '到货地点', '项目地点', '收货地点', '地址', '地点'],
  qualification: ['资格要求', '资质要求', '投标人资格', '技术要求', '技术条件', '技术质量', '质量标准', '技术性能', '货物质量'],
  contact: ['联系方式', '委托人', '代理机构', '联系人', '联系电话', '电话', '邮箱', '电子邮箱'],
}
const MATERIAL_GROUP_PAGE_SIZE = 8
const MATERIAL_DETAIL_PAGE_SIZE = 12

function normalized(value: unknown): string {
  return procurementValue(typeof value === 'string' ? value : value == null ? '' : String(value))
}

function sourceFingerprint(source: ProcurementFactSource): string {
  return [source.source_kind, source.record_id, source.document_id ?? '', source.page ?? '', source.quote || '', source.sha256 || '']
    .map((value) => normalized(value).toLocaleLowerCase())
    .join('|')
}

function uniqueSources(sources: ProcurementFactSource[] | undefined): ProcurementFactSource[] {
  const seen = new Set<string>()
  return (Array.isArray(sources) ? sources : []).filter((source) => {
    if (!source || (source.source_kind !== 'announcement' && source.source_kind !== 'attachment') || !Number.isFinite(Number(source.record_id))) return false
    const key = sourceFingerprint(source)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function factFingerprint(fact: ProcurementFact): string {
  return `${fact.field}|${normalized(fact.value).toLocaleLowerCase()}`
}

function dedupeFacts(facts: ProcurementFact[]): ProcurementFact[] {
  const byKey = new Map<string, ProcurementFact>()
  facts.forEach((fact) => {
    if (!fact || !fact.field || !normalized(fact.value)) return
    const key = factFingerprint(fact)
    const existing = byKey.get(key)
    if (!existing) {
      byKey.set(key, { ...fact, value: normalized(fact.value), sources: uniqueSources(fact.sources) })
      return
    }
    const statuses = new Set([existing.status, fact.status])
    const status = statuses.has('conflict') ? 'conflict' : statuses.has('uncertain') ? 'uncertain' : 'extracted'
    byKey.set(key, {
      ...existing,
      status,
      note: existing.note || fact.note || null,
      sources: uniqueSources([...existing.sources, ...(fact.sources || [])]),
    })
  })
  return factOrder.flatMap((field) => Array.from(byKey.values()).filter((fact) => fact.field === field))
}

function quoteSupportedByRecord(record: ProjectRecord | undefined, value: string): string {
  if (!record?.body_text || !value) return ''
  const needle = value.replace(/\s+/g, '').toLocaleLowerCase()
  if (!needle) return ''
  const compactBody = record.body_text.replace(/\s+/g, '').toLocaleLowerCase()
  return compactBody.includes(needle) ? value : ''
}

function fallbackAnnouncementSource(value: string, records: ProjectRecord[], preferredRecordId?: number | null): ProcurementFactSource[] {
  const candidates = preferredRecordId != null
    ? [records.find((record) => record.id === preferredRecordId), ...records.filter((record) => record.id !== preferredRecordId)]
    : records
  const record = candidates.find((candidate) => Boolean(candidate && quoteSupportedByRecord(candidate, value)))
  if (!record) return []
  return [{ source_kind: 'announcement', record_id: record.id, quote: value }]
}

function fallbackFacts(
  briefing: ProjectBriefing | null | undefined,
  procurement: ProcurementData | null | undefined,
  records: ProjectRecord[],
  title: string,
): ProcurementFact[] {
  const sourceRecordId = briefing?.record_id ?? null
  const scope = cleanScopeExcerpt(procurement?.scope_summary, title) || cleanScopeExcerpt(briefing?.scope_excerpt, title)
  const deliveryPeriod = normalized(procurement?.delivery_period) || normalized(briefing?.delivery_excerpt).match(/(?:时间|期限|交付)[：:]?\s*([^；;。\n]+)/)?.[1] || ''
  const deliveryLocation = normalized(procurement?.delivery_location) || ''
  const qualification = cleanQualificationExcerpt(briefing?.qualification_excerpt)
  const contact = normalized(briefing?.contact_excerpt)
  const values: Array<[ProcurementFactField, string]> = [
    ['scope', scope],
    ['delivery_period', deliveryPeriod],
    ['delivery_location', deliveryLocation],
    ['qualification', qualification],
    ['contact', contact],
  ]
  return values.filter(([, value]) => Boolean(normalized(value))).map(([field, value]) => {
    const sources = fallbackAnnouncementSource(value, records, sourceRecordId)
    const normalizedValue = normalized(value)
    const containsOtherField = factOrder.some((candidate) => candidate !== field && (
      normalizedValue.includes(factLabels[candidate])
      || factMarkerLabels[candidate].some((marker) => normalizedValue.includes(marker))
    ))
    return {
      id: `legacy-${field}`,
      field,
      value: normalized(value),
      status: sources.length && !containsOtherField ? 'extracted' as const : 'uncertain' as const,
      note: containsOtherField ? '内容包含其他栏目，建议核对原始资料。' : sources.length ? null : '来源摘录尚未定位，请核对原始资料。',
      sources,
    }
  })
}

function sourceLabel(source: ProcurementFactSource, records: ProjectRecord[], documents: ProcurementDocument[]): string {
  if (source.source_kind === 'attachment') {
    return documents.find((document) => String(document.id) === String(source.document_id))?.name || `附件 ${source.document_id ?? ''}`
  }
  return records.find((record) => record.id === source.record_id)?.title || '公告正文'
}

function sourceUrl(source: ProcurementFactSource, records: ProjectRecord[], documents: ProcurementDocument[]): string {
  const document = source.source_kind === 'attachment'
    ? documents.find((candidate) => String(candidate.id) === String(source.document_id))
    : undefined
  const record = records.find((candidate) => candidate.id === source.record_id)
  return document?.url || record?.original_url || record?.canonical_url || api.archiveUrl(source.record_id)
}

function toReaderSource(source: ProcurementFactSource, records: ProjectRecord[], documents: ProcurementDocument[], index: number): ProcurementReaderSource {
  const document = source.source_kind === 'attachment'
    ? documents.find((candidate) => String(candidate.id) === String(source.document_id))
    : undefined
  return {
    ...source,
    key: `${source.source_kind}-${source.record_id}-${source.document_id ?? ''}-${source.page ?? ''}-${index}`,
    label: sourceLabel(source, records, documents),
    url: sourceUrl(source, records, documents),
    document_name: document?.name,
    document_status: document?.status,
    document_pages: document?.pages,
  }
}

function statusMeta(status: ProcurementFact['status']) {
  if (status === 'conflict') return { label: '冲突', color: 'error' as const }
  if (status === 'uncertain') return { label: '待核实', color: 'warning' as const }
  return { label: '已提取', color: 'success' as const }
}

function previewText(value: string): string {
  return normalized(value) || '未提供'
}

function isGeneratedMaterialScope(value: string, hasItems: boolean, hasSources: boolean): boolean {
  if (!hasItems || hasSources) return false
  return /^(?:本次采购)?\s*共\s*\d+\s*(?:项|条|种)?\s*(?:物料|材料|明细|资料)(?:[，,：:].*)?$/u.test(normalized(value))
}

interface MaterialGroup extends ProcurementGroup {
  hasQuantityConflict: boolean
  hasQuantityUncertain: boolean
  hasFieldConflict: boolean
  hasFieldUncertain: boolean
}

interface RecordAttachment {
  id: string | number
  name: string
  url: string
  status?: string
  pages?: number | null
}

function recordAttachments(record: ProjectRecord): RecordAttachment[] {
  const raw = (record as { attachments?: unknown }).attachments
  if (!Array.isArray(raw)) return []
  return raw.flatMap((value, index) => {
    if (!value || typeof value !== 'object') return []
    const attachment = value as Record<string, unknown>
    const url = typeof attachment.url === 'string' ? attachment.url : typeof attachment.source_url === 'string' ? attachment.source_url : ''
    const name = typeof attachment.name === 'string' ? attachment.name : typeof attachment.filename === 'string' ? attachment.filename : `附件 ${index + 1}`
    return [{
      id: typeof attachment.id === 'string' || typeof attachment.id === 'number' ? attachment.id : `${record.id}-${index}`,
      name,
      url,
      status: typeof attachment.status === 'string' ? attachment.status : undefined,
      pages: typeof attachment.pages === 'number' ? attachment.pages : null,
    }]
  })
}

function documentTypeLabel(document: ProcurementDocument): string {
  const mime = normalized(document.mime_type).toLocaleLowerCase()
  const name = normalized(document.name).toLocaleLowerCase()
  if (mime.includes('pdf') || name.endsWith('.pdf')) return 'PDF'
  if (mime.includes('word') || mime.includes('officedocument') || /\.(?:doc|docx)$/u.test(name)) return 'Word'
  if (mime.includes('excel') || mime.includes('spreadsheet') || /\.(?:xls|xlsx)$/u.test(name)) return 'Excel'
  if (mime.includes('powerpoint') || /\.(?:ppt|pptx)$/u.test(name)) return 'PPT'
  return document.mime_type || ''
}

function dedupeItems(items: ProcurementItem[]): ProcurementItem[] {
  const seen = new Map<string, ProcurementItem>()
  items.forEach((item) => {
    const key = [item.name, item.specification, item.material, item.quantity, item.unit, item.delivery_period, item.delivery_location, item.code, item.standard]
      .map(normalized).join('|').toLocaleLowerCase()
    const existing = seen.get(key)
    if (!existing) {
      seen.set(key, { ...item, sources: uniqueSources(item.sources) })
      return
    }
    seen.set(key, {
      ...existing,
      note: existing.note || item.note,
      status: existing.status === 'conflict' || item.status === 'conflict' ? 'conflict' : existing.status || item.status,
      sources: uniqueSources([...(existing.sources || []), ...(item.sources || [])]),
    })
  })
  return Array.from(seen.values())
}

function quantityConcern(item: ProcurementItem): 'conflict' | 'uncertain' | null {
  const note = normalized(item.note).toLocaleLowerCase()
  if (!note || !/(数量|单位|件数|数量单位|quantity|unit|qty|material\s*qty)/u.test(note)) return null
  if (item.status === 'conflict' || /(冲突|不一致|矛盾|不同|不符|conflict|inconsistent|mismatch)/u.test(note)) return 'conflict'
  if (item.status === 'uncertain' || /(待核|不确定|缺失|未知|uncertain|unknown|missing)/u.test(note)) return 'uncertain'
  return null
}

function materialGroups(items: ProcurementItem[]): MaterialGroup[] {
  return groupProcurementItems(items).map((group) => {
    const quantityConcerns = group.items.map(quantityConcern)
    const hasQuantityConflict = quantityConcerns.some((concern) => concern === 'conflict')
    const hasQuantityUncertain = !hasQuantityConflict && quantityConcerns.some((concern) => concern === 'uncertain')
    return {
      ...group,
      // An item can be in conflict on delivery or another field. Only an
      // explicit quantity/unit concern prevents the exact utility total.
      quantitySummary: hasQuantityConflict ? '数量有冲突，未合计' : group.quantitySummary,
      hasQuantityConflict,
      hasQuantityUncertain,
      hasFieldConflict: group.items.some((item) => item.status === 'conflict'),
      hasFieldUncertain: group.items.some((item) => item.status === 'uncertain'),
    }
  })
}

function itemSources(item: ProcurementItem, documents: ProcurementDocument[]): ProcurementFactSource[] {
  if (item.sources?.length) return uniqueSources(item.sources)
  const sourceKind: ProcurementSourceKind = isAnnouncementProcurementItem(item) || !hasProcurementDocumentId(item) ? 'announcement' : 'attachment'
  return [{
    source_kind: sourceKind,
    record_id: item.record_id,
    ...(sourceKind === 'attachment' && hasProcurementDocumentId(item) ? { document_id: String(item.source_document_id) } : {}),
    ...(item.page != null ? { page: item.page } : {}),
    quote: item.quote || '',
    ...(sourceKind === 'attachment' ? { sha256: documents.find((document) => String(document.id) === String(item.source_document_id))?.sha256 || undefined } : {}),
  }]
}

function SourceButton({
  label = '查看来源',
  sources,
  records,
  documents,
  onOpen,
}: {
  label?: string
  sources: ProcurementFactSource[]
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  onOpen: (sources: ProcurementReaderSource[], title: string) => void
}) {
  if (!sources.length) return <Text type="secondary" className="procurement-source-missing">来源未提供，可在原始资料中核对</Text>
  const readerSources = sources.map((source, index) => toReaderSource(source, records, documents, index))
  const accessibleLabel = `${label}${sources.length > 1 ? `（${sources.length}）` : ''}`
  return <Button type="link" size="small" className="procurement-source-button" aria-label={accessibleLabel} icon={<FileSearchOutlined aria-hidden="true" />} onClick={() => onOpen(readerSources, readerSources[0]?.label || '原始资料')}>{accessibleLabel}</Button>
}

function FactRow({
  fact,
  records,
  documents,
  onOpen,
}: {
  fact: ProcurementFact
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  onOpen: (sources: ProcurementReaderSource[], title: string) => void
}) {
  const meta = statusMeta(fact.status)
  return <div className={`procurement-fact-row procurement-fact-row-${fact.status}`}>
    <div className="procurement-fact-value">
      <div className="procurement-fact-value-heading"><Tag color={meta.color}>{meta.label}</Tag></div>
      <Paragraph className="procurement-fact-preview" ellipsis={{ rows: 3, expandable: 'collapsible', symbol: (expanded) => expanded ? '收起字段值' : '查看全部该字段值' }}>{previewText(fact.value)}</Paragraph>
      {fact.note && <Text type="secondary" className="procurement-fact-note">{fact.note}</Text>}
    </div>
    <div className="procurement-fact-source"><SourceButton sources={fact.sources} records={records} documents={documents} onOpen={onOpen} /></div>
  </div>
}

function FactField({
  field,
  facts,
  fallback,
  hasItems,
  records,
  documents,
  onOpen,
}: {
  field: ProcurementFactField
  facts: ProcurementFact[]
  fallback: string
  hasItems: boolean
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  onOpen: (sources: ProcurementReaderSource[], title: string) => void
}) {
  const collapseScope = field === 'scope' && hasItems && facts.length > 0
  const defaultExpanded = !collapseScope && facts.length <= 2
  const [expanded, setExpanded] = useState(defaultExpanded)
  useEffect(() => {
    setExpanded(!collapseScope && facts.length <= 2)
  }, [collapseScope, facts.length])
  const visibleFacts = expanded ? facts : facts.slice(0, 2)
  const hiddenCount = Math.max(0, facts.length - visibleFacts.length)
  const statusTags = Array.from(new Set(facts.map((fact) => fact.status))).map((status) => ({ status, ...statusMeta(status) }))
  return <div className={`procurement-fact-field procurement-fact-field-${field}`}>
    <div className="procurement-fact-field-heading"><Text strong>{factLabels[field]}</Text><span className="procurement-fact-field-statuses">{!expanded && hiddenCount > 0 && statusTags.map((tag) => <Tag color={tag.color} key={tag.status}>{tag.label}</Tag>)}</span>{facts.length > 1 && <Text type="secondary">{facts.length} 个值</Text>}</div>
    {facts.length === 0 ? <Text type="secondary" className="procurement-fact-missing">{fallback}</Text> : collapseScope && !expanded ? <div className="procurement-fact-collapsed">
      <Paragraph className="procurement-fact-preview" ellipsis={{ rows: 3, expandable: false }}>{previewText(facts[0].value)}</Paragraph>
      <div className="procurement-fact-collapsed-meta">{statusTags.map((tag) => <Tag color={tag.color} key={tag.status}>{tag.label}</Tag>)}<Button type="link" size="small" onClick={() => setExpanded(true)}>查看全部说法（{facts.length}）</Button></div>
    </div> : <>
      {visibleFacts.map((fact) => <FactRow key={`${fact.id || field}-${factFingerprint(fact)}`} fact={fact} records={records} documents={documents} onOpen={onOpen} />)}
      {(hiddenCount > 0 || collapseScope) && <Button type="link" size="small" className="procurement-fact-more" onClick={() => setExpanded((current) => !current)}>{expanded ? '收起说法' : `另 ${hiddenCount} 条 / 查看全部说法`}</Button>}
    </>}
  </div>
}

function MaterialDetail({
  item,
  procurement,
  records,
  documents,
  onOpen,
}: {
  item: ProcurementItem
  procurement: ProcurementData | null | undefined
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  onOpen: (sources: ProcurementReaderSource[], title: string) => void
}) {
  const sources = itemSources(item, documents)
  const sourceDocument = procurementDocumentFor(item, documents)
  const itemStatus = item.status ? statusMeta(item.status) : null
  return <div className="procurement-material-detail">
    <div><span className="procurement-material-label">规格</span><span>{normalized(item.specification) || procurementTechnicalMissingForItem(procurement, item)}</span></div>
    <div><span className="procurement-material-label">数量</span><span>{normalized(item.quantity) || '待核对'} {normalized(item.unit)}</span></div>
    {item.code && <div><span className="procurement-material-label">物料编号</span><span>{item.code}</span></div>}
    {item.standard && <div><span className="procurement-material-label">执行标准</span><span>{item.standard}</span></div>}
    {(item.delivery_period || item.delivery_location) && <div><span className="procurement-material-label">交付</span><span>{[item.delivery_period, item.delivery_location].filter(Boolean).join(' · ')}</span></div>}
    {item.note && <div className="procurement-material-note"><span className="procurement-material-label">说明</span><span>{item.note}</span></div>}
    {itemStatus && <Tag color={itemStatus.color}>{itemStatus.label}</Tag>}
    {sourceDocument?.status && sourceDocument.status !== 'parsed' && <Text type="secondary">{procurementDocumentError(sourceDocument.error) || procurementDocumentStatusMeta[sourceDocument.status]?.label || '附件状态待核对'}</Text>}
    <SourceButton sources={sources} records={records} documents={documents} onOpen={onOpen} />
  </div>
}

function OriginalMaterials({
  records,
  documents,
  onOpen,
}: {
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  onOpen: (sources: ProcurementReaderSource[], title: string) => void
}) {
  const files = records.flatMap((record) => recordAttachments(record).map((attachment) => ({ record, attachment })))
  const knownDocumentKeys = new Set(documents.map((document) => `${document.record_id}|${document.url}|${document.name}`))
  const visibleFiles = files.filter(({ record, attachment }) => !knownDocumentKeys.has(`${record.id}|${attachment.url}|${attachment.name}`))
  const statusLabel = (status?: string) => status === 'parsed' ? '已解析' : status === 'auth_required' ? '需访问权限' : status === 'failed' || status === 'unreadable' ? '读取失败' : '原始文件'
  return <section className="procurement-original-materials" aria-labelledby="procurement-original-heading">
    <div className="procurement-section-heading"><div><h3 id="procurement-original-heading">原始资料</h3><Text type="secondary">公告正文、附件、来源链接和归档统一从这里核对</Text></div><Text type="secondary">{records.length} 条公告 · {documents.length + visibleFiles.length} 个附件</Text></div>
    {records.length === 0 && documents.length === 0 && visibleFiles.length === 0 ? <div className="procurement-empty">暂无可用原始资料。</div> : <div className="procurement-original-list">
      {records.map((record) => {
        const source: ProcurementFactSource = { source_kind: 'announcement', record_id: record.id, quote: '' }
        const readerSource = toReaderSource(source, records, documents, 0)
        const filesForRecord = visibleFiles.filter((file) => file.record.id === record.id)
        return <article className="procurement-original-record" key={record.id}>
          <div className="procurement-original-record-heading"><div><Text strong>{record.title || `记录 #${record.id}`}</Text><Text type="secondary">记录 #{record.id} · {record.parse_status || '已接入'}</Text></div><Button type="link" size="small" aria-label="查看完整正文" icon={<FileSearchOutlined aria-hidden="true" />} onClick={() => onOpen([readerSource], record.title || `记录 #${record.id}`)}>查看完整正文</Button></div>
          <p className="procurement-original-preview">{record.body_text || '这条记录没有正文内容。'}</p>
          <div className="procurement-original-links">
            {(record.original_url || record.canonical_url) && <a href={record.original_url || record.canonical_url || ''} target="_blank" rel="noreferrer"><LinkOutlined /> 查看公告来源</a>}
            <a href={api.archiveUrl(record.id)} target="_blank" rel="noreferrer">下载归档</a>
          </div>
          {filesForRecord.length > 0 && <div className="procurement-original-files">{filesForRecord.map(({ attachment }) => <div className="procurement-original-file" key={`${record.id}-${attachment.id}`}><div><Text strong>{attachment.name}</Text><Text type="secondary">{attachment.pages && attachment.pages > 0 ? `${attachment.pages} 页` : '页数未提取'}</Text></div><div><Tag>{statusLabel(attachment.status)}</Tag>{attachment.url && <a href={attachment.url} target="_blank" rel="noreferrer">查看来源</a>}</div></div>)}</div>}
        </article>
      })}
      {documents.length > 0 && <div className="procurement-original-documents-grid">{documents.map((document) => {
        const source: ProcurementFactSource = { source_kind: 'attachment', record_id: document.record_id, document_id: document.id, quote: '', ...(document.sha256 ? { sha256: document.sha256 } : {}) }
        const readerSource = toReaderSource(source, records, documents, 0)
        const meta = procurementDocumentStatusMeta[document.status] || { label: '状态待核对', color: 'default' }
        const documentError = procurementDocumentError(document.error)
        return <article className="procurement-original-record procurement-original-document" key={`document-${document.id}`}>
          <div className="procurement-original-record-heading"><div><Text strong>{document.name || `附件 ${document.id}`}</Text><Text type="secondary">附件 · {procurementPageLabel(document.pages)}{documentTypeLabel(document) ? ` · ${documentTypeLabel(document)}` : ''}</Text></div><Tag color={meta.color}>{meta.label}</Tag></div>
          {documentError && <div className="procurement-document-error">{documentError}</div>}
          <div className="procurement-original-links">
            {document.status === 'parsed' && <Button type="link" size="small" aria-label="查看附件内容" icon={<FileSearchOutlined aria-hidden="true" />} onClick={() => onOpen([readerSource], document.name || '附件')}>查看附件内容</Button>}
            {document.url && <a href={document.url} target="_blank" rel="noreferrer"><LinkOutlined /> 查看来源</a>}
          </div>
        </article>
      })}</div>}
    </div>}
  </section>
}

export function ProcurementBriefing({
  projectId,
  briefing,
  procurement,
  records,
  title,
  onRefresh,
  refreshing,
  error,
}: ProcurementBriefingProps) {
  const [itemSearch, setItemSearch] = useState('')
  const [expandedGroups, setExpandedGroups] = useState<string[]>([])
  const [groupPage, setGroupPage] = useState(1)
  const [expandedItemPages, setExpandedItemPages] = useState<Record<string, number>>({})
  const [reader, setReader] = useState<{ sources: ProcurementReaderSource[]; title: string } | null>(null)
  const items = procurement?.items ?? []
  const documents = procurement?.documents ?? []
  const attachmentStatus = procurementAttachmentStatus(procurement)
  const attachmentMeta = procurementAttachmentStatusMeta[attachmentStatus]
  const facts = useMemo(() => dedupeFacts(
    Array.isArray(procurement?.facts)
      ? procurement.facts
      : fallbackFacts(briefing, procurement, records, title),
  ).filter((fact) => !isGeneratedMaterialScope(fact.value, items.length > 0, fact.sources.length > 0)), [briefing, items.length, procurement, records, title])
  const materialItems = useMemo(() => dedupeItems(items), [items])
  const groups = useMemo(() => materialGroups(materialItems), [materialItems])
  const filteredGroups = useMemo(() => {
    const query = itemSearch.trim().toLocaleLowerCase()
    if (!query) return groups
    return groups.flatMap((group) => {
      const matchingItems = group.items.filter((item) => [item.name, item.material, item.specification, item.code, item.standard, item.quantity, item.unit, item.delivery_period, item.delivery_location].some((value) => normalized(value).toLocaleLowerCase().includes(query)))
      if (!matchingItems.length) return []
      const matchingGroup = materialGroups(matchingItems)[0]
      return matchingGroup ? [{ ...matchingGroup, key: group.key }] : []
    })
  }, [groups, itemSearch])

  useEffect(() => {
    setGroupPage(1)
    setExpandedItemPages({})
  }, [itemSearch])

  useEffect(() => {
    const pageCount = Math.max(1, Math.ceil(filteredGroups.length / MATERIAL_GROUP_PAGE_SIZE))
    setGroupPage((current) => Math.min(current, pageCount))
  }, [filteredGroups.length])

  const visibleGroups = useMemo(() => {
    const start = (groupPage - 1) * MATERIAL_GROUP_PAGE_SIZE
    return filteredGroups.slice(start, start + MATERIAL_GROUP_PAGE_SIZE)
  }, [filteredGroups, groupPage])

  const openReader = (sources: ProcurementReaderSource[], readerTitle: string) => {
    if (!sources.length) return
    setReader({ sources, title: readerTitle })
  }
  const errorText = error instanceof Error ? error.message : '采购资料校验失败，请稍后重试。'

  return <section className="procurement-desk" aria-labelledby="procurement-briefing-heading">
    <div className="procurement-desk-heading">
      <div><h2 id="procurement-briefing-heading">采购事实</h2><Text type="secondary">结构化事实统一去重，冲突和待核实信息保留标记</Text></div>
      <div className="procurement-desk-actions">
        {procurement?.updated_at && <time dateTime={procurement.updated_at}>{formatDate(procurement.updated_at, true).slice(5)} 更新</time>}
        <Button type="text" size="small" aria-label={attachmentStatus === 'not_checked' ? '检查公开附件' : '刷新公开资料'} icon={<ReloadOutlined aria-hidden="true" />} loading={refreshing} onClick={onRefresh}>{attachmentStatus === 'not_checked' ? '检查公开附件' : '刷新公开资料'}</Button>
      </div>
    </div>
    {Boolean(error) && <Alert className="procurement-desk-error" type="error" showIcon message="采购资料校验失败" description={errorText} />}

    <div className="procurement-fact-layout">
      <section className="procurement-material-section" aria-labelledby="procurement-material-heading">
        <div className="procurement-section-heading"><div><h3 id="procurement-material-heading">物料明细</h3><Text type="secondary">同值明细去重；仅明确数量冲突时不合计</Text></div><Text type="secondary">{groups.length} 组 · {materialItems.length} 条</Text></div>
        {items.length > 0 && <Input className="procurement-material-search" allowClear prefix={<SearchOutlined />} value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="搜索名称、规格、材质或编号" aria-label="搜索物料明细" />}
        {items.length === 0 ? <div className="procurement-empty">{procurementMissingValue(procurement, '', 'items')}</div> : filteredGroups.length === 0 ? <div className="procurement-empty">当前筛选没有匹配的物料。</div> : <>
          <div className="procurement-material-groups">
            {visibleGroups.map((group) => {
              const expanded = expandedGroups.includes(group.key)
              const itemPageCount = Math.max(1, Math.ceil(group.items.length / MATERIAL_DETAIL_PAGE_SIZE))
              const itemPage = Math.min(expandedItemPages[group.key] || 1, itemPageCount)
              const itemStart = (itemPage - 1) * MATERIAL_DETAIL_PAGE_SIZE
              const visibleItems = group.items.slice(itemStart, itemStart + MATERIAL_DETAIL_PAGE_SIZE)
              return <div className="procurement-material-group" key={group.key}>
                <div className="procurement-material-group-heading"><div><Text strong>{group.name || '名称未提供'}</Text><Text type="secondary">{group.material || '材质待核对'} · {group.specificationSummary}</Text></div><div className="procurement-material-group-summary">{group.hasQuantityConflict && <Tag color="error">数量冲突，未合计</Tag>}{group.hasQuantityUncertain && <Tag color="warning">数量待核对</Tag>}{group.hasFieldConflict && !group.hasQuantityConflict && <Tag color="error">字段冲突</Tag>}{group.hasFieldUncertain && <Tag color="warning">待核实</Tag>}<span>{group.quantitySummary}</span><Button type="link" size="small" onClick={() => setExpandedGroups((current) => current.includes(group.key) ? current.filter((key) => key !== group.key) : [...current, group.key])}>{expanded ? '收起明细' : `查看明细（${group.items.length}）`}</Button></div></div>
                {expanded && <div className="procurement-material-details">{visibleItems.map((item, index) => <MaterialDetail key={`${group.key}-${itemStart + index}`} item={item} procurement={procurement} records={records} documents={documents} onOpen={openReader} />)}{group.items.length > MATERIAL_DETAIL_PAGE_SIZE && <Pagination className="procurement-material-detail-pagination" size="small" current={itemPage} pageSize={MATERIAL_DETAIL_PAGE_SIZE} total={group.items.length} showSizeChanger={false} showTotal={(total, range) => `${range[0]}–${range[1]} / ${total} 条`} onChange={(page) => setExpandedItemPages((current) => ({ ...current, [group.key]: page }))} />}</div>}
              </div>
            })}
          </div>
          {filteredGroups.length > MATERIAL_GROUP_PAGE_SIZE && <Pagination className="procurement-material-pagination" current={groupPage} pageSize={MATERIAL_GROUP_PAGE_SIZE} total={filteredGroups.length} showSizeChanger={false} showTotal={(total, range) => `${range[0]}–${range[1]} / ${total} 组`} onChange={(page) => setGroupPage(page)} />}
        </>}
      </section>

      <section className="procurement-fact-section" aria-labelledby="procurement-facts-heading">
        <div className="procurement-section-heading"><div><h3 id="procurement-facts-heading">范围、交付与参与要求</h3><Text type="secondary">结构化字段和每个值的证据来源</Text><Text type="secondary" className="procurement-attachment-note">{procurementAttachmentMessage(procurement, items)}</Text></div><Tag color={attachmentMeta.color}>{attachmentMeta.label}</Tag></div>
        <div className="procurement-fact-grid">
          {factOrder.map((field) => {
            const fieldFacts = facts.filter((fact) => fact.field === field)
            const fallback = procurementMissingValue(procurement, '', field === 'scope' ? 'scope' : field === 'delivery_period' || field === 'delivery_location' ? 'delivery' : field)
            return <FactField key={field} field={field} facts={fieldFacts} fallback={fallback} hasItems={items.length > 0} records={records} documents={documents} onOpen={openReader} />
          })}
        </div>
      </section>
    </div>

    <OriginalMaterials records={records} documents={documents} onOpen={openReader} />
    <ProcurementSourceReader projectId={projectId} records={records} documents={documents} sources={reader?.sources || []} open={Boolean(reader)} title={reader?.title} onClose={() => setReader(null)} />
  </section>
}

