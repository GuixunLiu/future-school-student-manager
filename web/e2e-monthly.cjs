// 端到端：管理员运营月报，新增/续费学生去重与活动明细
const assert = require('assert/strict');
const fs = require('fs');
const { chromium } = require('playwright');
const API = 'http://127.0.0.1:18081';
const seed = JSON.parse(fs.readFileSync('e2e-seed.json', 'utf8'));

async function activity(token, studentId, kind, occurredAt, ref) {
  const response = await fetch(API + '/v1/admin/student-activity', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ student_id: studentId, kind, occurred_at: occurredAt, source_ref: ref })
  });
  const data = await response.json();
  assert.ok(response.ok, response.status + ' ' + JSON.stringify(data));
}

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true
  });
  const errors = [];
  try {
    const login = await fetch(API + '/v1/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ organization: 'school-test', login: 'owner-test', password: 'e2e-test-password-12345' })
    });
    const loginData = await login.json();
    assert.ok(login.ok, JSON.stringify(loginData));
    const token = loginData.access_token;
    const now = new Date(Date.now() + 8 * 3600 * 1000);
    const month = now.getUTCFullYear() + '-' + String(now.getUTCMonth() + 1).padStart(2, '0');
    const day = String(now.getUTCDate()).padStart(2, '0');
    await activity(token, seed.students[0].id, 'new', month + '-01T09:00:00+08:00', 'e2e-month-new-1');
    await activity(token, seed.students[0].id, 'renewal', month + '-' + day + 'T08:00:00+08:00', 'e2e-month-renew-1');
    await activity(token, seed.students[1].id, 'renewal', month + '-' + day + 'T08:10:00+08:00', 'e2e-month-renew-2');

    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('http://127.0.0.1:5173/login');
    await page.locator('input[autocomplete="organization"]').fill('school-test');
    await page.locator('input[autocomplete="username"]').fill('owner-test');
    await page.locator('input[type="password"]').fill('e2e-test-password-12345');
    await page.locator('button[type="submit"]').click();
    await page.waitForURL('http://127.0.0.1:5173/');

    await page.goto('http://127.0.0.1:5173/teacher/monthly');
    await page.locator('.cards').waitFor();
    const cards = await page.locator('.cards').innerText();
    assert.match(cards, /新增学生\s*1\s*人（去重）/);
    assert.match(cards, /续费学生\s*2\s*人（去重）/);
    assert.match(cards, /续费记录\s*2\s*笔/);
    assert.equal(await page.locator('tbody tr').count(), 3);
    assert.match(await page.locator('.note').innerText(), /不会据此调整学生课时/);
    console.log('1 ok: 新增1人、续费2人、续费2笔，明细3条');

    await page.screenshot({ path: 'e2e-monthly.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('MONTHLY E2E PASSED');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
