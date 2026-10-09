// Read-only checks for the information displayed before entering project details.
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
    const first = page.getByTestId('opportunity-item').first();
    await first.waitFor();
    const preview = await first.getByTestId('opportunity-scope').innerText();
    if (preview.trim().length < 15) throw new Error('Initial feed does not expose procurement information');
    if (detailRequests.length) throw new Error('Initial page fetched details per item');
    await first.getByRole('button', {name: '展开原文'}).click();
    await first.getByTestId('opportunity-source').waitFor();
    if (detailRequests.length !== 1) throw new Error('Source expansion must load only the requested project');
    if (await first.getByTestId('opportunity-source').innerText() === '') throw new Error('Source expansion is empty');
    if (new URL(page.url()).pathname !== '/') throw new Error('Source expansion left the feed');
    await first.getByRole('button', {name: '收起原文'}).click();
    await first.getByRole('button', {name: '展开原文'}).click();
    await first.getByTestId('opportunity-source').waitFor();
    if (detailRequests.length !== 1) throw new Error('Cached source expansion fetched again');
    const search = page.getByPlaceholder('搜索项目标题、采购方、设备');
    await search.fill('2套');
    const searchResponse = page.waitForResponse(response => {
      const requestUrl = new URL(response.url());
      return requestUrl.pathname === '/api/projects' && requestUrl.searchParams.get('q') === '2套';
    });
    await search.press('Enter');
    const result = await (await searchResponse).json();
    if (!result.items.some(item => item.briefing.scope_excerpt.includes('2套'))) throw new Error('Scope search failed');
    await page.getByTestId('opportunity-scope').filter({hasText:'2套'}).first().waitFor();
    if (await page.locator('.opportunity-tab-active').count()) throw new Error('Search kept an unrelated category highlighted');
    await page.getByRole('button', {name:/^全部/}).click();
    await page.getByTestId('opportunity-item').first().waitFor();
    const secondPage = page.locator('.ant-pagination-item-2');
    if (await secondPage.count()) {
      const pageResponse = page.waitForResponse(response => {
        const requestUrl = new URL(response.url());
        return requestUrl.pathname === '/api/projects' && requestUrl.searchParams.get('page') === '2';
      });
      await secondPage.click();
      await pageResponse;
      await page.getByTestId('opportunity-item').first().waitFor();
      if (!await page.getByRole('button', {name:/^全部/}).getAttribute('aria-current')) throw new Error('Pagination cleared the current category');
    }
    console.log(JSON.stringify({passed: true, initialPreviewCharacters: preview.length, detailRequests: detailRequests.length, inlineExpansion: true, scopeSearch: true, pagination: true}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
