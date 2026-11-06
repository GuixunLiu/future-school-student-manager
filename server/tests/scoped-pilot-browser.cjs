'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const out=path.resolve(__dirname,'../../../../outputs/scoped-pilot-ui-2026-10-28');fs.mkdirSync(out,{recursive:true});
const child=spawn(path.join(__dirname,'../.venv/Scripts/python.exe'),[path.join(__dirname,'serve_scoped_pilot_fixture.py'),'--fictional'],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONPATH:undefined}});
let stderr='';child.stderr.on('data',b=>stderr+=b);let close;const ended=new Promise(r=>close=r);child.on('close',close);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function login(page){await page.locator('[name=organization]').fill('fictional');await page.locator('[name=login]').fill('fictional-teacher');await page.locator('[name=password]').fill('Fictional scoped pilot password 2026!');await page.locator('#login button').click();await page.waitForFunction(()=>document.querySelectorAll('#students tr').length===16);}
(async()=>{let browser;
 try{
  const info=await new Promise((resolve,reject)=>{let body='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout: '+stderr)),20000);child.on('error',reject);child.stdout.on('data',b=>{body+=b;const n=body.indexOf('\n');if(n>=0){clearTimeout(timer);try{resolve(JSON.parse(body.slice(0,n)))}catch(e){reject(e)}}});child.once('close',()=>reject(Error('Fixture closed: '+stderr)));});
  assert.equal(info.fictional,true);const url=info.fixture_url;
  for(let n=0;n<80;n++){try{await new Promise((resolve,reject)=>{const r=http.get(url+'/health/ready',res=>{res.resume();res.statusCode===200?resolve():reject(Error('not ready'))});r.on('error',reject)});break}catch(e){if(n===79)throw e;await sleep(100)}}
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.type()==='prompt'?d.accept('虚构浏览器测试原因'):d.accept());
  page.on('request',r=>assert.equal(new URL(r.url()).hostname,'127.0.0.1'));
  await page.goto(url);await login(page);assert.equal(await page.locator('#students img').count(),0);
  const draft='虚构未保存草稿：循环与条件判断。';await page.locator('#draft').fill(draft);await page.locator('[data-leave]').first().click();await page.waitForFunction(()=>document.querySelectorAll('[data-cancel]').length===1);
  assert.equal(await page.locator('#draft').inputValue(),draft);
  await page.reload();await login(page);assert.equal(await page.locator('[data-cancel]').count(),1);
  const saved='虚构本节课内容：学习循环与条件判断，完成练习。';await page.locator('#draft').fill(saved);await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('草稿已保存'));
  await page.reload();await login(page);assert.equal(await page.locator('#draft').inputValue(),saved);
  await page.locator('[data-cancel]').click();await page.waitForFunction(()=>document.querySelectorAll('[data-cancel]').length===0);
  await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
  let release,seen;const held=new Promise(r=>release=r),arrived=new Promise(r=>seen=r);let armed=true;
  await page.route('**/pilot/leaves',async route=>{if(armed&&route.request().method()==='GET'){armed=false;seen();await held;}await route.continue();});
  await page.locator('[data-leave]').first().click();await arrived;await page.locator('#logout').click();await page.locator('#login').waitFor();release();await sleep(300);
  assert.equal(await page.locator('#students').count(),0);assert.equal(await page.locator('#login').count(),1);
  assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser-check.json'),JSON.stringify({fictional_only:true,desktop_and_mobile:true,xss_escaped:true,leave_cancel_after_reload:true,draft_persisted:true,unsaved_draft_retained_during_leave:true,late_reply_after_logout_did_not_restore_data:true,credential_storage_used:false,page_errors:errors},null,2)+'\n');
  console.log('Scoped browser passed: fictional 16-person roster, desktop/mobile, escaped names, draft persistence, leave cancellation after reload, unsaved text and stale logout reply protection.');
 }finally{if(browser)await browser.close();child.stdin.end();await Promise.race([ended,sleep(6000)]);if(child.exitCode===null)child.kill();}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
