'use strict';
// Fictional credentials and a private disposable database only. Never packaged.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net'),{spawn}=require('node:child_process');
const runtime=process.env.PLAYWRIGHT_MODULE||'C:/Users/liugu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright';
const {chromium}=require(runtime);
const root=path.resolve(__dirname,'..'),python=process.env.PORTAL_TEST_PYTHON||path.join(root,'.venv','Scripts','python.exe');
const password='Fictional browser-only passphrase 2026!';
const folder=fs.mkdtempSync(path.join(os.tmpdir(),'wxnotify-ui-test-'));
let child,browser;
const screenshots=path.resolve(root,'..','docs','screenshots');fs.mkdirSync(screenshots,{recursive:true});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
  const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const port=s.address().port;s.close(()=>resolve(port));});});
  const base='http://127.0.0.1:'+port;
  child=spawn(python,[path.join(__dirname,'serve_portal_fixture.py'),'--database',path.join(folder,'fixture.sqlite'),'--port',String(port)],{cwd:root,windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  let log='';child.stdout.on('data',data=>log+=data);child.stderr.on('data',data=>log+=data);
  for(let i=0;;i++){
    if(child.exitCode!==null)throw Error('Fictional UI server exited before readiness');
    try{const r=await fetch(base+'/health/ready');if(r.ok)break;}catch{}
    if(i>200)throw Error('UI server did not become ready');await sleep(100);
  }
  async function api(url,method='GET',data,token){
    const r=await fetch(base+url,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});
    assert(r.ok,`Fixture request failed ${method} ${url}: ${r.status}`);return r.json();
  }
  const owner=(await api('/v1/auth/login','POST',{organization:'school-ui',login:'owner-ui',password})).access_token;
  const classes=(await api('/v1/classes?limit=100','GET',undefined,owner)).items;
  const schoolClass=classes.find(c=>c.course_name==='信息学奥赛'),otherClass=classes.find(c=>c.course_name==='创意绘画');
  const people=(await api('/v1/admin/people?limit=100','GET',undefined,owner)).items;
  const teacherId=people.find(p=>p.login==='teacher-ui').id;
  const lesson=(await api('/v1/lessons?day=2026-10-25','GET',undefined,owner)).items.find(l=>l.class_id===schoolClass.id);
  browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const context=await browser.newContext({viewport:{width:1366,height:1000}}),page=await context.newPage();
  const errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){external.push(route.request().url());return route.abort();}return route.continue();});
  async function login(name,reload=true){
    if(reload)await page.goto(base);await page.locator('#loginForm [name=organization]').fill('school-ui');await page.locator('#loginForm [name=login]').fill(name);await page.locator('#loginForm [name=password]').fill(password);await page.locator('#loginForm button').click();await page.locator('#appScreen').waitFor({state:'visible'});await page.locator('#main h1').waitFor();
  }
  async function nav(view,title){await page.locator(`#navigation [data-id="${view}"]`).click();if(title)await page.getByRole('heading',{level:1,name:title,exact:true}).waitFor();}
  async function success(text){try{await page.waitForFunction(text=>!document.querySelector('#message').hidden&&document.querySelector('#message').textContent.includes(text),text);}catch{throw Error('Expected status '+text+'; current status: '+await page.locator('#message').textContent());}}
  async function noOverflow(){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Page overflows viewport');}

  // Teacher: own classes, homonym identifiers, leave/revoke, content and explicit absence choice.
  await login('teacher-ui');
  assert.equal(await page.locator('.lesson-card').count(),1);
  assert.equal(await page.locator('#navigation [data-id=people]').count(),0);
  assert.equal(await page.locator('#navigation [data-id=monthly]').count(),0);
  assert.equal(await page.locator('[data-action=new-lesson]').count(),0);
  await page.screenshot({path:path.join(screenshots,'server-teacher-workbench.png'),fullPage:true,animations:'disabled'});
  await page.locator('[data-action=open-lesson]').click();await page.locator('#contentForm').waitFor();
  const homonyms=page.locator('tbody tr').filter({hasText:'演示同名学生'});assert.equal(await homonyms.count(),2);
  assert.match(await homonyms.nth(0).textContent(),/DEMO-10[01]/);assert.match(await homonyms.nth(1).textContent(),/DEMO-10[01]/);
  const absentRow=page.locator('tbody tr').filter({hasText:'DEMO-101'});
  await absentRow.getByRole('button',{name:'办理请假'}).click();await page.locator('#leaveForm [name=reason]').fill('演示：当天事假');await page.locator('#leaveForm input[type=checkbox]').check();await page.locator('#leaveForm button[type=submit]').click();await success('本次请假已保存');
  let detail=await api('/v1/lessons/'+lesson.id+'/workspace','GET',undefined,owner);
  assert.equal(detail.expected,4);assert.equal(detail.arrived,1);assert.equal(detail.leave_count,1);assert.equal(detail.rate_percent,25);assert.equal(detail.unexcused.length,2);
  assert.equal(await absentRow.locator('input[name=missing]').count(),0);
  await absentRow.getByRole('button',{name:'撤销请假'}).click();await page.locator('#cancelLeaveForm [name=reason]').fill('演示：学生恢复到课');await page.locator('#cancelLeaveForm button[type=submit]').click();await success('请假已撤销');
  await page.locator('#absenceForm button[type=submit]').click();await success('请先选择');
  await absentRow.locator('input[name=missing]').check();await page.locator('#absenceForm input[name=confirmed]').check();await page.locator('#absenceForm button[type=submit]').click();await success('已生成 1 条');
  const body='条件判断与循环练习。\n下节课复习输入输出。';
  await page.locator('#contentForm textarea').fill(body);
  await absentRow.getByRole('button',{name:'办理请假'}).click();await success('请先保存课堂内容');assert.equal(await page.locator('#editorDialog').isVisible(),false);assert.equal(await page.locator('#contentForm textarea').inputValue(),body);
  // Failure keeps the unsaved content and never reports success.
  await page.route('**/v1/lessons/*/content',async route=>{if(route.request().method()==='PUT')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'演示保存失败，请稍后重试'}})});return route.continue();});
  await page.locator('#contentForm button[type=submit]').click();await success('演示保存失败');assert.equal(await page.locator('#contentForm textarea').inputValue(),body);
  await page.unroute('**/v1/lessons/*/content');
  await page.locator('#contentForm button[type=submit]').click();await success('课堂内容已保存');
  assert.equal((await api('/v1/lessons/'+lesson.id+'/workspace','GET',undefined,owner)).content.body,body);
  assert.equal((await api('/v1/lessons/'+lesson.id+'/workspace','GET',undefined,owner)).content.publish_after_class,0);
  await page.locator('#contentForm [name=publish_after_class]').check();await page.locator('#contentForm button[type=submit]').click();await success('课堂内容已保存');
  const savedContent=(await api('/v1/lessons/'+lesson.id+'/workspace','GET',undefined,owner)).content;assert.equal(savedContent.publish_after_class,1);assert.equal(savedContent.audience,'arrived');
  await page.screenshot({path:path.join(screenshots,'server-lesson-workspace.png'),fullPage:true,animations:'disabled'});
  await nav('weekly','我的教学周报');assert.match(await page.locator('#main').textContent(),/请假计应到/);
  assert.equal((await page.evaluate(()=>Object.keys(localStorage))).length,0);assert.equal((await page.evaluate(()=>Object.keys(sessionStorage))).length,0);assert.deepEqual(await context.cookies(),[]);
  // Refresh loses the in-memory login. No secret is persisted in browser storage.
  await page.reload();await page.locator('#loginScreen').waitFor({state:'visible'});assert.equal(await page.locator('#main').textContent(),'');
  await login('owner-ui');
  await nav('people','人员与身份');
  await page.locator('[data-action=new-person]').click();
  await page.locator('#personForm [name=display_name]').fill('演示新老师');await page.locator('#personForm [name=login]').fill('created-ui');await page.locator('#personForm [name=password]').fill(password);await page.locator('#personForm [value=teacher]').check();await page.locator('#personForm [value=admin]').check();await page.locator('#personForm button[type=submit]').click();await success('人员已创建');
  assert.match(await page.locator('tbody tr').filter({hasText:'演示新老师'}).textContent(),/老师.*管理员|管理员.*老师/);
  await page.screenshot({path:path.join(screenshots,'server-admin-people.png'),fullPage:true,animations:'disabled'});
  await nav('classes','班级与任课');await page.locator('#classSearch').fill('奥赛');assert.equal(await page.locator('.class-row').count(),1);await page.locator('[data-action=assign-teachers]').click();
  assert.equal(await page.locator('#assignmentForm input[value="'+teacherId+'"]').isChecked(),true);
  // A stale permission form must not silently overwrite a concurrent change.
  await api('/v1/admin/classes/'+schoolClass.id+'/teachers','PUT',{teacher_ids:[]},owner);
  await page.locator('#assignmentForm [name=confirmed]').check();await page.locator('#assignmentForm button[type=submit]').click();await success('班级授权已被更新');assert.equal(await page.locator('#editorDialog').isVisible(),true);
  await page.locator('#closeDialog').click();await page.locator('[data-action=assign-teachers]').click();await page.locator('#assignmentForm input[value="'+teacherId+'"]').check();await page.locator('#assignmentForm [name=confirmed]').check();await page.locator('#assignmentForm button[type=submit]').click();await success('任课授权已保存');
  assert.deepEqual((await api('/v1/admin/classes/'+schoolClass.id+'/teachers','GET',undefined,owner)).teacher_ids,[teacherId]);
  // Create course/class and verify real persisted data through the API.
  await page.locator('[data-action=new-class]').click();await page.locator('#classForm [name=course_name]').fill('演示机器人');await page.locator('#classForm [name=name]').fill('演示机器人 · 隔周周日');await page.locator('#classForm button[type=submit]').click();await success('班级已创建');
  assert.equal((await api('/v1/classes?limit=100','GET',undefined,owner)).items.filter(c=>c.course_name==='演示机器人').length,1);
  await nav('students','学生名册');
  await page.locator('[data-action=new-student]').click();await page.locator('#studentForm [name=name]').fill('<img src=x onerror=window.uiXss=1>');await page.locator('#studentForm [name=external_ref]').fill('XSS-TEST-ONLY');await page.locator('#studentForm [name=class_id]').selectOption(otherClass.id);await page.locator('#studentForm [name=hours]').fill('-1.25');await page.locator('#studentForm button[type=submit]').click();await success('联调学生已创建');
  assert.equal(await page.evaluate(()=>window.uiXss),undefined);assert.equal(await page.locator('#main img').count(),0);assert.match(await page.locator('#main').textContent(),/<img src=x/);assert.match(await page.locator('#main').textContent(),/-1.25/);
  await nav('monthly','新生与续费');await page.locator('[data-action=new-activity]').click();await page.locator('#activityForm [name=kind]').selectOption('renewal');await page.locator('#activityForm input[type=checkbox]').check();await page.locator('#activityForm button[type=submit]').click();await success('业务记录已登记');
  assert.equal((await api('/v1/reports/monthly?month=2026-10','GET',undefined,owner)).renewal_events,1);
  await nav('lessons','课堂工作台');await page.locator('[data-action=new-lesson]').click();await page.locator('#newLessonClass').selectOption(schoolClass.id);await page.waitForFunction(id=>!!document.querySelector('#newLessonTeacher option[value="'+id+'"]'),teacherId);await page.locator('#newLessonTeacher').selectOption(teacherId);await page.locator('#lessonForm [name=day]').fill('2026-11-08');await page.locator('#lessonForm [name=start]').fill('10:00');await page.locator('#lessonForm [name=end]').fill('11:30');await page.locator('#lessonForm input[type=checkbox]').check();await page.locator('#lessonForm button[type=submit]').click();await success('本次课堂及应到名单已确认');
  assert.equal((await api('/v1/lessons?day=2026-11-08','GET',undefined,owner)).items.length,1);assert.equal((await api('/v1/lessons?day=2026-11-01','GET',undefined,owner)).items.length,0);
  await nav('devices','平板设备');await page.locator('[data-action=new-device]').click();await page.locator('#deviceForm [name=name]').fill('演示独立平板');await page.locator('#deviceForm input[value="'+schoolClass.id+'"]').check();await page.locator('#deviceForm button[type=submit]').click();await page.locator('#deviceCredentialOnce').waitFor();
  const firstDeviceToken=await page.locator('#deviceCredentialOnce').inputValue();assert(firstDeviceToken.length>30);await page.locator('#closeDeviceCredential').click();assert.equal(await page.locator('#deviceCredentialOnce').count(),0);
  const deviceRow=page.locator('tbody tr').filter({hasText:'演示独立平板'});await deviceRow.getByRole('button',{name:'同步权限'}).click();assert.equal(await page.locator('#deviceGrantForm [name=enabled]').isChecked(),false);await page.locator('#deviceGrantForm [name=enabled]').check();await page.locator('#deviceGrantForm [name=allow_local_leaves]').check();await page.locator('#deviceGrantForm input[required]').check();await page.locator('#deviceGrantForm button[type=submit]').click();await success('设备同步权限已保存');assert.match(await deviceRow.textContent(),/已启用.*已委托设备/);
  await deviceRow.getByRole('button',{name:'轮换凭证'}).click();await page.locator('#rotateDeviceForm input[required]').check();await page.locator('#rotateDeviceForm button[type=submit]').click();await page.locator('#deviceCredentialOnce').waitFor();const rotatedDeviceToken=await page.locator('#deviceCredentialOnce').inputValue();assert.notEqual(rotatedDeviceToken,firstDeviceToken);await page.locator('#closeDeviceCredential').click();assert.equal((await fetch(base+'/v1/device/sync-plan',{headers:{Authorization:'Bearer '+firstDeviceToken}})).status,401);assert.equal((await fetch(base+'/v1/device/sync-plan',{headers:{Authorization:'Bearer '+rotatedDeviceToken}})).status,200);
  assert.equal(await page.locator('#main').textContent().then(t=>t.includes(rotatedDeviceToken)),false);assert.equal(await page.evaluate(()=>Object.keys(localStorage).length+Object.keys(sessionStorage).length),0);await noOverflow();
  await nav('settings','通知设置');await page.locator('#preferencesForm [name=weekly_admin]').check();await page.locator('#preferencesForm button[type=submit]').click();await success('接收选择已保存');
  assert.equal(await page.locator('#scheduleForm [name=enabled]').isChecked(),false);await page.locator('#scheduleForm [name=enabled]').check();await page.locator('#scheduleForm button[type=submit]').click();await success('演练安排已保存');await page.locator('[data-action=jobs-tick]').click();await success('已生成');await page.locator('[data-action=simulate]').click();await success('未发送微信');
  await nav('notices','通知演练记录');await page.locator('#allNotices').check();await page.locator('.notice-card').first().waitFor();assert.match(await page.locator('#main').textContent(),/接收人/);
  await page.locator('#logoutButton').click();await page.locator('#loginScreen').waitFor({state:'visible'});assert.equal(await page.locator('#main').textContent(),'');
  await login('dual-ui');assert.equal(await page.locator('#accountRoles .role-tag').count(),2);assert.equal(await page.locator('.lesson-card').count(),1);await page.locator('#lessonFilter [name=scope]').selectOption('authorized');await page.locator('#lessonFilter button').click();await page.waitForFunction(()=>document.querySelectorAll('.lesson-card').length===2);
  await nav('weekly','全校教学周报');await page.locator('#weeklyFilter [name=scope]').selectOption('teaching');await page.locator('#weeklyFilter button').click();await page.getByRole('heading',{level:1,name:'我的教学周报',exact:true}).waitFor();
  for(const viewport of [{width:1366,height:1000},{width:800,height:1280},{width:600,height:960},{width:390,height:844}]){
    await page.setViewportSize(viewport);await nav('lessons','课堂工作台');await noOverflow();await page.locator('[data-action=open-lesson][data-id="'+lesson.id+'"]').click();await page.locator('#contentForm').waitFor();await noOverflow();await nav('classes','班级与任课');await noOverflow();await page.locator('[data-action=new-class]').click();await noOverflow();await page.locator('#closeDialog').click();await nav('people','人员与身份');await noOverflow();await nav('settings','通知设置');await noOverflow();
    if(viewport.width===390)await page.screenshot({path:path.join(screenshots,'server-workspace-mobile.png'),fullPage:true,animations:'disabled'});
  }
  // Losing teacher authorization immediately blocks the still-open browser session.
  await page.setViewportSize({width:1366,height:1000});await page.locator('#logoutButton').click();await page.locator('#loginScreen').waitFor({state:'visible'});await login('teacher-ui');
  await page.locator('[data-action=open-lesson]').click();await page.locator('#contentForm').waitFor();await api('/v1/admin/classes/'+schoolClass.id+'/teachers','PUT',{teacher_ids:[]},owner);
  await page.locator('#contentForm textarea').fill('这次不应保存的内容');await page.locator('#contentForm button').click();await success('没有该班级权限');assert.equal((await api('/v1/lessons/'+lesson.id+'/workspace','GET',undefined,owner)).content.body,body);
  page.on('dialog',d=>d.accept());await page.locator('#logoutButton').click();await page.locator('#loginScreen').waitFor({state:'visible'});
  assert.equal(await page.locator('#main').textContent(),'');
  // Exit remains possible during a stalled request; a late old-session 401 cannot log out a new session.
  await login('owner-ui');let heldRoute,signal;
  const held=new Promise(resolve=>signal=resolve);
  await page.route('**/v1/admin/people?*',route=>{heldRoute=route;signal();});
  await page.locator('#navigation [data-id=people]').click();await held;await page.locator('#logoutButton').click();await page.locator('#loginScreen').waitFor({state:'visible'});assert.equal(await page.locator('#main').textContent(),'');
  await login('owner-ui',false);await heldRoute.continue();await page.unroute('**/v1/admin/people?*');await sleep(250);assert.equal(await page.locator('#appScreen').isVisible(),true);assert.equal(await page.locator('#accountName').textContent(),'演示管理员');
  assert.deepEqual(await context.cookies(),[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  assert(!log.includes(password));assert(!log.includes(owner));
  console.log('Portal browser acceptance passed: real login/API, teacher/admin/dual roles, homonyms, leave/revoke/denominator, explicit absence selection, content failure/retry, refresh/logout privacy, person/class creation, optimistic authorization conflict, XSS escaping, monthly activity, fortnightly actual lessons, preferences and simulation, live revocation and four responsive sizes. No external requests.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  if(child&&child.exitCode===null){child.kill();await Promise.race([new Promise(resolve=>child.once('exit',resolve)),sleep(5000)]);}
  const resolved=path.resolve(folder),prefix=path.resolve(os.tmpdir())+path.sep+'wxnotify-ui-test-';
  if(!resolved.startsWith(prefix)||resolved===path.parse(resolved).root)throw Error('Refusing unsafe temporary cleanup');
  fs.rmSync(resolved,{recursive:true,force:true});
});
