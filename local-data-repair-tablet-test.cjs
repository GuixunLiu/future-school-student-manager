'use strict';
// Only the separate .synctest package, using invented records. Never attach to production.
const path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const {fixture,makePlan}=require('./local-data-repair-regression.cjs');
const {chromium}=require('playwright');
const adb=path.resolve('../android-toolchain/sdk/platform-tools/adb.exe'),pkg='cn.mashangchengzhang.studentmanager.synctest',serial='HVA13XVR';
const run=args=>execFileSync(adb,['-s',serial,...args],{timeout:60000,maxBuffer:8*1024*1024});
let browser,port;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function connect(){const pid=run(['shell','pidof',pkg]).toString().trim();assert(/^\d+$/.test(pid));run(['forward','tcp:'+port,'localabstract:webview_devtools_remote_'+pid]);for(let i=0;i<60;i++){try{const rows=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();if(rows.some(r=>r.url==='https://appassets.androidplatform.net/assets/index.html')){browser=await chromium.connectOverCDP('http://127.0.0.1:'+port);const page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url()==='https://appassets.androidplatform.net/assets/index.html');await page.waitForFunction(()=>window.__futureSchoolAppStarted);return page}}catch(_){}await wait(300)}throw Error('Isolated test WebView not ready');}
(async()=>{
  port=await new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const value=s.address().port;s.close(()=>r(value))})});
  run(['shell','am','start','-W','-n',pkg+'/cn.mashangchengzhang.studentmanager.FutureSchoolLauncherActivity']);let page=await connect();
  const data=fixture();data.settings.repairSyntheticFixture=true;
  await page.evaluate(data=>{if(state.students.length&&!state.settings.repairSyntheticFixture)throw Error('Refusing to replace nonfixture records');AndroidBridge.discardLocalRepairCheckpoint();state=data;committedStateSnapshot=JSON.stringify(state);localStorage.setItem(STORAGE_KEY,committedStateSnapshot);isAdminAuthenticated=true;currentView='settings';render()},data);
  // Establish the original fixture on disk before testing an immediate kill of the repair transaction.
  await wait(1800);await browser.close();browser=null;run(['shell','am','force-stop',pkg]);run(['shell','am','start','-W','-n',pkg+'/cn.mashangchengzhang.studentmanager.FutureSchoolLauncherActivity']);page=await connect();assert.equal(await page.evaluate(()=>state.students.length),3);await page.evaluate(()=>isAdminAuthenticated=true);
  const before=await page.evaluate(()=>JSON.stringify(state)),oldDiskImage=await page.evaluate(()=>localStorage.getItem(STORAGE_KEY)),plan=await makePlan(JSON.parse(before));
  const result=await page.evaluate(p=>applyLocalDataRepair(p),plan);assert.equal(result.changes,3);assert(result.before.verified&&result.after.verified);
  const after=await page.evaluate(()=>JSON.stringify(state));
  for(const [phase,text]of [['before',before],['after',after]]){const receipt=result[phase],remote='/sdcard/Download/未来学校同步测试/资料修复备份/'+receipt.fileName,bytes=run(['exec-out','cat',remote]);assert.equal(bytes.toString('utf8'),text);assert.equal(receipt.bytes,bytes.length);assert.equal(receipt.sha256,crypto.createHash('sha256').update(bytes).digest('hex'));}
  // Deterministically model WebView retaining the old disk image after the native commit.
  await page.evaluate(old=>localStorage.setItem(STORAGE_KEY,old),oldDiskImage);await wait(1800);
  await browser.close();browser=null;run(['shell','am','force-stop',pkg]);run(['shell','am','start','-W','-n',pkg+'/cn.mashangchengzhang.studentmanager.FutureSchoolLauncherActivity']);page=await connect();
  assert.deepEqual(await page.evaluate(()=>({ids:state.students.map(s=>s.id),hours:state.students.map(s=>s.hours),audit:state.settings.localDataRepairs.length,valid:!!validateBackupState(JSON.parse(localStorage.getItem(STORAGE_KEY)))})),{ids:[1,100,3],hours:[19,22,12],audit:1,valid:true});
  assert.equal(await page.evaluate(()=>window.__localRepairRecovered),true,'Native checkpoint must recover the approved repair from the old persisted image');
  const restored=await page.evaluate(after=>{state=validateBackupState(JSON.parse(after));ensureClasses();ensureEnrollmentHistory();normalizeLongTermStudentAssignments();localStorage.setItem(STORAGE_KEY,JSON.stringify(state));return{ids:state.students.map(s=>s.id),faces:state.students.slice(0,2).map(s=>s.faceImage),histories:state.students.slice(0,2).map(s=>s.enrollmentHistory.length),attendance:state.attendance.length,transactions:state.transactions.length}},after);
  assert.deepEqual(restored,{ids:[1,100,3],faces:['fictional-pixels-A','fictional-pixels-B'],histories:[0,0],attendance:1,transactions:1});
  await page.evaluate(()=>{state.students[2].hours=99;localStorage.setItem(STORAGE_KEY,JSON.stringify(state))});await wait(1800);await browser.close();browser=null;run(['shell','am','force-stop',pkg]);run(['shell','am','start','-W','-n',pkg+'/cn.mashangchengzhang.studentmanager.FutureSchoolLauncherActivity']);page=await connect();assert.equal(await page.evaluate(()=>state.students[2].hours),99,'Checkpoint must never overwrite a later user save');
  console.log('Isolated physical tablet repair passed: both native backups read back byte-identical with verified length/SHA-256, unchanged faces/hours/records, forced old-disk recovery, later-user-edit protection, restart persistence and repaired full-backup restore validation. Production package untouched.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(port)try{run(['forward','--remove','tcp:'+port])}catch(_){} });
