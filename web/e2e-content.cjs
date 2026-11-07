// 端到端：教师课堂内容、未到提醒与通知记录空状态
const assert = require('assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true
  });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', (e) => errors.push(e.message));

    await page.goto('http://127.0.0.1:5173/login');
    await page.locator('input[autocomplete="organization"]').fill('school-test');
    await page.locator('input[autocomplete="username"]').fill('owner-test');
    await page.locator('input[type="password"]').fill('e2e-test-password-12345');
    await page.locator('button[type="submit"]').click();
    await page.waitForURL('http://127.0.0.1:5173/');

    // 1. 课次按时间排序；明确选择 09:00 课，保存全班可见的课堂内容草稿。
    await page.goto('http://127.0.0.1:5173/teacher/content');
    await page.locator('.lesson-item').first().waitFor();
    assert.match(await page.locator('.lesson-item').first().innerText(), /07:00/);
    await page.locator('.lesson-item').filter({ hasText: '09:00' }).click();
    await page.locator('textarea').fill('本节重点：条件判断\n课后练习：完成练习一');
    await page.locator('input[type="radio"][value="roster"]').check();
    await page.locator('button.primary').click();
    await page.waitForFunction(() => document.querySelector('.save-row') && /草稿保存成功/.test(document.querySelector('.save-row').innerText));
    assert.equal(await page.locator('textarea').inputValue(), '本节重点：条件判断\n课后练习：完成练习一');
    console.log('1 ok: 课堂内容草稿保存并回显，全班范围');

    // 2. 已结束课有一名未到学生，可提交提醒；无家长接收人时明确为 0 条。
    await page.locator('.lesson-item').filter({ hasText: '07:00' }).click();
    await page.locator('button.remind').waitFor();
    assert.match(await page.locator('.absence-panel').innerText(), /王雨桐/);
    await page.locator('button.remind').click();
    await page.waitForFunction(() => /已确认未到名单，生成 0 条模拟通知/.test(document.body.innerText));
    console.log('2 ok: 未到名单正确，未伪造通知发送结果');

    // 3. 当前管理员没有本人通知，页面显示空状态和模拟声明。
    await page.locator('.tab').filter({ hasText: '通知记录' }).click();
    await page.locator('.empty').waitFor();
    assert.match(await page.locator('.simulation-banner').innerText(), /未真实发送微信/);
    assert.match(await page.locator('.empty').innerText(), /暂无通知记录/);
    console.log('3 ok: 通知记录空状态与模拟声明');

    await page.screenshot({ path: 'e2e-content.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('CONTENT E2E PASSED');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
