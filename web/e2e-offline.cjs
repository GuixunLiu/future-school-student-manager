// 端到端：离线签到 → 队列撤销 → 补传 → 缓存兜底
const assert = require('assert/strict');
const { chromium } = require('playwright');

const DEVICE_TOKEN = require('./e2e-seed.json').device_token;

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true
  });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.on('pageerror', (e) => errors.push(e.message));

    // 登录 + 进入 09:00 课堂 + 配设备令牌
    await page.goto('http://127.0.0.1:5173/login');
    await page.locator('input[autocomplete="organization"]').fill('school-test');
    await page.locator('input[autocomplete="username"]').fill('owner-test');
    await page.locator('input[type="password"]').fill('e2e-test-password-12345');
    await page.locator('button[type="submit"]').click();
    await page.waitForURL('http://127.0.0.1:5173/');
    await page.locator('.lesson-card', { hasText: '09:00' }).click();
    await page.locator('.member').first().waitFor();
    await page.locator('.device-banner input').fill(DEVICE_TOKEN);
    await page.locator('.device-banner button').click();
    await page.locator('.device-banner').waitFor({ state: 'detached' });

    const member = page.locator('.member', { hasText: '待签到' }).first();
    const name = await member.locator('.info b').innerText();

    // 1. 模拟断网（只断设备事件通道），签到 → 入队
    await page.route('**/v1/device/**', (route) => route.abort());
    await member.click();
    await page.locator('.modal .primary').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /离线记录/.test(document.querySelector('.toast').textContent));
    assert.match(await page.locator('.member').filter({ hasText: name }).innerText(), /待补传/);
    assert.match(await page.locator('.flush-btn').innerText(), /补传 1 条/);
    console.log('1 ok: 断网签到入队，' + name + ' 标记待补传，补传按钮显示 1 条');

    // 2. 撤销未上传的签到（不产生服务器记录）
    await page.locator('.member').filter({ hasText: name }).click();
    await page.locator('.modal .danger').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /未上传/.test(document.querySelector('.toast').textContent));
    assert.match(await page.locator('.member').filter({ hasText: name }).innerText(), /待签到/);
    assert.equal(await page.locator('.flush-btn').count(), 0);
    console.log('2 ok: 队列内撤销成功，恢复待签到，队列清空');

    // 3. 再次断网签到 → 恢复网络 → 手动补传
    await page.locator('.member').filter({ hasText: name }).click();
    await page.locator('.modal .primary').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /离线记录/.test(document.querySelector('.toast').textContent));
    await page.unroute('**/v1/device/**');
    await page.locator('.flush-btn').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /已补传 1 条/.test(document.querySelector('.toast').textContent));
    await page.waitForFunction((n) => {
      const cards = [...document.querySelectorAll('.member')];
      const mine = cards.find((c) => c.textContent.includes(n));
      return mine && /已签到/.test(mine.textContent);
    }, name);
    console.log('3 ok: 补传成功，' + name + ' 服务器侧已签到');

    // 4. 缓存兜底：断掉 workspace 接口，重进课堂 → 离线横幅 + 缓存名单
    await page.route('**/v1/lessons/*/workspace', (route) => route.abort());
    await page.locator('.lesson-header .link').first().click(); // 返回首页
    await page.locator('.lesson-card', { hasText: '09:00' }).click();
    await page.locator('.offline-banner').waitFor();
    assert.equal(await page.locator('.member').count(), 3);
    assert.match(await page.locator('.offline-banner').innerText(), /本机缓存/);
    console.log('4 ok: workspace 断网时缓存兜底，名单完整 + 离线横幅');

    await page.unroute('**/v1/lessons/*/workspace');
    await page.screenshot({ path: 'e2e-offline.png' });
    assert.deepEqual(errors, []);
    console.log('OFFLINE E2E PASSED');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
