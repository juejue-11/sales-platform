// Read-only acceptance of the real project #166 after restarting the service.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))

;(async () => {
  const base = process.argv[2] || 'http://127.0.0.1:8766'
  const output = path.resolve(process.argv[3] || 'data/procurement-166-live-qa')
  fs.mkdirSync(output, { recursive: true })
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  try {
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const response = await page.request.get(`${base}/api/projects/166`)
    assert.equal(response.status(), 200)
    const detail = await response.json()
    const pump = detail.procurement.items.find(item => item.name === '煤矿井下压裂泵')
    assert.equal(pump.quantity, '2')
    assert.equal(pump.unit, '套')
    assert.equal(detail.procurement.attachment_status, 'restricted')
    assert.ok(detail.records.find(record => record.id === pump.record_id).body_text.includes(pump.quote))
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 })
      await page.goto(`${base}/projects/166`)
      await page.getByRole('heading', { name: '采购信息', exact: true }).waitFor()
      const section = page.locator('.detail-procurement')
      const text = await section.innerText()
      assert.ok(text.includes('煤矿井下压裂泵') && text.includes('2 套'))
      assert.ok(text.includes('30') && text.includes('甲方指定地点'))
      assert.ok(text.includes('公开附件受限'))
      assert.ok(!text.includes('解析失败') && !text.includes('点击“读取附件”'))
      assert.ok((await page.locator('.detail-meta-item').filter({ hasText: '涉及设备' }).innerText()).includes('煤矿井下压裂泵'))
      if (width === 390) {
        const quantity = section.locator('.detail-procurement-mobile-quantity').first()
        assert.ok((await quantity.innerText()).includes('2 套'))
        const bounds = await quantity.boundingBox()
        assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width)
      }
      await section.locator('button[aria-label*="展开煤矿井下压裂泵"]').click()
      const expanded = section.locator('.detail-procurement-expanded')
      assert.ok((await expanded.innerText()).includes('公告正文'))
      assert.equal(await expanded.locator('a[href*="#page="]').count(), 0)
      const size = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }))
      assert.ok(size.scroll <= width + 2, JSON.stringify(size))
      await section.screenshot({ path: path.join(output, `procurement-166-${width}.png`) })
    }
    assert.deepEqual(errors, [])
    console.log(JSON.stringify({ passed: true, project: 166, quantity: '2 套', widths: [1440, 390], screenshots: output }))
  } finally {
    await browser.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
