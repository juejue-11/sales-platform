// Browser acceptance: single panels, preserved drafts/local state, URL navigation,
// report hierarchy, keyboard navigation and sticky/responsive tabs. APIs are mocked.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))
const base = process.argv[2] || 'http://127.0.0.1:5173'
const output = path.resolve(process.argv[3] || path.join(__dirname, '../data/project-tabs-qa'))
fs.mkdirSync(output, { recursive: true })
const judgment = {
  id: 12, created_at: '2026-10-13T08:00:00+08:00', model: 'mock', input_fingerprint: 'mock', stale: false,
  decision: 'verify', summary: '建议核对技术规格后推进采购机会。', demand_summary: '采购井下压裂泵两套。',
  reasons: [{ text: '设备需求已由采购公告确认。', evidence: [] }], unknowns: ['额定压力尚未确认。'],
  next_actions: ['联系采购方取得技术清单。'],
  options: [{ id: 'verify-spec', title: '核对技术规格', description: '向采购方确认规格。', suitable_for: '当前项目', tradeoffs: [], unknowns: [], next_action: '取得技术清单' }],
  report: {
    version: 1,
    sections: [{ id: 'detail', title: '详细采购分析', blocks: [{ type: 'paragraph', text: '详细分析内容。'.repeat(260), evidence: [] }] }],
    opportunities: [{ id: 'opp', kind: 'explicit_demand', title: '压裂泵采购机会', description: '公告明确采购两套设备。', basis: '公告正文', uncertainties: ['需核对规格'], next_actions: ['联系采购方取得技术清单。'], evidence: [] }],
    gaps: [{ id: 'gap', description: '额定压力尚未确认。', status: 'not_stated', impact: '影响选型', next_action: '补齐技术规格', worth_researching: true }],
  },
}
const project = {
  id: 166, title: '煤矿井下压裂泵采购公告', buyer: '某矿业公司', equipment: ['压裂泵'], kind: 'tender', source_id: 'mock', record_ids: [166],
  published_at: '2026-10-08T08:00:00+08:00', deadline_at: '2026-10-29T09:30:00+08:00', updated_at: '2026-10-13T08:00:00+08:00',
  gate_status: 'passed', gate_reasons: [], ai_status: 'succeeded', ai_decision: 'verify', ai_summary: judgment.summary, needs_review: false,
  decision: { status: 'new', owner: '', next_action: '', note: '', selected_option_id: null, judgment_id: null, updated_at: null },
  records: [{ id: 166, title: '采购公告', body_text: '采购压裂泵两套。', facts: {}, inferences: {}, original_url: 'https://example.test/notice', parse_status: 'ok' }],
  judgment, judgment_history: [judgment], decision_history: [], related_projects: [], versions: [], research_sources: [],
  briefing: { record_id: 166, scope_excerpt: '采购压裂泵两套。', delivery_excerpt: '', qualification_excerpt: '', contact_excerpt: '', original_url: 'https://example.test/notice' },
  procurement: { status: 'partial', scope_summary: '采购压裂泵两套。', items: [{ name: '压裂泵', specification: '', material: '', quantity: '2', unit: '套', source_kind: 'announcement', source_document_id: '', record_id: 166, page: 0, quote: '采购压裂泵两套。' }], delivery_period: '', delivery_location: '', missing_fields: [], documents: [], attachment_status: 'none_public' },
}

;(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  let reads = 0
  let saved = null
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/projects/166', route => {
    reads++
    return route.fulfill({ json: project })
  })
  await page.route('**/api/projects/166/decision', route => {
    saved = route.request().postDataJSON()
    project.decision = { ...saved, updated_at: '2026-10-13T09:00:00+08:00' }
    return route.fulfill({ json: project })
  })
  const tab = name => page.getByRole('tab', { name: new RegExp(name) })
  const select = async name => {
    await tab(name).click()
    await page.waitForFunction(label => [...document.querySelectorAll('[role="tab"]')].some(el => el.textContent.includes(label) && el.getAttribute('aria-selected') === 'true'), name)
  }
  const singlePanel = async () => assert.equal(await page.getByRole('tabpanel').count(), 1)
  try {
    await page.goto(`${base}/projects/166?source=pool`)
    await tab('采购信息').waitFor()
    const desktopLayout = await page.evaluate(() => ({
      sidebar: document.querySelector('.app-sider').getBoundingClientRect().width,
      sidebarStyle: getComputedStyle(document.querySelector('.app-sider')).flex,
      search: document.querySelector('.procurement-material-search').getBoundingClientRect().width,
      materialWidth: document.querySelector('.procurement-material-section').getBoundingClientRect().width,
      delivery: getComputedStyle(document.querySelector('.procurement-fact-preview')).fontSize,
    }))
    assert.equal(desktopLayout.sidebar, 176, 'Desktop navigation must retain its configured width')
    assert.ok(desktopLayout.search <= desktopLayout.materialWidth, 'Desktop search should stay within the material section')
    assert.equal(await page.getByRole('tab').count(), 5)
    await singlePanel()
    const search = page.getByPlaceholder('搜索名称、规格、材质或编号')
    await search.fill('压裂泵')
    await page.getByRole('button', { name: /查看完整正文/ }).click()
    await page.locator('.ant-drawer-content .procurement-reader-text').waitFor()
    await page.locator('.ant-drawer-close').click()
    await page.locator('.ant-drawer').waitFor({ state: 'hidden' })
    await select('行动与决定')
    await page.getByLabel('负责人', { exact: true }).fill('测试负责人')
    await page.getByLabel('备注', { exact: true }).fill('未保存的沟通记录')
    await page.getByRole('radio', { name: /核对技术规格/ }).check()
    const initialReads = reads
    await select('AI 研判')
    await singlePanel()
    assert.ok(page.url().includes('tab=analysis'))
    assert.ok(page.url().includes('source=pool'))
    const headings = await page.getByRole('tabpanel').getByRole('heading').allTextContents()
    assert.ok(headings.indexOf('分析结论') < headings.indexOf('信息缺口'))
    assert.ok(headings.indexOf('信息缺口') < headings.indexOf('机会判断'))
    assert.ok(headings.indexOf('机会判断') < headings.indexOf('详细采购分析'))
    const reportText = page.locator('.agent-report-paragraph .detail-disclosure')
    const reportToggle = reportText.getByRole('button', { name: '展开全文' })
    assert.equal(await reportToggle.getAttribute('aria-expanded'), 'false')
    const collapsedHeight = await reportText.locator('.ant-typography').evaluate(el => el.getBoundingClientRect().height)
    await reportToggle.click()
    assert.equal(await reportText.getByRole('button', { name: '收起全文' }).getAttribute('aria-expanded'), 'true')
    assert.ok(await reportText.locator('.ant-typography').evaluate(el => el.getBoundingClientRect().height) > collapsedHeight * 2)
    await page.waitForTimeout(300) // Let the tab indicator transition settle for visual QA.
    await page.screenshot({ path: path.join(output, 'analysis-desktop.png') })
    await page.evaluate(() => window.scrollTo(0, 900))
    const sticky = await page.evaluate(() => ({
      header: document.querySelector('.app-header').getBoundingClientRect().bottom,
      tabs: document.querySelector('.detail-tab-bar').getBoundingClientRect().top,
    }))
    assert.ok(Math.abs(sticky.tabs - sticky.header) < 2, JSON.stringify(sticky))
    await select('行动与决定')
    assert.equal(await page.getByLabel('负责人', { exact: true }).inputValue(), '测试负责人')
    assert.equal(await page.getByLabel('备注', { exact: true }).inputValue(), '未保存的沟通记录')
    assert.ok(await page.getByRole('radio', { name: /核对技术规格/ }).isChecked())
    await select('采购信息')
    await page.getByRole('heading', { name: '采购事实', exact: true }).waitFor()
    assert.equal(await search.inputValue(), '压裂泵')
    await page.locator('.procurement-original-preview').waitFor({ state: 'visible' })
    assert.equal(reads, initialReads, 'Switching tabs must not reload project data')
    await page.goBack()
    await page.waitForURL(/tab=decision/)
    assert.equal(await tab('行动与决定').getAttribute('aria-selected'), 'true')
    await page.getByRole('button', { name: /保存人工决定/ }).click()
    await page.getByText('人工决定已保存，历史记录已保留', { exact: true }).waitFor()
    assert.equal(saved.owner, '测试负责人')
    assert.equal(saved.selected_option_id, 'verify-spec')
    assert.equal(saved.judgment_id, 12)
    await select('历史与关联')
    await singlePanel()
    assert.ok(await page.getByRole('button', { name: /记录关联/ }).isVisible())
    await select('项目调查')
    await singlePanel()
    assert.ok(await page.getByRole('heading', { name: '项目调查', exact: true }).isVisible())
    await select('AI 研判')
    await page.reload()
    await page.getByRole('heading', { name: /分析结论/ }).waitFor()
    assert.equal(await tab('AI 研判').getAttribute('aria-selected'), 'true')
    await tab('AI 研判').focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await page.waitForURL(/tab=research/)
    await page.getByRole('heading', { name: '项目调查', exact: true }).waitFor()
    assert.equal(await tab('项目调查').getAttribute('aria-selected'), 'true')
    for (const width of [1920, 1280, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await select('采购信息')
      const layout = await page.evaluate(() => {
        const tabs = [...document.querySelectorAll('[role="tab"]')]
        const wrap = document.querySelector('.ant-tabs-nav-wrap')
        return { page: document.documentElement.scrollWidth, row: tabs.map(t => t.getBoundingClientRect().top), scroll: wrap.scrollWidth, client: wrap.clientWidth, overflow: getComputedStyle(wrap).overflowX }
      })
      assert.ok(layout.page <= width + 1, JSON.stringify(layout))
      assert.ok(layout.row.every(y => Math.abs(y - layout.row[0]) < 1), 'Tabs must stay in one row')
      if (width < 720) {
        assert.ok(layout.scroll > layout.client)
        await page.locator('.ant-tabs-nav-wrap').hover()
        await page.mouse.wheel(500, 0)
        await page.waitForFunction(() => new DOMMatrix(getComputedStyle(document.querySelector('.ant-tabs-nav-list')).transform).m41 < 0)
        await page.waitForTimeout(300) // The Tabs component briefly suppresses clicks after a swipe.
      }
      await select('历史与关联')
      await singlePanel()
      await select('AI 研判')
      await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(output, `analysis-${width}.png`) })
    }
    project.judgment = { ...judgment, report: null }
    await page.goto(`${base}/projects/166?tab=analysis`)
    await page.getByRole('heading', { name: /分析结论/ }).waitFor()
    const legacy = await page.getByRole('tabpanel').innerText()
    assert.ok(legacy.indexOf('待确认信息') < legacy.indexOf('AI 建议下一步'))
    assert.ok(legacy.indexOf('AI 建议下一步') < legacy.indexOf('AI 理由与证据'))
    await page.goto(`${base}/projects/166?tab=invalid`)
    await tab('采购信息').waitFor()
    assert.equal(await tab('采购信息').getAttribute('aria-selected'), 'true')
    // Extraction errors stay identifiable, with a readable full-value disclosure.
    const anomalousLocation = '甲方指定地点；联系人：' + '此处混入公告其他字段，必须核对原文。'.repeat(20) + '完整字段末尾'
    project.procurement.delivery_location = anomalousLocation
    project.procurement.delivery_period = '合同签订之日起30个日历天'
    project.records[0].body_text += '\n交货地点：' + anomalousLocation
    for (const width of [1920, 1440, 1280, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(`${base}/projects/166`)
      const location = page.locator('.procurement-fact-field-delivery_location')
      await location.waitFor()
      assert.ok((await location.innerText()).includes('待核实') || (await location.innerText()).includes('待核对'))
      assert.equal(await location.getByRole('button', { name: '展开全文' }).count(), 0)
      const valueDisclosure = location.getByText('查看全部该字段值', { exact: true })
      if (await valueDisclosure.isVisible()) await valueDisclosure.click()
      assert.ok((await location.innerText()).includes('完整字段末尾'), 'The full extracted value must be reachable')
      assert.ok(await location.locator('.procurement-fact-preview').evaluate(el => el.scrollHeight <= el.clientHeight + 1), 'Expanded fact values must not remain clipped')
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= width + 1)
      await page.screenshot({ path: path.join(output, `procurement-${width}.png`), fullPage: true })
    }
    assert.deepEqual(errors, [])
    console.log(JSON.stringify({ passed: true, widths: [1920, 1440, 1280, 768, 390, 320], screenshots: output }))
  } catch (error) {
    console.error(JSON.stringify({ url: page.url(), errors, body: (await page.locator('body').innerText()).slice(0, 2000) }))
    await page.screenshot({ path: path.join(output, 'failure.png') })
    throw error
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
