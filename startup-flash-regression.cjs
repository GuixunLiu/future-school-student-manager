'use strict';
// Delayed resources and failed app startup must never expose the static admin shell.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'app/src/main/assets');let browser;
const server=http.createServer((req,res)=>{const file=path.resolve(root,'.'+new URL(req.url,'http://localhost').pathname.replace(/^\/$/,'/index.html'));if(!file.startsWith(root+path.sep))return res.writeHead(403).end();fs.readFile(file,(err,b)=>{if(err)return res.writeHead(404).end();res.setHeader('Content-Type',file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':'application/octet-stream');res.end(b)})});
async function waiting(page){
 await page.locator('#appStartup').waitFor({state:'visible'});
 assert.equal(await page.locator('.app-shell').evaluate(n=>getComputedStyle(n).visibility),'hidden');
 assert.equal(await page.locator('html').getAttribute('data-app-startup'),'pending');
 assert.equal(await page.locator('body').evaluate(n=>n.classList.contains('kiosk-mode')),true);
 assert.equal(await page.locator('#navList').isVisible(),false);
}
async function ready(page){
 await page.waitForFunction(()=>window.__futureSchoolAppStarted===true);
 assert.equal(await page.locator('#appStartup').isVisible(),false);
 assert.equal(await page.locator('.app-shell').evaluate(n=>getComputedStyle(n).visibility),'visible');
 assert.equal(await page.locator('html').getAttribute('data-app-startup'),null);
 assert.equal(await page.evaluate(()=>isAdminAuthenticated),false);
 assert.equal(await page.locator('#navList').isVisible(),false);
 assert.equal(await page.evaluate(()=>currentView),'checkin');
}
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 for(const viewport of [{width:1280,height:800},{width:800,height:1280}])for(const resource of ['app.js','class-space.css']){
  const context=await browser.newContext({viewport}),page=await context.newPage();let release;
  const gate=new Promise(r=>release=r);await page.route('**/'+resource,async route=>{await gate;await route.continue()});
  await page.goto(url,{waitUntil:'commit'});await waiting(page);await page.waitForTimeout(150);await waiting(page);
  if(resource==='app.js'&&viewport.width===1280)await page.screenshot({path:path.resolve(__dirname,'docs/screenshots/startup-loading.png')});
  release();await ready(page);
  await page.evaluate(()=>{isAdminAuthenticated=true;currentView='dashboard';render()});assert.equal(await page.locator('#navList').isVisible(),true);
  await context.close();
 }
 const context=await browser.newContext(),page=await context.newPage();let fail=true;
 await page.route('**/app.js',r=>fail?r.abort():r.continue());await page.goto(url);await waiting(page);
 await page.getByRole('button',{name:'重新加载',exact:true}).waitFor();assert.match(await page.locator('#appStartup').textContent(),/程序暂未启动/);await waiting(page);
 fail=false;await page.getByRole('button',{name:'重新加载',exact:true}).click();await ready(page);await context.close();
 console.log('Startup flash passed: inline guard before delayed CSS/JS, landscape/portrait, no static admin navigation, complete kiosk first render, authenticated navigation preserved, failure remains covered and reload recovers.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();await new Promise(r=>server.close(r))});
