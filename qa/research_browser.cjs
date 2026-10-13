const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

const [rawUrl, rawOutput] = process.argv.slice(2);
const baseUrl = (rawUrl || '').replace(/\/$/, '');
const outputDir = rawOutput || path.resolve(process.cwd(), 'qa-output');
const EXPECTED_NORMAL_QUOTE = '合同生效后60天内交货';
const VIEWPORTS = {
  desktop: { width: 1440, height: 1000 },
  mobile: { width: 390, height: 844 },
};

if (!baseUrl) {
  console.error('Usage: node qa/research_browser.cjs <base-url> <output-dir>');
  process.exitCode = 2;
  return;
}

fs.mkdirSync(outputDir, { recursive: true });

const statusLabels = {
  running: '补查中',
  completed: '已完成',
  limited: '受限停止',
  failed: '补查失败',
  not_started: '未开始',
  not_performed: '未执行主动补查',
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function getJson(request, urlPath) {
  const response = await request.get(`${baseUrl}${urlPath}`);
  const body = await response.text();
  if (!response.ok()) throw new Error(`GET ${urlPath} failed (${response.status()}): ${body.slice(0, 500)}`);
  try {
    return JSON.parse(body);
  } catch (error) {
    throw new Error(`GET ${urlPath} returned invalid JSON: ${error.message}`);
  }
}

function findResearchEvidence(detail, quote) {
  const reasons = detail.judgment?.reasons || [];
  const evidence = reasons.flatMap((reason) => reason.evidence || []);
  return evidence.find((item) => item && typeof item.source_ref === 'string' && (!quote || item.quote === quote));
}

function findLegacyEvidence(detail) {
  const reasons = detail.judgment?.reasons || [];
  const evidence = reasons.flatMap((reason) => reason.evidence || []);
  return evidence.find((item) => item && !item.source_ref && item.record_id != null);
}

function investigationSources(detail, investigation) {
  return investigation?.sources || detail.research_sources || [];
}

function researchSourceFor(detail, evidence, investigation) {
  if (!evidence) return undefined;
  return investigationSources(detail, investigation).find((source) => (
    source.source_ref === evidence.source_ref
    && (!source.source_version || source.source_version === evidence.source_version)
  ));
}

function findHistoryRun(runs) {
  return runs.items.find((run) => ['failed', 'limited'].includes(run.investigation?.status)) || runs.items[0];
}

async function checkNoOverflow(page, label) {
  const size = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
  }));
  assert(Math.max(size.documentWidth, size.bodyWidth) <= size.innerWidth + 2, `${label}: horizontal overflow ${JSON.stringify(size)}`);
}

async function waitForHeading(page, title) {
  await page.getByRole('heading', { name: title, exact: true }).first().waitFor({ state: 'visible', timeout: 15000 });
}

async function snapshotFromCitation(page, projectId, evidence, source) {
  assert(evidence && source, `project ${projectId}: research evidence/source missing`);
  const citationUrl = evidence.source_url || source.url;
  const candidates = page.locator('.research-evidence-citation').filter({ hasText: evidence.quote });
  let citation = null;
  for (let index = 0; index < await candidates.count(); index += 1) {
    const candidate = candidates.nth(index);
    const links = await candidate.locator('a').evaluateAll((anchors) => anchors.map((anchor) => anchor.getAttribute('href') || ''));
    if (links.some((href) => href === citationUrl || href.includes(citationUrl))) {
      citation = candidate;
      break;
    }
  }
  assert(citation, `project ${projectId}: could not identify the research citation for ${evidence.source_ref}`);
  await citation.waitFor({ state: 'visible', timeout: 10000 });
  const snapshotRequest = page.waitForResponse((response) => {
    if (response.request().method() !== 'GET' || response.status() !== 200) return false;
    const decoded = decodeURIComponent(response.url());
    return decoded.includes(`/api/projects/${projectId}/research/sources/`)
      && decoded.includes(`/versions/${evidence.source_version}`);
  }, { timeout: 10000 });
  await citation.getByRole('button').click();
  const response = await snapshotRequest;
  const drawer = page.locator('.research-snapshot-drawer').last();
  await drawer.waitFor({ state: 'visible', timeout: 10000 });
  await drawer.getByText(evidence.quote, { exact: false }).first().waitFor({ state: 'visible', timeout: 10000 });
  await drawer.getByText(new RegExp(escapeRegExp(evidence.source_version))).first().waitFor({ state: 'visible', timeout: 10000 });
  const snapshotBody = await drawer.innerText();
  assert(snapshotBody.includes(evidence.quote), `project ${projectId}: saved quote is missing from snapshot drawer`);
  assert(snapshotBody.includes(evidence.source_version), `project ${projectId}: source version is missing from snapshot drawer`);
  await drawer.getByRole('button', { name: /关闭|Close/ }).click().catch(() => {});
  return { requestUrl: response.url(), sourceRef: evidence.source_ref, sourceVersion: evidence.source_version };
}

async function loadProject(page, projectId, viewportName, screenshotName, detail) {
  await page.setViewportSize(VIEWPORTS[viewportName]);
  await page.goto(`${baseUrl}/projects/${projectId}`, { waitUntil: 'domcontentloaded' });
  await waitForHeading(page, detail.title);
  await page.locator('.detail-page').waitFor({ state: 'visible' });
  await checkNoOverflow(page, `project ${projectId} ${viewportName}`);
  await page.screenshot({ path: path.join(outputDir, screenshotName), fullPage: true });
}

async function run() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: VIEWPORTS.desktop });
  const pageErrors = [];
  const nonGetApiRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (request.url().includes('/api/') && request.method() !== 'GET') nonGetApiRequests.push({ method: request.method(), url: request.url() });
  });

  const result = {
    browser: 'passed',
    title: '项目调查闭环验收',
    viewport: { desktop: 1440, mobile: 390 },
    scenarios: [],
    snapshots: [],
    history: {},
    runtimeErrors: pageErrors,
  };

  try {
    const normalDetail = await getJson(page.request, '/api/projects/9101');
    const normalEvidence = findResearchEvidence(normalDetail, EXPECTED_NORMAL_QUOTE);
    const normalInvestigation = normalDetail.judgment?.investigation || normalDetail.agent_run?.investigation;
    const normalSource = researchSourceFor(normalDetail, normalEvidence, normalInvestigation);
    assert(normalEvidence, '9101: expected research evidence with the delivery quote was not found');
    assert(normalEvidence.source_ref, '9101: research evidence has no source_ref');
    assert(normalEvidence.quote === EXPECTED_NORMAL_QUOTE, '9101: research quote does not match the expected text');
    assert(normalEvidence.source_version, '9101: research evidence has no source_version');
    assert(normalSource, '9101: evidence source is not present in the project source list');
    assert(normalSource.source_ref === normalEvidence.source_ref, '9101: evidence/source_ref mismatch');
    await loadProject(page, 9101, 'desktop', 'research-9101-desktop.png', normalDetail);
    const normalSnapshot = await snapshotFromCitation(page, 9101, normalEvidence, normalSource);
    result.snapshots.push({ projectId: 9101, ...normalSnapshot });
    await loadProject(page, 9101, 'mobile', 'research-9101-mobile.png', normalDetail);
    await page.getByText('项目调查', { exact: true }).first().waitFor({ state: 'visible' });
    result.scenarios.push({ projectId: 9101, title: normalDetail.title, researchEvidence: { sourceRef: normalEvidence.source_ref, sourceVersion: normalEvidence.source_version, quote: normalEvidence.quote } });

    const legacyDetail = await getJson(page.request, '/api/projects/9102');
    const legacyEvidence = findLegacyEvidence(legacyDetail);
    assert(legacyEvidence, '9102: legacy record evidence was not found in the API response');
    assert(['not_performed', undefined].includes(legacyDetail.judgment?.investigation?.status)
      && !legacyDetail.agent_run?.investigation, '9102: legacy report unexpectedly contains a new investigation record');
    await loadProject(page, 9102, 'desktop', 'research-9102-desktop.png', legacyDetail);
    await page.getByText('未执行主动补查', { exact: true }).first().waitFor({ state: 'visible' });
    const legacyCitation = page.locator('.detail-evidence-item').filter({ hasText: legacyEvidence.quote }).first();
    await legacyCitation.waitFor({ state: 'visible', timeout: 10000 });
    const legacyHref = await legacyCitation.locator('a').first().getAttribute('href');
    assert(legacyHref && !legacyHref.includes('undefined'), `9102: legacy evidence href is invalid (${legacyHref})`);
    if (legacyHref.startsWith('/')) {
      const legacyResponse = await page.request.get(`${baseUrl}${legacyHref}`);
      assert(legacyResponse.ok(), `9102: legacy evidence href returned ${legacyResponse.status()}`);
    }
    await loadProject(page, 9102, 'mobile', 'research-9102-mobile.png', legacyDetail);
    result.scenarios.push({ projectId: 9102, title: legacyDetail.title, legacyRecordId: legacyEvidence.record_id });

    const retryDetail = await getJson(page.request, '/api/projects/9103');
    const retryJudgmentInvestigation = retryDetail.judgment?.investigation || null;
    const retryLatestRun = retryDetail.agent_run || null;
    const retryLatestInvestigation = retryLatestRun?.investigation || null;
    assert(retryJudgmentInvestigation, '9103: old successful report investigation is missing');
    assert(retryLatestRun, '9103: latest run is missing');
    assert(retryLatestInvestigation && ['failed', 'limited'].includes(retryLatestInvestigation.status), `9103: latest run is not failed/limited (${retryLatestInvestigation?.status || 'missing'})`);
    assert(retryDetail.judgment?.agent_run_id !== retryLatestRun.id, '9103: old report is bound to the latest failed/limited run');
    const oldSource = retryJudgmentInvestigation.sources[0];
    assert(oldSource, '9103: old report source is missing');
    await loadProject(page, 9103, 'desktop', 'research-9103-desktop.png', retryDetail);
    const retryPanel = page.locator('#research');
    await retryPanel.locator('.research-panel-heading').getByText(statusLabels[retryLatestInvestigation.status], { exact: true }).waitFor({ state: 'visible' });
    assert(!(await retryPanel.innerText()).includes('本轮运行未形成新的调查记录'), '9103: a saved investigation was incorrectly described as missing');
    await retryPanel.getByText(statusLabels[retryLatestInvestigation.status], { exact: true }).first().waitFor({ state: 'visible' });
    const latestEvent = retryLatestInvestigation.events[retryLatestInvestigation.events.length - 1];
    if (latestEvent?.purpose) await retryPanel.getByText(latestEvent.purpose, { exact: true }).first().waitFor({ state: 'visible' });
    const oldSourceRow = retryPanel.locator('.research-source-main').filter({ hasText: oldSource.title }).first();
    await oldSourceRow.waitFor({ state: 'visible', timeout: 10000 });
    const technicalDetails = retryPanel.locator('.research-technical-details');
    if (retryJudgmentInvestigation.policy_version && retryLatestRun.usage && await technicalDetails.count()) {
      const oldTechnical = technicalDetails.filter({ hasText: retryJudgmentInvestigation.policy_version }).first();
      if (await oldTechnical.count()) {
        const oldTechnicalText = await oldTechnical.innerText();
        const latestUsageText = `模型调用 ${retryLatestRun.usage.model_calls} 次`;
        assert(!oldTechnicalText.includes(latestUsageText), '9103: old report technical details use latest run usage');
      }
    }

    const runs = await getJson(page.request, '/api/projects/9103/research/runs');
    assert(Array.isArray(runs.items) && runs.items.length > 0, '9103: research run history is empty');
    const historyRun = findHistoryRun(runs);
    const historyIndex = runs.items.findIndex((run) => run.id === historyRun.id);
    const historyDetail = await getJson(page.request, `/api/projects/9103/research/runs/${historyRun.id}`);
    const historyInvestigation = historyDetail.investigation;
    assert(historyInvestigation, `9103: historical run ${historyRun.id} has no investigation detail`);
    const historyItem = retryPanel.locator('.research-history .ant-collapse-item').nth(historyIndex);
    await retryPanel.getByRole('button', { name: /调查运行历史/ }).click();
    await historyItem.locator('.ant-collapse-header').click();
    const readHistoryButton = historyItem.getByRole('button', { name: /读取本次调查记录/ });
    if (await readHistoryButton.count()) {
      const historyResponse = page.waitForResponse((response) => response.url().includes(`/api/projects/9103/research/runs/${historyRun.id}`) && response.status() === 200, { timeout: 10000 });
      await readHistoryButton.click();
      await historyResponse;
    }
    await historyItem.getByText(statusLabels[historyInvestigation.status], { exact: true }).first().waitFor({ state: 'visible' });
    if (historyInvestigation.stop_reason) await historyItem.getByText(historyInvestigation.stop_reason, { exact: false }).first().waitFor({ state: 'visible' });
    if (historyInvestigation.unresolved?.[0]) await historyItem.getByText(historyInvestigation.unresolved[0], { exact: false }).first().waitFor({ state: 'visible' });
    const historySource = historyInvestigation.sources?.find((source) => source.source_version) || historyInvestigation.sources?.[0];
    assert(historySource, `9103: historical run ${historyRun.id} has no source to open`);
    assert(historySource.source_version, `9103: historical run ${historyRun.id} source has no saved version`);
    await historyItem.getByText(historySource.source_version, { exact: false }).first().waitFor({ state: 'visible' });
    const historySnapshotButton = historyItem.getByRole('button', { name: /查看保存快照/ }).first();
    await historySnapshotButton.waitFor({ state: 'visible', timeout: 10000 });
    const historySnapshotResponse = page.waitForResponse((response) => {
      if (response.request().method() !== 'GET' || response.status() !== 200) return false;
      const decoded = decodeURIComponent(response.url());
      return decoded.includes(`/api/projects/9103/research/sources/${historySource.source_ref}/versions/${historySource.source_version}`);
    }, { timeout: 10000 });
    await historySnapshotButton.click();
    await historySnapshotResponse;
    const historyDrawer = page.locator('.research-snapshot-drawer').last();
    await historyDrawer.waitFor({ state: 'visible', timeout: 10000 });
    await historyDrawer.getByText(historySource.source_version, { exact: false }).first().waitFor({ state: 'visible' });
    await historyDrawer.getByRole('button', { name: /关闭|Close/ }).click().catch(() => {});
    result.history = {
      runId: historyRun.id,
      status: historyInvestigation.status,
      sourceRef: historySource.source_ref,
      sourceVersion: historySource.source_version || null,
    };
    await page.screenshot({ path: path.join(outputDir, 'research-9103-history-desktop.png'), fullPage: true });
    await loadProject(page, 9103, 'mobile', 'research-9103-mobile.png', retryDetail);
    result.scenarios.push({ projectId: 9103, title: retryDetail.title, latestStatus: retryLatestInvestigation.status, oldSourceRef: oldSource.source_ref });

    assert(nonGetApiRequests.length === 0, `unexpected non-GET API requests (paid/model action guard): ${JSON.stringify(nonGetApiRequests)}`);
    assert(pageErrors.length === 0, `pageerror: ${pageErrors.join('\n')}`);
    result.runtimeErrors = pageErrors;
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(JSON.stringify({ browser: 'failed', title: '项目调查闭环验收', error: error.message }));
  process.exitCode = 1;
});
