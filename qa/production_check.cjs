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
    for (const [route, title, name] of pages) {
      await page.goto(url + route);
      await page.getByRole('heading', { name: title, exact: true, level: 2 }).waitFor();
      await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true });
    }
    await page.goto(url);
    await page.getByRole('heading', { name: '机会池', exact: true, level: 2 }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }));
    if (sizes.scroll > sizes.width + 2) throw new Error('Mobile overflow: ' + JSON.stringify(sizes));
    if (errors.length) throw new Error(errors.join('\n'));
    console.log(JSON.stringify({ passed: true, routes: pages.map(item => item[0]), sizes, screenshots: output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
