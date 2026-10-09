// Guard against changing quantities, specifications, or money while formatting excerpts.
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const format = await import(pathToFileURL(path.resolve(__dirname, '../frontend/src/components/opportunities/text.ts')).href);
  assert.equal(format.cleanOpportunityTitle('【西安研究院】煤矿井下压裂泵采购(GHY-26697)项目公告'), '煤矿井下压裂泵采购(GHY-26697)');
  assert.equal(format.cleanOpportunityTitle('选矿厂山特维克破碎机国产化备件6项公开招标招标公告'), '选矿厂山特维克破碎机国产化备件6项');
  const monetary = format.cleanQualificationExcerpt('投标人资格要求3.2本次要求注册资金200.0万元，报价12.5万元。3.3本次要求同类业绩。');
  assert.ok(monetary.includes('200.0万元') && monetary.includes('12.5万元'));
  assert.ok(monetary.includes('\n3.3本次'));
  const specification = format.cleanScopeExcerpt('采购范围：泵型号AB-1.2，压力1.5MPa，采购2套。');
  assert.ok(specification.includes('AB-1.2') && specification.includes('1.5MPa') && specification.includes('2套'));
  assert.equal(format.isTodayShanghai('不是日期'), false);
  assert.equal(format.isTodayShanghai(new Date().toISOString()), true);
  console.log(JSON.stringify({passed: true, quantitiesAndModelsPreserved: true, invalidDateHandled: true}));
})().catch(error => {console.error(error); process.exitCode = 1;});
