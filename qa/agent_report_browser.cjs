// Frontend-only acceptance: API responses are intercepted in the browser, so
// this check never reads a production database or calls a model provider.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

const [rawUrl, rawOutput] = process.argv.slice(2);
const baseUrl = (rawUrl || 'http://127.0.0.1:5173').replace(/\/$/, '');
const outputDir = rawOutput || path.resolve(process.cwd(), 'qa-output-agent-report');
fs.mkdirSync(outputDir, { recursive: true });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const evidence = {
  record_id: 42,
  document_id: 'doc-7',
  document_name: '技术附件.pdf',
  quote: '合同生效后60天内交货',
  page: 3,
  source_url: 'https://example.test/attachment.pdf',
  snapshot_url: '/api/projects/42/documents/doc-7/snapshot?sha256=attachment-v1',
  document_sha256: 'attachment-v1',
};

const report = {
  version: 1,
  sections: [
    {
      id: 'decision',
      title: '本项目判断',
      blocks: [
        { type: 'paragraph', text: '公告明确了交付要求，项目仍需核对技术规格。', evidence: [evidence] },
        { type: 'list', items: [{ text: '先核对技术附件中的规格与数量。', evidence: [evidence] }] },
        { type: 'table', columns: ['判断项', '当前结论'], rows: [{ cells: ['交付期', '合同生效后60天内'], evidence: [evidence] }] },
      ],
    },
  ],
  opportunities: [
    { id: 'demand-1', kind: 'explicit_demand', title: '附件中的交付要求', description: '这是当前采购明确提出的交付条件。', basis: '公告附件原文', uncertainties: [], next_actions: ['核对技术规格'], evidence: [evidence] },
    { id: 'signal-1', kind: 'potential_opportunity', title: '后续技术服务线索', description: '同一项目可能产生后续服务需求。', basis: '项目背景与阶段信息', uncertainties: ['服务范围尚未说明'], next_actions: ['向采购方确认'], evidence: [] },
  ],
  gaps: [
    { id: 'gap-1', description: '技术规格尚未完整读取', status: 'read_failed', impact: '无法完成企业匹配判断。', next_action: '补读附件快照', worth_researching: true },
  ],
};

const currentProject = {
  id: 42,
  title: '矿山运输设备采购项目',
  buyer: '示例矿业公司',
  equipment: ['运输设备'],
  kind: 'procurement',
  source_id: 'ccgp_local_tender',
  record_ids: [42],
  published_at: '2026-10-01T00:00:00Z',
  deadline_at: '2099-10-20T00:00:00Z',
  updated_at: '2026-10-12T00:00:00Z',
  gate_status: 'candidate',
  gate_reasons: [],
  ai_status: 'succeeded',
  ai_decision: 'recommend',
  ai_summary: '建议关注该项目。',
  needs_review: false,
  decision: { status: 'new', owner: '', next_action: '', note: '', selected_option_id: null, updated_at: null },
  opportunity_signals: report.opportunities,
  briefing: { record_id: 42, scope_excerpt: '采购运输设备', delivery_excerpt: '合同生效后60天内交货', qualification_excerpt: '', contact_excerpt: '', original_url: 'https://example.test/project' },
    procurement: {
    status: 'complete', scope_summary: '采购运输设备', items: [{ name: '运输设备', specification: '按附件', material: '', quantity: '2', unit: '套', delivery_period: '合同生效后60天内', delivery_location: '示例矿区', source_kind: 'attachment', source_document_id: 'doc-7', record_id: 42, page: 3, quote: evidence.quote }], delivery_period: '合同生效后60天内', delivery_location: '示例矿区', missing_fields: [], documents: [{ id: 'doc-7', record_id: 42, name: '技术附件.pdf', url: evidence.source_url, status: 'parsed', pages: 4, sha256: evidence.document_sha256, fetched_at: '2026-10-12T00:00:00Z' }], updated_at: '2026-10-12T00:00:00Z', attachment_status: 'available', attachment_note: '',
  },
  research_summary: { status: 'not_performed', source_count: 0 },
};

const detail = {
  ...currentProject,
  records: [{ id: 42, title: currentProject.title, body_text: '采购运输设备，详见技术附件。', original_url: 'https://example.test/project', canonical_url: 'https://example.test/project', parse_status: 'parsed' }],
  judgment: {
    id: 501, created_at: '2026-10-12T01:00:00Z', model: 'test-model', input_fingerprint: 'fingerprint', stale: false,
    decision: 'recommend', summary: '建议关注该项目。', demand_summary: '采购运输设备。', reasons: [], unknowns: [], next_actions: ['核对技术规格'], options: [], analysis_meta: { semantic_version: 'market_default_v1', decision_scope: 'market', company_match: 'not_evaluated' }, agent_run_id: 9001, report,
  },
  judgment_history: [
    { id: 500, created_at: '2026-10-11T01:00:00Z', model: 'old-model', input_fingerprint: 'old', stale: true, decision: 'consider', summary: '旧版判断仍可追溯。', demand_summary: '旧版需求摘要。', reasons: [{ text: '旧版理由', evidence: [evidence] }], unknowns: ['旧版待确认'], next_actions: ['旧版下一步'], options: [], report: null },
    { id: 501, created_at: '2026-10-12T01:00:00Z', model: 'test-model', input_fingerprint: 'fingerprint', stale: false, decision: 'recommend', summary: '当前动态报告。', demand_summary: '当前需求摘要。', reasons: [], unknowns: [], next_actions: [], options: [], report },
  ],
  decision_history: [], related_projects: [], versions: [], agent_run: { id: 9001, job_id: 1, engine: 'pi_sdk', status: 'succeeded', started_at: '2026-10-12T01:00:00Z', finished_at: '2026-10-12T01:01:00Z', error: null, steps: [], usage: { model_calls: 1, tool_calls: 0, input_tokens: 10, output_tokens: 20 }, limits: { max_turns: 4, max_tool_calls: 4, timeout_ms: 10000, max_output_tokens: 1000 }, investigation: { policy_version: 'test', status: 'not_performed', events: [], sources: [], unresolved: [] } },
  research_sources: [],
};

const settings = {
  version: 3,
  business: { company_name: '', scope_description: '', customer_role: '经销商', equipment_categories: '输送设备', mineral_types: '煤矿', demand_focus: '技改、备件', target_keywords: [], exclude_keywords: [], supported_products: '', capabilities: '', solution_examples: '', region_preferences: '' },
  ai: { base_url: '', model: '', enabled: false, has_api_key: false, engine: 'pi_sdk', sdk_ready: true },
  import: { path: '', auto_import: false, poll_seconds: 300 },
  analysis: { goal: '围绕矿山设备经销商和生产商的市场开发需求，调查项目事实，识别明确需求与有依据的潜在机会，说明参与条件、关键缺口和下一步行动，并提供可核对的来源。', semantic_version: 'market_default_v1', company_profile_present: false },
};

async function fulfillJson(route, body, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function run() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/snapshot')) return fulfillJson(route, { text: '', page_texts: [{ page: 1, text: '附件保存快照正文\n合同生效后60天内交货' }], sha256: 'attachment-v1', fetched_at: '2026-10-12T00:00:00Z' });
    if (url.pathname === '/api/health') return fulfillJson(route, { ok: true, app: 'sales-screening' });
    if (url.pathname === '/api/dashboard') return fulfillJson(route, { total_records: 2, total_projects: 2, gate_counts: { candidate: 1, review: 0, signal: 1 }, ai_counts: { succeeded: 2 }, ai_decision_counts: { recommend: 1 }, decision_counts: { new: 2 }, deadline_soon: [], deadline_soon_count: 0, data_updated_at: '2026-10-12T00:00:00Z', last_import_at: null, import_error: null, file_stale: false, import_pending: false, source_notes: '', ai_configured: false });
    if (url.pathname === '/api/projects' && route.request().method() === 'GET') return fulfillJson(route, { items: [currentProject, { ...currentProject, id: 43, title: '后续服务线索', gate_status: 'signal', opportunity_signals: [report.opportunities[1]], briefing: { ...currentProject.briefing, record_id: 43 }, record_ids: [43] }], total: 2, page: 1, page_size: 12 });
    if (url.pathname === '/api/projects/42') return fulfillJson(route, detail);
    if (url.pathname === '/api/projects/42/research/runs') return fulfillJson(route, { items: [], total: 0 });
    if (url.pathname === '/api/settings') return fulfillJson(route, settings);
    return fulfillJson(route, { detail: 'not implemented in frontend fixture' }, 404);
  });

  try {
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await page.getByTestId('opportunity-item').first().waitFor();
    assert(await page.getByRole('button', { name: /当前机会与线索/ }).count() === 1, 'default opportunity tab must include signals');
    assert(await page.getByText('明确采购', { exact: true }).count() > 0, 'explicit demand label is missing from the opportunity list');
    assert(await page.getByText('潜在线索', { exact: true }).count() > 0, 'potential opportunity label is missing from the opportunity list');

    await page.goto(`${baseUrl}/projects/42`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'Agent 研判报告', exact: true }).waitFor();
    assert((await page.locator('.agent-report').innerText()).includes('判断项'), 'dynamic table block did not render');
    assert((await page.locator('.agent-report-table-evidence').innerText()).includes('第 1 行依据'), 'table evidence is not associated with its row');
    assert((await page.locator('.agent-report').innerText()).includes('读取失败'), 'failed gap status did not render');
    assert(await page.getByText('需求摘要', { exact: true }).count() === 0, 'fixed demand summary duplicated a new Agent report');
    assert(await page.getByText('公告与附件提取资料', { exact: true }).count() === 1, 'raw extraction must be separately accessible');
    await page.screenshot({ path: path.join(outputDir, 'agent-report-detail.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(documentWidth <= 392, `mobile report overflow: ${documentWidth}`);
    await page.screenshot({ path: path.join(outputDir, 'agent-report-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '保存快照', exact: true }).first().click();
    await page.getByText('附件保存快照正文', { exact: false }).waitFor();
    assert(await page.getByRole('link', { name: '在线原文', exact: true }).count() > 0, 'online original link is missing beside the saved snapshot');
    await page.getByRole('button', { name: /关闭/ }).last().click().catch(() => {});

    await page.getByText('历史、版本与关联', { exact: true }).waitFor();
    await page.getByText('展开历史与关联', { exact: true }).click();
    await page.getByText(/历史判断（2）/).click();
    await page.getByText('展开旧报告全文', { exact: true }).click();
    assert(await page.getByText('旧版理由', { exact: true }).count() > 0, 'legacy judgment reasons were not expandable');
    await page.getByText('展开 Agent 报告全文', { exact: true }).click();
    assert(await page.locator('.detail-history-item .agent-report').count() === 1, 'current history report did not expand alongside the legacy report');
    const reportIds = await page.locator('.agent-report [id]').evaluateAll((elements) => elements.map((element) => element.id));
    assert(new Set(reportIds).size === reportIds.length, 'expanded reports must have unique heading identifiers');

    await page.goto(`${baseUrl}/settings`, { waitUntil: 'domcontentloaded' });
    await page.getByText('平台分析目标（系统默认）', { exact: true }).waitFor();
    for (const label of ['客户角色', '设备类别', '矿种 / 应用场景', '需求方向', '关注地区', '关注关键词']) assert(await page.locator('#customer-preferences').getByText(label, { exact: true }).count() > 0, `customer preference field missing: ${label}`);
    assert(await page.getByText('企业资料（可选）', { exact: true }).count() > 0, 'optional company profile section is missing');
    assert(await page.locator('#company-card').getByText('关注地区', { exact: true }).count() === 0, 'region preference must not be in the company profile card');
    assert(await page.locator('#company-card').getByText('关注关键词', { exact: true }).count() === 0, 'target keywords must not be in the company profile card');
    assert(await page.getByText('明确排除规则', { exact: true }).count() === 1, 'explicit exclusion section is missing');
    assert(await page.locator('#rule-config').getByText('关注关键词', { exact: true }).count() === 0, 'target keywords must not be in the exclusion rules');

    await page.screenshot({ path: path.join(outputDir, 'agent-report-browser.png'), fullPage: true });
    assert(pageErrors.length === 0, `browser page errors: ${pageErrors.join('; ')}`);
    console.log(JSON.stringify({ passed: true, outputDir, scenarios: ['signal-default', 'dynamic-report', 'attachment-snapshot', 'legacy-report', 'settings-three-layers'], pageErrors }));
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
