import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Alert, Button, Collapse, Drawer, Input, Select, Spin, Tag, Typography } from 'antd'
import { LinkOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type {
  ProcurementDocument,
  ProcurementDocumentSnapshot,
  ProcurementDocumentStatus,
  ProcurementSourceKind,
  ProjectRecord,
} from '../../types'
import { StructuredFields } from '../ReadableFields'

const { Text } = Typography

// The server snapshot is already bounded (and page-aware for PDFs). The
// drawer keeps a smaller viewport and navigates through the complete returned
// snapshot so the user can still reach a quote near the end of a long source.
const READER_WINDOW_CHARS = 24_000
const READER_WINDOW_OVERLAP = 256
const READER_BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DIV', 'DL', 'DT', 'FIGCAPTION',
  'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI',
  'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'TABLE', 'TBODY', 'TD', 'TH', 'THEAD', 'TR',
  'UL',
])

export interface ProcurementReaderSource {
  key: string
  source_kind: ProcurementSourceKind
  record_id: number
  document_id?: number | string
  page?: number | null
  quote?: string | null
  sha256?: string | null
  label: string
  url?: string | null
  document_name?: string | null
  document_status?: ProcurementDocumentStatus
  document_pages?: number | null
}

export interface ProcurementSourceReaderProps {
  projectId: number
  records: ProjectRecord[]
  documents: ProcurementDocument[]
  sources: ProcurementReaderSource[]
  open: boolean
  onClose: () => void
  title?: string
}

function readerContentSignature(props: ProcurementSourceReaderProps): string {
  const sourceSignature = props.sources.map((source) => [
    source.key,
    source.source_kind,
    source.record_id,
    source.document_id ?? '',
    source.page ?? '',
    source.quote ?? '',
    source.sha256 ?? '',
    source.document_status ?? '',
    source.document_pages ?? '',
  ].join('\u0001')).join('\u0002')
  const recordSignature = props.records.map((record) => [record.id, record.body_text ?? '', record.parse_status ?? ''].join('\u0001')).join('\u0002')
  const documentSignature = props.documents.map((document) => [document.id, document.sha256 ?? '', document.status ?? '', document.pages ?? ''].join('\u0001')).join('\u0002')
  return [props.projectId, props.open, props.title ?? '', sourceSignature, recordSignature, documentSignature].join('\u0003')
}

function areReaderPropsEqual(previous: ProcurementSourceReaderProps, next: ProcurementSourceReaderProps): boolean {
  return readerContentSignature(previous) === readerContentSignature(next)
}

function sourceHref(href: string, page?: number | null): string {
  if (!href || page == null || page <= 0) return href
  const hashIndex = href.indexOf('#')
  return `${hashIndex >= 0 ? href.slice(0, hashIndex) : href}#page=${page}`
}

const readingUnits = /^(?:吨|千克|公斤|克|米|厘米|毫米|立方米|平方米|台|套|件|项|批|个|箱|年|月|日|小时|分钟|万元|元|％|%|t|kg|mm|cm|m|kw|kva|hz|a|v)/iu
const readingPunctuation = /^[：:，,；;。.!！？、）)】\]}]/u
const readingNumberedLine = /^(?:\d+[.)、](?=\s|[A-Za-z\u4e00-\u9fff]|$)|[一二三四五六七八九十百千万]+[、.)](?=\s|[A-Za-z\u4e00-\u9fff]|$)|[-*•])\s*/u
const readingSectionNumber = /^(?:\d{1,2}(?:\.\d{1,2}){1,3})(.*)$/u

function isReadingNumberedLine(line: string): boolean {
  if (readingNumberedLine.test(line)) return true
  const match = line.match(readingSectionNumber)
  if (!match) return false
  const suffix = match[1].trim()
  // Keep numbered headings such as 1.1项目名称 on their own reading line,
  // while leaving dates, quantities, phone numbers, and unit values joined.
  return Boolean(suffix && !readingUnits.test(suffix) && /^[A-Za-z\u4e00-\u9fff]{2}/u.test(suffix))
}

function joinReadingFragments(raw: string): string {
  const paragraphs = raw.replace(/\r\n?/g, '\n').split(/\n{2,}/u)
  return paragraphs.map((paragraph) => {
    const lines = paragraph.split('\n').map((line) => line.trim()).filter(Boolean)
    if (lines.length <= 1) return lines[0] || ''
    let output = lines[0]
    for (const current of lines.slice(1)) {
      const previous = output.slice(-1)
      const preserveBreak = isReadingNumberedLine(current)
      if (preserveBreak) {
        output += `\n${current}`
      } else if (readingPunctuation.test(current) || /[([{【「“]$/u.test(previous)) {
        output += current
      } else if (/^[A-Za-z]/u.test(current) && /[A-Za-z]$/u.test(previous)) {
        output += ` ${current}`
      } else if (/^\d/u.test(current) && /\d$/.test(previous)) {
        output += current
      } else if (readingUnits.test(current) || /\d$/u.test(previous)) {
        output += current
      } else {
        output += current
      }
    }
    return output
  }).filter(Boolean).join('\n\n').trim()
}

function normalizeHtmlForReading(raw: string): string {
  if (!raw || typeof DOMParser === 'undefined' || !/<[a-z][\s\S]*>/i.test(raw)) {
    return joinReadingFragments(raw.replace(/[ \t]+\n/g, '\n').replace(/\n[ \t]+/g, '\n'))
  }

  const document = new DOMParser().parseFromString(raw, 'text/html')
  const chunks: string[] = []
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      chunks.push(node.nodeValue || '')
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const element = node as HTMLElement
    if (element.tagName === 'SCRIPT' || element.tagName === 'STYLE' || element.tagName === 'NOSCRIPT') return
    const block = READER_BLOCK_TAGS.has(element.tagName)
    if (block) chunks.push('\n')
    element.childNodes.forEach(visit)
    if (block) chunks.push('\n')
  }
  document.body.childNodes.forEach(visit)
  return joinReadingFragments(chunks.join('')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n'))
}

/**
 * Convert HTML fragment breaks only for the reading surface. The record body
 * and the immutable attachment response remain untouched for audit purposes.
 */
export function normalizeProcurementReadingText(value: string): string {
  return normalizeHtmlForReading(value)
}

function snapshotText(snapshot: ProcurementDocumentSnapshot, page?: number | null): string {
  const pages = Array.isArray(snapshot.page_texts)
    ? snapshot.page_texts.filter((entry) => entry && typeof entry.text === 'string')
    : []
  if (page != null && page > 0) {
    const pageText = pages.find((entry) => entry.page === page)?.text
    if (typeof pageText === 'string') return pageText
  }
  if (typeof snapshot.text === 'string') return snapshot.text
  return pages.map((entry) => entry.text || '').join('\n\n')
}

function looseIndexOf(text: string, quote: string): number {
  const needle = quote.replace(/\s+/g, '').toLocaleLowerCase()
  if (!needle) return -1
  let normalized = ''
  const positions: number[] = []
  for (let index = 0; index < text.length; index += 1) {
    if (/\s/.test(text[index])) continue
    normalized += text[index].toLocaleLowerCase()
    positions.push(index)
  }
  const found = normalized.indexOf(needle)
  return found < 0 ? -1 : positions[found]
}

function looseRange(text: string, quote: string): [number, number] | null {
  const needle = quote.replace(/\s+/g, '').toLocaleLowerCase()
  if (!needle) return null
  let normalized = ''
  const positions: number[] = []
  for (let index = 0; index < text.length; index += 1) {
    if (/\s/.test(text[index])) continue
    normalized += text[index].toLocaleLowerCase()
    positions.push(index)
  }
  const found = normalized.indexOf(needle)
  if (found < 0) return null
  return [positions[found], positions[found + needle.length - 1] + 1]
}

function literalMatches(text: string, query: string): number[] {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return []
  const haystack = text.toLocaleLowerCase()
  const matches: number[] = []
  let offset = 0
  while (offset <= haystack.length - needle.length) {
    const found = haystack.indexOf(needle, offset)
    if (found < 0) break
    matches.push(found)
    offset = found + Math.max(needle.length, 1)
  }
  return matches
}

function highlightLiteral(text: string, query: string, activeMatch: number) {
  const needle = query.trim()
  if (!needle) return text
  const lowerText = text.toLocaleLowerCase()
  const lowerNeedle = needle.toLocaleLowerCase()
  const output: ReactNode[] = []
  let cursor = 0
  let matchIndex = 0
  while (cursor < text.length) {
    const found = lowerText.indexOf(lowerNeedle, cursor)
    if (found < 0) {
      output.push(text.slice(cursor))
      break
    }
    if (found > cursor) output.push(text.slice(cursor, found))
    output.push(<mark key={`${found}-${matchIndex}`} data-reader-match={matchIndex === activeMatch ? 'active' : 'match'}>{text.slice(found, found + needle.length)}</mark>)
    cursor = found + needle.length
    matchIndex += 1
  }
  return output
}

function sourceUnavailableMessage(source: ProcurementReaderSource, document?: ProcurementDocument): string {
  if (source.source_kind === 'announcement') return '公告正文没有可读取的保存内容。'
  if (document?.status === 'auth_required') return '该附件需要登录或具备访问权限，暂时不能读取保存内容。'
  if (document?.status === 'unsupported') return '该附件格式暂不支持解析，无法在此展示正文。'
  if (document?.status === 'unreadable') return '该附件无法读取，无法在此展示正文。'
  if (document?.status === 'failed') return '该附件获取或解析失败，无法在此展示正文。'
  return '该附件没有可读取的保存内容。'
}

function recordForSource(source: ProcurementReaderSource, records: ProjectRecord[]) {
  return records.find((record) => record.id === source.record_id)
}

function documentForSource(source: ProcurementReaderSource, documents: ProcurementDocument[]) {
  if (source.document_id == null) return undefined
  return documents.find((document) => String(document.id) === String(source.document_id))
}

export const ProcurementSourceReader = memo(function ProcurementSourceReader({
  projectId,
  records,
  documents,
  sources,
  open,
  onClose,
  title = '原始资料',
}: ProcurementSourceReaderProps) {
  const [sourceKey, setSourceKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [rawText, setRawText] = useState('')
  const [readingMode, setReadingMode] = useState<'reading' | 'raw'>('reading')
  const [quoteExpanded, setQuoteExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const [activeMatch, setActiveMatch] = useState(0)
  const [windowStart, setWindowStart] = useState(0)
  const requestRef = useRef(0)
  const textRef = useRef<HTMLDivElement>(null)
  const readerScrollTopRef = useRef<number | null>(null)
  const evidenceAnchorRef = useRef('')
  const recordsRef = useRef(records)
  const documentsRef = useRef(documents)
  recordsRef.current = records
  documentsRef.current = documents

  const activeSource = useMemo(() => sources.find((source) => source.key === sourceKey) || sources[0], [sourceKey, sources])
  const activeSourceKey = activeSource?.key || ''
  const activeRecord = activeSource ? recordForSource(activeSource, records) : undefined
  const activeDocument = activeSource ? documentForSource(activeSource, documents) : undefined
  const externalUrl = activeSource
    ? sourceHref(activeSource.url || (activeSource.source_kind === 'attachment'
      ? activeDocument?.url || ''
      : activeRecord?.original_url || activeRecord?.canonical_url || api.archiveUrl(activeSource.record_id)), activeSource.page)
    : ''
  const readingText = useMemo(() => normalizeProcurementReadingText(rawText), [rawText])
  const viewText = readingMode === 'raw' ? rawText : readingText
  const matches = useMemo(() => literalMatches(viewText, query), [query, viewText])
  const evidencePosition = useMemo(() => {
    if (!activeSource?.quote || !viewText) return -1
    return looseIndexOf(viewText, activeSource.quote)
  }, [activeSource?.quote, viewText])
  const boundedWindowStart = Math.max(0, Math.min(windowStart, Math.max(0, viewText.length - READER_WINDOW_CHARS)))
  const windowEnd = Math.min(viewText.length, boundedWindowStart + READER_WINDOW_CHARS)
  const isTruncated = viewText.length > READER_WINDOW_CHARS
  const activeGlobalMatch = matches[activeMatch] ?? -1
  const activeMatchEnd = activeGlobalMatch >= 0 && query.trim() ? activeGlobalMatch + query.trim().length : -1
  const evidenceAnchorEnd = evidencePosition >= 0 ? evidencePosition + 420 : -1
  const renderWindowStart = Math.max(0, boundedWindowStart - READER_WINDOW_OVERLAP)
  const renderBaseEnd = Math.min(viewText.length, windowEnd + READER_WINDOW_OVERLAP)
  const renderWindowEnd = Math.min(viewText.length, Math.max(
    renderBaseEnd,
    activeGlobalMatch >= renderWindowStart && activeGlobalMatch < renderBaseEnd ? activeMatchEnd : -1,
    evidencePosition >= renderWindowStart && evidencePosition < renderBaseEnd ? evidenceAnchorEnd : -1,
  ))
  const boundedText = viewText.slice(renderWindowStart, renderWindowEnd)
  const activeWindowMatch = activeGlobalMatch >= renderWindowStart && activeGlobalMatch < renderWindowEnd
    ? matches.slice(0, activeMatch + 1).filter((match) => match >= renderWindowStart && match < renderWindowEnd).length - 1
    : -1
  const evidenceIsLong = Boolean(activeSource?.quote && activeSource.quote.replace(/\s+/g, '').length > 420)
  const locationLabel = evidencePosition >= 0
    ? `正文第 ${evidencePosition + 1} 字附近${evidenceIsLong ? '（引文较长，已标记起始片段）' : ''}`
    : activeSource?.quote
      ? '当前快照中未找到原引文，请核对在线原文'
      : '未提供可定位引文'

  const sourceVersion = activeSource
    ? activeSource.source_kind === 'announcement'
      ? `${activeSource.key}|${activeRecord?.body_text || ''}`
      : `${activeSource.key}|${activeSource.sha256 || activeDocument?.sha256 || ''}|${activeDocument?.status || ''}|${activeDocument?.pages ?? ''}`
    : ''

  const resetReaderScroll = () => {
    readerScrollTopRef.current = null
  }

  useLayoutEffect(() => {
    const element = textRef.current
    const previousScrollTop = readerScrollTopRef.current
    if (!element || previousScrollTop == null || Math.abs(element.scrollTop - previousScrollTop) <= 0.5) return
    element.scrollTop = previousScrollTop
  })

  const loadSource = useCallback(async (source: ProcurementReaderSource | undefined) => {
    const requestId = ++requestRef.current
    resetReaderScroll()
    evidenceAnchorRef.current = ''
    setLoading(true)
    setError(null)
    setRawText('')
    setQuery('')
    setActiveMatch(0)
    setWindowStart(0)
    setReadingMode('reading')
    if (!source) {
      setLoading(false)
      return
    }
    try {
      if (source.source_kind === 'announcement') {
        const record = recordForSource(source, recordsRef.current)
        if (!record?.body_text) throw new Error(sourceUnavailableMessage(source))
        if (requestId === requestRef.current) setRawText(record.body_text)
      } else {
        const document = documentForSource(source, documentsRef.current)
        // The current attachment list can omit an older document referenced by
        // this reading. An explicit id and hash still identify its saved
        // snapshot; the existing API checks both project ownership and version.
        const historicalSnapshot = !document && source.document_id != null && Boolean(source.sha256)
        if ((!document && !historicalSnapshot) || (document && document.status !== 'parsed')) throw new Error(sourceUnavailableMessage(source, document))
        const snapshot = await api.documentSnapshot(projectId, document?.id ?? source.document_id!, source.sha256 || document?.sha256)
        if (snapshot.status && snapshot.status !== 'parsed') throw new Error(sourceUnavailableMessage(source, document))
        if (requestId !== requestRef.current) return
        setRawText(snapshotText(snapshot, source.page))
      }
    } catch (loadError) {
      if (requestId === requestRef.current) setError(loadError)
    } finally {
      if (requestId === requestRef.current) setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    if (!open) {
      requestRef.current += 1
      return
    }
    setSourceKey((current) => (sources.some((source) => source.key === current) ? current : sources[0]?.key || ''))
  }, [open, sources])

  useEffect(() => {
    if (!open) return
    void loadSource(activeSource)
    return () => { requestRef.current += 1 }
  }, [activeSourceKey, loadSource, open, sourceVersion])

  useEffect(() => {
    setQuoteExpanded(false)
  }, [activeSourceKey])

  useEffect(() => {
    if (matches.length === 0) {
      setActiveMatch(0)
      return
    }
    setActiveMatch((current) => Math.min(current, matches.length - 1))
  }, [matches.length])

  useEffect(() => {
    const searchQuery = query.trim()
    const position = searchQuery ? (matches[activeMatch] ?? -1) : evidencePosition
    if (position < 0) return
    if (!searchQuery) {
      const anchorKey = `${activeSourceKey}|${sourceVersion}|${readingMode}`
      if (evidenceAnchorRef.current === anchorKey) return
      evidenceAnchorRef.current = anchorKey
    }
    const nextStart = Math.floor(position / READER_WINDOW_CHARS) * READER_WINDOW_CHARS
    setWindowStart((current) => current === nextStart ? current : nextStart)
  }, [activeMatch, activeSourceKey, evidencePosition, matches, query, readingMode, sourceVersion])

  useEffect(() => {
    if (!textRef.current || loading) return
    const target = textRef.current.querySelector('[data-reader-match="active"]')
    if (target instanceof HTMLElement) target.scrollIntoView({ block: 'center', behavior: 'auto' })
    else if (evidencePosition >= 0) {
      const evidence = textRef.current.querySelector('[data-reader-evidence]')
      if (evidence instanceof HTMLElement) evidence.scrollIntoView({ block: 'center' })
    }
  }, [activeMatch, boundedText, evidencePosition, loading, query, windowStart])

  const renderText = () => {
    if (!boundedText) return <Text type="secondary">保存快照没有可显示的正文。</Text>
    if (query.trim()) return <>{highlightLiteral(boundedText, query, activeWindowMatch)}</>
    if (evidencePosition >= 0 && activeSource?.quote) {
      const quoteRange = looseRange(boundedText, activeSource.quote)
      const localStart = quoteRange?.[0] ?? evidencePosition - renderWindowStart
      if (localStart >= 0 && localStart < boundedText.length) {
        const localEnd = quoteRange ? Math.min(quoteRange[1], localStart + 420) : Math.min(boundedText.length, localStart + 420)
        return <>{boundedText.slice(0, localStart)}<mark data-reader-evidence="true">{boundedText.slice(localStart, localEnd)}</mark>{boundedText.slice(localEnd)}</>
      }
    }
    return boundedText
  }

  const retry = () => { void loadSource(activeSource) }
  const errorText = error instanceof Error ? error.message : '保存快照读取失败，请稍后重试。'

  return (
    <Drawer
      className="procurement-source-reader"
      rootClassName="procurement-source-reader-root"
      title={title}
      placement="right"
      width={Math.min(760, window.innerWidth - 24)}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {!activeSource ? <Text type="secondary">暂无可打开的来源。</Text> : <>
        <div className="procurement-reader-source-bar">
          <div className="procurement-reader-source-meta">
            <Tag color={activeSource.source_kind === 'attachment' ? 'blue' : 'default'}>{activeSource.source_kind === 'attachment' ? '附件' : '公告正文'}</Tag>
            <Text strong>{activeSource.document_name || activeSource.label}</Text>
            <Text type="secondary">记录 #{activeSource.record_id}</Text>
            {activeSource.page != null && activeSource.page > 0 && <Text type="secondary">第 {activeSource.page} 页</Text>}
          </div>
          {sources.length > 1 && <Select
            size="small"
            value={activeSource.key}
            onChange={setSourceKey}
            aria-label="选择来源"
            options={sources.map((source) => ({ value: source.key, label: `${source.label}${source.page != null && source.page > 0 ? ` · 第 ${source.page} 页` : ''}` }))}
          />}
        </div>

        {activeSource.quote && <div className={`procurement-reader-quote${quoteExpanded ? ' procurement-reader-quote-expanded' : ''}`}>
          <span className="procurement-reader-quote-text">“{activeSource.quote}”</span>
          {activeSource.quote.length > 420 && <Button type="link" size="small" className="procurement-reader-quote-toggle" aria-label={quoteExpanded ? '收起引文' : '展开引文'} onClick={() => setQuoteExpanded((current) => !current)}>{quoteExpanded ? '收起引文' : '展开引文'}</Button>}
        </div>}
        <div className="procurement-reader-actions">
          <Input
            allowClear
            prefix={<SearchOutlined />}
            value={query}
            onChange={(event) => { resetReaderScroll(); setQuery(event.target.value) }}
            onPressEnter={() => { resetReaderScroll(); setActiveMatch((current) => matches.length ? (current + 1) % matches.length : 0) }}
            placeholder="在当前资料中搜索（按原文匹配）"
            aria-label="搜索原始资料"
          />
          <span className="procurement-reader-search-meta">{query.trim() ? `找到 ${matches.length} 处` : `证据定位：${locationLabel}`}</span>
          {query.trim() && <>
            <Button size="small" disabled={!matches.length} onClick={() => { resetReaderScroll(); setActiveMatch((current) => (current - 1 + matches.length) % matches.length) }}>上一处</Button>
            <Button size="small" disabled={!matches.length} onClick={() => { resetReaderScroll(); setActiveMatch((current) => (current + 1) % matches.length) }}>下一处</Button>
          </>}
          <Button size="small" type={readingMode === 'reading' ? 'primary' : 'default'} onClick={() => { resetReaderScroll(); setReadingMode('reading') }}>阅读文本</Button>
          <Button size="small" type={readingMode === 'raw' ? 'primary' : 'default'} onClick={() => { resetReaderScroll(); setReadingMode('raw') }}>原始文本</Button>
          {externalUrl && <a className="procurement-reader-external" href={externalUrl} target="_blank" rel="noreferrer"><LinkOutlined /> 查看来源</a>}
        </div>

        {loading && <div className="procurement-reader-loading"><Spin size="small" /><Text type="secondary">正在读取保存的快照…</Text></div>}
        {Boolean(error) && !loading && <Alert type="error" showIcon message="原始资料读取失败" description={errorText} action={<Button type="link" size="small" icon={<ReloadOutlined />} onClick={retry}>重试</Button>} />}
        {!loading && !error && <>
          <div className="procurement-reader-location"><Text type="secondary">{activeSource.page != null && activeSource.page > 0 ? `第 ${activeSource.page} 页 · ` : ''}{locationLabel}</Text>{isTruncated && <Text type="secondary">当前显示 {boundedWindowStart + 1}–{windowEnd} / {viewText.length} 字</Text>}</div>
          <div ref={textRef} onScroll={() => { readerScrollTopRef.current = textRef.current?.scrollTop ?? null }} className={`procurement-reader-text procurement-reader-text-${readingMode}`} role="document">
            {renderText()}
          </div>
          {isTruncated && <div className="procurement-reader-pagination">
            <Button size="small" disabled={boundedWindowStart <= 0} onClick={() => { resetReaderScroll(); setWindowStart(Math.max(0, boundedWindowStart - READER_WINDOW_CHARS)) }}>上一段</Button>
            <Button size="small" disabled={windowEnd >= viewText.length} onClick={() => { resetReaderScroll(); setWindowStart(Math.min(viewText.length - READER_WINDOW_CHARS, boundedWindowStart + READER_WINDOW_CHARS)) }}>下一段</Button>
            <Text type="secondary">长文窗口可继续翻阅，搜索和证据定位覆盖完整快照</Text>
          </div>}
          {activeRecord && <details className="procurement-reader-metadata">
            <summary>来源记录元数据（可选）</summary>
            <div className="procurement-reader-metadata-grid">
              <div><Text type="secondary">记录标题</Text><div>{activeRecord.title || `记录 #${activeRecord.id}`}</div></div>
              <div><Text type="secondary">解析状态</Text><div>{activeRecord.parse_status || '已接入'}</div></div>
            </div>
            <Collapse ghost items={[
              { key: 'facts', label: '原始抽取字段', children: <StructuredFields value={activeRecord.facts} emptyText="暂无原始抽取字段" /> },
              { key: 'inferences', label: '原始推测与待核实线索', children: <StructuredFields value={activeRecord.inferences} emptyText="暂无原始推测" tone="warning" /> },
            ]} />
          </details>}
        </>}
      </>}
    </Drawer>
  )
}, areReaderPropsEqual)
