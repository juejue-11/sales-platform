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
    const measurements = [];
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await page.goto(base+'/projects/174');
      await page.getByRole('heading',{name:'采购信息',exact:true}).waitFor();
      const section = page.locator('.detail-procurement');
      await page.getByText('共143项物料',{exact:false}).first().waitFor();
      const text = await section.innerText();
      assert.ok(text.includes('普碳中板'));
      assert.ok(text.includes('三日内供完'));
      assert.ok(text.includes('本溪市'));
      assert.ok(text.includes('采购方案.doc'));
      assert.ok(text.includes('暂不支持解析'));
      assert.ok(!text.includes('未识别'));
      assert.equal(await page.locator('.detail-page .ant-card').count(),0);
      const links = await section.locator('a[href*="#page="]').evaluateAll(xs=>xs.map(x=>x.getAttribute('href')));
      assert.ok(links.some(url=>url.endsWith('#page=1')));
      const size = await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
      assert.ok(size.scroll<=width+2,JSON.stringify(size));
      measurements.push(size);
      await page.screenshot({path:path.join(output,`procurement-${width}.png`),fullPage:true});
      await page.screenshot({path:path.join(output,`procurement-${width}-viewport.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});
    if(process.argv.includes('--refresh')) {
      const [response] = await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/projects/174/documents/refresh') && r.request().method()==='POST',{timeout:65000}),
        page.getByRole('button',{name:/重新核对资料/}).click(),
      ]);
      assert.equal(response.status(),200);
      assert.equal((await response.json()).procurement.items.length,143);
      await page.getByRole('button',{name:/重新核对资料/}).waitFor();
    }
    await page.getByRole('textbox',{name:'搜索物料明细'}).fill('H型钢');
    await page.locator('.detail-procurement-table-wrap tbody tr.ant-table-row').first().waitFor();
    const names = await page.locator('.detail-procurement-item-name').allTextContents();
    assert.ok(names.length>0 && names.every(name=>name.includes('H型钢')),names.join(','));
    await page.getByRole('textbox',{name:'搜索物料明细'}).fill('');
    await page.locator('.detail-procurement-item-name').first().filter({hasText:'普碳中板'}).waitFor();
    assert.equal(errors.length,0,errors.join('\n'));
    console.log(JSON.stringify({passed:true,items:143,search:true,sourcePages:true,measurements,screenshots:output}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
