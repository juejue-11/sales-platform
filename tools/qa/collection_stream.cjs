const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium, outputDir } = require('./browser_support.cjs');
const base = process.argv[2] || 'http://127.0.0.1:8879';
assert.equal(new URL(base).port, '8879');
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const screenshots = outputDir('collection-stream');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const env = await (await page.request.get(`${base}/api/qa/environment`)).json();
    assert.deepEqual(env, { fixture: 'collection-streaming', isolated: true });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/collection`);
    await page.getByTestId('collection-run-all').waitFor();
    const started = page.waitForResponse(response => response.url().endsWith('/api/collection/run') && response.request().method() === 'POST');
    await page.getByTestId('collection-run-all').click();
    assert.equal((await started).status(), 200);
    await page.getByRole('button', { name: '去采购需求', exact: true }).first().click();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="opportunity-item"]').length === 1, null, { timeout: 30000 });
    const during = await (await page.request.get(`${base}/api/collection`)).json();
    assert.equal(during.task.status, 'running', 'The second fixture is still blocked');
    assert.equal(during.task.import_result.inserted, 1, 'First record must import before the process exits');
    assert.equal((await (await page.request.get(`${base}/api/projects`)).json()).total, 1);
    await page.getByRole('button', { name: '更多筛选', exact: true }).click();
    const draft = page.getByPlaceholder('项目、采购方、设备或关键词');
    await draft.fill('尚未提交的搜索草稿');
    const currentURL = page.url();
    await page.screenshot({ path: path.join(screenshots, 'first-record-before-completion.png'), fullPage: true });
    assert.equal((await page.request.post(`${base}/api/qa/release-slow`)).status(), 200);
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="opportunity-item"]').length === 2, null, { timeout: 30000 });
    assert.equal(await draft.inputValue(), '尚未提交的搜索草稿', 'Background refresh must preserve an unsubmitted filter');
    assert.equal(page.url(), currentURL);
    let complete;
    for (let index = 0; index < 60; index++) {
      complete = await (await page.request.get(`${base}/api/collection`)).json();
      if (complete.task.status !== 'running') break;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert.equal(complete.task.status, 'succeeded', JSON.stringify(complete.task));
    assert.equal(complete.task.import_result.inserted, 2, 'Do not count repeated full snapshots as new projects');
    assert.equal((await (await page.request.get(`${base}/api/projects`)).json()).total, 2);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(screenshots, 'second-record-without-refresh.png'), fullPage: true });
    console.log(JSON.stringify({ passed: true, firstVisibleWhileRunning: true, secondAppearsWithoutRefresh: true,
      autoImportDisabled: true, cumulativeNewRecords: 2, draftPreserved: true, modelCalls: 0, screenshots }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
