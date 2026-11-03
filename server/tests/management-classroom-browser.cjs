'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=require('C:/Users/liugu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'../..'),out=path.resolve(root,'../../outputs/teacher-classroom-2026-11-02'),stop=crypto.randomBytes(24).toString('hex');
(async()=>{fs.mkdirSync(out,{recursive:true});const child=spawn(path.join(root,'server/.venv/Scripts/python.exe'),[path.join(__dirname,'serve_management_classroom_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:'server;server/tests',CLASSROOM_FIXTURE_STOP:stop,PYTHONIOENCODING:'utf-8'},stdio:['ignore','pipe','pipe'],windowsHide:true});let browser,err='';child.stderr.on('data',b=>err+=b);
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('Fixture timeout '+err.slice(-1000))),30000);child.stdout.on('data',b=>{if(b.toString().includes('FICTIONAL_CLASSROOM_READY')){clearTimeout(t);resolve();}});child.on('exit',c=>{clearTimeout(t);reject(Error('Fixture exit '+c+' '+err.slice(-1000)));});});
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1365,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.clock.install({time:new Date('2026-11-07T15:00:00+08:00')});
  // The public HTTP entry may not expose crypto.randomUUID. Mutations must
  // still generate stable per-submit references with getRandomValues.
  await page.addInitScript(()=>{Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true});});
  await page.goto('http://127.0.0.1:18109/');await page.getByLabel('机构',{exact:true}).fill('fictional');await page.getByLabel('账号',{exact:true}).fill('teacher');await page.getByLabel('密码',{exact:true}).fill('fictional-approval-test-only');await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('heading',{name:'今日课程',exact:true}).waitFor();await page.getByRole('button',{name:'查看 虚构班甲 名单',exact:true}).waitFor();assert(!(await page.locator('main').innerText()).includes('不应给甲教师看的班'));
  await page.screenshot({path:path.join(out,'today-courses-desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'查看 虚构班甲 名单',exact:true}).click();await page.getByRole('heading',{name:'当前学生2',exact:true}).waitFor();
  await page.getByRole('button',{name:'未签到',exact:true}).click();await page.getByRole('heading',{name:'当前学生2',exact:true}).waitFor();await page.getByRole('heading',{name:'当前学生3',exact:true}).waitFor({state:'hidden'});
  await page.getByRole('button',{name:'申请请假',exact:true}).click();await page.getByLabel('申请原因',{exact:true}).fill('虚构浏览器请假验收');await page.getByRole('button',{name:'提交申请',exact:true}).click();await page.getByText('已有待审批申请，请到审批列表查看进度。',{exact:true}).waitFor();
  await page.getByLabel('上课日期（北京时间）',{exact:true}).fill('2026-10-24');await page.getByRole('button',{name:'查看课程',exact:true}).click();await page.getByRole('button',{name:'查看 虚构班甲 名单',exact:true}).click();await page.getByRole('heading',{name:'历史到课学生',exact:true}).waitFor();
  assert.match(await page.locator('main').innerText(),/14:03.*人脸签到/);assert(await page.getByRole('button',{name:'申请请假',exact:true}).isDisabled());assert(await page.getByRole('button',{name:'申请补签',exact:true}).isDisabled());
  await page.getByLabel('搜索姓名或学号',{exact:true}).fill('H000');await page.getByRole('button',{name:'搜索学生',exact:true}).click();await page.getByRole('heading',{name:'历史到课学生',exact:true}).waitFor();await page.getByRole('heading',{name:'历史请假学生',exact:true}).waitFor({state:'hidden'});
  await page.getByRole('button',{name:'刷新',exact:true}).click();await page.getByRole('heading',{name:'历史到课学生',exact:true}).waitFor();assert.equal(await page.getByLabel('上课日期（北京时间）',{exact:true}).inputValue(),'2026-10-24');
  await page.screenshot({path:path.join(out,'historical-students-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'historical-students-mobile.png'),fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  console.log('Teacher classroom browser passed: default today, scoped class cards, status/name filters, stable refresh, old read-only timestamps, HTTP-compatible application submission, mobile containment. Fictional data only.');
 }finally{await browser?.close();await fetch('http://127.0.0.1:18109/__fictional_fixture_stop',{method:'POST',headers:{'X-Fixture-Stop':stop}}).catch(()=>{});await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const t=setTimeout(()=>{child.kill();resolve();},5000);child.once('exit',()=>{clearTimeout(t);resolve();});});}
})().catch(e=>{console.error(e);process.exitCode=1;});
