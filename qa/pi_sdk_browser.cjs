const path = require('node:path');
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async () => {
  const [url, output] = process.argv.slice(2);
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url + '/projects/9101');
    await page.getByRole('heading', { name: 'Pi隔离验收：破碎机备件采购', exact: true }).waitFor();
    // Public API verifies honest activity before browser rendering assertions.
    const data = await (await page.request.get(url + '/api/projects/9101')).json();
    if (data.agent_run?.status !== 'succeeded') throw new Error('Agent result is not ready');
    await page.getByText('Agent 执行记录', { exact: false }).first().waitFor();
    await page.getByText(/^查看执行步骤/).click();
    const readStep = data.agent_run.steps.find(step => step.stage === 'tool_end' && step.tool === 'read_project_records');
    if (!readStep) throw new Error('No recorded project read');
    await page.getByText(readStep.label, { exact: true }).first().waitFor();
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
    await page.screenshot({ path: path.join(output, 'sdk-project-desktop.png'), fullPage: true });
    if (!await page.getByRole('radio').first().isChecked()) throw new Error('Agent selection did not persist');
    await page.setViewportSize({ width: 390, height: 844 });
    const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }));
    if (size.scroll > size.width + 2) throw new Error('Mobile Agent overflow ' + JSON.stringify(size));
    await page.screenshot({ path: path.join(output, 'sdk-project-mobile.png'), fullPage: true });
    await page.goto(url + '/settings');
    await page.getByRole('heading', { name: '业务配置', exact: true }).waitFor();
    await page.getByText('Pi Agent SDK', { exact: false }).first().waitFor();
    if (errors.length) throw new Error(errors.join('\n'));
    console.log(JSON.stringify({ browser: 'passed', sdk: true, mobile: size, runtimeErrors: errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
