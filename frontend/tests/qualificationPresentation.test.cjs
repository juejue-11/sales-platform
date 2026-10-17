const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { loadTypeScript } = require('./loadTypeScript.cjs')
const { splitQualificationText, qualificationTitle, qualificationEmphasis, qualificationSections } = loadTypeScript(path.join(__dirname, '../src/components/procurement/qualificationPresentation.ts'))

test('generated section IDs still expose complete qualification clauses instead of a short excerpt', () => {
  const sections = [{ id: 'section-1', title: '采购方与项目概况', requirements: [] }, { id: 'section-3', title: '投标人资格要求', requirements: [{ text: '首台（套）装备例外及法律限制均须保留。' }] }, { id: 'qualification', title: '谁能参加', requirements: [] }]
  assert.deepEqual(qualificationSections(sections), sections.slice(1))
})

test('all eleven original clauses and nested evidence exceptions survive the real notice split', () => {
  const notice = JSON.parse(fs.readFileSync(path.join(__dirname, '../../backend/tests/fixtures/notice_224.json'), 'utf8'))
  const text = notice.body_text.split('3.投标人资格要求\n')[1].split('\n4.招标文件的获取')[0]
  const clauses = splitQualificationText(text)
  assert.equal(clauses.length, 11)
  assert.equal(clauses.join(''), text)
  assert.equal(qualificationTitle(clauses[0]), '主体资格')
  assert.equal(qualificationTitle(clauses[1]), '制造商及代理商要求')
  assert.equal(qualificationTitle(clauses[2]), '类似项目业绩')
  assert.ok(clauses[2].includes('90万元'))
  assert.ok(clauses[2].includes('省级及以上重大技术装备推广应用目录'))
  assert.ok(clauses[2].includes('授权代理商应提供制造商业绩证明'))
  assert.equal(qualificationTitle(clauses[10]), '联合体参与要求')
})

test('numbered paragraphs keep subordinate proof notes inside their parent clause', () => {
  for (const text of [
    '1. 主体资格：须具备法人资格。\n2. 业绩要求：至少1项（注：1.提供合同。2.允许首台套例外）。\n3. 不接受联合体。',
    '（一）须具备法人资格。\n（二）提供合同（1）合同盖章；（2）签署日期。\n（三）不接受联合体。',
    '一、须具备法人资格。二、须提供营业执照。三、不接受联合体。',
  ]) {
    const clauses = splitQualificationText(text)
    assert.equal(clauses.length, 3)
    assert.equal(clauses.join(''), text)
  }
})

test('unstructured text and dates are preserved without speculative sentence splitting', () => {
  const text = '提供业绩证明，时间为2021.1.1以来。授权代理商须提供制造商材料；首台（套）装备可例外。'
  assert.deepEqual(splitQualificationText(text), [text])
  assert.equal(qualificationTitle('其他条件待核实。'), '其他资格条件')
})

test('legacy excerpts with a heading touching the first clause still split all requirements', () => {
  const text = '投标人资格要求3.1投标人须具有独立承担民事责任的能力。 3.2投标人应为制造商或授权代理商。 3.3近五年至少1项90万元业绩（注：1.须提供合同。2.首台套可例外）。'
  const clauses = splitQualificationText(text)
  assert.equal(clauses.length, 3)
  assert.equal(clauses.join(''), text)
})

test('amounts, years and counts receive local emphasis without changing the text', () => {
  const text = '近五年须提供至少 1 项合同金额不低于 90 万元的业绩。'
  const parts = text.split(qualificationEmphasis)
  assert.equal(parts.join(''), text)
  assert.deepEqual(parts.filter((_, index) => index % 2), ['近五年', '至少 1 项', '不低于 90 万元'])
})
