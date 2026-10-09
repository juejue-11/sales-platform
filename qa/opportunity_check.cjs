// Read-only acceptance: compact browsing, actual procurement cues, and click-through.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8766';
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const detailRequests = [];
    page.on('request', request => {
      if (/\/api\/projects\/\d+$/.test(request.url())) detailRequests.push(request.url());
    });
    await page.goto(url);
    const cards = page.getByTestId('opportunity-item');
    await cards.first().waitFor();
    const density = await cards.evaluateAll(items => ({
      heights: items.map(item => item.getBoundingClientRect().height),
      visible: items.filter(item => {
        const bounds = item.getBoundingClientRect();
        return bounds.top >= 0 && bounds.bottom <= innerHeight;
      }).length,
      columns: new Set(items.slice(0, 3).map(item => Math.round(item.getBoundingClientRect().left))).size,
    }));
    assert.equal(density.columns, 3, 'Desktop must show comparable cards in 3 columns');
    assert.ok(density.heights.every(height => height <= 270), 'No announcement should dominate the viewport');
    assert.ok(density.visible >= 6, `Only ${density.visible} full cards visible`);
    assert.equal(detailRequests.length, 0, 'Browsing must not fetch every announcement body');
    const first = cards.first();
    assert.ok((await first.innerText()).includes('2套'), 'First card must expose actual purchase quantity');
    assert.ok(!(await first.innerText()).includes('国家企业信用信息公示系统'), 'Full qualification boilerplate belongs in details');
    const cardLink = await first.evaluate(item => item.matches('a') ? item.getAttribute('href') : item.querySelector('a')?.getAttribute('href'));
    assert.equal(cardLink, '/projects/166');
    const focusableCard = page.locator('a[href="/projects/166"]').filter({has: page.getByRole('heading')}).first();
    await focusableCard.focus();
    assert.ok(await focusableCard.evaluate(element => element === document.activeElement), 'The whole card must be keyboard focusable');
    await first.click({position: {x: 20, y: 100}});
    await page.waitForURL('**/projects/166');
    await page.getByRole('heading', {name: '采购信息', exact: true}).waitFor();
    assert.ok((await page.locator('.detail-page').innerText()).includes('30个日历天'), 'Click must reveal delivery requirement');
    assert.equal(detailRequests.length, 1, 'Click loads exactly the selected project');
    await page.getByRole('button', {name: /返回机会池/}).click();
    await cards.first().waitFor();
    const search = page.getByPlaceholder('搜索项目标题、采购方、设备');
    await search.fill('2套');
    const searchResponse = page.waitForResponse(response => {
      const requestUrl = new URL(response.url());
      return requestUrl.pathname === '/api/projects' && requestUrl.searchParams.get('q') === '2套';
    });
    await search.press('Enter');
    const result = await (await searchResponse).json();
    assert.ok(result.items.some(item => item.briefing.scope_excerpt.includes('2套')));
    await cards.first().waitFor();
    assert.equal(await page.locator('.opportunity-tab-active').count(), 0, 'Search must clear unrelated category highlighting');
    await page.getByRole('button', {name:/^全部/}).click();
    await cards.first().waitFor();
    const secondPage = page.locator('.ant-pagination-item-2');
    const pageResponse = page.waitForResponse(response => {
      const requestUrl = new URL(response.url());
      return requestUrl.pathname === '/api/projects' && requestUrl.searchParams.get('page') === '2';
    });
    await secondPage.click();
    await pageResponse;
    await cards.first().waitFor();
    const pageTwoTitle = await cards.first().getByRole('heading').innerText();
    await cards.first().click();
    await page.waitForURL(/\/projects\/\d+$/);
    await page.getByRole('button', {name:/返回机会池/}).click();
    await cards.first().waitFor();
    assert.equal(await cards.first().getByRole('heading').innerText(), pageTwoTitle, 'Back should restore the browsing page');
    assert.ok(await page.getByRole('button', {name:/^全部/}).getAttribute('aria-current'));
    console.log(JSON.stringify({passed: true, density, clickThrough: true, scopeSearch: true, backRestoresPage: true}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
