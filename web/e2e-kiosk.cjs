// 端到端联调：登录 → 今日课堂 → 课堂名单 → 设备令牌配置 → 签到 → 撤销
// 虚构资料，本机回环；依赖 5173(vite) 与 18081(business api)
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

    // 1. 未登录访问首页 → 跳登录页
    await page.goto('http://127.0.0.1:5173/');
    await page.waitForURL('**/login**');
    console.log('1 ok: 未登录重定向到登录页');

    // 2. 登录
    await page.locator('input[autocomplete="organization"]').fill('school-test');
    await page.locator('input[autocomplete="username"]').fill('owner-test');
    await page.locator('input[type="password"]').fill('e2e-test-password-12345');
    await page.locator('button[type="submit"]').click();
    await page.waitForURL('http://127.0.0.1:5173/');
    await page.locator('.lesson-card').first().waitFor();
    const cardText = await page.locator('.lesson-card').first().innerText();
    assert.match(cardText, /E2E演示班/);
    assert.match(cardText, /应到 3/);
    console.log('2 ok: 登录成功，今日课堂卡片显示 E2E演示班 应到3');

    // 3. 进入 09:00 的课（种子另有一节 07:00 已结束课，卡片顺序按 id 不定）
    await page.locator('.lesson-card', { hasText: '09:00' }).click();
    await page.waitForURL('**/lesson/**');
    await page.locator('.member').first().waitFor();
    assert.equal(await page.locator('.member').count(), 3);
    console.log('3 ok: 课堂名单 3 人');

    // 4. 配置设备令牌
    await page.locator('.device-banner input').fill(DEVICE_TOKEN);
    await page.locator('.device-banner button').click();
    await page.locator('.device-banner').waitFor({ state: 'detached' });
    console.log('4 ok: 设备令牌校验通过，配置条消失');

    // 5. 签到第一个待签到学生（余额断言用相对变化，种子里该生可能已扣过课时）
    const pending = page.locator('.member', { hasText: '待签到' }).first();
    const studentName = await pending.locator('.info b').innerText();
    const beforeText = await pending.innerText();
    const beforeUnits = Number(beforeText.match(/余 ([\d.]+) 课时/)[1]);
    await pending.click();
    await page.locator('.modal .primary').click();
    await page.locator('.toast').waitFor();
    const toast1 = await page.locator('.toast').innerText();
    assert.match(toast1, /签到成功|幂等去重/);
    const arrivedCard = page.locator('.member').filter({ hasText: studentName }).filter({ hasText: '已签到' });
    assert.match(await arrivedCard.innerText(), new RegExp(studentName));
    assert.match(await arrivedCard.innerText(), new RegExp('余 ' + (beforeUnits - 1) + ' 课时'));
    console.log('5 ok: 签到成功，余额 ' + beforeUnits + '→' + (beforeUnits - 1) + ' 课时');

    // 6. 撤销签到（同一人）
    await arrivedCard.click();
    await page.locator('.modal .danger').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /已撤销/.test(document.querySelector('.toast').textContent));
    const reverted = page.locator('.member', { hasText: studentName }).first();
    await page.waitForFunction((name) => {
      const cards = [...document.querySelectorAll('.member')];
      const mine = cards.find((c) => c.textContent.includes(name));
      return mine && !/已签到/.test(mine.textContent);
    }, studentName);
    assert.match(await reverted.innerText(), new RegExp('余 ' + beforeUnits + ' 课时'));
    console.log('6 ok: 撤销成功，课时退回（→' + beforeUnits + '）');

    // 7. 给另一名学生办理请假
    const leaveTarget = page.locator('.member', { hasText: '待签到' })
      .filter({ hasNotText: studentName }).first();
    const leaveName = await leaveTarget.locator('.info b').innerText();
    await leaveTarget.click();
    await page.locator('.modal .leave-btn').click();
    await page.locator('.modal .reason').fill('家中有事');
    await page.locator('.modal .primary').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /办理请假/.test(document.querySelector('.toast').textContent));
    const leaveCard = page.locator('.member').filter({ hasText: leaveName });
    assert.match(await leaveCard.innerText(), /已请假/);
    assert.match(await page.locator('.stats').innerText(), /请假 1/);
    console.log('7 ok: 请假办理成功，' + leaveName + ' 标记已请假，请假计数 1');

    // 8. 撤销请假，恢复待签到
    await leaveCard.click();
    await page.locator('.modal .reason').fill('确认到校');
    await page.locator('.modal .danger').click();
    await page.waitForFunction(() =>
      document.querySelector('.toast') && /已撤销/.test(document.querySelector('.toast').textContent));
    assert.match(await page.locator('.member').filter({ hasText: leaveName }).innerText(), /待签到/);
    console.log('8 ok: 撤假成功，恢复待签到');

    await page.screenshot({ path: 'e2e-kiosk.png' });
    assert.deepEqual(errors, []);
    console.log('E2E PASSED: 全链路无前端异常');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
