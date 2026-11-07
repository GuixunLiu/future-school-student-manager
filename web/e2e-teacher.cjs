// 端到端：教师工作台周报（管理员全校范围 + 教师本人范围）
const assert = require('assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true
  });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.on('pageerror', (e) => errors.push(e.message));

    async function login(user) {
      await page.goto('http://127.0.0.1:5173/login');
      await page.evaluate(() => sessionStorage.clear());
      await page.goto('http://127.0.0.1:5173/login');
      await page.locator('input[autocomplete="organization"]').fill('school-test');
      await page.locator('input[autocomplete="username"]').fill(user);
      await page.locator('input[type="password"]').fill(user === 'owner-test' ? 'e2e-test-password-12345' : 'teacher-password-12345');
      await page.locator('button[type="submit"]').click();
      await page.waitForURL('http://127.0.0.1:5173/');
    }

    // 1. 管理员视角：全校周报
    await login('owner-test');
    await page.goto('http://127.0.0.1:5173/teacher');
    await page.locator('.cards').waitFor();
    const cards = await page.locator('.cards').innerText();
    assert.match(cards, /67%/);        // 2/3 实到
    assert.match(cards, /应到人次\s*3/);
    assert.match(cards, /实到人次\s*2/);
    assert.match(await page.locator('.week-bar').innerText(), /全校范围/);
    const classTable = await page.locator('.panel').first().innerText();
    assert.match(classTable, /E2E演示班/);
    console.log('1 ok: 管理员周报 67% 3应到2实到，班级表含 E2E演示班');

    // 2. 教师视角：本人任课周报
    await login('teacher-e2e');
    await page.goto('http://127.0.0.1:5173/teacher');
    await page.locator('.cards').waitFor();
    assert.match(await page.locator('.cards').innerText(), /67%/);
    assert.match(await page.locator('.week-bar').innerText(), /我的任课范围/);
    console.log('2 ok: 教师本人周报 67%，范围标记正确');

    // 3. 上一周无数据
    await page.locator('.week-bar button').first().click();
    await page.waitForFunction(() =>
      document.querySelector('.cards') &&
      /—/.test(document.querySelector('.cards').innerText));
    console.log('3 ok: 上一周无课次显示 —');

    await page.screenshot({ path: 'e2e-teacher.png' });
    assert.deepEqual(errors, []);
    console.log('TEACHER E2E PASSED');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
