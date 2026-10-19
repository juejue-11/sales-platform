import type { ProcurementReading, ProcurementReadingRequirement, ProcurementReadingSection } from '../../types'
import { formatDate } from '../Common'
import { submissionMilestone } from './reading'

export interface ReadingKeyFact {
  label: string
  value: string
  sectionId?: string
  note?: string
}

export function defaultReadingSection(sections: ProcurementReadingSection[]): ProcurementReadingSection | undefined {
  return sections.find(section => section.requirements.length > 0 && /(?:采购方|采购人|招标人).*(?:概况|概览)|(?:概况|概览).*(?:采购方|采购人|招标人)/.test(section.title))
    ?? sections.find(section => section.requirements.length > 0 && (section.id === 'buyer' || /^(?:采购方|采购人|招标人|谁在采购)(?:信息|介绍|概况)?$/.test(section.title)))
    ?? sections.find(section => section.requirements.length > 0)
    ?? sections[0]
}

const datePattern = /\d{4}(?:年\s*\d{1,2}月\s*\d{1,2}日|[-/]\d{1,2}[-/]\d{1,2})(?:[ T]*\d{1,2}:\d{2}(?::\d{2})?)?/g
const amountPattern = /[￥¥]?\s*\d[\d,]*(?:\.\d+)?\s*(?:万元|元)/g
const lotsIn = (text: string) => [...new Set(text.match(/(?:第\s*)?\d+\s*(?:标包|包|标段)/g) ?? [])]
const supported = (requirement: ProcurementReadingRequirement) => requirement.sources?.length > 0 && !['uncertain', 'conflict', 'missing'].includes(requirement.status || '')
function deadlineDates(text: string): string[] {
  return text.split(/[。；;\n]/).flatMap(clause => {
    const marker = clause.match(/(?:投标(?:文件)?(?:递交)?截止(?:时间|日期)?|报价截止(?:时间|日期)?|提交(?:文件)?截止(?:时间|日期)?|递交截止(?:时间|日期)?|递交时间|提交时间)(?:及开标时间)?\s*(?:为|是|[:：])?\s*/)
    if (!marker || marker.index == null) return []
    const afterMarker = clause.slice(marker.index + marker[0].length).replace(/^[（(]北京时间[)）]\s*/, '')
    if (!new RegExp(`^${datePattern.source}`).test(afterMarker)) return []
    return afterMarker.match(datePattern) ?? []
  })
}

// A compact index into existing clauses, never a new procurement fact or total.
export function readingKeyFacts(reading: ProcurementReading | null | undefined, sections: ProcurementReadingSection[]): ReadingKeyFact[] {
  const clauses = sections.flatMap(section => section.requirements.filter(supported).map(requirement => ({ section, requirement })))
  const currentLot = clauses.find(({ requirement }) => /本次(?:招标|采购)(?:内容|范围)?(?:为|是|[:：])\s*\d+\s*包/.test(requirement.text))
  const explicitLots = [...new Set(clauses.flatMap(({ requirement }) => requirement.lot != null && requirement.lot !== '' ? [String(requirement.lot)] : []))]
  const lotClause = currentLot ?? clauses.find(({ requirement }) => /标包|标段|\d+包/.test(requirement.text))
  const lotValue = currentLot ? lotsIn(currentLot.requirement.text).join('、') : explicitLots.length ? explicitLots.join('、') : lotClause ? lotsIn(lotClause.requirement.text).join('、') : ''
  const deadlineClauses = clauses.filter(({ requirement }) => !/保证金/.test(requirement.text) && deadlineDates(requirement.text).length > 0)
  const dates = [...new Set(deadlineClauses.flatMap(({ requirement }) => deadlineDates(requirement.text)))]
  const submission = submissionMilestone(reading)
  const requiresReview = reading?.stale || ['pending', 'failed', 'not_configured'].includes(reading?.status || '')
  const deposits = clauses.filter(({ requirement }) => /保证金/.test(requirement.text) && (requirement.text.match(amountPattern)?.length ?? 0) > 0)
  const depositValues = [...new Set(deposits.map(({ requirement }) => {
    const lots = requirement.lot != null ? String(requirement.lot) : lotsIn(requirement.text).join('、')
    const amounts = [...new Set(requirement.text.match(amountPattern) ?? [])].map(value => value.trim()).join(' / ')
    return `${lots ? `${lots} · ` : ''}${amounts}`
  }))]
  const simpleDeposit = deposits.every(({ requirement }) => lotsIn(requirement.text).length <= 1 && (requirement.text.match(amountPattern)?.length ?? 0) === 1 && !/按|比例|预算|合同|成交|中标|履约|%|％/.test(requirement.text))
  return [
    { label: '标包', value: lotValue || '以采购条款为准', sectionId: lotClause?.section.id },
    { label: '提交截止', value: requiresReview ? '待复核' : submission?.at ? formatDate(submission.at, true) : dates.length === 1 ? dates[0] : dates.length > 1 ? '多个时间，查看条款' : '待核对', sectionId: deadlineClauses[0]?.section.id, note: !requiresReview && !submission && dates.length === 1 ? '条款摘录' : undefined },
    { label: '保证金', value: requiresReview ? '待复核' : depositValues.length === 1 && simpleDeposit ? depositValues[0] : depositValues.length > 0 ? '查看保证金条款' : '以采购条款为准', sectionId: deposits[0]?.section.id ?? sections.find(section => /保证金/.test(section.title))?.id },
  ]
}
