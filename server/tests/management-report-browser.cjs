'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require('C:/Users/liugu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'../..'),output=path.resolve(root,'../../outputs/canonical-reports-2026-11-02'),stop=crypto.randomBytes(24).toString('hex');
async function main(){
 fs.mkdirSync(output,{recursive:true});
 const child=spawn(path.join(root,'server/.venv/Scripts/python.exe'),[path.join(__dirname,'serve_management_reports_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:'server;server/tests',PYTHONIOENCODING:'utf-8',REPORT_FIXTURE_STOP:stop},stdio:['ignore','pipe','pipe'],windowsHide:true});
 let errors='',browser;child.stderr.on('data',b=>{errors+=b.toString();});
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fictional fixture startup timeout: '+errors.slice(-1000))),30000);child.stdout.on('data',b=>{if(b.toString().includes('FICTIONAL_REPORT_FIXTURE_READY')){clearTimeout(timer);resolve();}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+errors.slice(-1000)));});});
  for(let i=0;i<30;i++){try{if((await fetch('http://127.0.0.1:18099/')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1365,height:1000}}),pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
  async function login(who){await page.goto('http://127.0.0.1:18099/');await page.getByLabel('机构',{exact:true}).fill('fictional');await page.getByLabel('账号',{exact:true}).fill(who);await page.getByLabel('密码',{exact:true}).fill('fictional-approval-test-only');await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByRole('button',{name:'周报与历史周报',exact:true}).click();await page.getByLabel('选择周次').selectOption('2026-10-19');await page.getByRole('button',{name:'查看周报',exact:true}).click();}
  await login('owner');await page.getByText('接入前历史周报',{exact:true}).waitFor();assert.match(await page.locator('main').innerText(),/2\.57/);
  assert.equal(await page.locator('main img').count(),0);assert.equal(await page.evaluate(()=>window.fixtureXss),undefined);
  await page.screenshot({path:path.join(output,'history-report-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'history-report-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  await page.setViewportSize({width:1365,height:1000});await login('teacher');await page.getByText('接入前课堂明细汇总',{exact:true}).waitFor();
  const text=await page.locator('main').innerText();assert.match(text,/虚构班甲/);assert(!text.includes('不应给甲教师看的班'));assert(!text.includes('<img'));
  await page.getByRole('button',{name:'接入前补签申请',exact:true}).click();await page.getByText('原补签申请0',{exact:true}).waitFor();
  const oldRequests=await page.locator('main').innerText();assert(!oldRequests.includes('原补签申请1'));assert(!oldRequests.includes('不猜测已删除课堂归属'));assert.deepEqual(pageErrors,[]);
  console.log('Real browser reports passed: history totals, scoped teacher, escaped names, mobile horizontal containment. Fictional data only.');
 }finally{
  if(browser)await browser.close();
  try{await fetch('http://127.0.0.1:18099/__fictional_fixture_stop',{method:'POST',headers:{'x-fixture-stop':stop}});}catch{}
  if(child.exitCode===null)await new Promise(resolve=>{const t=setTimeout(()=>{child.kill();resolve();},5000);child.once('exit',()=>{clearTimeout(t);resolve();});});
 }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
