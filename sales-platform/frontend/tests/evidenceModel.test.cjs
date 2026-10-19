const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScript } = require('./loadTypeScript.cjs')
const { selectDirectEvidence, groupEvidence, quoteInSource, quoteExcerpt, attachmentVersion } = loadTypeScript(path.join(__dirname, '../src/components/analysis/evidenceModel.ts'))
const cite = (quote, extra = {}) => ({ record_id: 1, quote, ...extra })

test('submission, opening and file sale are distinct even when their dates happen to match', () => {
  const candidates = [cite('文件发售时间：2026年10月23日13:30。'), cite('递交时间：2026年\n10月23日13:30之前。'), cite('开标时间：2026年10月23日13:30。')]
  assert.deepEqual(selectDirectEvidence('投标截止时间为2026-10-23 13:30', candidates).map(item => item.quote), [candidates[1].quote])
  assert.deepEqual(selectDirectEvidence('开标时间为2026-10-23 13:30', candidates).map(item => item.quote), [candidates[2].quote])
  assert.deepEqual(selectDirectEvidence('文件发售时间为2026-10-23 13:30', candidates).map(item => item.quote), [candidates[0].quote])
})

test('unlabelled dates, incorrect dates or times and another lot are not direct support', () => {
  const claim = '02包投标截止时间为2026-10-23 13:30'
  for (const quote of ['2026年10月23日13:30。', '递交时间：2026年10月24日13:30。', '递交时间：2026年10月23日16:00。', '01包递交时间：2026年10月23日13:30。']) assert.equal(selectDirectEvidence(claim, [cite(quote)]).length, 0, quote)
})

test('a combined deadline/opening assertion is not substantiated by a deadline quote alone', () => {
  const claim = '投标截止与开标时间均为2026-10-23 13:30'
  const deadline = cite('递交时间：2026年10月23日13:30之前。')
  const opening = cite('开标时间：2026年10月23日13:30。')
  assert.equal(selectDirectEvidence(claim, [deadline]).length, 0)
  assert.equal(selectDirectEvidence(claim, [deadline, opening]).length, 2)
})

test('one multi-topic quote contributes only its contiguous supporting clause', () => {
  const quote = '发售时间：2026年10月13日至19日；递交时间：2026年10月23日13:30之前。开标时间：2026年10月24日13:30。'
  const selected = selectDirectEvidence('投标截止时间为2026-10-23 13:30', [cite(quote)])
  assert.deepEqual(selected.map(item => item.quote), ['递交时间：2026年10月23日13:30之前。'])
  assert.ok(quote.includes(selected[0].quote))
})

test('identical and contained excerpts merge under one source without mixing records or versions', () => {
  const detail = { records: [{ id: 1, body_text: '递交时间：2026年10月23日13:30之前。' }, { id: 2, body_text: '' }], procurement: { documents: [] } }
  const groups = groupEvidence([cite('递交时间：2026年10月23日13:30之前。'), cite('2026年10月23日13:30'), cite('递交时间：2026年10月23日13:30之前。'), cite('另一条', { record_id: 2 })], detail)
  assert.equal(groups.length, 2)
  assert.equal(groups[0].evidence.length, 1)
  const attachmentGroups = groupEvidence([cite('原文一', { document_id: 'd1', document_sha256: 'version1' }), cite('原文二', { document_id: 'd1', document_sha256: 'version2' })], detail)
  assert.equal(attachmentGroups.length, 2)
})

test('original provenance permits OCR whitespace but rejects stitched quotations', () => {
  const source = '递交时间：2026年\n10月23日13:30。文件售价800元。购买招标文件后办理CA。'
  assert.equal(quoteInSource('递交时间：2026年10月23日13:30。', source), true)
  assert.equal(quoteInSource('递交时间：2026年10月23日13:30。购买招标文件后办理CA。', source), false)
  assert.equal(quoteInSource('', source), false)
})

test('opposite participation restrictions and conflicting quantities are not supporting evidence', () => {
  assert.equal(selectDirectEvidence('本项目接受联合体参与供货投标。', [cite('本项目不接受联合体参与供货投标。')]).length, 0)
  assert.equal(selectDirectEvidence('供货业绩合同额不低于90万元。', [cite('供货业绩合同额不低于50万元。')]).length, 0)
  assert.equal(selectDirectEvidence('供货业绩合同额不低于90万元。', [cite('供货业绩合同额不高于90万元。')]).length, 0)
})

test('unrelated research cannot substantiate a current procurement fact', () => {
  const evidence = { source_ref: 's1', source_version: 'v1', title: '历史公告', relation: 'buyer_background', quote: '投标截止时间为2026-10-23 13:30。' }
  assert.equal(selectDirectEvidence('投标截止时间为2026-10-23 13:30', [evidence]).length, 0)
})

test('long quote preview remains one exact span and exposes expansion boundaries', () => {
  const quote = '项目背景。'.repeat(80) + '投标截止时间为2026年10月23日13:30。' + '文件说明。'.repeat(80)
  const excerpt = quoteExcerpt(quote, '投标截止时间为2026-10-23 13:30')
  assert.ok(quote.includes(excerpt.text))
  assert.ok(excerpt.text.includes('投标截止'))
  assert.equal(excerpt.before, true)
  assert.equal(excerpt.after, true)
  assert.ok(excerpt.text.length <= 240)
  const financialQuote = '证明材料说明，'.repeat(100) + '供货业绩合同额不低于90万元，' + '证明材料说明，'.repeat(100)
  assert.ok(quoteExcerpt(financialQuote, '供货业绩合同额不低于90万元').text.includes('90万元'))
})

test('legacy snapshot URLs can supply a version only for this exact project and attachment', () => {
  const hash = 'a'.repeat(64)
  const evidence = cite('采购原文', { document_id: 'doc-a', snapshot_url: `/api/projects/224/documents/doc-a/snapshot?sha256=${hash}` })
  assert.equal(attachmentVersion(evidence, 224), hash)
  assert.equal(attachmentVersion(evidence, 225), null)
  assert.equal(attachmentVersion({ ...evidence, document_id: 'doc-b' }, 224), null)
  assert.equal(attachmentVersion({ ...evidence, snapshot_url: '/api/projects/224/documents/doc-a/snapshot?sha256=' }, 224), null)
  assert.equal(attachmentVersion(cite('采购原文', { document_id: 'doc-a' }), 224), null)
})
