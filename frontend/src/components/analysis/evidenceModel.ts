import type { Evidence, ProjectDetail } from '../../types'

const normalize = (text: string) => text.normalize('NFKC').replace(/\s+/g, '').toLowerCase()
const eventPatterns = [
  { key: 'submission', pattern: /投标(?:文件)?(?:递交)?截止|递交(?:截止|时间)|报价截止|响应(?:文件)?截止|提交(?:截止|时间)/ },
  { key: 'opening', pattern: /开标/ },
  { key: 'sale', pattern: /发售|文件获取|文件领取|文件下载|获取(?:招标|采购)文件/ },
  { key: 'registration', pattern: /报名/ },
  { key: 'clarification', pattern: /答疑|澄清|提问/ },
]
function events(text: string) {
  const value = normalize(text)
  if (/业绩|履约/.test(value) && /截止(?:日期|时间|期)?(?:前|之前)/.test(value) && !/截止(?:时间|日期)为|递交时间/.test(value)) return []
  return eventPatterns.filter(event => event.pattern.test(value)).map(event => event.key)
}

function dateValues(text: string): string[] {
  return [...normalize(text).matchAll(/(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})日?(?:t?(\d{1,2})[:：时](\d{2}))?/g)]
    .map(match => `${match[1]}-${Number(match[2])}-${Number(match[3])}${match[4] ? ` ${Number(match[4])}:${match[5]}` : ''}`)
}

function matchesClaim(claim: string, quote: string): boolean {
  const value = normalize(quote), judgment = normalize(claim)
  const lot = judgment.match(/\d+(?:包|标段)/)?.[0], quoteLot = value.match(/\d+(?:包|标段)/)?.[0]
  if (lot && quoteLot && lot !== quoteLot) return false
  const quantities = (text: string) => [...text.matchAll(/(\d+(?:\.\d+)?)(万元|元|日历天|天|项|台|套|件|%)/g)]
  const claimQuantities = quantities(judgment), quoteQuantities = quantities(value)
  if (claimQuantities.some(wanted => quoteQuantities.some(actual => actual[2] === wanted[2]) && !quoteQuantities.some(actual => actual[2] === wanted[2] && actual[1] === wanted[1]))) return false
  const bounds = (text: string) => [...text.matchAll(/(不低于|不小于|不少于|至少|不高于|不大于|不超过|至多|低于|小于|高于|大于|超过)(\d+(?:\.\d+)?)(万元|元|日历天|天|项|台|套|件|%)/g)]
  const boundType = (value: string) => /不低于|不小于|不少于|至少/.test(value) ? 'min' : /不高于|不大于|不超过|至多/.test(value) ? 'max' : /低于|小于/.test(value) ? 'less' : 'greater'
  if (bounds(judgment).some(wanted => quoteQuantities.some(actual => actual[1] === wanted[2] && actual[2] === wanted[3]) && !bounds(value).some(actual => actual[2] === wanted[2] && actual[3] === wanted[3] && boundType(actual[1]) === boundType(wanted[1])))) return false
  for (const subject of ['联合体', '代理商', '制造商']) {
    const policy = (text: string) => text.match(new RegExp(`(不接受|不允许|不得|禁止|允许|接受|支持)[^，。；]{0,4}${subject}`))?.[1]
    const claimPolicy = policy(judgment), quotePolicy = policy(value)
    if (claimPolicy && quotePolicy && /不|禁止/.test(claimPolicy) !== /不|禁止/.test(quotePolicy)) return false
  }
  const claimEvents = events(claim), quoteEvents = events(quote)
  if (claimEvents.length && !claimEvents.some(event => quoteEvents.includes(event))) return false
  const claimDates = dateValues(claim), quoteDates = dateValues(quote)
  if (claimDates.length) {
    if (!quoteDates.length && (claimEvents.length || !/业绩|资质|资格/.test(judgment))) return false
    if (!quoteDates.every(date => claimDates.some(wanted => wanted === date || (!wanted.includes(' ') && date.startsWith(wanted))))) return false
    // Dates alone cannot identify which procurement milestone the quotation describes.
    if (claimEvents.length) return true
  }
  const terms = /业绩|制造商|代理商|授权|资质|资格|供货|运输|安装|调试|技术服务|破碎机|交货|交付|合同|付款|支付|注册|购买|下载|招标文件|技术附件|验收|规格|数量|型号|参数|联系人/g
  const meaningful = [...new Set(judgment.match(terms) ?? [])]
  if (meaningful.filter(term => value.includes(term)).length >= 2) return true
  const plain = (text: string) => normalize(text).replace(/[\p{P}\p{S}]/gu, '')
  const left = plain(claim), right = plain(quote)
  if (Math.min(left.length, right.length) >= 6 && (left.includes(right) || right.includes(left))) return true
  // Unclassified facts need a substantial literal match, not merely a common word.
  for (let i = 0; i <= left.length - 8; i++) if (right.includes(left.slice(i, i + 8))) return true
  return false
}

/** Retain contiguous quotations only. Never join separate spans to manufacture support. */
export function selectDirectEvidence(claim: string, evidence: Evidence[]): Evidence[] {
  const selected: Evidence[] = []
  for (const item of evidence) {
    if (!item.quote?.trim()) continue
    if ('source_ref' in item && item.relation !== 'same_procurement') continue
    const fragments = item.quote.split(/(?<=[。！？；;])/u).filter(part => part.trim())
    for (const quote of fragments) {
      if (!matchesClaim(claim, quote)) continue
      const candidate = { ...item, quote }
      if (!selected.some(other => evidenceSourceKey(other) === evidenceSourceKey(candidate) && normalize(other.quote) === normalize(quote))) selected.push(candidate)
    }
  }
  // A combined milestone assertion needs direct support for every named event.
  if (events(claim).some(event => !selected.some(item => events(item.quote).includes(event)))) return []
  return selected
}

export function evidenceSourceKey(evidence: Evidence): string {
  if ('source_ref' in evidence) return `research:${evidence.source_ref}:${evidence.source_version}`
  return evidence.document_id != null
    ? `document:${evidence.record_id}:${evidence.document_id}:${evidence.document_sha256 || evidence.snapshot_url || ''}`
    : `announcement:${evidence.record_id}`
}

export interface EvidenceGroup {
  key: string
  title: string
  href: string
  evidence: Evidence[]
  text?: string
  completeText?: boolean
}

export function attachmentVersion(evidence: Evidence, projectId: number): string | null {
  if ('source_ref' in evidence || evidence.document_id == null) return null
  if (evidence.document_sha256?.trim()) return evidence.document_sha256.trim()
  if (!evidence.snapshot_url?.startsWith('/api/')) return null
  try {
    const url = new URL(evidence.snapshot_url, 'http://localhost')
    const expected = `/api/projects/${projectId}/documents/${encodeURIComponent(String(evidence.document_id))}/snapshot`
    const hash = url.searchParams.get('sha256')
    return url.pathname === expected && hash && /^[a-f0-9]{64}$/i.test(hash) ? hash : null
  } catch { return null }
}

function safeHref(value: string): string { return /^(https?:\/\/|\/api\/)/i.test(value) ? value : '' }

export function groupEvidence(evidence: Evidence[], detail: ProjectDetail): EvidenceGroup[] {
  const groups = new Map<string, EvidenceGroup>()
  for (const item of evidence) {
    const version = attachmentVersion(item, detail.id)
    const key = !('source_ref' in item) && item.document_id != null && version ? `document:${item.record_id}:${item.document_id}:${version}` : evidenceSourceKey(item)
    let group = groups.get(key)
    if (!group) {
      if ('source_ref' in item) {
        group = { key, title: item.title || '采购资料', href: safeHref(item.source_url), evidence: [] }
      } else {
        const record = detail.records.find(record => record.id === item.record_id)
        const document = detail.procurement?.documents.find(document => String(document.id) === String(item.document_id) && document.record_id === item.record_id)
        if (item.document_id == null && !record) continue
        group = { key, title: item.document_id != null ? item.document_name || document?.name || '采购附件' : '采购公告',
          href: safeHref((item.document_id != null ? item.source_url || document?.url : record?.original_url || record?.canonical_url) || ''),
          evidence: [], text: item.document_id == null ? record?.body_text : undefined }
      }
      groups.set(key, group)
    }
    // Identical and contained fragments from one source do not need separate headings or quotes.
    const value = normalize(item.quote)
    if (group.evidence.some(other => normalize(other.quote).includes(value))) continue
    group.evidence = group.evidence.filter(other => !value.includes(normalize(other.quote)))
    group.evidence.push(item)
  }
  return [...groups.values()]
}

/** Whitespace changes from PDF/OCR are allowed; non-contiguous AI paraphrases are not. */
export function quoteInSource(quote: string, source: string): boolean {
  return Boolean(quote.trim()) && normalize(source).includes(normalize(quote))
}

export function quoteExcerpt(quote: string, claim: string, limit = 180): { text: string; before: boolean; after: boolean } {
  const value = quote.replace(/\s+/g, ' ').trim()
  if (value.length <= limit) return { text: value, before: false, after: false }
  const required = eventPatterns.filter(event => event.pattern.test(claim))
  const eventMatch = required.map(event => value.search(event.pattern)).filter(index => index >= 0).sort((a, b) => a - b)[0]
  const amounts = claim.match(/\d+(?:\.\d+)?(?:万元|日历天|天|台|套|件|项)/g) ?? []
  const amountMatch = amounts.map(amount => value.indexOf(amount)).find(index => index >= 0)
  const match = eventMatch ?? amountMatch ?? 0
  const preceding = Math.max(value.lastIndexOf('。', match - 1), value.lastIndexOf('；', match - 1), value.lastIndexOf('，', match - 1))
  const start = Math.max(0, match - 40, preceding + 1)
  const following = value.slice(match).search(/[。；，]/)
  const end = Math.min(start + limit, following >= 0 ? match + following + 1 : value.length)
  return { text: value.slice(start, end), before: start > 0, after: end < value.length }
}
