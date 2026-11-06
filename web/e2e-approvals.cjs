// 端到端：候选管理库的补签驳回、请假批准与教师只读视角
const assert = require('assert/strict');
const { chromium } = require('playwright');
const API = 'http://127.0.0.1:18109';
const PASSWORD = 'fictional-approval-test-only';

async function call(method, path, body, token) {
  const response = await fetch(API + path, {
    method,
    headers: {
      Accept: 'application/json',
      ...(token ? { Authorization: 'Bearer ' + token } : {}),
      ...(body ? { 'Content-Type': 'application/json' } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await response.json();
  assert.ok(response.ok, method + ' ' + path + ': ' + response.status + ' ' + JSON.stringify(data));
  return data;
}
async function tokenFor(login) {
  const result = await call('POST', '/v1/auth/login', { organization: 'fictional', login, password: PASSWORD });
  return result.access_token;
}
async function submitRequest(token, kind, ref) {
  return call('POST', '/v1/management/requests', {
    workspace_role: 'teacher', lesson_id: 'new-a', student_id: 'current-2',
    kind, reason: kind === 'makeup' ? '核实学生实际到课，申请补签' : '家庭事由，申请请假',
    request_ref: ref, expected_revision: 0
  }, token);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true
  });
  const errors = [];
  try {
    const teacherToken = await tokenFor('teacher');
    await submitRequest(teacherToken, 'makeup', 'e2e-makeup-request');
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', (e) => errors.push(e.message));

    async function login(user) {
      await page.goto('http://127.0.0.1:5173/login');
      await page.evaluate(() => sessionStorage.clear());
      await page.goto('http://127.0.0.1:5173/login');
      await page.locator('input[autocomplete="organization"]').fill('fictional');
      await page.locator('input[autocomplete="username"]').fill(user);
      await page.locator('input[type="password"]').fill(PASSWORD);
      await page.locator('button[type="submit"]').click();
      await page.waitForURL('http://127.0.0.1:5173/');
    }

    // 1. 管理员驳回补签，页面保留审批意见，不改变原课堂记录。
    await login('owner');
    await page.goto('http://127.0.0.1:5173/teacher/approvals');
    const makeup = page.locator('.request-card').filter({ hasText: '当前学生2' }).filter({ hasText: '补签' });
    await makeup.waitFor();
    await makeup.locator('button.reject-btn').click();
    await page.locator('.decision-modal textarea').fill('签到资料不足，驳回后补充核对');
    await page.locator('button.decision-submit').click();
    await page.waitForFunction(() => /驳回已保存，签到和课时未改变/.test(document.body.innerText));
    await makeup.locator('.status.rejected').waitFor();
    assert.match(await makeup.innerText(), /已驳回/);
    assert.match(await makeup.innerText(), /签到资料不足/);
    console.log('1 ok: 管理员驳回补签，意见与状态保留');

    // 2. 同一课堂重新申请请假并批准，状态与平板接收状态分开显示。
    await submitRequest(teacherToken, 'leave', 'e2e-leave-request');
    await page.locator('.heading-actions button').click();
    const leave = page.locator('.request-card').filter({ hasText: '当前学生2' }).filter({ hasText: '请假' });
    await leave.waitFor();
    await leave.locator('button.approve-btn').click();
    await page.locator('.decision-modal textarea').fill('已核实请假事由，同意');
    await page.locator('button.decision-submit').click();
    await page.waitForFunction(() => /批准已保存，等待平板接收；不代表平板已保存/.test(document.body.innerText));
    await leave.locator('.status.approved').waitFor();
    assert.match(await leave.innerText(), /已批准/);
    assert.match(await leave.innerText(), /尚无可接收的平板/);
    console.log('2 ok: 请假批准生效，未伪报平板已保存');

    // 3. 教师可查看自己的申请进度，但没有审批按钮。
    await login('teacher');
    await page.goto('http://127.0.0.1:5173/teacher/approvals');
    await page.locator('.request-card').first().waitFor();
    assert.equal(await page.locator('.request-card').count(), 2);
    assert.equal(await page.locator('.approve-btn').count(), 0);
    assert.equal(await page.locator('.reject-btn').count(), 0);
    console.log('3 ok: 教师只读自己的申请记录');

    await page.screenshot({ path: 'e2e-approvals.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('APPROVALS E2E PASSED');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
