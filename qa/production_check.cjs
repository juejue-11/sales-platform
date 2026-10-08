// Read-only browser verification against the running production service.
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8766';
  const output = path.resolve(__dirname, '../data/production-verification');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const pages = [
      ['/', '机会池', 'home'],
      ['/projects/93', '选矿厂美卓破碎机国产化备件12项公开招标招标公告', 'project'],
      ['/settings', '业务配置', 'settings'],
      ['/operations', '处理状态', 'operations'],
    ];
    const sizes = [];
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      for (const [route, title, name] of pages) {
        await page.goto(url + route);
        await page.getByRole('heading', { name: title, exact: true, level: 2 }).waitFor();
        if (name === 'home') await page.locator('article.dashboard-project-row').first().waitFor();
        if (name === 'operations') await page.getByRole('button', { name: /立即导入 records.jsonl/ }).waitFor();
        await page.evaluate(() => document.fonts.ready);
        const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }));
        sizes.push({ route, ...size });
        if (size.scroll > size.width + 2) throw new Error('Page overflow: ' + route + ' ' + JSON.stringify(size));
        if (name === 'home') {
          const tableSize = await page.locator('.dashboard-list-scroll').evaluate(el => ({scroll: el.scrollWidth, width: el.clientWidth}));
          if (tableSize.scroll > tableSize.width + 2) throw new Error('Project list overflow: ' + JSON.stringify(tableSize));
        }
        if (name === 'home' && viewport.width === 390) {
          const row = page.locator('article.dashboard-project-row').first();
          for (const selector of ['.dashboard-project-title', '.dashboard-rule-cell', '.dashboard-ai-cell', '.dashboard-decision-cell', '.dashboard-project-dates', '.dashboard-project-action']) {
            const bounds = await row.locator(selector).boundingBox();
            if (!bounds || bounds.x < 0 || bounds.x + bounds.width > viewport.width + 2) throw new Error('Mobile project field clipped: ' + selector);
          }
          await row.screenshot({ path: path.join(output, 'mobile-project-row.png') });
        }
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: path.join(output, name + (viewport.width === 390 ? '-mobile' : '') + '.png'), fullPage: true });
        if (name === 'home') await page.screenshot({ path: path.join(output, viewport.width === 390 ? 'home-mobile-viewport.png' : 'home-viewport.png') });
      }
    }
    if (errors.length) throw new Error(errors.join('\n'));
    console.log(JSON.stringify({ passed: true, routes: pages.map(item => item[0]), sizes, screenshots: output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
