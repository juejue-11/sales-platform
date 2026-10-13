const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

(async()=>{
  const base = process.argv[2] || 'http://127.0.0.1:8766';
  const output = path.resolve(__dirname,'../data/procurement-qa/174');
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,channel:'msedge'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror',error=>errors.push(error.message));
    const detailResponse = await page.request.get(base+'/api/projects/174');
    assert.equal(detailResponse.status(),200);
    const initialDetail = await detailResponse.json();
    const hasUnsupportedDocument = initialDetail.procurement.documents.some(document=>document.status==='unsupported');
    const measurements = [];
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await page.goto(base+'/projects/174');
      await page.getByRole('heading',{name:'采购信息',exact:true}).waitFor();
      const section = page.locator('.detail-procurement');
      await page.getByText('共 143 条采购明细',{exact:true}).waitFor();
      const text = await section.innerText();
      assert.ok(text.includes('普碳中板'));
      assert.ok(text.includes('三日内供完'));
      assert.ok(text.includes('本溪市'));
      assert.ok(text.includes('21 组物料'));
      assert.ok(text.includes('交货地点'));
      assert.ok(text.includes('具体收货地址待确认'));
      assert.ok(!text.includes('同类物料汇总'));
      assert.ok(!text.includes('已读取部分采购资料'));
      assert.ok(!text.includes('共143项物料，包括'));
      assert.equal(await section.getByRole('heading',{name:'采购范围',exact:true}).count(),1);
      assert.ok(!text.includes('采购范围明细'));
      assert.ok(text.includes('采购方案.doc'));
      if(hasUnsupportedDocument) assert.ok(text.includes('暂不支持解析'));
      else assert.ok(text.includes('已解析'));
      assert.ok(!text.includes('未识别'));
      assert.equal(await page.locator('.detail-page .ant-card').count(),0);
      const summaryRows = section.locator('.detail-procurement-group-table > .ant-table-wrapper > .ant-spin-nested-loading > .ant-spin-container > .ant-table tbody > tr.ant-table-row');
      assert.equal(await summaryRows.count(),8);
      assert.equal(await section.locator('.detail-procurement-item-name').filter({hasText:'普碳中板'}).count(),1);
      const plateRow = section.locator('tr.ant-table-row').filter({has:page.locator('.detail-procurement-item-name',{hasText:'普碳中板'})});
      assert.ok((await plateRow.innerText()).includes('113 t'));
      assert.ok((await plateRow.innerText()).includes('4–20mm'));
      await page.getByRole('button',{name:'展开普碳中板 Q235B明细',exact:true}).click();
      assert.equal(await section.locator('.detail-procurement-expanded tbody tr.ant-table-row').count(),10);
      const links = await section.locator('a[href*="#page="]').evaluateAll(xs=>xs.map(x=>x.getAttribute('href')));
      assert.ok(links.some(url=>url.endsWith('#page=1')));
      assert.ok((await section.locator('.detail-procurement-expanded').innerText()).includes('C0863808'));
      await section.locator('.detail-procurement-expanded').screenshot({path:path.join(output,`expanded-plate-${width}.png`)});
      await page.getByRole('button',{name:'收起普碳中板 Q235B明细',exact:true}).click();
      const size = await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
      assert.ok(size.scroll<=width+2,JSON.stringify(size));
      if(width===390) {
        const nameBounds = await section.locator('.detail-procurement-item-name').first().boundingBox();
        assert.ok(nameBounds.x>=0 && nameBounds.x+nameBounds.width<=width,'Mobile material name must remain visible after collapsing details');
        const actionBounds = await page.getByRole('button',{name:'展开普碳中板 Q235B明细',exact:true}).boundingBox();
        assert.ok(actionBounds.x>=0 && actionBounds.x+actionBounds.width<=width,'Mobile detail action must be visible beside material name');
      }
      measurements.push(size);
      await page.screenshot({path:path.join(output,`procurement-${width}.png`),fullPage:true});
      await section.evaluate(element=>window.scrollTo(0,element.getBoundingClientRect().top+window.scrollY-16));
      await page.screenshot({path:path.join(output,`procurement-${width}-viewport.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});
    if(process.argv.includes('--refresh')) {
      const [response] = await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/projects/174/documents/refresh') && r.request().method()==='POST',{timeout:65000}),
        page.getByRole('button',{name:'更新公开资料',exact:true}).click(),
      ]);
      assert.equal(response.status(),200);
      assert.equal((await response.json()).procurement.items.length,143);
      await page.getByRole('button',{name:'更新公开资料',exact:true}).waitFor();
    }
    await page.getByRole('textbox',{name:'搜索物料明细'}).fill('H型钢');
    await page.locator('.detail-procurement-table-wrap tbody tr.ant-table-row').first().waitFor();
    const names = await page.locator('.detail-procurement-item-name').allTextContents();
    assert.ok(names.length>0 && names.every(name=>name.includes('H型钢')),names.join(','));
    await page.getByRole('textbox',{name:'搜索物料明细'}).fill('');
    await page.locator('.detail-procurement-item-name').first().filter({hasText:'普碳中板'}).waitFor();
    await page.getByRole('textbox',{name:'搜索物料明细'}).fill('C0863808');
    await page.getByText('找到 1 组，1 条明细；',{exact:false}).waitFor();
    assert.ok((await page.locator('.detail-procurement-group-table').innerText()).includes('5 t'));
    await page.getByRole('button',{name:'展开普碳中板 Q235B明细',exact:true}).click();
    assert.equal(await page.locator('.detail-procurement-expanded tbody tr.ant-table-row').count(),1);
    assert.ok((await page.locator('.detail-procurement-expanded').innerText()).includes('C0863808'));
    assert.equal(errors.length,0,errors.join('\n'));
    console.log(JSON.stringify({passed:true,items:143,groups:21,groupExpansion:true,search:true,sourcePages:true,labeledDelivery:true,measurements,screenshots:output}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
