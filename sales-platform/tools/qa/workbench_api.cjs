// Run only against tools.qa.serve_fixture and its isolated database.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium, outputDir } = require('./browser_support.cjs');

const base = process.argv[2] || 'http://127.0.0.1:8877';
assert.equal(new URL(base).port, '8877', 'Real API smoke must use the isolated QA port');
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'QA must run on this computer');
const screenshots = outputDir('workbench-api');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const environment = await page.request.get(`${base}/api/qa/environment`);
    assert.equal(environment.status(), 200, 'Start the isolated QA fixture server first');
    assert.deepEqual(await environment.json(), { fixture: 'notice-224', isolated: true });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`${base}/?view=all`);
    await page.getByTestId('opportunity-item').waitFor();
    const card = await page.getByTestId('opportunity-item').innerText();
    assert.ok(card.includes('2026-10-23'), card);
    assert.equal(await page.getByTestId('opportunity-item').locator('time[datetime="2026-10-23T13:30:00+08:00"]').count(), 1);
    assert.ok(card.includes('明确采购'), card);
    await page.screenshot({ path: path.join(screenshots, 'home.png'), fullPage: true });
    await page.getByTestId('opportunity-item').getByRole('link', { name: '查看详情', exact: true }).click();
    const detailTabs = page.locator('.detail-tabs > .detail-tab-bar').getByRole('tab');
    assert.equal(await detailTabs.count(), 7);
    await page.getByRole('tab', { name: '采购内容', exact: true }).click();
    await page.getByTestId('procurement-reading').waitFor();
    assert.ok((await page.getByTestId('procurement-reading').innerText()).includes('02包'));
    await page.getByRole('tab', { name: '资格要求', exact: true }).click();
    assert.ok((await page.getByRole('tabpanel', { name: '资格要求', exact: true }).innerText()).includes('首台（套）'));
    await page.getByRole('tab', { name: '时间节点', exact: true }).click();
    const schedule = await page.getByRole('tabpanel', { name: '时间节点', exact: true }).innerText();
    for (const phrase of ['2026-10-19 16:00', '2026-10-23 13:30']) assert.ok(schedule.includes(phrase), phrase);
    await page.screenshot({ path: path.join(screenshots, 'reading.png'), fullPage: true });
    await page.getByRole('tab', { name: '跟进记录', exact: true }).click();
    await page.getByTestId('followup-panel').waitFor();
    let state = await (await page.request.get(`${base}/api/projects/224/followup`)).json();
    if (!state.is_following) {
      await page.getByRole('button', { name: '加入我的跟进' }).click();
      await page.locator('.followup-submission').waitFor();
    }
    if (!state.submitted_at) {
      const fields = page.locator('.followup-submission input');
      await fields.nth(0).fill('2026-10-14T11:00');
      await fields.nth(1).fill('QA电子采购平台');
      await fields.nth(2).fill('QA-RECEIPT-224');
      const expectedAt = await page.evaluate(() => new Date('2026-10-14T11:00').toISOString());
      await page.locator('.followup-submission').getByRole('button', { name: '确认已提交' }).click();
      const saved = page.waitForResponse((response) => response.url().endsWith('/followup') && response.request().method() === 'PUT');
      await page.getByRole('dialog').getByRole('button', { name: '确认已提交' }).click();
      const response = await saved;
      assert.equal(response.status(), 200, await response.text());
      state = await response.json();
      assert.equal(state.stage, 'submitted');
      assert.equal(Date.parse(state.submitted_at), Date.parse(expectedAt));
      assert.equal(state.submission_channel, 'QA电子采购平台');
      assert.equal(state.receipt, 'QA-RECEIPT-224');
      await page.waitForFunction(() => document.querySelector('.followup-submission input')?.disabled);
    }
    if (state.stage === 'submitted') {
      await page.locator('.followup-overview .ant-select').first().click();
      const waiting = page.waitForResponse((response) => response.url().endsWith('/followup') && response.request().method() === 'PUT');
      await page.getByText('等待结果', { exact: true }).last().click();
      const response = await waiting;
      assert.equal(response.status(), 200, await response.text());
      assert.equal((await response.json()).stage, 'awaiting_result');
    }
    const persisted = await (await page.request.get(`${base}/api/projects/224/followup`)).json();
    assert.equal(persisted.submitted_at, state.submitted_at);
    assert.equal(persisted.stage, 'awaiting_result');
    const following = await (await page.request.get(`${base}/api/projects?is_following=true`)).json();
    assert.equal(following.total, 1);
    await page.screenshot({ path: path.join(screenshots, 'followup.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, backend: 'real isolated API', tabs: 7, sourcedDates: true, lotAndException: true, realSubmissionRoundtrip: true, screenshots }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
