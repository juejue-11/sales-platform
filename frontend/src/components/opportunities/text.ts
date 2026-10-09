const announcementSuffix = /(?:公开招标|邀请招标|招标公告|项目公告|采购公告|公告)+$/

export function normalizeOpportunityText(value: string | null | undefined): string {
  return value?.replace(/\s+/g, ' ').trim() ?? ''
}

/** Keep the buyer's project identity while dropping source page labels. */
export function cleanOpportunityTitle(value: string | null | undefined): string {
  const original = normalizeOpportunityText(value)
  if (!original) return ''
  let title = original.replace(/^【[^】]+】\s*/, '').trim()
  let previous = ''
  while (title && title !== previous && announcementSuffix.test(title)) {
    previous = title
    title = title.replace(announcementSuffix, '').trim()
  }
  return title || original
}

/** Keep a product or work subject visible when a source title has a long project prefix. */
export function compactOpportunityTitle(value: string | null | undefined): string {
  const title = cleanOpportunityTitle(value)
  if (!title) return ''
  const withoutCode = title.replace(/^[A-Z]{2,}(?:-[A-Z0-9]+){2,}\s*/i, '').trim()
  const afterProcurement = withoutCode.match(/(?:采购|生产设备采购)\s*[-—]\s*([^。]+)$/)?.[1]?.trim()
  const candidate = afterProcurement || withoutCode.match(/项目\s*([^。]{2,32})$/)?.[1]?.trim()
  if (!candidate || !/(?:设备|机|泵|车|库|材料|备件|电缆|工程|机器人|生产线|支柱|电机|清洗)/.test(candidate)) return withoutCode
  return candidate.replace(/(?:GHY|HYCG)[-_A-Z0-9]*$/i, '').trim() || withoutCode
}

function sectionBreaks(value: string): string {
  // This intentionally matches 3.1 / 3.1.1 style section numbers only.
  // A value such as 200.0 (万元) must remain a monetary amount.
  return value.replace(/(?<![\d.])(?=(?:[1-9]\d?)\.\s*(?:[1-9]\d?)(?:\.\s*[1-9]\d?)*(?=[本投供资财业信能其招采项要不有中工地交质详施]))/g, '\n')
}

export function cleanScopeExcerpt(value: string | null | undefined, title?: string | null): string {
  const normalized = normalizeOpportunityText(value)
  if (!normalized) return ''
  const attachmentSentence = normalized.match(/本项目招标内容、范围及规模详见附件[^。！？]*[。！？]?/)?.[0]
  const cleanedTitle = cleanOpportunityTitle(title)
  const titleAppearsInScope = Boolean(cleanedTitle && normalized.includes(cleanedTitle))
  if (attachmentSentence && titleAppearsInScope && /招标失败/.test(normalized)) return attachmentSentence
  const withoutLabel = normalized.replace(/^(?:招标范围|采购范围|项目概况与招标范围)\s*[:：]?\s*/, '')
  return sectionBreaks(withoutLabel.replace(/^2\.1\s*(?:项目名称|项目概况与招标范围)?\s*[:：]?\s*/, '').trim())
}

export function cleanQualificationExcerpt(value: string | null | undefined): string {
  const normalized = normalizeOpportunityText(value)
  if (!normalized) return ''
  const withoutLabel = normalized.replace(/^(?:三、)?(?:投标人资格要求|资格条件)\s*/, '')
  return sectionBreaks(withoutLabel)
}

export function qualificationHighlights(value: string | null | undefined, count = 3): string {
  const cleaned = cleanQualificationExcerpt(value)
  if (!cleaned) return ''
  const parts = cleaned.split('\n').filter(Boolean)
  const numbered = parts.filter((part) => /^\d+\.\d+/.test(part))
  const selected = (numbered.length ? numbered : parts).slice(0, count)
  const highlights = selected.map((part) => {
    const sentence = part.match(/^[^。；]+[。；]?/)?.[0] ?? part
    return sentence.length > 150 ? `${sentence.slice(0, 150)}…` : sentence
  }).join('\n')
  return `${highlights}\n完整条件请展开原文核对`
}

export function isTodayShanghai(value: string | null | undefined): boolean {
  if (!value) return false
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return false
  const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
  return formatter.format(date) === formatter.format(new Date())
}

/**
 * Return a short, source-backed sentence for a catalogue card.
 *
 * The feed is intentionally conservative: it prefers a clause containing a
 * quantity, specification, or delivery term and otherwise leaves the fact
 * undiscovered. It never turns a long qualification or contact block into a
 * clipped-looking recommendation.
 */
export function procurementTeaser(value: string | null | undefined, title?: string | null): string {
  const cleaned = cleanScopeExcerpt(value, title)
  if (!cleaned) return ''
  const tableCue = cleaned.match(/(?:本次采购共\s*\d+\s*个包段|设备名称及数量见下表|明细见[^。；]{0,8}需求一览表)/)?.[0]
  const clauses = cleaned
    .split(/(?<=[。！？；;,，])\s*|\n+/)
    .map((part) => part.replace(/^[\s:：,，。；;]+|[\s:：,，。；;]+$/g, '').trim())
    .filter((part) => part.length > 0)
    .filter((part) => !/^(?:招标方式|资金落实情况|招标失败转其他采购方式|本项目招标内容、范围及规模详见附件)/.test(part))

  const usableClauses = clauses.filter((part) => !/(?:设备名称及数量见下表|规格型号\s*单位\s*数量|包号\s*设备)/.test(part) && !/^\d+\.\d+\s*设备?$/.test(part))
  const specific = usableClauses.find((part) => /\d+(?:\.\d+)?\s*(?:台|套|根|项(?!目)|件|个|批|条|米|吨|组|标段|包)|额定负载|规格型号|型号/.test(part))
  const broad = usableClauses.find((part) => /采购|设备|工程|施工|备件|材料|电机|电缆|压裂|输送机|支柱|机器人|清洗机|钻孔|破碎机/.test(part))
  const timing = usableClauses.find((part) => /交货期|交期|工期|服务期/.test(part))
  const selected = specific ?? broad ?? tableCue ?? timing ?? clauses[0] ?? ''
  if (!selected) return ''
  const readable = selected.replace(/^(?:项目概况|采购范围|招标范围)\s*[:：]?\s*/, '').trim()
  if (readable.length <= 120) return readable
  const concise = readable.match(/[^。；;]{0,72}(?:\d+(?:\.\d+)?\s*(?:台|套|根|项(?!目)|件|个|批|条|米|吨|组|标段|包)|额定负载|规格型号|交货期|交期|工期|服务期)[^。；;]{0,46}/)?.[0]
  const result = concise?.trim() || readable.slice(0, 117).trimEnd()
  return result.length > 120 ? `${result.slice(0, 117).trimEnd()}…` : result
}

function sourceFact(value: string, pattern: RegExp, label: string, limit = 34): string {
  const match = value.match(pattern)
  if (!match) return ''
  const raw = match[0].replace(/^[\s:：,，。；;]+|[\s:：,，。；;]+$/g, '').trim()
  if (!raw) return ''
  const concise = raw.length > limit ? `${raw.slice(0, limit - 1).trimEnd()}…` : raw
  return `${label} ${concise.replace(/^(?:交货期|交期|工期|服务期|交货地点)\s*[:：]?\s*/, '')}`.trim()
}

/** Select at most two concise facts for a card, preserving the source wording. */
export function procurementFacts(
  scope: string | null | undefined,
  delivery: string | null | undefined,
  equipment: string[] = [],
): string[] {
  const scopeText = normalizeOpportunityText(scope)
  const deliveryText = normalizeOpportunityText(delivery)
  const facts: string[] = []
  const quantity = sourceFact(scopeText, /\d+(?:\.\d+)?\s*(?:台|套|根|项(?!目)|件|个|批|条|米|吨|组|标段|包)/, '数量')
  if (quantity) facts.push(quantity)

  const deliveryPattern = /(?:交货期|交期|工期|服务期)\s*(?:\([^)]*\))?\s*[:：]?\s*[^。；;\n]{1,42}/
  const deliveryFact = sourceFact(scopeText, deliveryPattern, '交付') || sourceFact(deliveryText, deliveryPattern, '交付')
  if (deliveryFact && facts.length < 2) facts.push(deliveryFact)

  if (facts.length < 2) {
    const hasTableHeader = /(?:设备名称及数量见下表|规格型号\s*单位\s*数量|包号\s*设备)/.test(scopeText)
    const specFact = !hasTableHeader && sourceFact(scopeText, /(?:额定负载\s*[:：]?\s*[^。；;\n]{1,32}|(?:规格型号|型号)\s*[:：]?\s*[A-Za-z0-9][^。；;\n]{1,32}|(?:功率|B\s*=|V\s*=|kW|MPa)\s*[:：=]?\s*[^。；;\n]{1,32})/, '规格')
    if (specFact) facts.push(specFact)
  }

  if (facts.length < 2 && equipment[0]) facts.push(`设备 ${equipment[0]}`)
  return facts.slice(0, 2)
}

/** True only for a future deadline within the requested number of days. */
export function isDeadlineWithinDays(value: string | null | undefined, days = 7): boolean {
  if (!value) return false
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return false
  const remaining = timestamp - Date.now()
  return remaining >= 0 && remaining <= days * 24 * 60 * 60 * 1000
}
