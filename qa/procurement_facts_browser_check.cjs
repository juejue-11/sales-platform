// Mock API acceptance for the structured procurement-facts desk.
//
// Run the Vite preview separately and pass its URL as the first argument:
//   node qa/procurement_facts_browser_check.cjs http://127.0.0.1:4173 [output]
//
// The API is completely mocked below.  This check deliberately exercises the
// browser workflow (source drawers, search, tabs, responsive layout and
// stale-request handling) instead of checking source files or static markup.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))

const base = (process.argv[2] || 'http://127.0.0.1:4173').replace(/\/$/, '')
const output = path.resolve(process.argv[3] || path.join(__dirname, '../../tmp/procurement-facts-browser-qa'))
fs.mkdirSync(output, { recursive: true })

const originalUrl = 'https://example.test/notices/PROC-166'
const archiveUrl = '/api/records/166/archive'
const bodyScopeQuote = '采购范围：井下压裂泵 2 套，矿用电缆 100 米。'
const bodyDeliveryQuote = '交货期：合同签订后30个日历天。'
const bodyLocationQuote = '交货地点：甲方指定仓库。'
const fragmentedBody = ['项目名称', '：', '10月西钢', '团块破碎机', '1.3 代理', '机构:', '鞍钢招标公司', '交货期', ':', '2027-02-26'].join('\n')
const attachmentDeliveryQuote = '附件第2页：交货期为合同签订后45个日历天。'
const attachmentScopeQuote = '附件第2页：货物名称井下压裂泵；数量3套。'
const snapshotUniqueText = '附件快照唯一正文：合同签订后45个日历天。'
const longTailMarker = 'TK(240)[A]'
const longTailQuote = `后段来源引文 ${longTailMarker} ${'长引文片段，用于验证超过四百二十字符的证据定位仍可访问。'.repeat(80)}结束。`
const longBody = `${'长公告前段资料，用于验证大于十万字符的正文窗口。\n'.repeat(5000)}${longTailQuote}`
const longRecordId = 168
const longOriginalUrl = 'https://example.test/notices/PROC-168'
const longSource = { source_kind: 'announcement', record_id: longRecordId, quote: longTailQuote }
assert.ok(longBody.length > 100_000, `long fixture body must exceed 100k characters: ${longBody.length}`)
assert.ok(longTailQuote.length > 420, `long fixture quote must exceed 420 characters: ${longTailQuote.length}`)

// The body is intentionally shorter than the old 300-character disclosure
// limit, while containing many blank/node-like lines.  A preview must still
// be bounded and the reader must normalize this text for reading.
const shortBody = [
  '采购公告：井下压裂泵 2 套。', '', '', '', '', '', '', '', '', '',
  bodyScopeQuote, '', '', '', '', '', '', '', '', '', '',
  bodyDeliveryQuote, '', '', '', '', '', '', '', '', '', '',
  bodyLocationQuote, '', '', '', '', '', '', '', '', '', '',
  fragmentedBody, '', '',
  '采购部联系方式：采购部 010-12345678。',
].join('\n')
assert.ok(shortBody.length < 300, `fixture body must stay below 300 characters: ${shortBody.length}`)
assert.ok((shortBody.match(/\n/g) || []).length >= 30, 'fixture body must contain many line breaks')

const bodySource = {
  source_kind: 'announcement',
  record_id: 166,
  quote: bodyScopeQuote,
}
const bodyDeliverySource = {
  source_kind: 'announcement',
  record_id: 166,
  quote: bodyDeliveryQuote,
}
const attachmentDeliverySource = {
  source_kind: 'attachment',
  record_id: 166,
  document_id: 'doc-quote-166',
  page: 2,
  quote: attachmentDeliveryQuote,
  sha256: 'sha-166-v1',
}
const attachmentScopeSource = {
  source_kind: 'attachment',
  record_id: 166,
  document_id: 'doc-quote-166',
  page: 2,
  quote: attachmentScopeQuote,
  sha256: 'sha-166-v1',
}

function judgment() {
  return {
    id: 1661,
    created_at: '2026-10-13T08:00:00+08:00',
    model: 'mock-browser',
    input_fingerprint: 'mock-browser',
    stale: false,
    decision: 'verify',
    summary: '采购事实存在一项交付时间冲突，需先核对附件。',
    demand_summary: '井下压裂泵与矿用电缆。',
    reasons: [],
    unknowns: ['附件中的交付时间需要核对。'],
    next_actions: ['核对技术附件第2页。'],
    options: [],
    report: null,
  }
}

function documents() {
  return [
    {
      id: 'doc-quote-166', record_id: 166, name: '技术附件.pdf', url: 'https://example.test/files/tech-166.pdf',
      status: 'parsed', pages: 3, sha256: 'sha-166-v1', fetched_at: '2026-10-13T07:00:00+08:00',
      error: null, mime_type: 'application/pdf', byte_size: 2048,
    },
    {
      id: 'doc-restricted-166', record_id: 166, name: '投标文件.zip', url: 'https://example.test/files/restricted-166.zip',
      status: 'auth_required', pages: null, sha256: null, fetched_at: null, error: 'auth_required',
      mime_type: 'application/zip', byte_size: null,
    },
    {
      id: 'doc-failed-166', record_id: 166, name: '补充报价.xlsx', url: 'https://example.test/files/failed-166.xlsx',
      status: 'failed', pages: null, sha256: null, fetched_at: null, error: 'fetch_failed',
      mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', byte_size: null,
    },
  ]
}

function supplementalMaterialItems() {
  const common = {
    delivery_period: '合同签订后30个日历天',
    delivery_location: '甲方指定仓库',
    source_kind: 'announcement',
    source_document_id: '',
    record_id: 166,
    page: 0,
    quote: bodyScopeQuote,
    sources: [bodySource],
  }
  const normal = [
    { name: '标准阀件', specification: '1.1 型', material: '合金钢', quantity: '1.1', unit: '套', status: 'extracted' },
    { name: '标准阀件', specification: '2.2 型', material: '合金钢', quantity: '2.2', unit: '套', status: 'conflict', note: '交货时间与公告不一致。' },
    { name: '标准阀件', specification: '散装长度', material: '合金钢', quantity: '5', unit: '米', status: 'extracted' },
    { name: '标准阀件', specification: '规格未提供', material: '合金钢', quantity: '', unit: '套', status: 'uncertain', note: '数量未提供，待核对。' },
  ].map((item) => ({ ...common, ...item }))
  const bulk = Array.from({ length: 13 }, (_, index) => ({
    ...common,
    name: '批量阀件', material: '铸钢', specification: `批量规格${String(index + 1).padStart(2, '0')}`,
    quantity: String(index + 1), unit: '套', status: 'extracted',
  }))
  const extraGroups = Array.from({ length: 10 }, (_, index) => ({
    ...common,
    name: `分页物料-${String(index + 1).padStart(2, '0')}`, material: '铝合金', specification: `分页规格${index + 1}`,
    quantity: '1', unit: '件', status: 'extracted',
  }))
  return [...normal, ...bulk, ...extraGroups]
}

function procurement() {
  return {
    status: 'partial',
    scope_summary: '井下压裂泵与矿用电缆',
    delivery_period: '合同签订后30个日历天',
    delivery_location: '甲方指定仓库',
    missing_fields: [],
    updated_at: '2026-10-13T08:00:00+08:00',
    attachment_status: 'restricted',
    attachment_note: '公开附件部分受限：需注册并购买招标文件后获取。',
    facts: [
      {
        id: 'fact-scope-166', field: 'scope', value: '井下压裂泵 2 套', status: 'extracted',
        sources: [bodySource],
      },
      {
        // One field intentionally has both body and attachment sources.  The
        // UI must retain both citations while rendering one field value.
        id: 'fact-delivery-166', field: 'delivery_period', value: '合同签订后30个日历天', status: 'conflict',
        note: '附件第2页写45个日历天，与公告正文不一致。',
        sources: [bodyDeliverySource, attachmentDeliverySource],
      },
      {
        id: 'fact-location-166', field: 'delivery_location', value: '甲方指定仓库', status: 'uncertain',
        note: '公告未写明具体门牌，待核实。', sources: [{ ...bodySource, quote: bodyLocationQuote }],
      },
      {
        id: 'fact-qualification-166', field: 'qualification', value: '矿山设备生产资质', status: 'extracted',
        sources: [attachmentScopeSource],
      },
      {
        id: 'fact-contact-166', field: 'contact', value: '采购部 010-12345678', status: 'extracted',
        sources: [{ ...bodySource, quote: '采购部联系方式：采购部 010-12345678。' }],
      },
    ],
    items: [
      {
        name: '井下压裂泵', specification: '额定压力待核对', material: '', quantity: '2', unit: '套',
        delivery_period: '合同签订后30个日历天', delivery_location: '甲方指定仓库', source_kind: 'announcement',
        source_document_id: '', record_id: 166, page: 0, quote: bodyScopeQuote, status: 'extracted', sources: [bodySource],
      },
      {
        // Same material, a different sourced quantity.  This must remain a
        // visible conflict instead of becoming a silent 5 套 total.
        name: '井下压裂泵', specification: '额定压力待核对', material: '', quantity: '3', unit: '套',
        delivery_period: '合同签订后45个日历天', delivery_location: '甲方指定仓库', source_kind: 'attachment',
        source_document_id: 'doc-quote-166', record_id: 166, page: 2, quote: attachmentScopeQuote, status: 'conflict', sources: [attachmentScopeSource],
      },
      {
        name: '矿用电缆', specification: '截面待核对', material: '', quantity: '100', unit: '米',
        delivery_period: '合同签订后30个日历天', delivery_location: '甲方指定仓库', source_kind: 'attachment',
        source_document_id: 'doc-quote-166', record_id: 166, page: 2, quote: attachmentScopeQuote, status: 'extracted', sources: [attachmentScopeSource],
      },
      ...supplementalMaterialItems(),
    ],
    documents: documents(),
  }
}

function project166() {
  return {
    id: 166,
    title: '井下压裂泵采购公告', buyer: '示例矿业有限公司', equipment: ['井下压裂泵'], kind: 'tender', source_id: 'mock-browser', record_ids: [166],
    published_at: '2026-10-08T08:00:00+08:00', deadline_at: '2026-10-29T09:30:00+08:00', updated_at: '2026-10-13T08:00:00+08:00',
    gate_status: 'passed', gate_reasons: [], ai_status: 'succeeded', ai_decision: 'verify', ai_summary: '采购事实存在一项交付时间冲突，需先核对附件。', needs_review: false,
    decision: { status: 'new', owner: '', next_action: '', note: '', selected_option_id: null, judgment_id: null, updated_at: null },
    records: [{
      id: 166, title: '采购公告', body_text: shortBody, facts: {}, inferences: {}, original_url: originalUrl,
      canonical_url: 'https://example.test/canonical/PROC-166', archive_path: 'archive/166.html', parse_status: 'ok',
    }],
    judgment: judgment(), judgment_history: [], decision_history: [], related_projects: [], versions: [], research_sources: [], agent_run: null,
    briefing: { record_id: 166, scope_excerpt: bodyScopeQuote, delivery_excerpt: bodyDeliveryQuote, qualification_excerpt: '矿山设备生产资质', contact_excerpt: '采购部联系方式：采购部 010-12345678。', original_url: originalUrl },
    procurement: procurement(),
  }
}

function project167() {
  return {
    ...project166(),
    id: 167, title: '竞态切换项目', buyer: '另一采购方', record_ids: [167],
    records: [{ id: 167, title: '竞态项目公告', body_text: '项目 B 正文，供切换竞态使用。', facts: {}, inferences: {}, original_url: 'https://example.test/notices/PROC-167', canonical_url: null, parse_status: 'ok' }],
    briefing: { record_id: 167, scope_excerpt: '项目 B 正文。', delivery_excerpt: '', qualification_excerpt: '', contact_excerpt: '', original_url: 'https://example.test/notices/PROC-167' },
    procurement: {
      status: 'partial', scope_summary: '项目 B 正文。', items: [], delivery_period: '', delivery_location: '', missing_fields: [], documents: [],
      updated_at: '2026-10-13T08:00:00+08:00', attachment_status: 'none_public', attachment_note: '',
      facts: [{ id: 'fact-b', field: 'scope', value: '项目 B 正文', status: 'extracted', sources: [{ source_kind: 'announcement', record_id: 167, quote: '项目 B 正文。' }] }],
    },
  }
}

function project168() {
  const value = project166()
  value.id = longRecordId
  value.title = '超长正文采购公告'
  value.record_ids = [longRecordId]
  value.ai_status = 'running'
  value.records = [{
    id: longRecordId,
    title: '超长正文公告',
    body_text: longBody,
    facts: {},
    inferences: {},
    original_url: longOriginalUrl,
    canonical_url: null,
    parse_status: 'ok',
  }]
  value.briefing = {
    record_id: longRecordId,
    scope_excerpt: '超长正文采购范围。',
    delivery_excerpt: '',
    qualification_excerpt: '超长正文资格要求。',
    contact_excerpt: '',
    original_url: longOriginalUrl,
  }
  value.procurement = {
    ...value.procurement,
    items: [],
    documents: [],
    attachment_status: 'none_public',
    facts: value.procurement.facts.map((fact) => fact.field === 'qualification'
      ? { ...fact, value: '超长正文资格要求', sources: [longSource] }
      : { ...fact, sources: (fact.sources || []).map((source) => ({ ...source, record_id: longRecordId })) }),
  }
  return value
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function visibleCount(locator) {
  let count = 0
  for (let index = 0; index < await locator.count(); index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) count += 1
  }
  return count
}

async function currentReader(page) {
  const reader = page.locator('.procurement-source-reader:visible').last()
  await reader.waitFor({ state: 'visible' })
  return reader
}

async function closeReader(page) {
  const reader = page.locator('.procurement-source-reader:visible').last()
  if (await reader.count()) {
    const closeButton = reader.getByRole('button', { name: /关闭|close/i }).first()
    if (await closeButton.count()) await closeButton.click()
    else await page.keyboard.press('Escape')
    await reader.waitFor({ state: 'hidden' }).catch(() => undefined)
  }
}

async function openAttachmentFromDeliveryFact(page) {
  const field = page.locator('.procurement-fact-field').filter({ hasText: '交付时间' }).first()
  await field.getByRole('button', { name: /查看来源/ }).click()
  const reader = await currentReader(page)
  const chooser = reader.getByRole('combobox', { name: '选择来源' })
  // Ant Design's read-only combobox input sits underneath its selected-value
  // span; click the visible selector surface so the interaction opens it.
  await reader.locator('.ant-select-selector').click()
  // Select the attachment with the native keyboard path when the reader did
  // not already retain that source from an earlier open. The portalized
  // dropdown can be positioned outside the Drawer viewport in headless
  // Chromium, while ArrowDown/Enter still exercise Ant Design's real onChange.
  if (!(await reader.innerText()).includes('技术附件.pdf')) {
    await chooser.press('ArrowDown')
    await chooser.press('Enter')
  }
  return reader
}

async function assertNoOverflow(page, width) {
  const size = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }))
  assert.ok(size.document <= width + 1 && size.body <= width + 1, `horizontal page overflow at ${width}px: ${JSON.stringify(size)}`)
}

async function assertFactDesk(page) {
  assert.ok(await visibleCount(page.getByRole('heading', { name: '采购事实', exact: true })) >= 1, '采购事实 heading is missing')
  assert.equal(await page.getByRole('heading', { name: '原始资料', exact: true }).count(), 1, '原始资料 should have one heading')
  const bodyText = await page.locator('body').innerText()
  for (const oldSection of ['采购范围明细', '同类物料汇总', '已读取部分采购资料', '附件与来源']) {
    assert.equal(bodyText.includes(oldSection), false, `old duplicated section remains: ${oldSection}`)
  }
  assert.match(bodyText, /冲突/)
  assert.match(bodyText, /待核实/)
  assert.match(bodyText, /公开附件受限/)
  assert.match(bodyText, /需登录|下载失败|获取受限/)
  assert.equal(await page.getByText('合同签订后30个日历天', { exact: true }).count(), 1, 'body and attachment sources must not duplicate the same fact field')
}

async function assertMaterials(page) {
  const materials = page.locator('.procurement-material-section')
  await materials.getByRole('textbox', { name: '搜索物料明细' }).fill('矿用电缆')
  await materials.getByText('矿用电缆', { exact: true }).waitFor({ state: 'visible' })
  assert.equal(await materials.getByText('井下压裂泵', { exact: true }).count(), 0, 'material search must preserve filtering')

  await materials.getByRole('textbox', { name: '搜索物料明细' }).fill('井下压裂泵')
  const pumpGroups = materials.locator('.procurement-material-group').filter({ hasText: '井下压裂泵' })
  await pumpGroups.first().waitFor({ state: 'visible' })
  const pumpGroupText = (await pumpGroups.allInnerTexts()).join('\n')
  assert.match(pumpGroupText, /数量有冲突|冲突未合计|字段冲突/)
  assert.doesNotMatch(pumpGroupText, /5\s*套/, 'conflicting 2 套 and 3 套 rows must not become a silent 5 套 total')
  // The UI may group same-name rows before material is known, or keep each
  // conflicting source as its own group. Expand every matching group so the
  // acceptance remains about the preserved 2/3 values rather than a grouping
  // implementation detail.
  for (let index = 0; index < await pumpGroups.count(); index += 1) {
    const group = pumpGroups.nth(index)
    const details = group.locator('.procurement-material-details')
    if (await details.count() === 0 || !(await details.isVisible().catch(() => false))) {
      await group.getByRole('button', { name: /查看明细/ }).click()
      await details.waitFor({ state: 'visible' })
    }
  }
  const detailText = (await pumpGroups.allInnerTexts()).join('\n')
  assert.match(detailText, /数量\s*2\s*套/)
  assert.match(detailText, /数量\s*3\s*套/)

  const search = materials.getByRole('textbox', { name: '搜索物料明细' })
  await search.fill('标准阀件')
  const normalGroup = materials.locator('.procurement-material-group').filter({ hasText: '标准阀件' }).first()
  await normalGroup.waitFor({ state: 'visible' })
  const normalText = await normalGroup.innerText()
  assert.match(normalText, /3\.3\s*套/, 'same name/material quantities 1.1 and 2.2 must sum exactly')
  assert.match(normalText, /5\s*米/, 'different units must remain separately totaled')
  assert.match(normalText, /数量待核对/, 'empty quantity must remain explicitly pending')
  assert.match(normalText, /字段冲突|冲突/, 'delivery/status conflict must remain visible')
  assert.doesNotMatch(normalText, /数量冲突|数量有冲突/, 'delivery conflict must not be mislabeled as quantity conflict')
  await normalGroup.getByRole('button', { name: /查看明细/ }).click()
  const normalDetails = normalGroup.locator('.procurement-material-details')
  await normalDetails.waitFor({ state: 'visible' })
  assert.match(await normalDetails.innerText(), /数量\s*1\.1\s*套/)
  assert.match(await normalDetails.innerText(), /数量\s*2\.2\s*套/)

  await search.fill('')
  const groupPagination = materials.locator('.procurement-material-pagination')
  await groupPagination.waitFor({ state: 'visible' })
  assert.match(await groupPagination.innerText(), /1\s*[–-]\s*8\s*\/\s*\d+\s*组/)
  await groupPagination.locator('.ant-pagination-next').click()
  await materials.getByText('分页物料-10', { exact: true }).waitFor({ state: 'visible' })
  await search.fill('批量阀件')
  const bulkGroup = materials.locator('.procurement-material-group').filter({ hasText: '批量阀件' }).first()
  await bulkGroup.waitFor({ state: 'visible' })
  assert.equal(await materials.locator('.procurement-material-pagination').count(), 0, 'search should reduce groups without stale outer pagination')
  await bulkGroup.getByRole('button', { name: /查看明细/ }).click()
  const bulkDetails = bulkGroup.locator('.procurement-material-details')
  await bulkDetails.waitFor({ state: 'visible' })
  const detailPagination = bulkDetails.locator('.procurement-material-detail-pagination')
  await detailPagination.waitFor({ state: 'visible' })
  assert.match(await bulkDetails.innerText(), /批量规格01/)
  await detailPagination.locator('.ant-pagination-next').click()
  await bulkDetails.getByText('批量规格13', { exact: true }).waitFor({ state: 'visible' })
  assert.equal(await bulkDetails.getByText('批量规格01', { exact: true }).count(), 0, 'detail pagination must move to the next page')
  await bulkDetails.getByRole('button', { name: /查看来源/ }).click()
  const pagedSourceReader = await currentReader(page)
  await pagedSourceReader.locator('.procurement-reader-text').waitFor({ state: 'visible' })
  assert.ok((await pagedSourceReader.innerText()).includes(bodyScopeQuote), 'source button must remain usable from paginated details')
  await closeReader(page)

  await search.fill('')
  await materials.getByText('井下压裂泵', { exact: true }).first().waitFor({ state: 'visible' })
  assert.equal(await materials.getByText('分页物料-10', { exact: true }).count(), 0, 'search reset must return material groups to page one')
}

async function assertOriginalLinksAndPreview(page) {
  const original = page.locator('.procurement-original-materials')
  const preview = original.locator('.procurement-original-preview').first()
  await preview.waitFor({ state: 'visible' })
  const previewMetrics = await preview.evaluate((element) => {
    const style = getComputedStyle(element)
    const lineHeight = Number.parseFloat(style.lineHeight)
    return {
      height: element.getBoundingClientRect().height,
      clientHeight: element.clientHeight,
      lineHeight,
      lineClamp: style.webkitLineClamp,
    }
  })
  assert.equal(previewMetrics.lineClamp, '3', 'body preview must be limited to three lines')
  assert.ok(Number.isFinite(previewMetrics.lineHeight) && previewMetrics.clientHeight <= previewMetrics.lineHeight * 3 + 2,
    `body preview is not bounded to three lines: ${JSON.stringify(previewMetrics)}`)
  assert.ok((await preview.innerText()).includes('采购公告'), 'body preview should retain the first source line')

  assert.equal(await original.locator(`a[href="${originalUrl}"]`).count(), 1, 'original URL must remain available')
  assert.equal(await original.locator(`a[href="${archiveUrl}"]`).count(), 1, 'archive URL must remain available')
  assert.equal(await original.locator('a[href="https://example.test/files/tech-166.pdf"]').count(), 1, 'document source URL must remain available')

  const fullTextButton = original.getByRole('button', { name: /查看完整正文/ }).first()
  await fullTextButton.click()
  const reader = await currentReader(page)
  const readerText = reader.locator('.procurement-reader-text')
  await readerText.waitFor({ state: 'visible' })
  const normalizedText = await readerText.innerText()
  assert.ok(normalizedText.includes(bodyScopeQuote), 'full body reader should contain normalized source text')
  assert.doesNotMatch(normalizedText, /\n{5,}/, 'normalized body reader should not show a wall of blank lines')

  const search = reader.getByRole('textbox', { name: '搜索原始资料' })
  const normalizedFragment = '项目名称：10月西钢团块破碎机'
  assert.ok(normalizedText.includes(normalizedFragment), 'reading mode should join fragmented node newlines into a searchable phrase')
  await search.fill(normalizedFragment)
  await page.waitForFunction(() => document.querySelectorAll('mark[data-reader-match]').length >= 1)
  assert.ok(await reader.locator('mark[data-reader-match]').count() >= 1, 'fragmented project name should be searchable in reading mode')
  await reader.getByRole('button', { name: '原始文本', exact: true }).click()
  const rawText = await readerText.innerText()
  assert.ok(rawText.includes(['项目名称', '：', '10月西钢', '团块破碎机'].join('\n')), 'raw mode should preserve source node newlines')
  await reader.getByRole('button', { name: '阅读文本', exact: true }).click()
  await search.fill('')
  await search.fill('采购')
  await reader.getByText(/找到\s*[2-9]\d*?\s*处|找到\s*[2-9]\s*处/, { exact: false }).waitFor({ state: 'visible' })
  assert.ok(await reader.locator('mark[data-reader-match]').count() >= 2, 'keyword matches must be highlighted')
  const previous = reader.getByRole('button', { name: '上一处', exact: true })
  const next = reader.getByRole('button', { name: '下一处', exact: true })
  assert.equal(await previous.count(), 1)
  assert.equal(await next.count(), 1)
  await next.click()
  await previous.click()

  await search.fill('这个词在正文中不存在')
  const noMatchText = await reader.innerText()
  assert.match(noMatchText, /找到\s*0\s*处|未找到|没有匹配/, 'no-match feedback must be visible')
  assert.equal(await reader.locator('mark[data-reader-match]').count(), 0)
  await closeReader(page)
}

async function assertBodySourceQuote(page) {
  const scopeField = page.locator('.procurement-fact-field').filter({ hasText: '采购范围' }).first()
  const allClaims = scopeField.getByRole('button', { name: /查看全部说法/ }).first()
  if (await allClaims.count()) await allClaims.click()
  await scopeField.getByRole('button', { name: /查看来源/ }).first().click()
  const reader = await currentReader(page)
  await reader.locator('.procurement-reader-quote').getByText(bodyScopeQuote, { exact: false }).waitFor({ state: 'visible' })
  assert.ok((await reader.innerText()).includes(bodyScopeQuote), 'fact source should locate its announcement quote')
  await closeReader(page)
}

async function assertSnapshotWorkflow(page, snapshotCalls) {
  const firstReader = await openAttachmentFromDeliveryFact(page)
  await firstReader.locator('.procurement-reader-text').waitFor({ state: 'visible' })
  let readerText = await firstReader.innerText()
  assert.ok(readerText.includes('技术附件.pdf'), 'snapshot reader should show document name')
  assert.match(readerText, /第\s*2\s*页/)
  assert.ok(readerText.includes(attachmentDeliveryQuote), 'snapshot reader should show attachment quote')
  assert.ok(readerText.includes(snapshotUniqueText), 'snapshot text should be rendered')
  assert.equal(snapshotCalls.length, 1)
  assert.match(snapshotCalls[0].url, /doc-quote-166\/snapshot\?sha256=sha-166-v1/)
  await closeReader(page)

  // Reopen the same source.  The mocked second snapshot request fails and
  // must clear the previous text while presenting a readable error.
  const failedReader = await openAttachmentFromDeliveryFact(page)
  await failedReader.getByText('原始资料读取失败', { exact: true }).waitFor({ state: 'visible' })
  readerText = await failedReader.innerText()
  assert.ok(!readerText.includes(snapshotUniqueText), 'failed snapshot must not retain stale text')
  assert.equal(snapshotCalls.length, 2)
  await closeReader(page)

  // The third request is deliberately delayed.  Change projects while it is
  // in flight; the old response must not repopulate the new project drawer.
  await openAttachmentFromDeliveryFact(page)
  await page.waitForTimeout(80)
  assert.equal(snapshotCalls.length, 3)
  await page.goto(`${base}/projects/167?tab=procurement`)
  await page.getByRole('heading', { name: '采购事实', exact: true }).first().waitFor()
  await page.waitForTimeout(1400)
  assert.equal(await page.locator('.procurement-source-reader:visible').count(), 0, 'project change must close the old source reader')
  assert.doesNotMatch(await page.locator('body').innerText(), new RegExp(snapshotUniqueText), 'stale snapshot response must not appear after project change')
}

async function assertLongReaderAndPolling(page, projectReads) {
  await page.goto(`${base}/projects/${longRecordId}?tab=procurement`)
  await page.getByRole('heading', { name: '采购事实', exact: true }).first().waitFor()
  const qualification = page.locator('.procurement-fact-field').filter({ hasText: '资格要求' }).first()
  await qualification.getByRole('button', { name: /查看来源/ }).click()
  const reader = await currentReader(page)
  const readerText = reader.locator('.procurement-reader-text')
  await readerText.waitFor({ state: 'visible' })
  await page.waitForFunction((marker) => [...document.querySelectorAll('.procurement-reader-text')].some((element) => element.textContent?.includes(marker)), longTailMarker)
  const initialText = await readerText.innerText()
  assert.ok(initialText.includes(longTailMarker), 'source reader must reach a quote near the end of a >100k body')
  const expandQuote = reader.getByRole('button', { name: '展开引文', exact: true })
  if (await expandQuote.count()) await expandQuote.click()
  assert.ok(await reader.locator('.procurement-reader-quote').innerText().then((text) => text.includes(longTailQuote)), 'long source quote must remain visible as an anchor')
  assert.ok(await reader.locator('mark[data-reader-evidence="true"]').count() >= 1, 'long source quote must mark an evidence anchor in the reader')
  assert.match(await reader.locator('.procurement-reader-location').innerText(), /正文第\s*\d+\s*字附近/)

  const search = reader.getByRole('textbox', { name: '搜索原始资料' })
  await search.fill(longTailMarker)
  await reader.getByText('找到 1 处', { exact: true }).waitFor({ state: 'visible' })
  assert.equal(await reader.locator('mark[data-reader-match]').count(), 1, 'literal TK(240)[A] search must produce one match')
  await reader.getByRole('button', { name: '下一处', exact: true }).click()
  await reader.getByRole('button', { name: '上一处', exact: true }).click()
  assert.equal(await search.inputValue(), longTailMarker)

  await reader.getByRole('button', { name: '原始文本', exact: true }).click()
  const rawReaderText = reader.locator('.procurement-reader-text-raw')
  await rawReaderText.waitFor({ state: 'visible' })
  assert.ok((await rawReaderText.innerText()).includes(longTailMarker), 'raw mode must expose the full-text tail window')
  const previousSegment = reader.getByRole('button', { name: '上一段', exact: true })
  const nextSegment = reader.getByRole('button', { name: '下一段', exact: true })
  assert.equal(await previousSegment.count(), 1)
  assert.equal(await nextSegment.count(), 1)
  await search.fill('')
  const tailLocation = await reader.locator('.procurement-reader-location').innerText()
  await previousSegment.click()
  await page.waitForFunction((before) => [...document.querySelectorAll('.procurement-reader-location')].some((element) => element.textContent !== before), tailLocation)
  assert.ok(!(await rawReaderText.innerText()).includes(longTailMarker), 'previous raw window should move away from the tail')
  await nextSegment.click()
  await rawReaderText.getByText(longTailMarker, { exact: false }).waitFor({ state: 'visible' })
  assert.ok((await rawReaderText.innerText()).includes(longTailMarker), 'next raw window should return to the evidence tail')
  await search.fill(longTailMarker)
  await reader.getByText('找到 1 处', { exact: true }).waitFor({ state: 'visible' })
  const scrollBefore = await rawReaderText.evaluate((element) => {
    const max = Math.max(0, element.scrollHeight - element.clientHeight)
    element.scrollTop = Math.min(240, max)
    return { top: element.scrollTop, max, client: element.clientHeight }
  })
  assert.ok(scrollBefore.max > 0, 'long reader must provide a scrollable text surface')
  const queryBefore = await search.inputValue()
  await page.waitForTimeout(3600)
  assert.ok(projectReads() >= 2, `running project should be polled after 3 seconds; reads=${projectReads()}`)
  assert.equal(await search.inputValue(), queryBefore, 'polling/re-read must preserve Drawer search query')
  const scrollAfter = await rawReaderText.evaluate((element) => element.scrollTop)
  assert.ok(Math.abs(scrollAfter - scrollBefore.top) <= 2, `polling/re-read reset Drawer scroll: before=${scrollBefore.top}, after=${scrollAfter}`)
  await closeReader(page)
}

async function assertOtherTabs(page) {
  const expected = [
    ['AI 研判', /分析结论/],
    ['项目调查', /项目调查/],
    ['行动与决定', /可选行动与人工决定/],
    ['历史与关联', /历史、版本与关联/],
  ]
  for (const [tabName, heading] of expected) {
    const tab = page.getByRole('tab', { name: tabName, exact: true })
    await tab.click()
    await page.waitForFunction((name) => [...document.querySelectorAll('[role="tab"]')].some((element) => element.textContent?.includes(name) && element.getAttribute('aria-selected') === 'true'), tabName)
    assert.ok(await page.getByRole('tabpanel').getByRole('heading', { name: heading }).count() >= 1, `${tabName} tab should remain navigable`)
  }
  await page.getByRole('tab', { name: '采购信息', exact: true }).click()
}

;(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const consoleErrors = []
  const pageErrors = []
  const failedRequests = []
  const unexpectedApiRequests = []
  const snapshotCalls = []
  const expectedHttpErrors = []
  let project168Reads = 0

  page.on('console', (message) => {
    if (message.type() !== 'error') return
    // The second mocked snapshot deliberately returns 503. Chromium logs the
    // response as a resource error even though the UI handles it correctly.
    if (/\b503\b|Service Unavailable/i.test(message.text())) expectedHttpErrors.push(message.text())
    else consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('requestfailed', (request) => failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText || ''}`))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() !== 'GET') {
      unexpectedApiRequests.push(`${request.method()} ${url.pathname}`)
      await route.abort()
      return
    }
    if (url.pathname === '/api/projects/166') {
      await route.fulfill({ json: project166() })
      return
    }
    if (url.pathname === `/api/projects/${longRecordId}`) {
      project168Reads += 1
      await route.fulfill({ json: project168() })
      return
    }
    if (url.pathname === '/api/projects/167') {
      await route.fulfill({ json: project167() })
      return
    }
    if (url.pathname === '/api/projects/166/documents/doc-quote-166/snapshot') {
      const callNumber = snapshotCalls.length + 1
      snapshotCalls.push({ url: url.toString(), callNumber })
      if (callNumber === 2) {
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'mock snapshot unavailable' }) })
        return
      }
      if (callNumber === 3) await delay(1200)
      try {
        await route.fulfill({
          json: {
            id: 'doc-quote-166', record_id: 166, name: '技术附件.pdf', url: 'https://example.test/files/tech-166.pdf', status: 'parsed', pages: 3,
            sha256: 'sha-166-v1', fetched_at: '2026-10-13T07:00:00+08:00',
            text: `${attachmentDeliveryQuote}\n${snapshotUniqueText}\n${attachmentScopeQuote}`,
            page_texts: [{ page: 1, text: '技术附件首页。' }, { page: 2, text: `${attachmentDeliveryQuote}\n${snapshotUniqueText}\n${attachmentScopeQuote}` }],
          },
        })
      } catch {
        // Navigation can cancel the delayed request.  The assertion below is
        // about the UI ignoring the response, so a cancelled route is fine.
      }
      return
    }
    unexpectedApiRequests.push(`${request.method()} ${url.pathname}`)
    await route.abort()
  })

  try {
    await page.goto(`${base}/projects/166?tab=procurement`)
    await page.getByRole('heading', { name: '采购事实', exact: true }).first().waitFor()
    await assertFactDesk(page)
    await assertMaterials(page)
    await assertOriginalLinksAndPreview(page)
    await assertBodySourceQuote(page)
    await assertSnapshotWorkflow(page, snapshotCalls)
    await assertLongReaderAndPolling(page, () => project168Reads)

    // Return to the original mocked project after the race and verify that
    // each non-procurement panel is still reachable with the same payload.
    await page.goto(`${base}/projects/166?tab=procurement`)
    await page.getByRole('heading', { name: '采购事实', exact: true }).first().waitFor()
    await assertOtherTabs(page)

    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 })
      await page.goto(`${base}/projects/166?tab=procurement`)
      await page.getByRole('heading', { name: '采购事实', exact: true }).first().waitFor()
      await assertNoOverflow(page, width)
      await page.screenshot({ path: path.join(output, `procurement-facts-${width}.png`), fullPage: true })
    }

    assert.deepEqual(consoleErrors, [], `browser console errors: ${consoleErrors.join('\n')}`)
    assert.deepEqual(pageErrors, [], `browser page errors: ${pageErrors.join('\n')}`)
    assert.deepEqual(unexpectedApiRequests, [], `unexpected live API requests: ${unexpectedApiRequests.join('\n')}`)
    assert.deepEqual(failedRequests, [], `failed browser requests: ${failedRequests.join('\n')}`)
    console.log(JSON.stringify({
      passed: true,
      project: 166,
      widths: [1440, 390],
      factStatuses: ['extracted', 'uncertain', 'conflict'],
      sourceReader: { bodyQuote: true, keywordSearch: true, noMatchFeedback: true, snapshotCalls: snapshotCalls.length, raceGuard: true },
      materials: { grouping: true, search: true, quantityConflict: true, decimalUnits: true, groupPagination: true, detailPagination: true },
      longReader: { bodyLength: longBody.length, quoteLength: longTailQuote.length, literalMarker: longTailMarker, pollingReads: project168Reads },
      links: { original: originalUrl, archive: archiveUrl },
      screenshots: output,
    }))
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => undefined)
    console.error(JSON.stringify({
      url: page.url(), consoleErrors, pageErrors, failedRequests, unexpectedApiRequests, snapshotCalls,
      readers: await page.locator('.procurement-source-reader').evaluateAll((elements) => elements.map((element) => ({
        className: element.className,
        display: getComputedStyle(element).display,
        visibility: getComputedStyle(element).visibility,
        text: (element.textContent || '').slice(0, 500),
      }))).catch(() => []),
      body: (await page.locator('body').innerText().catch(() => '')).slice(0, 5000),
    }))
    throw error
  } finally {
    await browser.close()
  }
})().catch((error) => { console.error(error); process.exitCode = 1 })
