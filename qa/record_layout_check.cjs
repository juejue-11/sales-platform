// Read-only layout acceptance for the continuous project record workspace.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

(async () => {
  const base = process.argv[2] || 'http://127.0.0.1:8766';
  const output = path.resolve(__dirname, '../data/record-layout-verification');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const metrics = [];
    for (const width of [1920, 1440, 390]) {
      await page.setViewportSize({width, height:1000});
      await page.goto(base + '/projects/166');
      await page.locator('.detail-hero h2').waitFor();
      await page.evaluate(() => document.fonts.ready);
      assert.equal(await page.locator('.detail-page .ant-card').count(), 0, 'Detail content must not remain a stack of cards');
      const metric = await page.evaluate(() => {
        const title = document.querySelector('.detail-hero h2');
        const summary = document.querySelector('.detail-summary-conclusion');
        const briefing = document.querySelector('.detail-briefing');
        const documentColumn = document.querySelector('.detail-workspace > .detail-main-column');
        const properties = document.querySelector('.detail-info-rail');
        return {
          width:innerWidth,
          scroll:document.documentElement.scrollWidth,
          titleHeight:title.getBoundingClientRect().height,
          lineHeight:parseFloat(getComputedStyle(title).lineHeight),
          summaryFont:summary ? parseFloat(getComputedStyle(summary).fontSize) : null,
          briefingBackground:getComputedStyle(briefing).backgroundColor,
          briefingBorder:getComputedStyle(briefing).borderLeftWidth,
          columnGap: properties.getBoundingClientRect().left - documentColumn.getBoundingClientRect().right,
        };
      });
      assert.ok(metric.scroll <= width + 2, 'No horizontal page overflow');
      if (width >= 1440) assert.ok(metric.titleHeight <= metric.lineHeight + 2, 'The real title should fit on one desktop line');
      if (width >= 1440) assert.ok(metric.columnGap <= 50, 'Properties must stay next to the document');
      if (metric.summaryFont) assert.ok(metric.summaryFont <= 18, 'AI narrative should read as body text');
      assert.equal(metric.briefingBorder, '0px', 'Procurement section must not have an outer card border');
      assert.ok((await page.locator('.detail-briefing').innerText()).includes('2套'));
      assert.ok((await page.locator('.detail-briefing').innerText()).includes('30个日历天'));
      if (width === 390) {
        const navigationPositions = await page.locator('.detail-page a[href^="#"]').evaluateAll(links => links.map(link => link.getBoundingClientRect()).filter(bounds => bounds.width > 0 && bounds.height > 0).map(bounds => bounds.top));
        assert.ok(navigationPositions.some(top => top >= 0 && top < 400), 'Section navigation must be near the title on mobile');
        const deadlinePositions = await page.getByText('2026-10-29 09:30', {exact:true}).evaluateAll(items => items.map(item => item.getBoundingClientRect()).filter(bounds => bounds.width > 0 && bounds.height > 0).map(bounds => bounds.top));
        assert.ok(deadlinePositions.some(top => top >= 0 && top < 400), 'Mobile deadline must not be buried beneath the document');
      }
      metrics.push(metric);
      await page.screenshot({path:path.join(output, `record-${width}.png`), fullPage:true});
      await page.screenshot({path:path.join(output, `record-${width}-viewport.png`)});
    }
    await page.setViewportSize({width:1440, height:1000});
    await page.goto(base + '/projects/166');
    await page.getByRole('heading', {name:'采购信息', exact:true}).waitFor();
    const qualification = page.locator('.detail-briefing-qualifications');
    await qualification.locator('summary').click();
    assert.ok(await qualification.getAttribute('open') !== null);
    assert.ok((await qualification.innerText()).includes('防爆合格证'));
    await qualification.locator('summary').click();
    assert.equal(await qualification.getAttribute('open'), null);
    const sectionLinks = page.locator('.detail-page a[href^="#"]');
    assert.ok(await sectionLinks.count() >= 3, 'Reading navigation must reach the main sections');
    for (const link of await sectionLinks.all()) {
      const href = await link.getAttribute('href');
      assert.ok(await page.locator(href).count(), `Missing anchor target ${href}`);
    }
    const lastLink = sectionLinks.last();
    await lastLink.click();
    assert.ok(new URL(page.url()).hash, 'Section navigation should work');
    await page.getByRole('button', {name:/返回机会池/}).click();
    await page.waitForURL(base + '/');
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log(JSON.stringify({passed:true, metrics, qualifiedSourceReadable:true, sectionNavigation:true, screenshots:output}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
