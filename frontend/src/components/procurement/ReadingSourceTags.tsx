import { useState } from 'react'
import { Button, Popover, Tag } from 'antd'
import { FileSearchOutlined, LinkOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { ProcurementDocument, ProcurementReadingSource, ProjectRecord } from '../../types'
import type { ProcurementReaderSource } from './ProcurementSourceReader'
import { sourceLabel } from './reading'
import { sourceHrefWithPage } from './presentation'

export type OpenReadingSource = (sources: ProcurementReaderSource[], title: string) => void

interface ReadingSourceTagsProps {
  sources: ProcurementReadingSource[]
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  onRead: OpenReadingSource
  context?: string
  compact?: boolean
}

type SourceDetailProps = Omit<ReadingSourceTagsProps, 'sources' | 'compact'> & { source: ProcurementReadingSource; index: number; onClose: () => void }

function SourceDetail({ source, index, records, documents, onRead, context, onClose }: SourceDetailProps) {
  const record = records.find(item => item.id === source.record_id)
  const document = source.source_kind === 'attachment' ? documents.find(item => String(item.id) === String(source.document_id)) : undefined
  const attachment = source.source_kind === 'attachment'
  const title = attachment ? document?.name || '采购附件（名称待补充）' : record?.title || `公告记录 #${source.record_id}`
  const url = attachment ? document?.url : record?.original_url || record?.canonical_url || api.archiveUrl(source.record_id)
  const readerSource: ProcurementReaderSource = {
    ...source,
    key: `reading-${source.source_kind}-${source.record_id}-${source.document_id ?? ''}-${source.page ?? ''}-${index}`,
    label: sourceLabel(source, records, documents), url,
    document_name: document?.name, document_status: document?.status, document_pages: document?.pages,
  }
  return <div className="reading-source-detail">
    <div className="reading-source-detail-heading"><FileSearchOutlined /><strong>{title}</strong></div>
    <div className="reading-source-detail-meta"><span>{attachment ? '采购附件' : '公告正文'}</span><span>记录 #{source.record_id}</span>{source.document_id != null && <span>附件 {source.document_id}</span>}{source.page != null && source.page > 0 && <span>第 {source.page} 页</span>}</div>
    <blockquote>{source.quote?.trim() ? source.quote : '来源已登记，原文引文暂未提供。'}</blockquote>
    {source.sha256 && <details className="reading-source-version"><summary>资料版本已记录</summary><code>{source.sha256}</code></details>}
    <div className="reading-source-detail-actions"><Button size="small" type="primary" onClick={() => { onClose(); onRead([readerSource], context || '采购要求来源') }}>阅读原文</Button>{url && <a href={sourceHrefWithPage(url, source.page)} target="_blank" rel="noreferrer"><LinkOutlined /> 打开在线原文</a>}</div>
  </div>
}

function SourceTag({ source, index, ...props }: Omit<ReadingSourceTagsProps, 'sources' | 'compact'> & { source: ProcurementReadingSource; index: number }) {
  const [open, setOpen] = useState(false)
  const label = source.source_kind === 'attachment' ? `附件${source.page != null && source.page > 0 ? ` · 第 ${source.page} 页` : ''}` : '公告正文'
  return <Popover trigger="click" placement="bottom" autoAdjustOverflow open={open} onOpenChange={setOpen} classNames={{ root: 'reading-source-popover' }} title={null} content={<SourceDetail {...props} source={source} index={index} onClose={() => setOpen(false)} />}>
    <button type="button" className="reading-source-tag" aria-label={`查看${label}来源 ${index + 1}`} aria-expanded={open}><FileSearchOutlined aria-hidden="true" />{label}</button>
  </Popover>
}

export function ReadingSourceTags({ sources, compact = false, ...props }: ReadingSourceTagsProps) {
  const [open, setOpen] = useState(false)
  // Only identical citations collapse; different quotes/pages remain available.
  const uniqueSources = (sources ?? []).filter((source, index, all) => all.findIndex(candidate => candidate.source_kind === source.source_kind && candidate.record_id === source.record_id && candidate.document_id === source.document_id && candidate.page === source.page && candidate.quote === source.quote && candidate.sha256 === source.sha256) === index)
  if (!uniqueSources.length) return compact ? <span className="reading-source-unavailable">来源待补充</span> : <Tag className="reading-source-missing">来源待补充</Tag>
  if (compact) return <Popover trigger="click" placement="bottom" autoAdjustOverflow open={open} onOpenChange={setOpen} classNames={{ root: 'reading-source-popover' }} title={null} content={<div className="reading-source-details">{uniqueSources.map((source, index) => <SourceDetail {...props} source={source} index={index} key={index} onClose={() => setOpen(false)} />)}</div>}>
    <button type="button" className="reading-source-link" aria-label={`查看${props.context || '采购要求'}来源`} aria-expanded={open}><FileSearchOutlined aria-hidden="true" />来源{uniqueSources.length > 1 ? `（${uniqueSources.length}）` : ''}</button>
  </Popover>
  return <div className="reading-source-list">{uniqueSources.map((source, index) => <SourceTag {...props} source={source} index={index} key={`${source.source_kind}-${source.record_id}-${source.document_id ?? ''}-${source.page ?? ''}-${index}`} />)}</div>
}
