import type { ProcurementReadingSection } from '../../types'

export function qualificationSections(sections: ProcurementReadingSection[]) {
  // AI readings may use generated IDs such as section-3; the business title
  // still identifies the qualification section without falling back to an excerpt.
  return sections.filter(section => section.id === 'qualification' || /资格|资质|准入/.test(section.title))
}

// Split only explicit peer clauses. Keep every character, including numbered
// evidence notes and exceptions inside parentheses, in its original order.
export function splitQualificationText(text: string): string[] {
  const markers = [...text.matchAll(/(?:^|(?<=[\s。；;])|(?<=资格要求)|(?<=资格条件))(?:(\d+)\.(\d+)(?![\d.])|([一二三四五六七八九十百\d]+)[、．.](?!\d)|([（(])([一二三四五六七八九十百\d]+)[）)])/g)]
  let depth = 0
  let cursor = 0
  const peers = markers.filter(marker => {
    for (; cursor < marker.index; cursor++) {
      if ('（('.includes(text[cursor])) depth++
      if ('）)'.includes(text[cursor])) depth = Math.max(0, depth - 1)
    }
    return depth === 0
  })
  const first = peers[0]
  const sameLevel = peers.filter(marker => first?.[1] ? marker[1] === first[1] : first?.[3] ? Boolean(marker[3]) : Boolean(marker[4]) && /\d/.test(marker[5]) === /\d/.test(first?.[5] ?? ''))
  if (sameLevel.length < 2) return [text]
  const starts = [0, ...sameLevel.slice(1).map(marker => marker.index)]
  return starts.map((start, index) => text.slice(start, starts[index + 1] ?? text.length))
}

export function qualificationTitle(text: string): string {
  const content = text.trim().replace(/^(?:\d+\.\d+|[一二三四五六七八九十百\d]+[、．.]|[（(][一二三四五六七八九十百\d]+[）)])\s*/, '')
  const heading = content.match(/^([^：:\n。；]{2,22})[：:]/)?.[1]
  if (heading) return heading
  const lead = content.split(/[。；]/)[0]
  const titles: [RegExp, string][] = [
    [/独立承担民事责任|独立法人|法人资格/, '主体资格'],
    [/业绩|类似项目|类似合同/, '类似项目业绩'],
    [/制造商|生产厂家|代理商|授权代理/, '制造商及代理商要求'],
    [/信用中国|失信被执行人|税收违法/, '信用及税收记录'],
    [/国家企业信用|严重违法失信/, '企业信用记录'],
    [/商业道德|商业信誉/, '商业信誉要求'],
    [/暂停|取消投标资格/, '投标资格限制'],
    [/停产停业|许可证|吊销执照/, '经营及许可状态'],
    [/清算|破产|履约能力/, '持续履约能力'],
    [/黑名单/, '供应商黑名单限制'],
    [/联合体/, '联合体参与要求'],
    [/资格后审|资格预审/, '资格审查方式'],
    [/资质|资格证|认证|证书/, '资质及认证要求'],
    [/证明材料|证明文件|营业执照/, '证明材料要求'],
  ]
  return titles.find(([pattern]) => pattern.test(lead))?.[1] || '其他资格条件'
}

export const qualificationEmphasis = /(独立承担民事责任|制造商或授权代理商|不接受联合体投标|(?:近\s*)?[一二三四五六七八九十百\d]+\s*年(?:以内|以来|内)?|(?:至少\s*|不少于\s*|不低于\s*)?[￥¥]?\d[\d,.]*\s*(?:亿元|万元|元|项|份|套)|(?:至少\s*|不少于\s*)[一二三四五六七八九十百]+\s*(?:项|份|套))/g
