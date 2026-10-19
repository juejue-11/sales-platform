const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');

const typescriptPath = process.env.TYPESCRIPT_MODULE_PATH || path.resolve(__dirname, '../../frontend/node_modules/typescript');
if (!fs.existsSync(typescriptPath)) throw new Error('Install frontend dependencies or set TYPESCRIPT_MODULE_PATH.');
const typescript = require(typescriptPath);
const helperPath = path.resolve(__dirname, '../../frontend/src/utils/procurementGroups.ts');
const source = fs.readFileSync(helperPath, 'utf8');
const compiled = typescript.transpileModule(source, {
  compilerOptions: { target: typescript.ScriptTarget.ES2022, module: typescript.ModuleKind.CommonJS },
  fileName: helperPath,
}).outputText;
const helperModule = new Module(helperPath, module);
helperModule.filename = helperPath;
helperModule.paths = Module._nodeModulePaths(path.dirname(helperPath));
helperModule._compile(compiled, helperPath);
const { groupProcurementItems } = helperModule.exports;

const item = (overrides = {}) => ({
  name: ' 普碳中板 ',
  specification: '4mm',
  material: ' Q235B ',
  quantity: '1',
  unit: 't',
  delivery_period: '三日内供完',
  delivery_location: '本溪市',
  code: 'SRC-1',
  standard: 'GB/T 700',
  note: 'source note',
  source_document_id: 174,
  record_id: 7,
  page: 3,
  quote: '采购方案.doc#page=3',
  ...overrides,
});

(() => {
  const sourceItems = [
    item({ specification: '4mm', quantity: '0.1', code: 'A', page: 1 }),
    item({ specification: '5mm', quantity: '0.2', code: 'B', page: 2 }),
    item({ specification: '6mm', quantity: '1', code: 'C', page: 3 }),
    item({ specification: '8mm', quantity: '2', code: 'D', page: 4 }),
    item({ specification: '10mm', quantity: '10', code: 'E', page: 5 }),
    item({ specification: '12mm', quantity: '20', code: 'F', page: 6 }),
    item({ specification: '14mm', quantity: '30', code: 'G', page: 7 }),
    item({ specification: '16mm', quantity: '40', code: 'H', page: 8 }),
    item({ specification: '18mm', quantity: '5', code: 'I', page: 9 }),
    item({ specification: '20mm', quantity: '4.7', code: 'J', page: 10, standard: 'GB/T 1591' }),
  ];
  const originalObjects = [...sourceItems];
  const groups = groupProcurementItems(sourceItems);
  assert.equal(groups.length, 1, 'same normalized name/material should form one group');
  assert.equal(groups[0].key, JSON.stringify(['普碳中板', 'Q235B']));
  assert.equal(groups[0].name, '普碳中板');
  assert.equal(groups[0].material, 'Q235B');
  assert.deepEqual(groups[0].specifications, ['4mm', '5mm', '6mm', '8mm', '10mm', '12mm', '14mm', '16mm', '18mm', '20mm']);
  assert.equal(groups[0].specificationSummary, '4–20mm', 'numeric specifications should use an accurate range');
  assert.equal(groups[0].quantitySummary, '113 t', 'decimal totals must not contain binary floating noise');
  assert.deepEqual(groups[0].items, originalObjects, 'items retain source object identity and order');
  assert.equal(groups[0].items[9].standard, 'GB/T 1591', 'varying standards stay in the same group');
  assert.equal(groups[0].items[9].quote, '采购方案.doc#page=3', 'source provenance remains available after grouping');
  assert.deepEqual(sourceItems.map((sourceItem) => sourceItem.code), ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']);
})();

(() => {
  const sourceItems = [
    item({ specification: 'A型', quantity: '3', unit: 't', code: 'T' }),
    item({ specification: 'B型', quantity: '2', unit: '件', code: 'P' }),
    item({ specification: 'C型', quantity: '1-2', unit: '件', code: 'R' }),
    item({ specification: 'D型', quantity: '-4', unit: 't', code: 'N' }),
    item({ specification: 'E型', quantity: '', unit: 't', code: 'M' }),
    item({ specification: 'F型', quantity: '5', unit: '', code: 'U' }),
    item({ specification: 'G型', quantity: '6', unit: '未知', code: 'unknown-unit' }),
  ];
  const group = groupProcurementItems(sourceItems)[0];
  assert.equal(group.quantitySummary, '3 t；2 件；另有5项数量待核对');
  assert.equal(group.specificationSummary, 'A型、B型、C型等7种规格');
  assert.equal(group.items.length, sourceItems.length);
})();

(() => {
  const sourceItems = [
    item({ name: '', material: 'Q235B', code: 'missing-name-1' }),
    item({ name: '普碳中板', material: '', code: 'missing-material' }),
    item({ name: '', material: 'Q235B', code: 'missing-name-2' }),
    item({ name: '普碳中板', material: 'Q235B', code: 'complete' }),
  ];
  const groups = groupProcurementItems(sourceItems);
  assert.equal(groups.length, 4, 'rows missing a grouping field must remain conservatively separate');
  assert.deepEqual(groups.map((group) => group.items[0].code), sourceItems.map((sourceItem) => sourceItem.code));
  assert.equal(groups.reduce((count, group) => count + group.items.length, 0), sourceItems.length);
})();

(() => {
  const sourceItems = [
    item({ specification: '10×20mm', quantity: '1', code: 'shape-1' }),
    item({ specification: 'DN100×80', quantity: '2', code: 'shape-2' }),
    item({ specification: 'φ25×6m', quantity: '3', code: 'shape-3' }),
    item({ specification: '4-20mm', quantity: '4', code: 'shape-4' }),
  ];
  const group = groupProcurementItems(sourceItems)[0];
  assert.equal(group.specificationSummary, '10×20mm、DN100×80、φ25×6m等4种规格');
  assert.equal(group.quantitySummary, '10 t');
})();

console.log(JSON.stringify({ passed: true, cases: 4, sourceRowsRetained: true, decimalTotals: true }));
