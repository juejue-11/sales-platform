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

function sectionBreaks(value: string): string {
  // This intentionally matches 3.1 / 3.1.1 style section numbers only.
  // A value such as 200.0 (万元) must remain a monetary amount.
  return value.replace(/(?<![\d.])(?=(?:[2-9]|[1-9]\d)\.\s*(?:[1-9]\d?)(?:\.\s*[1-9]\d?)*(?=[本投供资财业信能其招采项要不有中工地交质详施]))/g, '\n')
}

export function cleanScopeExcerpt(value: string | null | undefined, title?: string | null): string {
  const normalized = normalizeOpportunityText(value)
  if (!normalized) return ''
  const attachmentSentence = normalized.match(/本项目招标内容、范围及规模详见附件[^。！？]*[。！？]?/)?.[0]
  const cleanedTitle = cleanOpportunityTitle(title)
  const titleAppearsInScope = Boolean(cleanedTitle && normalized.includes(cleanedTitle))
  if (attachmentSentence && titleAppearsInScope && /招标失败/.test(normalized)) return attachmentSentence
  const withoutLabel = normalized.replace(/^(?:招标范围|项目概况与招标范围)\s*/, '')
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
  const highlights = parts.slice(0, count).join('\n')
  return parts.length > count ? `${highlights}\n其余条件见下方原文摘录` : highlights
}

export function isTodayShanghai(value: string | null | undefined): boolean {
  if (!value) return false
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return false
  const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
  return formatter.format(date) === formatter.format(new Date())
}
