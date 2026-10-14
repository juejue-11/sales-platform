// Mock API acceptance for announcement sourced procurement facts and public
// attachment states. Start the frontend separately and pass its base URL.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))

const base = process.argv[2] || 'http://127.0.0.1:5173'
const output = path.resolve(process.argv[3] || path.join(__dirname, '../data/procurement-body-qa'))
fs.mkdirSync(output, { recursive: true })

const announcementItem = {
  name: '煤矿井下压裂泵',
  specification: '',
  material: '',
  quantity: '2',
  unit: '套',
  delivery_period: '合同签订之日起30个日历天',
  delivery_location: '甲方指定地点',
  source_kind: 'announcement',
  source_document_id: '',
  record_id: 166,
  page: 0,
  quote: '煤矿井下压裂泵2套。交货期：合同签订之日起30个日历天。交货地点：甲方指定地点。',
}

function procurementFor(scenario) {
  const baseData = {
    status: 'partial',
    scope_summary: '本项目为煤矿井下压裂泵采购项目，采购煤矿井下压裂泵2套。',
    items: [announcementItem],
    delivery_period: '合同签订之日起30个日历天',
    delivery_location: '甲方指定地点',
    missing_fields: [],
    documents: [],
    updated_at: '2026-10-12T08:00:00+08:00',
    attachment_status: 'none_public',
    attachment_note: null,
  }
  if (scenario === 'restricted') {
    return { ...baseData, attachment_status: 'restricted', attachment_note: '需登记并购买招标文件后获取附件。' }
  }
  if (scenario === 'announcement_spec_public') {
    return {
      ...baseData,
      items: [{ ...announcementItem, quote: '煤矿井下压裂泵2套，公告正文另列额定压力35MPa；API 尚未提取规格字段。' }],
      scope_summary: '本项目为煤矿井下压裂泵采购项目，煤矿井下压裂泵2套，公告正文另列额定压力35MPa。',
    }
  }
  if (scenario === 'failed') {
    return {
      ...baseData,
      status: 'unavailable',
      attachment_status: 'failed',
      documents: [{ id: 'doc-failed', record_id: 166, name: '招标文件.pdf', url: 'https://example.test/failed.pdf', status: 'failed', pages: null, sha256: null, fetched_at: null, error: 'fetch_failed', mime_type: 'application/pdf', byte_size: null }],
    }
  }
  if (scenario === 'none_public') {
    return { ...baseData, items: [], scope_summary: '', delivery_period: '', delivery_location: '', attachment_status: 'none_public' }
  }
  if (scenario === 'legacy') {
    return {
      ...baseData,
      status: 'complete',
      items: [{ ...announcementItem, source_kind: undefined, source_document_id: 'doc-legacy', page: 2, specification: '额定压力 35MPa' }],
      documents: [{ id: 'doc-legacy', record_id: 166, name: '采购清单.pdf', url: 'https://example.test/legacy.pdf', status: 'parsed', pages: 4, sha256: 'legacy', fetched_at: '2026-10-12T08:00:00+08:00', error: null, mime_type: 'application/pdf', byte_size: 100 }],
      attachment_status: undefined,
    }
  }
  return baseData
}

function projectFor(scenario) {
  return {
    id: 166,
    title: '煤矿井下压裂泵采购公告',
    buyer: '某矿业公司',
    equipment: [],
    kind: 'tender',
    source_id: 'mock',
    record_ids: [166],
    published_at: '2026-10-08T08:00:00+08:00',
    deadline_at: '2026-10-29T09:30:00+08:00',
    updated_at: '2026-10-12T08:00:00+08:00',
    gate_status: 'active',
    gate_reasons: [],
    ai_status: 'idle',
    ai_decision: null,
    ai_summary: null,
    needs_review: false,
    decision: { status: 'new', owner: '', next_action: '', note: '', selected_option_id: null, judgment_id: null, updated_at: null },
    records: [{ id: 166, title: '煤矿井下压裂泵采购公告', body_text: scenario === 'announcement_spec_public' ? '招标范围：煤矿井下压裂泵2套，额定压力35MPa。交货期：合同签订之日起30个日历天。交货地点：甲方指定地点。' : '招标范围：煤矿井下压裂泵2套。交货期：合同签订之日起30个日历天。交货地点：甲方指定地点。', facts: {}, inferences: {}, original_url: 'https://example.test/notice-166', canonical_url: null, parse_status: 'ok' }],
    judgment: null,
    judgment_history: [],
    decision_history: [],
    related_projects: [],
    versions: [],
    briefing: { record_id: 166, scope_excerpt: scenario === 'announcement_spec_public' ? '本项目为煤矿井下压裂泵采购项目，煤矿井下压裂泵2套，额定压力35MPa。' : '本项目为煤矿井下压裂泵采购项目，煤矿井下压裂泵2套。', delivery_excerpt: '交货期：合同签订之日起30个日历天；交货地点：甲方指定地点。', qualification_excerpt: '', contact_excerpt: '', original_url: 'https://example.test/notice-166' },
    procurement: procurementFor(scenario),
  }
}

async function checkWidth(page, width) {
  const size = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }))
  assert.ok(size.scroll <= width + 2, `Page overflow at ${width}px: ${JSON.stringify(size)}`)
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage()
  const errors = []
  let scenario = 'announcement'
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/api/projects/166', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projectFor(scenario)) })
  })

  try {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 })
      await page.goto(`${base}/projects/166`)
      await page.getByRole('heading', { name: '采购信息', exact: true }).waitFor()
      const section = page.locator('.detail-procurement')
      const text = await section.innerText()
      assert.ok(text.includes('煤矿井下压裂泵'))
      assert.ok(text.includes('2 套'))
      assert.ok(text.includes('30个日历天'))
      assert.ok(text.includes('甲方指定地点'))
      assert.ok(text.includes('详细规格待核对'))
      assert.ok(!text.includes('公告未公开，详见招标文件'))
      assert.equal(await section.locator('.detail-procurement-actions button').filter({ hasText: '更新公开资料' }).count(), 1)
      assert.equal(await page.getByText('点击“读取附件”，查看采购清单。', { exact: true }).count(), 0)
      assert.ok((await page.locator('.detail-meta-item').filter({ hasText: '设备线索' }).innerText()).includes('煤矿井下压裂泵'))
      await section.locator('button[aria-label*="展开煤矿井下压裂泵"]').click()
      const expanded = section.locator('.detail-procurement-expanded')
      assert.ok((await expanded.innerText()).includes('公告正文'))
      assert.equal(await expanded.locator('a[href*="#page="]').count(), 0)
      await checkWidth(page, width)
      const collapseButton = section.getByRole('button', { name: /收起煤矿井下压裂泵/ })
      assert.ok(await collapseButton.evaluate(el => {
        const rect = el.getBoundingClientRect()
        return rect.left >= 0 && rect.right <= window.innerWidth
      }), 'Group actions must remain visible when the detail table scrolls')
      await page.screenshot({ path: path.join(output, `announcement-viewport-${width}.png`) })
      await page.screenshot({ path: path.join(output, `announcement-${width}.png`), fullPage: true })
    }

    for (const nextScenario of ['announcement_spec_public', 'restricted', 'none_public', 'failed', 'legacy']) {
      scenario = nextScenario
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.goto(`${base}/projects/166`)
      await page.getByRole('heading', { name: '采购信息', exact: true }).waitFor()
      const section = page.locator('.detail-procurement')
      const text = await section.innerText()
      if (nextScenario === 'announcement_spec_public') {
        await section.locator('button[aria-label*="展开煤矿井下压裂泵"]').click()
        const expanded = section.locator('.detail-procurement-expanded')
        const expandedText = await expanded.innerText()
        assert.ok(expandedText.includes('额定压力35MPa'))
        assert.ok(expandedText.includes('详细规格待核对'))
        assert.ok(!expandedText.includes('公告未公开，详见招标文件'))
      } else if (nextScenario === 'restricted') {
        assert.ok(text.includes('公开附件受限'))
        assert.ok(text.includes('需登记并购买招标文件后获取附件'))
        assert.equal(await section.getByText('需登记并购买招标文件后获取附件。', { exact: true }).count(), 1)
        assert.ok(!text.includes('解析失败'))
      } else if (nextScenario === 'none_public') {
        assert.ok(text.includes('未发现公开附件'))
        assert.ok(!text.includes('解析失败'))
        assert.ok(!text.includes('点击“读取附件”'))
      } else if (nextScenario === 'failed') {
        assert.ok(text.includes('公开附件检查失败'))
        assert.ok(text.includes('附件下载失败'))
        assert.ok(text.includes('煤矿井下压裂泵'))
      } else {
        await section.locator('button[aria-label*="展开煤矿井下压裂泵"]').click()
        const expanded = section.locator('.detail-procurement-expanded')
        const expandedText = await expanded.innerText()
        assert.ok(expandedText.includes('采购清单.pdf · 第 2 页'))
        assert.ok(text.includes('已解析'))
        assert.ok((await expanded.locator('a[href*="#page=2"]').count()) > 0)
        assert.equal(await section.locator('.detail-procurement-actions button').filter({ hasText: '更新公开资料' }).count(), 1)
      }
      await checkWidth(page, 1440)
      await page.setViewportSize({ width: 390, height: 1000 })
      await checkWidth(page, 390)
    }
    assert.equal(errors.length, 0, errors.join('\n'))
    console.log(JSON.stringify({ passed: true, scenarios: ['announcement', 'announcement_spec_public', 'restricted', 'none_public', 'failed', 'legacy'], widths: [1440, 390], screenshots: output }))
  } finally {
    await browser.close()
  }
})().catch((error) => { console.error(error); process.exitCode = 1 })
