import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Spin, Typography } from 'antd'
import { api } from '../../api'
import type { Evidence, ProjectDetail as ProjectDetailData, ResearchEvidence, ResearchSource } from '../../types'
import { formatDate } from '../Common'
import { ResearchEvidenceCitation } from '../research/ResearchDisplay'

const { Text } = Typography

interface AttachmentSnapshotPage {
  page: number
  text: string
}

interface AttachmentSnapshot {
  text?: string | null
  page_texts?: AttachmentSnapshotPage[]
  sha256?: string | null
  fetched_at?: string | null
}

function sourceHrefWithPage(href: string, page?: number | null): string {
  if (!href || page == null || page <= 0) return href
  const hashIndex = href.indexOf('#')
  return `${hashIndex >= 0 ? href.slice(0, hashIndex) : href}#page=${page}`
}

function AttachmentSnapshotDrawer({ url, quote, open, onClose }: { url: string; quote: string; open: boolean; onClose: () => void }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [text, setText] = useState('')
  const [meta, setMeta] = useState<{ sha256?: string; fetchedAt?: string }>({})

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setText('')
    setMeta({})
    void fetch(url)
      .then(async (response) => {
        const payload = await response.json() as AttachmentSnapshot
        if (!response.ok) throw new Error('保存快照读取失败')
        if (cancelled) return
        const pageTexts = Array.isArray(payload.page_texts)
          ? payload.page_texts
            .filter((page): page is AttachmentSnapshotPage => Boolean(page) && typeof page === 'object' && typeof page.text === 'string')
            .map((page) => page.text)
            .join('\n\n')
          : ''
        setText(payload.text || pageTexts)
        setMeta({ sha256: payload.sha256 || undefined, fetchedAt: payload.fetched_at || undefined })
      })
      .catch((loadError) => { if (!cancelled) setError(loadError) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, url])

  return <Drawer title="附件保存快照" open={open} onClose={onClose} width={Math.min(720, window.innerWidth - 24)} destroyOnClose>
    <div className="detail-attachment-snapshot">
      {quote && <blockquote>“{quote}”</blockquote>}
      {meta.sha256 && <Text type="secondary">内容哈希：{meta.sha256}</Text>}
      {meta.fetchedAt && <Text type="secondary">保存时间：{formatDate(meta.fetchedAt, true)}</Text>}
      {loading && <div><Spin size="small" /> 正在读取保存快照…</div>}
      {Boolean(error) && !loading && <Alert type="error" showIcon message="保存快照读取失败" description={error instanceof Error ? error.message : '请稍后重试'} />}
      {!loading && !error && <pre className="detail-attachment-snapshot-text" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text || '保存快照没有可显示的正文。'}</pre>}
    </div>
  </Drawer>
}

export interface EvidenceCitationProps {
  projectId: number
  evidence: Evidence
  procurement: ProjectDetailData['procurement']
  records: ProjectDetailData['records']
  sources: ResearchSource[]
}

/** Shared citation renderer for report blocks, history and the current detail view. */
export function EvidenceCitation({ projectId, evidence, procurement, records, sources }: EvidenceCitationProps) {
  const [snapshotOpen, setSnapshotOpen] = useState(false)
  if ('source_ref' in evidence) {
    const researchEvidence: ResearchEvidence = evidence
    const source = sources.find((candidate) => candidate.source_ref === researchEvidence.source_ref && (!candidate.source_version || candidate.source_version === researchEvidence.source_version))
    return <ResearchEvidenceCitation projectId={projectId} evidence={researchEvidence} source={source} />
  }

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

  return <div className="detail-evidence-item">
    <span className="detail-evidence-source">
      <span>{label}</span>
      {evidence.snapshot_url && <Button type="link" size="small" onClick={() => setSnapshotOpen(true)}>保存快照</Button>}
      {href && <a href={href} target="_blank" rel="noreferrer">在线原文</a>}
    </span>
    <q>{evidence.quote}</q>
    {evidence.snapshot_url && <AttachmentSnapshotDrawer url={evidence.snapshot_url} quote={evidence.quote} open={snapshotOpen} onClose={() => setSnapshotOpen(false)} />}
  </div>
}
