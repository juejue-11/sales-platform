const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScript } = require('./loadTypeScript.cjs')
const { buildDecisionView, sameTask } = loadTypeScript(path.join(__dirname, '../src/components/analysis/decisionView.ts'))

const evidence = [{ record_id: 1, quote: '设备采购，交付期60日。' }]
function project(overrides = {}) {
  return { id: 1, gate_status: 'candidate', ai_status: 'succeeded', ai_decision: 'recommend', ai_summary: '明确设备采购，技术条件仍待核对。', judgment: { id: 1, decision: 'recommend', stale: false, summary: '明确设备采购，技术条件仍待核对。', reasons: [{ text: '设备采购，交付期60日。', evidence }], unknowns: [], next_actions: [] }, ...overrides }
}
function report(overrides = {}) { return { version: 1, sections: [], opportunities: [], gaps: [], ...overrides } }

test('market recommendation does not claim that company capabilities were matched', () => {
  const view = buildDecisionView(project())
  assert.equal(view.recommendation, '建议跟进')
  assert.equal(view.match, 'not_evaluated')
})

test('old report never becomes the active decision during failure, rerun, or invalid project status', () => {
  for (const ai_status of ['failed', 'stale', 'running', 'queued', 'skipped', 'idle', 'not_configured']) {
    const view = buildDecisionView(project({ ai_status }))
    assert.equal(view.recommendation, '待评估', ai_status)
    assert.equal(view.summary, '', ai_status)
    assert.equal(view.facts.length + view.risks.length + view.actions.length, 0, ai_status)
  }
  for (const gate_status of ['excluded', 'expired', 'closed', 'duplicate']) assert.equal(buildDecisionView(project({ gate_status })).recommendation, '待评估')
})

test('structured report is canonical, excluding flattened duplicates and peripheral opportunities', () => {
  const data = project()
  data.judgment.report = report({
    gaps: [{ id: 'tech', description: '02包设备型号和技术参数未公开', impact: '无法确认代理品牌能否满足技术要求', next_action: '购买招标文件并核对技术规格', status: 'not_read' }, { id: 'other', description: '01包资格条件未公开', impact: '可能遗漏另一设备包的机会', next_action: '检索01包公告', status: 'not_read' }],
    opportunities: [{ kind: 'explicit_demand', description: '02包采购设备。', evidence, uncertainties: ['设备型号、参数尚未公开'], next_actions: ['按公告要求购买招标文件，获取02包技术规格与供货清单'] }, { kind: 'potential_opportunity', uncertainties: ['01包资格条件未公开'], next_actions: ['检索01包公告'] }],
  })
  data.judgment.unknowns = ['不应再次读取的预算缺口']
  data.judgment.next_actions = ['不应再次读取的跟进建议']
  const view = buildDecisionView(data)
  assert.equal(view.risks.length, 1)
  assert.equal(view.risks[0].title, '技术与供货要求待核实')
  assert.equal(view.actions.length, 1)
  assert.ok(!JSON.stringify(view).includes('01包'))
  assert.ok(!JSON.stringify(view).includes('不应再次读取'))
})

test('different lots and distinct deadline events stay separate and high impacts come first', () => {
  const data = project()
  data.judgment.report = report({ gaps: [
    { description: '报价付款条件未明确', status: 'not_stated' },
    { description: '01包技术参数待核实', status: 'not_read' },
    { description: '02包技术参数待核实', status: 'not_read' },
    { description: '答疑截止时间未说明', status: 'not_stated' },
    { description: '投标递交时间未确认', status: 'not_stated' },
    { description: '资质要求不适用', status: 'not_applicable' },
  ] })
  const view = buildDecisionView(data)
  assert.equal(view.risks.length, 5)
  assert.deepEqual(view.risks.map(item => item.priority), [0, 0, 0, 1, 1])
  assert.equal(sameTask('核实01包设备技术参数', '核实02包设备技术参数'), false)
  assert.equal(sameTask('确认投标截止2026-10-23', '确认投标截止2026-10-24'), false)
})

test('legacy fields remain usable without a report and technical execution trivia is filtered out', () => {
  const data = project()
  data.judgment.unknowns = ['设备型号和规格未公开', '模型版本号缺失', '设备型号和规格未公开']
  data.judgment.next_actions = ['联系采购方确认规格', '联系采购方确认规格']
  const view = buildDecisionView(data)
  assert.equal(view.risks.length, 1)
  assert.equal(view.actions.length, 1)
  assert.equal(view.facts.length, 1)
  assert.deepEqual(view.facts[0].evidence, evidence)
})

test('facts are capped at three and long summaries stay bounded', () => {
  const data = project()
  data.judgment.summary = '项目采购设备。'.repeat(100)
  data.judgment.reasons = Array.from({ length: 7 }, (_, i) => ({ text: `项目第${i}批采购${i}件设备`, evidence }))
  const view = buildDecisionView(data)
  assert.ok(view.summary.length <= 150)
  assert.ok((view.summary.match(/。/g) || []).length <= 2)
  assert.ok(view.facts.length <= 3)
})

test('core judgment retains the AI qualification instead of filling the card with a repeated project title', () => {
  const data = project()
  data.judgment.summary = `${'采购项目背景'.repeat(30)}。设备参数未公开，需购买招标文件。采购需求明确，建议重点跟进。`
  const view = buildDecisionView(data)
  assert.ok(view.summary.includes('建议重点跟进'))
  assert.ok(view.summary.includes('设备参数未公开'))
  assert.ok(view.summary.length <= 120)
})

test('a deadline fact keeps its own citations instead of inheriting the entire timeline paragraph', () => {
  const data = project()
  data.judgment.report = report({ sections: [{ id: 'timeline', title: '时间与流程', blocks: [{ type: 'paragraph', text: '文件发售时间2026-10-13至2026-10-19；投标截止与开标时间均为2026-10-23 13:30，通过采购平台开标。', evidence: [
    { record_id: 1, quote: '2026年\n10月13日9:00 - 2026年10月19日16:00' },
    { record_id: 1, quote: '递交时间：\n2026年10月23日13:30之前。' },
    { record_id: 1, quote: '开标时间：\n2026年10月23日13:30。' },
  ] }] }] })
  const fact = buildDecisionView(data).facts[0]
  assert.equal(fact.text, '投标截止与开标时间均为2026-10-23 13:30')
  assert.deepEqual(fact.evidence.map(item => item.quote), ['递交时间：\n2026年10月23日13:30之前。', '开标时间：\n2026年10月23日13:30。'])
})
