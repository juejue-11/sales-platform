import type { Evidence, ProjectDetail } from '../../types'
import { selectDirectEvidence } from './evidenceModel'

export interface DecisionItem { text: string; evidence: Evidence[] }
export interface DecisionRisk extends DecisionItem {
  title: string
  impact: string
  priority: number
  action: string
}

const topics = [
  { key: 'qualification', pattern: /资质|资格|业绩|授权|代理品牌|制造商/, title: '参与资格待核实', priority: 0 },
  { key: 'deadline', pattern: /截止|窗口|报名时间|递交时间|投标时点/, title: '参与时间窗口待确认', priority: 0 },
  { key: 'technical', pattern: /规格|型号|参数|处理能力|供货范围|验收|备件|技术要求/, title: '技术与供货要求待核实', priority: 0 },
  { key: 'access', pattern: /标书费|支付方式|购买|注册|CA证书|招标文件|技术附件/, title: '文件获取条件待确认', priority: 1 },
  { key: 'delivery', pattern: /交货|交付|运输|安装|调试/, title: '交付能力待核实', priority: 1 },
  { key: 'commercial', pattern: /预算|报价|付款|回款|保证金|合同条款/, title: '商务条件待确认', priority: 1 },
  { key: 'contact', pattern: /联系人|联系方式/, title: '沟通渠道待确认', priority: 2 },
]

function clean(text: string): string {
  return text.replace(/\*\*|^\s*[-#•]+\s*/g, '').replace(/（(?:尚未读取|原文未说明|读取失败|当前日期未知)）/g, '').replace(/\s+/g, ' ').trim()
}

export function concise(text: string, limit = 100, sentences = 1): string {
  const value = clean(text)
  const parts = value.match(/[^。！？!?]+[。！？!?]?/g) ?? []
  const excerpt = parts.slice(0, sentences).join('')
  if (excerpt.length <= limit) return excerpt
  // Cut at a clause boundary when possible, never crop using CSS.
  const prefix = excerpt.slice(0, limit - 1)
  const boundary = Math.max(prefix.lastIndexOf('；'), prefix.lastIndexOf('，'))
  return `${prefix.slice(0, boundary > limit * 0.55 ? boundary : undefined)}…`
}

function coreJudgment(text: string): string {
  const parts = clean(text).match(/[^。！？!?]+[。！？!?]?/g) ?? []
  if (parts.length <= 2 && text.length <= 120) return clean(text)
  const first = parts.find(part => /建议|值得|谨慎|暂不|不宜/.test(part)) ?? parts[0]
  if (!first) return ''
  // Keep a qualification alongside the verdict rather than a long repeated project title.
  const second = parts.find(part => part !== first && /未|尚|但|缺乏|待核|待确认|需核/.test(part)) ?? parts.find(part => part !== first)
  return concise(first, 75) + (second ? concise(second, 45) : '')
}

const normalized = (text: string) => clean(text).replace(/[\p{P}\p{S}\s]/gu, '').toLowerCase()

function bigrams(text: string): Set<string> {
  const value = normalized(text)
  return new Set(Array.from({ length: Math.max(0, value.length - 1) }, (_, i) => value.slice(i, i + 2)))
}

/** Conservative near-duplicate check: shared subject alone is not enough. */
export function sameTask(a: string, b: string): boolean {
  const lotA = a.match(/\d+(?:包|标段)/)?.[0], lotB = b.match(/\d+(?:包|标段)/)?.[0]
  if (lotA && lotB && lotA !== lotB) return false
  const dates = (value: string) => value.match(/\d{4}[-年/]\d{1,2}[-月/]\d{1,2}/g)?.sort().join(',')
  const datesA = dates(a), datesB = dates(b)
  if (datesA && datesB && datesA !== datesB) return false
  const left = normalized(a), right = normalized(b)
  if (!left || !right) return false
  if (left === right || (Math.min(left.length, right.length) >= 10 && (left.includes(right) || right.includes(left)))) return true
  const aa = bigrams(a), bb = bigrams(b)
  const intersection = [...aa].filter(value => bb.has(value)).length
  return intersection / Math.max(1, Math.min(aa.size, bb.size)) >= 0.78
}

function uniqueEvidence(items: Evidence[]): Evidence[] {
  return items.filter((item, index) => items.findIndex(other => JSON.stringify(other) === JSON.stringify(item)) === index)
}

function subject(text: string): string {
  // Separate lots and deadlines so unrelated participation conditions are never merged.
  const lot = text.match(/(?:\d+|[一二三四五六七八九十]+)(?:包|标段)/)?.[0] ?? ''
  const deadline = text.match(/答疑|澄清|提问|开标|发售|递交|报名/)?.[0] ?? ''
  return `${lot}:${deadline}`
}

function sameSubject(a: string, b: string): boolean {
  const [lotA, eventA] = subject(a).split(':'), [lotB, eventB] = subject(b).split(':')
  return (!lotA || !lotB || lotA === lotB) && eventA === eventB
}

function topic(text: string) { return topics.find(item => item.pattern.test(text)) }
function actionKey(text: string): string | null {
  const context = subject(text).split(':')[0]
  if (!/截止|答疑|澄清|支付方式/.test(text) && /购买|获取|下载/.test(text) && /招标文件|技术规格|技术参数|技术文件|技术附件/.test(text)) return `documents:${context}`
  return null
}
const peripheral = /其他设备|其它设备|后续设备|潜在机会|另行参与|另一设备包|遗漏.*机会|后续采购|同项目后续/

export function buildDecisionView(detail: ProjectDetail) {
  const stale = Boolean(detail.judgment?.stale || detail.ai_status === 'stale')
  const valid = detail.ai_status === 'succeeded' && !stale && !['excluded', 'expired', 'closed', 'duplicate'].includes(detail.gate_status)
  const judgment = valid ? detail.judgment : null
  const report = judgment?.report
  const riskCandidates: DecisionRisk[] = []
  const addRisk = (text: string, impact = '', action = '', evidence: Evidence[] = []) => {
    if (!clean(text) || peripheral.test(`${text}${impact}`)) return
    const category = topic(text) ?? topic(impact)
    // Technical execution gaps and optional background research are not procurement risks.
    if (!category) return
    const existing = riskCandidates.find(item => sameTask(item.text, text) || (
      (topic(item.text) ?? topic(item.impact))?.key === category.key && sameSubject(item.text, text)
    ))
    if (existing) {
      existing.evidence = uniqueEvidence([...existing.evidence, ...evidence])
      if (!existing.impact) existing.impact = impact
      if (!existing.action) existing.action = action
      return
    }
    const priority = category.key === 'deadline' && /答疑|澄清|提问|开标/.test(text) && !/投标截止|递交|报名截止/.test(text) ? 1 : category.priority
    const title = category.key === 'deadline' && /答疑|澄清|提问/.test(text) ? '答疑截止时间待确认' : category.title
    riskCandidates.push({ text: clean(text), title, impact: clean(impact), action: clean(action), evidence, priority })
  }

  // Structured gaps take precedence over the legacy flattened copies.
  for (const gap of report?.gaps ?? []) {
    if (gap.status !== 'not_applicable') addRisk(gap.description, gap.impact, gap.next_action)
  }
  const explicit = report?.opportunities.filter(item => item.kind === 'explicit_demand') ?? []
  const peripheralUnknowns = [...(report?.opportunities.filter(item => item.kind === 'potential_opportunity').flatMap(item => item.uncertainties) ?? []), ...(report?.gaps.filter(gap => gap.status === 'not_applicable' || peripheral.test(`${gap.description}${gap.impact}`)).map(gap => gap.description) ?? [])]
  for (const item of explicit) for (const text of item.uncertainties) addRisk(text)
  for (const text of report ? [] : judgment?.unknowns ?? []) {
    if (!peripheralUnknowns.some(item => sameTask(item, text))) addRisk(text)
  }

  const facts: DecisionItem[] = []
  const addFact = (text: string, evidence: Evidence[]) => {
    const deadlineClause = text.split(/[。；]/).find(part => /投标.{0,8}截止|递交|开标时间/.test(part))
    if (deadlineClause) text = deadlineClause.split(/[，,]/)[0]
    const category = topic(text)
    if (!clean(text) || !evidence.length || facts.some(item => sameTask(item.text, text) || (category && topic(item.text)?.key === category.key))) return
    facts.push({ text: clean(text), evidence: selectDirectEvidence(text, evidence) })
  }
  const reading = detail.procurement_reading
  if (valid && reading && !reading.stale) {
    for (const milestone of reading.milestones.filter(item => item.kind === 'submission')) {
      if (!milestone.at) continue
      const evidence: Evidence[] = milestone.sources.map(source => ({ record_id: source.record_id, document_id: source.document_id, page: source.page, quote: source.quote, document_sha256: source.sha256 }))
      addFact(`${milestone.label}：${milestone.at.replace('T', ' ')}`, evidence)
    }
  }
  // Time windows and eligibility gates carry more decision value than project background.
  const sectionRank = (title: string) => /时间/.test(title) ? 0 : /条件|门槛|资格/.test(title) ? 1 : 2
  const sections = [...(report?.sections ?? [])].sort((a, b) => sectionRank(a.title) - sectionRank(b.title))
  for (const section of sections) {
    if (/风险|缺口|待核|建议|判断|推断|潜在/.test(section.title)) continue
    for (const block of section.blocks) {
      if (block.type === 'paragraph') addFact(block.text, block.evidence)
      else if (block.type === 'list') {
        const items = [...block.items].sort((a, b) => Number(!/万元|资质|证书/.test(a.text)) - Number(!/万元|资质|证书/.test(b.text)))
        for (const item of items.slice(0, 1)) addFact(item.text, item.evidence)
      }
      else for (const row of block.rows) addFact(row.cells.map((cell, i) => `${block.columns[i] ?? ''}：${cell}`).join('；'), row.evidence)
    }
  }
  for (const item of explicit) addFact(item.description || item.basis, item.evidence)
  if (!report) for (const reason of judgment?.reasons ?? []) addFact(reason.text, reason.evidence)

  const risks = riskCandidates.sort((a, b) => a.priority - b.priority)
  const actions: Array<{ text: string; priority: number }> = []
  const addAction = (text: string, priority: number) => {
    if (!clean(text) || peripheral.test(text)) return
    const key = actionKey(text)
    const existing = actions.find(item => {
      const otherKey = actionKey(item.text)
      return sameTask(item.text, text) || (key && otherKey && sameSubject(item.text, text))
    })
    if (existing) { existing.priority = Math.min(existing.priority, priority); return }
    actions.push({ text: clean(text), priority })
  }
  for (const risk of risks) if (risk.action) addAction(risk.action, risk.priority)
  const explicitActions = explicit.flatMap(item => item.next_actions)
  const peripheralActions = report?.opportunities.filter(item => item.kind === 'potential_opportunity').flatMap(item => item.next_actions) ?? []
  for (const text of report ? explicitActions : judgment?.next_actions ?? (valid ? detail.ai_next_actions : []) ?? []) {
    if (peripheralActions.some(item => sameTask(item, text))) continue
    const category = topic(text)
    addAction(text, /CA证书|办理CA|完成供应商注册/.test(text) ? -1 : category?.priority ?? 1)
  }

  const meta = valid ? judgment?.analysis_meta ?? detail.analysis_meta : null
  const hasResult = valid && Boolean(judgment || detail.ai_summary)
  const decision = !hasResult ? null : detail.ai_decision ?? judgment?.decision
  const labels = { recommend: '建议跟进', consider: '谨慎跟进', verify: '待评估', not_recommended: '暂不建议' }
  return {
    recommendation: decision ? labels[decision] : '待评估',
    tone: decision === 'recommend' ? 'positive' : decision === 'not_recommended' ? 'negative' : 'neutral',
    summary: coreJudgment(judgment?.summary || (valid ? detail.ai_summary : '') || ''),
    match: meta?.company_match ?? 'not_evaluated',
    stale, hasResult, facts: facts.slice(0, 3), risks,
    actions: actions.sort((a, b) => a.priority - b.priority),
  }
}
