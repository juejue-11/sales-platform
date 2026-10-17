import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Alert, Button, Empty, Tag } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type {
  ProcurementData,
  ProcurementDocument,
  ProjectBriefing,
  ProcurementReading,
  ProcurementReadingRequirement,
  ProcurementReadingSection,
  ProjectRecord,
} from '../../types'
import { ReadingSourceTags, type OpenReadingSource } from './ReadingSourceTags'
import { ProcurementSourceReader, type ProcurementReaderSource } from './ProcurementSourceReader'
import { defaultReadingSection, readingKeyFacts } from './readingSummary'
import './ProcurementReading.css'

const SECTION_TITLES: Record<string, string> = {
  buyer: '采购方与项目概况',
  scope: '采购范围',
  delivery: '交付要求',
  qualification: '资格要求',
  schedule: '时间安排',
  materials: '费用与材料',
  participation: '参与方式',
  special: '特殊要求与待确认事项',
}

interface ProcurementReadingProps {
  projectId: number
  reading?: ProcurementReading | null
  legacyProcurement?: ProcurementData | null
  briefing?: ProjectBriefing | null
  records?: ProjectRecord[]
  onRefresh?: () => void
  refreshing?: boolean
  error?: unknown
  onOpenOperations?: () => void
  auxiliary?: ReactNode
}

function requirementStatusLabel(status: string): string {
  return ({ confirmed: '已确认', extracted: '已读取', uncertain: '待核对', conflict: '来源冲突', missing: '待补充' } as Record<string, string>)[status] ?? status
}

function requirementContent(requirement: ProcurementReadingRequirement) {
  const text = requirement.text?.trim() ? requirement.text : undefined
  const explanation = requirement.explanation?.trim() ? requirement.explanation : undefined
  // Keep older explanation-only readings accessible without presenting an
  // explanation as if it were the original requirement text.
  return { text: text || explanation || '要求正文待补充', explanation: text ? explanation : undefined, explanationOnly: !text && Boolean(explanation) }
}

function EmphasizedRequirement({ text }: { text: string }) {
  const parts = text.split(/(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(?:[ T]?\d{1,2}:\d{2})?|[￥¥]?\d[\d,.]*\s*(?:万元|元|％|%|日历天|工作日|台|套|吨|件|天)|不得|不接受|不允许|必须|须具备|截至|截止)/g)
  return <>{parts.map((part, index) => index % 2 ? <strong className="reading-semantic-emphasis" key={index}>{part}</strong> : part)}</>
}

// Older API responses use the same layout without inventing a completed reading.
function legacySections(procurement: ProcurementData | null | undefined, briefing: ProjectBriefing | null | undefined, records: ProjectRecord[]): ProcurementReadingSection[] {
  const sections: ProcurementReadingSection[] = []
  const add = (id: string, requirements: ProcurementReadingRequirement[]) => {
    if (requirements.length) sections.push({ id, title: SECTION_TITLES[id], requirements })
  }
  const excerpt = (id: string, text: string | null | undefined): ProcurementReadingRequirement[] => {
    if (!text?.trim()) return []
    const compact = (value: string) => value.replace(/\s+/g, '')
    const candidates = [...records].sort((a, b) => Number(b.id === briefing?.record_id) - Number(a.id === briefing?.record_id))
    const record = candidates.find(item => compact(item.body_text || '').includes(compact(text)))
    return [{ id, text, sources: record ? [{ source_kind: 'announcement', record_id: record.id, quote: text }] : [] }]
  }
  const field = (name: string, fallback: string | null | undefined, label?: string): ProcurementReadingRequirement[] => {
    const facts = (procurement?.facts ?? []).filter(fact => fact.field === name && fact.value?.trim())
    return facts.length ? facts.map(fact => ({ id: fact.id, text: label ? `${label}：${fact.value}` : fact.value, explanation: fact.note, status: fact.status, sources: fact.sources })) : excerpt(`legacy-${name}`, fallback)
  }
  add('scope', [
    ...field('scope', procurement?.scope_summary || briefing?.scope_excerpt),
    ...(procurement?.items ?? []).map((item, index): ProcurementReadingRequirement => ({
      id: `legacy-item-${index}`,
      text: [item.name, item.specification, item.material && `材质：${item.material}`, item.quantity != null && item.quantity !== '' && `数量：${item.quantity}${item.unit || ''}`, item.code && `编号：${item.code}`, item.standard && `标准：${item.standard}`, item.delivery_period && `交付时间：${item.delivery_period}`, item.delivery_location && `交付地点：${item.delivery_location}`, item.note].filter(Boolean).join('；'),
      status: item.status,
      sources: item.sources?.length ? item.sources : item.quote ? [{ source_kind: item.source_kind || (item.source_document_id != null ? 'attachment' : 'announcement'), record_id: item.record_id, ...(item.source_document_id != null ? { document_id: item.source_document_id } : {}), page: item.page, quote: item.quote }] : [],
    })),
  ])
  add('delivery', [
    ...field('delivery_period', procurement?.delivery_period || briefing?.delivery_excerpt, '交付时间'),
    ...field('delivery_location', procurement?.delivery_location, '交付地点'),
  ])
  add('qualification', field('qualification', briefing?.qualification_excerpt))
  add('participation', field('contact', briefing?.contact_excerpt))
  return sections
}

function SectionContent({ section, records, documents, onRead }: { section: ProcurementReadingSection; records: ProjectRecord[]; documents: ProcurementDocument[]; onRead: OpenReadingSource }) {
  return (
    <>
      <header className="reading-detail-header"><h2>{section.title}</h2><span>{section.requirements.length} 项要求</span></header>
      {section.requirements.length === 0 ? <p className="reading-category-empty">该分类暂无已读取要求，请查阅公告原文。</p> : (
        <div className="reading-requirements">
          {section.requirements.map((requirement, index) => {
            const content = requirementContent(requirement)
            return (
              <article className="reading-requirement" key={`${requirement.id}-${index}`} data-requirement-id={requirement.id}>
                <div className="reading-requirement-main">{content.explanationOnly && <span className="reading-explanation-note">说明 · 原文待补充</span>}<p className="reading-requirement-text"><EmphasizedRequirement text={content.text} /></p>{content.explanation && content.explanation !== content.text && <p className="reading-requirement-explanation">{content.explanation}</p>}</div>
                <div className="reading-requirement-meta">{requirement.lot != null && requirement.lot !== '' && <Tag className="reading-lot-tag">包 / 标段 {requirement.lot}</Tag>}{requirement.status && !['confirmed', 'extracted'].includes(requirement.status) && <Tag color={requirement.status === 'conflict' ? 'error' : ['uncertain', 'missing'].includes(requirement.status) ? 'warning' : 'default'}>{requirementStatusLabel(requirement.status)}</Tag>}<ReadingSourceTags compact sources={requirement.sources} records={records} documents={documents} onRead={onRead} context={section.title} /></div>
              </article>
            )
          })}
        </div>
      )}
    </>
  )
}

export function ProcurementReadingPanel({ projectId, reading, legacyProcurement, briefing, records = [], onRefresh, refreshing = false, error, onOpenOperations, auxiliary }: ProcurementReadingProps) {
  const [reader, setReader] = useState<{ sources: ProcurementReaderSource[]; title: string } | null>(null)
  const [selection, setSelection] = useState<{ projectId: number; index: number; id: string } | null>(null)
  const navigationRef = useRef<HTMLDivElement>(null)
  const detailRef = useRef<HTMLElement>(null)
  const instanceId = useId()
  const onRead: OpenReadingSource = (sources, sourceTitle) => setReader({ sources, title: sourceTitle })
  const sections = reading?.sections?.length ? reading.sections : legacySections(legacyProcurement, briefing, records)
  const selectedIndex = selection?.projectId === projectId && sections[selection.index]?.id === selection.id ? selection.index : sections.indexOf(defaultReadingSection(sections)!)
  const activeSection = sections[selectedIndex]
  const requirementCount = sections.reduce((total, section) => total + section.requirements.length, 0)
  const isFailure = reading?.status === 'failed' || reading?.status === 'not_configured'
  const documents = legacyProcurement?.documents ?? []
  const facts = readingKeyFacts(reading, sections)
  const selectSection = (index: number) => {
    setSelection({ projectId, index, id: sections[index].id })
    const top = detailRef.current?.getBoundingClientRect().top
    if (top != null && top < 112) window.scrollBy({ top: top - 112, behavior: 'instant' })
  }
  const navigateCategory = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = ['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : ['ArrowUp', 'ArrowLeft'].includes(event.key) ? -1 : 0
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : delta ? (index + delta + sections.length) % sections.length : null
    if (next == null) return
    event.preventDefault()
    selectSection(next)
    navigationRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus({ preventScroll: true })
    const button = navigationRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]
    if (button && navigationRef.current) {
      const nav = navigationRef.current
      nav.scrollTop = Math.max(0, button.offsetTop - nav.offsetTop - nav.clientHeight / 2)
      nav.scrollLeft = Math.max(0, button.offsetLeft - nav.offsetLeft - nav.clientWidth / 2)
    }
  }

  return (
    <div className="procurement-reading-panel" data-testid="procurement-reading">
      <header className="reading-toolbar">
        <dl className="reading-key-facts" aria-label="采购关键信息">{facts.map(fact => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.sectionId ? <button type="button" title="查看对应采购条款与来源" onClick={() => { const index = sections.findIndex(section => section.id === fact.sectionId); if (index >= 0) selectSection(index) }}>{fact.value}</button> : fact.value}{fact.note && <span className="reading-fact-note">{fact.note}</span>}</dd></div>)}</dl>
        {onRefresh && <Button type="text" size="small" className="reading-refresh" icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh}>更新资料</Button>}
      </header>

      {Boolean(error) && <Alert className="reading-error" type="error" showIcon message="采购资料更新失败" description={error instanceof Error ? error.message : '资料更新失败，请稍后重试。'} />}
      {isFailure && <Alert className="reading-processing-alert" type={reading?.status === 'not_configured' ? 'info' : 'warning'} showIcon message={reading?.status === 'not_configured' ? '采购要求尚未完成读取' : '资料读取未完成，请结合公告原文核对。'} action={onOpenOperations ? <Button type="link" onClick={onOpenOperations}>查看处理状态</Button> : undefined} />}
      {reading?.stale && <p className="reading-review-notice" role="status">资料已更新，当前说明待复核。</p>}

      <div className={`reading-workspace${auxiliary ? ' has-auxiliary' : ''}`}>
        <nav className="reading-navigation" aria-label="采购要求分类">
          <div className="reading-navigation-heading"><h2>采购要求</h2><span>共 {requirementCount} 项</span></div>
          <div className="reading-category-list" role="tablist" aria-label="采购要求业务分类" aria-orientation="vertical" ref={navigationRef}>{sections.map((section, index) => <button type="button" role="tab" key={`${section.id}-${index}`} id={`${instanceId}-category-${index}`} aria-controls={`${instanceId}-detail`} aria-selected={index === selectedIndex} tabIndex={index === selectedIndex ? 0 : -1} onClick={() => selectSection(index)} onKeyDown={event => navigateCategory(event, index)}><span>{section.title}</span><span className="reading-category-count">{section.requirements.length}</span></button>)}</div>
        </nav>
        <section className="reading-detail" ref={detailRef} role={activeSection ? 'tabpanel' : undefined} id={`${instanceId}-detail`} aria-labelledby={activeSection ? `${instanceId}-category-${selectedIndex}` : undefined} tabIndex={0} data-section={activeSection?.id}>
          {activeSection ? <SectionContent key={`${projectId}-${selectedIndex}`} section={activeSection} records={records} documents={documents} onRead={onRead} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="采购要求尚未形成，请查看公告原文。" />}
        </section>
        {auxiliary && <div className="reading-auxiliary">{auxiliary}</div>}
      </div>

      <ProcurementSourceReader projectId={projectId} records={records} documents={documents} sources={reader?.sources ?? []} open={Boolean(reader)} onClose={() => setReader(null)} title={reader?.title} />
    </div>
  )
}
