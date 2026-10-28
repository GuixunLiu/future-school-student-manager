'use strict';
// Isolated Chrome profile, synthetic records and disposable database only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net'),{spawn}=require('node:child_process');
const {chromium}=require('C:/Users/liugu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'archive-browser-'));
let child,browser;
(async()=>{
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})}),base='http://127.0.0.1:'+port;
 child=spawn(path.join(root,'.venv/Scripts/python.exe'),[path.join(__dirname,'serve_archive_fixture.py'),'--database',path.join(temp,'fixture.sqlite'),'--port',String(port)],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 let logs='';child.stderr.on('data',b=>logs+=b);child.stdout.on('data',b=>logs+=b);
 for(let i=0;;i++){if(child.exitCode!==null)throw Error('Fixture exited: '+logs);try{if((await fetch(base+'/health/ready')).ok)break}catch{}if(i>150)throw Error('Fixture readiness timeout');await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 async function login(who){await page.goto(base);await page.locator('[name=organization]').fill('school-ui');await page.locator('[name=login]').fill(who);await page.locator('[name=password]').fill('Fictional archive browser credential 2026!');await page.locator('#loginForm button').click();await page.locator('#appScreen').waitFor({state:'visible'});await page.locator('#main h1').waitFor();}
 await login('owner-ui');await page.locator('#navigation [data-id=archives]').click();await page.getByRole('heading',{level:1,name:'长期不来归档',exact:true}).waitFor();
 await page.locator('#archiveRows tbody tr').first().waitFor();assert.equal(await page.locator('#archiveRows tbody tr').count(),3);assert.equal(await page.locator('#archiveRows img,#archiveRows script').count(),0);assert.equal(await page.getByRole('button',{name:'恢复上课',exact:true}).count(),0);
 assert((await page.locator('#main').innerText()).includes('未记录，不推测班级'));
 await page.locator('#archiveSearch').fill('DEMO-002');assert.equal(await page.locator('#archiveRows tbody tr').count(),1);assert((await page.locator('#archiveRows').innerText()).includes('原周日下午班'));
 await page.locator('#archiveSearch').fill('DEMO-00');
 const screenshots=path.resolve(root,'../docs/screenshots');await page.screenshot({path:path.join(screenshots,'server-archive-review.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(screenshots,'server-archive-review-mobile.png'),fullPage:true});
 await page.setViewportSize({width:1440,height:1050});await page.locator('#navigation [data-id=people]').click();await page.getByRole('heading',{level:1,name:'人员与身份',exact:true}).waitFor();assert(await page.locator('#main [data-action=new-person]').isDisabled());
 await page.locator('#logoutButton').click();await login('teacher-ui');assert.equal(await page.locator('#navigation [data-id=archives]').count(),0);assert.deepEqual(errors,[]);
 console.log('Archive browser passed: full roster/search, same-name IDs, unknown previous class, escaped HTML, review write controls, teacher menu isolation, desktop/mobile no overflow. Fictional data only.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(child){child.kill();await new Promise(r=>child.exitCode!==null?r():child.once('exit',r));}const resolved=path.resolve(temp),base=path.resolve(os.tmpdir());if(!resolved.startsWith(base+path.sep+'archive-browser-'))throw Error('Unsafe cleanup');fs.rmSync(resolved,{recursive:true,force:true});});
