const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
function load(relative, requireModule) {
  const source = fs.readFileSync(path.join(__dirname, '../src/components/procurement', relative), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const adapter = { exports: {} }
  new Function('exports', 'module', 'require', compiled)(adapter.exports, adapter, requireModule)
  return adapter.exports
}
const reading = load('reading.ts')
const { readingKeyFacts, defaultReadingSection } = load('readingSummary.ts', name => name === './reading' ? reading : { formatDate: value => value })
const clause = (text, overrides = {}) => ({ id: text, text, sources: [{ source_kind: 'announcement', record_id: 1, quote: text }], ...overrides })
const section = (id, title, ...requirements) => ({ id, title, requirements })
const facts = sections => Object.fromEntries(readingKeyFacts(null, sections).map(item => [item.label, item.value]))

test('default category matches business title even when IDs and ordering vary', () => {
  const overview = section('section-8', '采购方与项目概况', clause('采购人为某公司'))
  assert.equal(defaultReadingSection([section('first', '其他要求', clause('其他要求')), overview]), overview)
  assert.equal(defaultReadingSection([section('empty', '采购方与项目概况'), overview]), overview)
  const reversed = section('section-9', '项目概况与采购方', clause('项目概况'))
  assert.equal(defaultReadingSection([section('first', '其他', clause('条款')), reversed]), reversed)
  assert.equal(defaultReadingSection([]), undefined)
})

test('summary shows the current package and directly supported amounts and deadline', () => {
  const value = facts([
    section('scope', '标包', clause('本项目划分01包和02包。'), clause('本次招标内容为02包。')),
    section('deposit', '保证金', clause('02包投标保证金金额为人民币肆万元整（￥40,000.00元）。')),
    section('submit', '递交', clause('递交时间为2026年10月23日13:30(北京时间)之前。')),
  ])
  assert.equal(value['标包'], '02包')
  assert.equal(value['提交截止'], '2026年10月23日13:30')
  assert.equal(value['保证金'], '02包 · ￥40,000.00元')
})

test('historical performance dates never become submission deadlines', () => {
  const value = facts([section('qualification', '资格', clause('投标人近三年（2023年1月1日～投标截止期前）无违约。'), clause('投标人提供2021年1月1日以来的业绩。'))])
  assert.equal(value['提交截止'], '待核对')
})

test('multiple deadlines and deposits remain scoped to their clauses without a guessed total', () => {
  const value = facts([section('lots', '标包',
    clause('01包投标截止时间：2026-10-20 10:00。'), clause('02包投标截止时间：2026-10-21 10:00。'),
    clause('01包保证金4万元；02包保证金6万元。'))])
  assert.equal(value['提交截止'], '多个时间，查看条款')
  assert.equal(value['保证金'], '查看保证金条款')
  assert.ok(!value['保证金'].includes('10万元'))
})

test('a percentage or budget reference never becomes a fixed deposit amount', () => {
  assert.equal(facts([section('deposit', '保证金', clause('投标保证金按预算50万元的2%收取。'))])['保证金'], '查看保证金条款')
})

test('uncertain and unsourced values stay out of the key facts', () => {
  for (const overrides of [{ sources: [] }, { status: 'uncertain' }, { status: 'conflict' }, { status: 'missing' }]) {
    const value = facts([section('x', '其他', clause('本次招标内容为99包。保证金1万元。投标截止时间：2026-10-20 10:00。', overrides))])
    assert.equal(value['标包'], '以采购条款为准')
    assert.equal(value['提交截止'], '待核对')
    assert.equal(value['保证金'], '以采购条款为准')
  }
})

test('stale reading retains the review cue instead of asserting a current deadline or amount', () => {
  const values = readingKeyFacts({ stale: true }, [section('deposit', '保证金', clause('保证金4万元。'))])
  assert.equal(values.find(item => item.label === '保证金').value, '待复核')
  assert.equal(values.find(item => item.label === '提交截止').value, '待复核')
})
