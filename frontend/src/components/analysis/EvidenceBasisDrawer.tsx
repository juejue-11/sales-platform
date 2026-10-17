import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Drawer, Spin } from 'antd'
import { api } from '../../api'
import type { Evidence, ProjectDetail } from '../../types'
import { attachmentVersion, groupEvidence, quoteExcerpt, quoteInSource, selectDirectEvidence } from './evidenceModel'
import type { EvidenceGroup } from './evidenceModel'
import './EvidenceBasisDrawer.css'

export interface DecisionBasis { title: string; text: string; evidence: Evidence[]; impact?: string }
interface Props { basis: DecisionBasis | null; detail: ProjectDetail; onClose: () => void }
interface SourceText { text: string; pages?: Map<number, string>; complete: boolean }
class UnverifiableSource extends Error {}

async function readSource(group: EvidenceGroup, projectId: number, cancelled: () => boolean, complete = false): Promise<SourceText> {
  if (group.text !== undefined && group.completeText !== false) return { text: group.text, complete: true }
  const item = group.evidence[0]
  if ('source_ref' in item) {
    if (!item.source_version) throw new UnverifiableSource('来源未提供保存版本')
    const pages = new Map<number, string>()
    let totalPages: number | null = null
    let unpaged = false
    const loadPage = async (page: number | undefined) => {
      if (pages.has(page ?? 0)) return
      let text = '', offset = 0, hasMore = true, actualPage = page
      while (hasMore && !cancelled()) {
        const response = await api.researchSourceVersion(projectId, item.source_ref, item.source_version, { page, offset, limit: 8000 })
        if (response.source.source_version !== item.source_version) throw new UnverifiableSource('来源版本不一致')
        totalPages = response.source.pages ?? totalPages
        actualPage = response.page ?? actualPage
        unpaged = response.page == null
        text += response.text
        hasMore = response.has_more
        const nextOffset = response.offset + response.text.length
        if (hasMore && nextOffset <= offset) throw new Error('原文读取未能继续')
        offset = nextOffset
      }
      pages.set(page ?? 0, text)
      if (actualPage != null) pages.set(actualPage, text)
    }
    for (const page of new Set(group.evidence.map(evidence => evidence.page ?? undefined))) await loadPage(page)
    if (complete) {
      if (totalPages == null && !unpaged) throw new UnverifiableSource('来源未提供完整页码')
      for (let page = 1; page <= (totalPages ?? 0) && !cancelled(); page++) await loadPage(page)
    }
    const count = totalPages as number | null
    const isComplete = unpaged || (count != null && Array.from({ length: count }, (_, index) => index + 1).every(page => pages.has(page)))
    const ordered = [...pages.entries()].filter(([page]) => page !== 0).sort(([a], [b]) => a - b)
    return { text: ordered.length ? ordered.map(([, text]) => text).join('\n\n') : pages.get(0) ?? '', pages, complete: isComplete }
  }
  if (item.document_id == null) return { text: '', complete: true }
  const version = attachmentVersion(item, projectId)
  if (!version) throw new UnverifiableSource('附件未提供保存版本')
  const snapshot = await api.documentSnapshot(projectId, item.document_id, version)
  if (snapshot.sha256 !== version) throw new UnverifiableSource('附件版本不一致')
  const pages = new Map((snapshot.page_texts ?? []).map(page => [page.page ?? 0, page.text ?? '']))
  return { text: snapshot.text || [...pages.values()].join('\n\n'), pages, complete: true }
}

function EvidenceSource({ group, claim, projectId }: { group: EvidenceGroup; claim: string; projectId: number }) {
  const [expanded, setExpanded] = useState<number[]>([])
  const [fullText, setFullText] = useState(false)
  const [fullContent, setFullContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const loadFullText = async () => {
    setLoading(true)
    setError(false)
    try {
      const source = await readSource(group, projectId, () => !mounted.current, true)
      if (!source.complete) throw new Error('完整原文尚未读取')
      if (mounted.current) setFullContent(source.text)
    } catch { if (mounted.current) setError(true) }
    finally { if (mounted.current) setLoading(false) }
  }
  return <section className="decision-evidence-source" aria-label={group.title}>
    <header className="decision-evidence-source-heading"><h4>{group.title}</h4>{group.href && <a href={group.href} target="_blank" rel="noreferrer">打开原文</a>}</header>
    {group.evidence.map((item, index) => {
      const excerpt = quoteExcerpt(item.quote, claim)
      const isExpanded = expanded.includes(index)
      const shortened = excerpt.before || excerpt.after
      return <div className="decision-evidence-fragment" key={`${item.page ?? ''}-${index}`}>
        {item.page != null && item.page > 0 && <span className="decision-evidence-page">第 {item.page} 页</span>}
        <blockquote className="decision-evidence-quote">{isExpanded ? item.quote.replace(/\s+/g, ' ').trim() : <>{excerpt.before && '…'}{excerpt.text}{excerpt.after && '…'}</>}</blockquote>
        {shortened && <Button type="link" size="small" aria-expanded={isExpanded} onClick={() => setExpanded(current => isExpanded ? current.filter(value => value !== index) : [...current, index])}>{isExpanded ? '收起引文' : '展开完整引文'}</Button>}
      </div>
    })}
    <Button type="link" size="small" aria-expanded={fullText} onClick={() => { if (!fullText && group.completeText === false && fullContent == null) void loadFullText(); setFullText(!fullText) }}>{fullText ? '收起完整原文' : '查看完整原文'}</Button>
    {fullText && (loading ? <div className="decision-evidence-loading" role="status"><Spin size="small" />正在读取完整原文…</div> : error ? <div className="decision-evidence-unavailable">完整原文暂无法读取。<Button type="link" size="small" onClick={() => void loadFullText()}>重试</Button></div> : <div className="decision-evidence-full-text" tabIndex={0} aria-label={`${group.title}完整原文`}>{(fullContent ?? group.text)?.replace(/(?<!\n)\n(?!\n)/g, ' ')}</div>)}
  </section>
}

export function EvidenceBasisDrawer({ basis, detail, onClose }: Props) {
  const groups = useMemo(() => basis ? groupEvidence(selectDirectEvidence(basis.text, basis.evidence), detail) : [], [basis, detail])
  const [resolved, setResolved] = useState<EvidenceGroup[]>([])
  const [resolvedFor, setResolvedFor] = useState<DecisionBasis | null>(null)
  const [loading, setLoading] = useState(false)
  const [unavailable, setUnavailable] = useState(false)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let cancelled = false
    setResolved([])
    setUnavailable(false)
    if (!basis) { setLoading(false); return }
    setLoading(true)
    void Promise.all(groups.map(async group => {
      try {
        const source = await readSource(group, detail.id, () => cancelled)
        const evidence = group.evidence.filter(item => quoteInSource(item.quote, source.pages?.get(item.page ?? 0) ?? source.text))
        return evidence.length ? { ...group, evidence, text: source.text, completeText: source.complete } : null
      } catch (error) {
        if (!cancelled && !(error instanceof UnverifiableSource)) setUnavailable(true)
        return null
      }
    })).then(results => {
      if (cancelled) return
      const verified = results.filter(item => item !== null)
      // Recheck combined assertions after provenance checks may have removed a citation.
      const supported = selectDirectEvidence(basis.text, verified.flatMap(group => group.evidence))
      setResolved(supported.length ? verified : [])
      setResolvedFor(basis)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [basis, detail.id, groups, retry])

  const isLoading = loading || Boolean(basis && resolvedFor !== basis)
  return <Drawer title={basis?.title ?? '关键事实依据'} open={Boolean(basis)} onClose={onClose} width="min(460px, 100vw)" rootClassName="decision-evidence-drawer" destroyOnHidden styles={{ body: { padding: 24 }, header: { padding: '18px 24px' } }}>
    {basis && <div className="decision-basis">
      <section className="decision-basis-judgment"><h3>判断内容</h3><p>{basis.text}</p>{basis.impact && <p className="decision-basis-impact">{basis.impact}</p>}</section>
      <section className="decision-basis-citations" aria-busy={isLoading}><h3>引用依据</h3>
        {isLoading ? <div className="decision-evidence-loading" role="status"><Spin size="small" />正在核对原文…</div> : <>
          {resolved.map(group => <EvidenceSource key={`${group.key}:${basis.text}`} group={group} claim={basis.text} projectId={detail.id} />)}
          {!resolved.length && <p className="decision-evidence-empty">暂无直接依据</p>}
          {unavailable && <div className="decision-evidence-unavailable"><span>部分原文暂无法读取，请重试。</span><Button type="link" size="small" onClick={() => setRetry(value => value + 1)}>重试</Button></div>}
        </>}
      </section>
    </div>}
  </Drawer>
}
