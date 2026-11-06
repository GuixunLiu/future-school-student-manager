'use strict';

// Run against assets extracted from the built APK.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const acorn = require(process.env.ACORN_MODULE || 'acorn');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assets = path.resolve(process.argv[2]);
const pageSource = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
const origin = 'https://appassets.androidplatform.net';
assert.match(pageSource, /classboard-compat\.js/);
for (const [, script] of pageSource.matchAll(/<script src="([^"]+\.js)"/g)) {
  try {
    acorn.parse(fs.readFileSync(path.join(assets, script), 'utf8'), {ecmaVersion: 2016});
  } catch (error) {
    throw new Error(script + ': ' + error.message);
  }
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH, headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1024, height: 600}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route(origin + '/assets/**', async route => {
      const name = new URL(route.request().url()).pathname.slice('/assets/'.length);
      const asset = path.join(assets, name);
      if (!fs.existsSync(asset)) return route.fulfill({status: 404});
      if (name === 'classboard-compat.css') {
        const body = fs.readFileSync(asset, 'utf8').replace(/@supports not \(.*\) \{/g, '@supports (display: flex) {');
        return route.fulfill({body, contentType: 'text/css'});
      }
      await route.fulfill({path: asset});
    });
    await page.addInitScript(() => {
      delete Object.entries;
      delete Object.values;
      delete Object.fromEntries;
      delete Array.prototype.flatMap;
      delete String.prototype.padStart;
      delete Element.prototype.append;
      delete Element.prototype.prepend;
      delete Element.prototype.replaceChildren;
      delete NodeList.prototype.forEach;
      delete Blob.prototype.text;
      window.PointerEvent = undefined;
      const fixture = {
        students: [
          {id: 1, name: '兼容测试甲', studentNo: '700001', className: '班牌测试班', hours: 5},
          {id: 2, name: '兼容测试乙', studentNo: '700002', className: '班牌测试班', hours: 6}
        ],
        classes: [{id: 3, name: '班牌测试班'}],
        attendance: [], transactions: [], settings: {cameraPositionConfigured: true}
      };
      if (!localStorage.getItem('mashang_offline_v1')) {
        localStorage.setItem('mashang_offline_v1', JSON.stringify(fixture));
      }
    });
    await page.goto(origin + '/assets/index.html');
    await page.waitForFunction(() => window.__futureSchoolAppStarted);
    assert.equal(await page.locator('#appStartup').isVisible(), false);
    assert.equal(await page.locator('#navList').isVisible(), false);
    assert.equal(await page.locator('.space-kiosk-home').evaluate(node => getComputedStyle(node).display), 'flex');
    for (const [width, height] of [[1920, 1008], [1280, 672], [960, 504], [1024, 552], [640, 336], [1280, 750]]) {
      await page.setViewportSize({width, height});
      for (const selector of ['.space-kiosk-intro h1', '#adminAccessBtn', '[data-check="人脸"]', '[data-check="姓名"]', '[data-check="学号"]']) {
        const rect = await page.locator(selector).boundingBox();
        assert.ok(rect && rect.width > 0 && rect.height > 0, selector + ' at ' + width);
        assert.ok(rect.x >= -1 && rect.y >= -1 && rect.x + rect.width <= width + 1 &&
          rect.y + rect.height <= height + 1, selector + ' clipped at ' + width + '×' + height + ': ' + JSON.stringify(rect));
      }
      const face = await page.locator('[data-check="人脸"]').boundingBox();
      assert.ok(face.height >= height * 0.4, 'main control fills too little of the display');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    await page.setViewportSize({width: 1024, height: 600});
    for (const [method, query, outcome] of [
      ['姓名', '兼容测试甲', '.checkin-success-content'],
      ['学号', '700002', '.checkin-success-content'],
      ['学号', '700002', '#toast:not(.hidden)']
    ]) {
      await page.locator('[data-check="' + method + '"]').click();
      await page.locator('#checkinForm input').fill(query);
      await page.locator('#checkinForm button[type="submit"]').click();
      await page.locator(outcome).waitFor();
      await page.reload();
      await page.waitForFunction(() => window.__futureSchoolAppStarted);
    }
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('mashang_offline_v1')));
    assert.deepEqual(saved.students.map(student => student.hours), [4, 5]);
    assert.equal(saved.attendance.length, 2);
    assert.equal(saved.transactions.length, 2);
    await page.setViewportSize({width: 1920, height: 1008});
    await page.evaluate(async () => {
      state.settings.adminPattern = await createPatternRecord([0, 1, 4, 7]);
      state.settings.adminAuthMode = 'pattern';
    });
    await page.locator('#adminAccessBtn').click();
    await page.locator('#patternBoard').waitFor();
    await page.evaluate(() => {
      const modal = document.getElementById('modal');
      return Promise.all(modal.getAnimations({subtree: true}).map(animation => animation.finished));
    });
    const points = await page.locator('.pattern-node').evaluateAll(nodes =>
      nodes.map(node => {
        const {width, height, x, y} = node.getBoundingClientRect();
        return {width, height, x: Math.round(x + width / 2), y: Math.round(y + height / 2)};
      })
    );
    assert.equal(new Set(points.map(point => point.x)).size, 3);
    assert.equal(new Set(points.map(point => point.y)).size, 3);
    assert.ok(points.every(point => point.width >= 100 && point.height >= 100));
    await page.evaluate(points => {
      const board = document.getElementById('patternBoard');
      for (const [type, index] of [['touchstart', 0], ['touchmove', 1], ['touchmove', 4], ['touchend', 7]]) {
        const point = points[index];
        const touch = new Touch({target: board, identifier: 1, clientX: point.x, clientY: point.y});
        board.dispatchEvent(new TouchEvent(type, {
          bubbles: true, cancelable: true, touches: type === 'touchend' ? [] : [touch], changedTouches: [touch]
        }));
      }
    }, points);
    await page.waitForFunction(() => isAdminAuthenticated);
    assert.deepEqual(errors, []);
    console.log('ES2016, six landscape sizes, touch pattern unlock, and name/number check-in with persistence and deduplication passed.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
