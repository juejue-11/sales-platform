const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium, outputDir } = require('./browser_support.cjs');

const base = process.argv[2] || 'http://127.0.0.1:5173';
const screenshotDir = outputDir('workbench-mock');

const reading = {
  version: 1,
  material_version: 'material-v3',
  status: 'complete',
  stale: false,
  summary: '某能源企业采购高压变频器，用于山西项目现场，当前进入报价 / 投标阶段。',
  demand_type: 'confirmed',
  stage: 'bidding',
  buyer: '山西能源有限公司',
  region: '山西省',
  category: ['高压变频器'],
  sections: [
    { id: 'buyer', title: '谁在采购', requirements: [{ id: 'buyer-1', text: '采购人为山西能源有限公司。', explanation: '采购单位以公告正文为准。', sources: [{ source_kind: 'announcement', record_id: 101, quote: '采购人为山西能源有限公司。' }] }] },
    { id: 'scope', title: '买什么', requirements: [{ id: 'scope-1', text: '采购 2 套高压变频器，分为两个标包。', lot: '1、2', sources: [{ source_kind: 'attachment', record_id: 101, document_id: 201, page: 4, quote: '采购 2 套高压变频器，分为两个标包。' }] }] },
    { id: 'delivery', title: '怎样交付', requirements: [{ id: 'delivery-1', text: '交货地点为山西省项目现场。', sources: [{ source_kind: 'announcement', record_id: 101, quote: '交货地点为山西省项目现场。' }] }] },
    { id: 'qualification', title: '谁能参加', requirements: [{ id: 'qualification-1', text: '需提供同类项目业绩，联合体限制以附件为准。', sources: [{ source_kind: 'attachment', record_id: 101, document_id: 201, page: 8, quote: '需提供同类项目业绩，联合体限制以附件为准。' }], status: 'uncertain' }] },
    { id: 'schedule', title: '什么时候办理', requirements: [{ id: 'schedule-1', text: '提问截止时间以平台节点为准。', sources: [{ source_kind: 'announcement', record_id: 101, quote: '提问截止时间以平台节点为准。' }] }] },
    { id: 'materials', title: '需要什么费用与材料', requirements: [{ id: 'materials-1', text: '保证金和资格材料清单见招标文件。', sources: [{ source_kind: 'attachment', record_id: 101, document_id: 201, page: 12, quote: '保证金和资格材料清单见招标文件。' }] }] },
    { id: 'participation', title: '怎样参与', requirements: [{ id: 'participation-1', text: '通过电子采购平台提交报价文件。', sources: [{ source_kind: 'announcement', record_id: 101, quote: '通过电子采购平台提交报价文件。' }] }] },
    { id: 'special', title: '特殊要求与待确认事项', requirements: [{ id: 'special-1', text: '两个标包的技术边界仍需核对附件。', sources: [{ source_kind: 'attachment', record_id: 101, document_id: 201, page: 15, quote: '两个标包的技术边界仍需核对附件。' }], status: 'uncertain' }] },
  ],
  milestones: [
    { id: 'm1', kind: 'document_access', label: '文件获取截止', at: '2026-10-17T10:00:00+08:00', sources: [{ source_kind: 'announcement', record_id: 101, quote: '文件获取截止 2026 年 10 月 17 日。' }] },
    { id: 'm2', kind: 'submission', label: '报价文件提交截止', at: '2026-10-20T10:00:00+08:00', sources: [{ source_kind: 'announcement', record_id: 101, quote: '报价文件提交截止 2026 年 10 月 20 日。' }] },
  ],
  coverage: [{ label: '公告正文', status: 'read' }, { label: '招标文件', status: 'partial', note: '第 16 页待补充' }],
  conflicts: [],
  unresolved: ['联合体限制需要核对完整附件。'],
};

const project = {
  id: 1,
  title: '山西能源高压变频器采购',
  buyer: '山西能源有限公司',
  equipment: ['高压变频器'],
  category: ['高压变频器'],
  region: '山西省',
  procurement_stage: 'bidding',
  demand_type: 'confirmed',
  followup_stage: null,
  is_following: false,
  kind: 'procurement',
  source_id: 'mock-source',
  record_ids: [101],
  published_at: '2026-10-12T08:00:00+08:00',
  deadline_at: '2099-01-01T00:00:00+08:00',
  updated_at: '2026-10-14T09:30:00+08:00',
  gate_status: 'candidate',
  gate_reasons: [],
  ai_status: 'succeeded',
  ai_decision: 'recommend',
  ai_summary: '建议先核对技术附件与企业能力。',
  needs_review: false,
  decision: { status: 'new', owner: '', next_action: '', note: '', selected_option_id: null, updated_at: null },
  procurement_reading: reading,
  procurement: { status: 'complete', scope_summary: '\u9ad8\u538b\u53d8\u9891\u5668\u91c7\u8d2d', items: [{ record_id: 101, name: '\u9ad8\u538b\u53d8\u9891\u5668', specification: '10kV', quantity: 2, unit: '\u5957', delivery_period: '\u5408\u540c\u540e 60 \u5929', delivery_location: '\u5c71\u897f\u9879\u76ee\u73b0\u573a', source_kind: 'announcement', quote: '\u91c7\u8d2d\u9ad8\u538b\u53d8\u9891\u5668 2 \u5957' }], delivery_period: '\u5408\u540c\u540e 60 \u5929', delivery_location: '\u5c71\u897f\u9879\u76ee\u73b0\u573a', missing_fields: [], documents: [], updated_at: '2026-10-14T09:30:00+08:00' },
  records: [{ id: 101, title: '山西能源采购公告', body_text: '公告正文', original_url: 'https://example.test/notice/101', parse_status: 'parsed' }],
  judgment: { id: 9, created_at: '2026-10-14T09:00:00+08:00', model: 'mock', input_fingerprint: 'fp', stale: false, decision: 'recommend', summary: '建议核对附件中的资格例外和两包技术边界。', demand_summary: '高压变频器采购', reasons: [{ text: '采购阶段明确，且提交节点有来源。', evidence: [{ record_id: 101, quote: '报价文件提交截止 2026 年 10 月 20 日。' }] }], unknowns: ['联合体限制需要核对完整附件。'], next_actions: ['核对技术附件并确认供货能力。'], options: [], report: null },
  judgment_history: [], decision_history: [], related_projects: [], versions: [], research_sources: [], agent_run: null,
};

function followup(isFollowing = false) {
  return {
    stage: 'evaluating', disposition: 'following', owner: '', next_action: '核对技术附件', outcome: null,
    submitted_at: null, submission_channel: null, receipt: null, needs_review: false, is_following: isFollowing,
    plan: { id: 'plan-1', eligible: true, kind: 'procurement', procedure: '采购跟进', tasks: [{ id: 'task-1', title: '核对技术附件', basis: 'required', owner: '', due_at: '2026-10-16', prerequisites: ['取得附件'], materials: ['mat-1'] }], materials: [{ id: 'mat-1', task_id: 'task-1', name: '技术规格书', basis: 'required' }] },
    tasks: isFollowing ? [{ id: 'task-1', title: '核对技术附件', status: 'todo', required: true, basis: 'required', owner: '', due_at: '2026-10-16', prerequisites: [], materials: [], completion_note: '', skipped_reason: null }] : [],
    materials: isFollowing ? [{ id: 'mat-1', task_id: 'task-1', name: '技术规格书', basis: 'required', status: 'not_prepared', ai_check_status: 'not_checked', ai_check_note: '', provided_at: null, note: '', plan_id: 'plan-1' }] : [], events: [], note: null, changed_tasks: [],
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = []
  let activeFollowup = followup(false)
  const payloads = { followup: [], adopt: [], task: [], material: [], readingRetry: [] };
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/dashboard') return route.fulfill({ json: { total_records: 1, total_projects: 1, gate_counts: { candidate: 1, review: 0, signal: 0 }, ai_counts: {}, ai_decision_counts: { recommend: 1 }, decision_counts: {}, followup_count: 0, deadline_soon: [], deadline_soon_count: 0, data_updated_at: project.updated_at, import_error: null, file_stale: false, import_pending: false, source_notes: '仅覆盖已接入来源。', ai_configured: true } });
    if (path === '/api/projects' && route.request().method() === 'GET') return route.fulfill({ json: { items: [project], total: 1, page: 1, page_size: 12 } });
    if (path === '/api/projects/1' && route.request().method() === 'GET') return route.fulfill({ json: project });
    if (path === '/api/projects/1/followup' && route.request().method() === 'GET') return route.fulfill({ json: activeFollowup });
    if (path === '/api/projects/1/followup/adopt' && route.request().method() === 'POST') { const body = route.request().postDataJSON(); payloads.adopt.push(body); assert.deepEqual(body, { plan_id: 'plan-1' }); activeFollowup = followup(true); return route.fulfill({ json: activeFollowup }); }
    if (path === '/api/projects/1/followup' && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      payloads.followup.push(body);
      assert.ok(Object.keys(body).every((key) => ['is_following', 'stage', 'disposition', 'owner', 'next_action', 'outcome', 'note', 'submitted_at', 'submission_channel', 'receipt', 'reason'].includes(key)));
      const hasSubmittedAt = Object.prototype.hasOwnProperty.call(body, 'submitted_at');
      const hasSubmissionChannel = Object.prototype.hasOwnProperty.call(body, 'submission_channel');
      const hasReceipt = Object.prototype.hasOwnProperty.call(body, 'receipt');
      if (body.stage === 'submitted') {
        assert.ok(hasSubmittedAt && typeof body.submitted_at === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(body.submitted_at), 'submitted confirmation must contain a timezone-bearing submitted_at');
        assert.ok(hasSubmissionChannel && typeof body.submission_channel === 'string' && body.submission_channel.trim(), 'submitted confirmation must contain submission_channel');
      } else {
        assert.equal(hasSubmittedAt, false, 'submitted_at cannot be saved outside submitted confirmation');
        assert.equal(hasSubmissionChannel, false, 'submission_channel cannot be saved outside submitted confirmation');
        assert.equal(hasReceipt, false, 'receipt cannot be saved outside submitted confirmation');
      }
      if (body.stage === 'awaiting_result') assert.ok(activeFollowup.submitted_at && activeFollowup.submission_channel, 'awaiting_result requires an existing submission');
      activeFollowup = { ...activeFollowup, ...body, is_following: true };
      return route.fulfill({ json: activeFollowup });
    }
    if (path.startsWith('/api/projects/1/followup/tasks/') && route.request().method() === 'PATCH') { const body = route.request().postDataJSON(); payloads.task.push(body); assert.ok(Object.keys(body).every((key) => ['title', 'basis', 'owner', 'due_at', 'prerequisites', 'materials', 'status', 'completion_note', 'skipped_reason', 'reason'].includes(key))); return route.fulfill({ json: activeFollowup }); }
    if (path.startsWith('/api/projects/1/followup/materials/') && route.request().method() === 'PATCH') { const body = route.request().postDataJSON(); payloads.material.push(body); assert.deepEqual(Object.keys(body), ['status']); return route.fulfill({ json: activeFollowup }); }
    if (path === '/api/status' && route.request().method() === 'GET') return route.fulfill({ json: { data_updated_at: project.updated_at, last_import_at: null, file_stale: false, import_pending: false, import_error: null, source_notes: 'mock', imports: [], jobs: [], job_counts: {}, reading_jobs: [{ id: 71, project_id: 1, status: 'failed', attempts: 2, error: 'mock reading failure', created_at: project.updated_at, updated_at: project.updated_at }], reading_job_counts: { failed: 1 }, ai_configured: true } });
    if (path === '/api/projects/1/reading' && route.request().method() === 'POST') { payloads.readingRetry.push(route.request().postDataJSON()); return route.fulfill({ json: { message: '已提交阅读重试' } }); }
    if (path === '/api/projects/1/reading/corrections' && route.request().method() === 'GET') return route.fulfill({ json: { corrections: [] } });
    if (path === '/api/projects/1/documents/refresh' && route.request().method() === 'POST') return route.fulfill({ json: { procurement: null, message: 'ok' } });
    return route.fulfill({ status: 404, json: { detail: 'mock route not found' } });
  });

  await page.goto(`${base}/`);
  await page.getByRole('heading', { name: '先看清需求，再决定跟进' }).waitFor();
  assert.equal(await page.getByTestId('opportunity-item').count(), 1);
  const cardText = await page.getByTestId('opportunity-item').innerText();
  assert.ok(cardText.includes('明确采购'));
  assert.ok(cardText.includes('提交截止') && cardText.includes('2026-10-20 10:00'));
  assert.ok(!cardText.includes('2099-01-01'));
  await page.screenshot({ path: path.join(screenshotDir, 'home.png'), fullPage: true });
  await page.getByRole('button', { name: '高级筛选' }).click();
  for (const label of ['设备 / 服务', '地区', '采购单位', '采购阶段', '关键时间']) assert.ok(await page.getByText(label, { exact: true }).count() >= 1, label);
  await page.getByLabel('设备 / 服务').fill('高压变频器');
  await page.getByLabel('地区').fill('山西');
  await page.getByLabel('采购单位').fill('山西能源');
  await page.getByTestId('opportunity-item').click();
  await page.getByRole('tab', { name: '采购内容', exact: true }).click();
  await page.locator('.reading-detail-header').waitFor();
  assert.equal(await page.getByRole('tab', { name: '采购内容', exact: true }).getAttribute('aria-selected'), 'true');
  const detailText = await page.getByTestId('procurement-reading').innerText();
  assert.ok(!detailText.includes('完整要求表'));
  assert.ok((await page.locator('.reading-key-facts').innerText()).includes('2026-10-20 10:00'));
  assert.equal(await page.locator('.reading-category-list [role="tab"]').count(), reading.sections.length);
  assert.equal(await page.locator('.reading-detail .reading-requirement').count(), 1);
  await page.locator('.reading-category-list').getByRole('tab', { name: /买什么/ }).click();
  assert.ok((await page.getByTestId('procurement-reading').innerText()).includes('采购 2 套高压变频器'));
  await page.screenshot({ path: path.join(screenshotDir, 'procurement-reading.png'), fullPage: true });
  await page.getByRole('tab', { name: '跟进记录', exact: true }).click();
  await page.getByText('尚未加入跟进').waitFor();
  const previewText = await page.getByTestId('followup-panel').innerText();
  assert.ok(previewText.includes('核对技术附件'));
  assert.ok(previewText.includes('技术规格书'));
  assert.ok(!previewText.includes('mat-1'));
  await page.getByRole('button', { name: '加入我的跟进' }).click();
  await page.getByText('跟进中').first().waitFor();
  assert.ok((await page.getByTestId('followup-panel').innerText()).includes('不等于已提交'));
  const submissionFields = page.locator('.followup-submission input');
  await submissionFields.nth(0).fill('2026-10-20T10:00');
  await submissionFields.nth(1).fill('电子采购平台');
  await submissionFields.nth(2).fill('R-2026-10-20');
  assert.equal(payloads.followup.length, 0, 'submission drafts must not save on blur');
  await page.locator('.followup-task .followup-status-select').click();
  await page.getByText('已完成', { exact: true }).last().click();
  await page.locator('.ant-modal textarea').fill('已核对技术附件');
  await page.locator('.ant-modal').getByRole('button').last().click();
  await page.waitForTimeout(100);
  assert.deepEqual(payloads.task.at(-1), { status: 'done', completion_note: '已核对技术附件' });
  assert.equal(await submissionFields.nth(0).inputValue(), '2026-10-20T10:00');
  assert.equal(await submissionFields.nth(1).inputValue(), '电子采购平台');
  assert.equal(await submissionFields.nth(2).inputValue(), 'R-2026-10-20');
  await page.locator('.followup-material .ant-select').click();
  await page.getByText('待核对', { exact: true }).last().click();
  await page.waitForTimeout(100);
  assert.deepEqual(payloads.material.at(-1), { status: 'pending_check' });
  assert.equal(await submissionFields.nth(0).inputValue(), '2026-10-20T10:00');
  assert.equal(await submissionFields.nth(1).inputValue(), '电子采购平台');
  assert.equal(await submissionFields.nth(2).inputValue(), 'R-2026-10-20');
  await page.locator('.followup-overview .ant-select').first().click();
  await page.getByText('已提交', { exact: true }).last().click();
  await page.getByText('确认真实提交', { exact: true }).waitFor();
  await page.getByRole('dialog').getByRole('button', { name: '确认已提交' }).click();
  await page.waitForTimeout(100);
  const submittedPayload = payloads.followup.at(-1);
  assert.equal(payloads.followup.length, 1);
  assert.equal(submittedPayload.stage, 'submitted');
  assert.match(submittedPayload.submitted_at, /(?:Z|[+-]\d{2}:\d{2})$/);
  assert.equal(submittedPayload.submission_channel, '电子采购平台');
  assert.equal(submittedPayload.receipt, 'R-2026-10-20');
  assert.equal(await submissionFields.nth(0).isDisabled(), true);
  assert.equal(await submissionFields.nth(1).isDisabled(), true);
  await page.locator('.followup-overview .ant-select').first().click();
  await page.getByText('等待结果', { exact: true }).last().click();
  await page.waitForTimeout(100);
  assert.deepEqual(payloads.followup.at(-1), { stage: 'awaiting_result' });
  await page.screenshot({ path: path.join(screenshotDir, 'followup.png'), fullPage: true });
  await page.goto(`${base}/operations`);
  await page.getByRole('heading', { name: '处理状态' }).waitFor();
  assert.ok(await page.getByText('采购阅读任务', { exact: false }).count() >= 1);
  assert.ok(await page.getByText('mock reading failure', { exact: true }).count() >= 1);
  await page.getByRole('button', { name: '重试该项目' }).click();
  await page.waitForTimeout(100);
  assert.deepEqual(payloads.readingRetry.at(-1), { force: true });
  assert.equal(errors.length, 0, errors.join('\n'));
  assert.equal(payloads.adopt.length, 1);
  assert.equal(payloads.followup.length, 2);
  assert.equal(payloads.task.length, 1);
  assert.equal(payloads.material.length, 1);
  console.log(JSON.stringify({ passed: true, filters: ['category', 'region', 'buyer', 'stage', 'key_time'], sharedReading: true, tabs: 3, followupPreviewAndAdopt: true, submissionConfirmationRoundtrip: true, payloads, screenshots: screenshotDir }));
  await browser.close();
})().catch(async (error) => { console.error(error); process.exitCode = 1; });
