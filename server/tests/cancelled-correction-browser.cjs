'use strict';
// Real Chromium against disposable PostgreSQL; fictional records only.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'outputs/cancelled-correction-browser'),stop=crypto.randomBytes(24).toString('hex');
const base='http://127.0.0.1:18127',adminToken='wxu_'+'a'.repeat(43),deviceToken='wxd_'+'d'.repeat(43);
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const child=spawn(path.join(root,'server/.test-venv/Scripts/python.exe'),[path.join(__dirname,'serve_cancelled_correction_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:'server;server/tests',CANCELLED_FIXTURE_STOP:stop,PYTHONIOENCODING:'utf-8'},stdio:['ignore','pipe','pipe'],windowsHide:true});
 let browser,err='';child.stderr.on('data',b=>err+=b);
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout '+err.slice(-2000))),30000);child.stdout.on('data',b=>{if(b.toString().includes('FICTIONAL_CANCELLED_READY')){clearTimeout(timer);resolve();}});child.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+' '+err.slice(-2000)));});});
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1365,height:1000}}),errors=[],makeupRequests=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&/\/v1\/admin\/lessons\/[^/]+\/makeup$/.test(request.url()))makeupRequests.push(request.postDataJSON());});
  await page.goto(base+'/runtime');
  await page.getByLabel('机构标识',{exact:true}).fill('fiction');
  await page.getByLabel('账号',{exact:true}).fill('fiction-admin-a');
  await page.getByLabel('密码',{exact:true}).fill('Fictional admin password 2026!');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'课表管理',exact:true}).waitFor();
  await page.getByRole('button',{name:'课表管理',exact:true}).click();
  await page.getByLabel('课表日期',{exact:true}).fill('1970-01-01');
  await Promise.all([
   page.waitForResponse(response=>response.url().includes('/v1/admin/lessons?date=1970-01-01')&&response.status()===200),
   page.getByRole('button',{name:'查看课表',exact:true}).click(),
  ]);
  await page.locator('button[data-action="review-cancelled"]:visible').first().waitFor();
  const reviews=page.locator('button[data-action="review-cancelled"]:visible');
  assert.equal(await reviews.count(),2);
  const makeupId=await reviews.nth(0).getAttribute('data-lesson-id'),leaveId=await reviews.nth(1).getAttribute('data-lesson-id');
  await reviews.nth(0).click();
  await page.getByRole('heading',{name:'取消课次签到处理 · 虚构甲班',exact:true}).waitFor();
  assert.match(await page.locator('#dialog-content').innerText(),/虚构学生甲[\s\S]*未签到 · v0[\s\S]*10 课时[\s\S]*1 课时/);
  await page.screenshot({path:path.join(out,'cancelled-roster-desktop.png'),fullPage:true});
  await page.locator('#device-dialog').getByRole('button',{name:'补签',exact:true}).click();
  await page.getByLabel('实际到课与补签原因',{exact:true}).fill('虚构取消前实际到课，平板冲突归档');
  await page.locator('#device-dialog').getByRole('checkbox').check();
  await page.getByRole('button',{name:'确认补签并扣课时',exact:true}).click();
  await page.getByText(/补签完成：虚构学生甲，服务器仅扣一次课时；平板可核对归档/).waitFor();
  assert.equal(makeupRequests.length,1);
  const duplicate=await page.request.post(base+'/v1/admin/lessons/'+makeupId+'/makeup',{headers:{Authorization:'Bearer '+adminToken},data:makeupRequests[0]});
  assert.equal(duplicate.status(),200);assert.equal((await duplicate.json()).duplicate,true);
  let detail=await (await page.request.get(base+'/v1/admin/lessons/'+makeupId,{headers:{Authorization:'Bearer '+adminToken}})).json();
  assert.equal(detail.students[0].balance_units,9);assert.equal(detail.students[0].status,'arrived');assert.equal(detail.lesson.state,'cancelled');
  let resolved=await page.request.post(base+'/v1/tablet/checkins/resolve',{headers:{Authorization:'Bearer '+deviceToken},data:{event_ref:'browser-cancel-makeup-resolution',lesson_id:makeupId,student_id:'student-a',occurred_at:100,attendance_revision:0,balance_revision:1,previous_balance_units:10,consumed_units:1}});
  assert.equal(resolved.status(),200);assert.equal((await resolved.json()).apply_local_debit,false);assert.equal((await resolved.json()).balance_units,9);
  await page.locator('button[data-action="review-cancelled"]:visible').nth(0).click();
  await page.getByText('已到 · v1',{exact:true}).waitFor();
  assert.equal(await page.locator('#device-dialog').getByRole('button',{name:'补签',exact:true}).count(),0);
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  await page.locator('button[data-action="review-cancelled"]:visible').nth(1).click();
  await page.locator('#device-dialog').getByRole('button',{name:'请假',exact:true}).click();
  await page.getByLabel('请假原因',{exact:true}).fill('虚构家长提前请假，取消课次归档');
  await page.locator('#device-dialog').getByRole('checkbox').check();
  await page.getByRole('button',{name:'确认请假',exact:true}).click();
  await page.getByText(/请假完成：虚构学生甲，未扣课时；平板可核对归档/).waitFor();
  detail=await (await page.request.get(base+'/v1/admin/lessons/'+leaveId,{headers:{Authorization:'Bearer '+adminToken}})).json();
  assert.equal(detail.students[0].balance_units,9);assert.equal(detail.students[0].status,'leave');assert.equal(detail.lesson.state,'cancelled');
  resolved=await page.request.post(base+'/v1/tablet/checkins/resolve',{headers:{Authorization:'Bearer '+deviceToken},data:{event_ref:'browser-cancel-leave-resolution',lesson_id:leaveId,student_id:'student-a',occurred_at:100,attendance_revision:0,balance_revision:1,previous_balance_units:10,consumed_units:1}});
  assert.equal(resolved.status(),200);assert.equal((await resolved.json()).apply_local_debit,false);assert.equal((await resolved.json()).status,'leave');
  await page.setViewportSize({width:390,height:844});
  await page.locator('button[data-action="review-cancelled"]:visible').nth(1).click();
  await page.getByText('请假 · v1',{exact:true}).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'cancelled-roster-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Cancelled correction browser passed: real admin login, cancelled roster, makeup and leave, safe replay, tablet archival, one debit only, persistent cancelled state and 390px layout.');
 }finally{
  await browser?.close();
  await fetch(base+'/__fixture_stop',{method:'POST',headers:{'X-Fixture-Stop':stop}}).catch(()=>{});
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>{child.kill();resolve();},5000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
