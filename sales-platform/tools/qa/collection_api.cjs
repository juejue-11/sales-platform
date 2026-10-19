// Real collector CLI, local fixture pages, isolated databases, and no model calls.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium, outputDir } = require('./browser_support.cjs');

const base = process.argv[2] || 'http://127.0.0.1:8879';
const url = new URL(base);
assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname));
assert.equal(url.port, '8879', 'Use the dedicated collection QA port');
const screenshots = outputDir('collection-api');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const marker = await page.request.get(`${base}/api/qa/environment`);
    assert.equal(marker.status(), 200);
    assert.deepEqual(await marker.json(), { fixture: 'collection-integration', isolated: true });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const getState = async () => {
      const response = await page.request.get(`${base}/api/collection`);
      assert.equal(response.status(), 200, await response.text());
      return response.json();
    };
    const finish = async () => {
      for (let i = 0; i < 100; i++) {
        const state = await getState();
        if (state.task && state.task.status !== 'running') return state;
        await new Promise(resolve => setTimeout(resolve, 300));
      }
      throw new Error('Collection task did not finish');
    };
    await page.goto(`${base}/collection`);
    await page.getByTestId('collection-page').waitFor();
    assert.ok((await page.getByTestId('collection-page').innerText()).includes('验收公开采购来源'));
    const started = page.waitForResponse(response => response.url().endsWith('/api/collection/run') && response.request().method() === 'POST');
    await page.getByTestId('collection-run-all').click();
    assert.equal((await started).status(), 200);
    const duplicate = await page.request.post(`${base}/api/collection/run`, { data: {} });
    assert.equal(duplicate.status(), 409, await duplicate.text());
    let state = await finish();
    assert.equal(state.task.status, 'succeeded', JSON.stringify(state.task));
    assert.equal(state.task.import_result.inserted, 1);
    const projects = await (await page.request.get(`${base}/api/projects`)).json();
    assert.equal(projects.total, 1, 'Collection must automatically import the notice');

    const changed = page.waitForResponse(response => response.url().endsWith('/sources/qa_blocked') && response.request().method() === 'PATCH');
    await page.getByTestId('collection-source-toggle-qa_blocked').click();
    assert.equal((await changed).status(), 200);
    const second = await page.request.post(`${base}/api/collection/run`, { data: {} });
    assert.equal(second.status(), 200);
    state = await finish();
    assert.equal(state.task.status, 'partial', JSON.stringify(state.task));
    assert.ok(state.task.import_result, 'A partial collection must still import saved data');
    assert.equal(state.sources.find(source => source.id === 'qa_blocked').last_run.status, 'failed');
    await page.reload();
    await page.getByTestId('collection-task').waitFor();
    await page.screenshot({ path: path.join(screenshots, 'desktop-partial.png'), fullPage: true });
    const single = page.waitForResponse(response => response.url().endsWith('/api/collection/run') && response.request().method() === 'POST');
    await page.getByTestId('collection-source-run-qa_public').click();
    assert.equal((await single).status(), 200);
    state = await finish();
    assert.equal(state.task.source_id, 'qa_public');
    assert.equal(state.task.status, 'succeeded');
    const after = await (await page.request.get(`${base}/api/projects`)).json();
    assert.equal(after.total, 1, 'Repeated collection and import must be idempotent');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.getByTestId('collection-page').waitFor();
    const width = await page.evaluate(() => ({ screen: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(width.content <= width.screen + 1, JSON.stringify(width));
    await page.screenshot({ path: path.join(screenshots, 'mobile.png'), fullPage: true });

    // Exercise schedule editing and import-error presentation with HTTP mocks;
    // these requests must never touch the computer's real task registration.
    const mocked = { ...state, task: { ...state.task, status: 'failed',
      import_result: { error: '验收导入失败' }, error: '验收导入失败' },
      schedule: { available: true, registered: true, enabled: true, at: '07:30', state: 'Ready', message: '每日采集已启用' } };
    let savedSchedule = null;
    await page.route('**/api/collection', route => route.fulfill({ json: mocked }));
    await page.route('**/api/collection/schedule', async route => {
      savedSchedule = route.request().postDataJSON();
      mocked.schedule = { ...mocked.schedule, ...savedSchedule };
      await route.fulfill({ json: { message: '每日设置已保存' } });
    });
    await page.reload();
    await page.getByTestId('collection-task').waitFor();
    assert.ok((await page.getByTestId('collection-task').innerText()).includes('采集已结束，导入未完成'));
    assert.equal(await page.locator('.collection-import-result strong').count(), 0, 'An import error must not be rendered as a count');
    const time = page.locator('#collection-schedule-time');
    await time.fill('08:40');
    await page.waitForTimeout(11000);
    assert.equal(await time.inputValue(), '08:40', 'Polling must preserve the time being edited');
    const reloadedSchedule = page.waitForResponse(response => response.url().endsWith('/api/collection') && response.request().method() === 'GET');
    await page.getByRole('button', { name: '保存每日设置' }).click();
    await reloadedSchedule;
    assert.deepEqual(savedSchedule, { enabled: true, at: '08:40' });
    await page.unroute('**/api/collection');

    await page.route('**/api/collection', route => route.fulfill({ json: { ...mocked, import_connected: false } }));
    await page.reload();
    await page.getByTestId('collection-import-warning').waitFor();
    assert.equal(await page.getByTestId('collection-run-all').isDisabled(), true);
    assert.equal(await page.getByTestId('collection-source-run-qa_public').isDisabled(), true);
    assert.equal(await page.getByTestId('collection-source-toggle-qa_public').isDisabled(), false);
    await page.getByRole('button', { name: '检查数据接入' }).click();
    assert.ok(page.url().endsWith('/settings?section=import'));
    await page.unroute('**/api/collection');

    await page.route('**/api/collection', route => route.fulfill({
      json: { available: false, message: '未找到采集模块，请检查安装。', running: false, sources: [], runs: [],
        schedule: { available: false, registered: false, enabled: false, at: null, state: 'unavailable', message: '自动采集管理不可用' }, task: null },
    }));
    await page.goto(`${base}/collection`);
    await page.getByTestId('collection-page').waitFor();
    assert.ok((await page.getByTestId('collection-page').innerText()).includes('未找到采集模块'));
    assert.equal(await page.getByTestId('collection-run-all').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, realCLI: true, automaticImport: true, duplicateBlocked: true,
      partialImported: true, sourceToggle: true, singleSource: true, idempotent: true, mobile: true,
      missingModule: true, scheduleEditPreserved: true, importErrorDistinguished: true, importPreflight: true, screenshots }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
