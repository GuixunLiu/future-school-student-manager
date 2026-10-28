'use strict';
process.env.TZ='Asia/Shanghai';
// Dedicated .synctest package only. Never attach to the production WebView or read its records.
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict'),{spawn,execFileSync}=require('child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/liugu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=__dirname,adb=path.resolve(root,'../android-toolchain/sdk/platform-tools/adb.exe'),serial=process.env.TABLET_SERIAL||'HVA13XVR',pkg='cn.mashangchengzhang.studentmanager.synctest';
const folder=fs.mkdtempSync(path.join(os.tmpdir(),'wxnotify-tablet-test-'));
let server,browser,reversePort,forwardPort,testPage,acceptanceReport,serverError;
let log='';const remoteMode=Boolean(process.env.SYNC_REMOTE_CONFIG);
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const run=args=>execFileSync(adb,['-s',serial,...args],{encoding:'utf8',windowsHide:true,timeout:120000}).trim();
const freePort=()=>new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
async function connect(){
  const pid=run(['shell','pidof',pkg]).split(/\s+/)[0];assert(/^\d+$/.test(pid));
  run(['forward','tcp:'+forwardPort,'localabstract:webview_devtools_remote_'+pid]);
  for(let i=0;i<60;i++){
    try{const targets=await(await fetch('http://127.0.0.1:'+forwardPort+'/json/list')).json();if(targets.some(t=>t.url==='https://appassets.androidplatform.net/assets/index.html')){
      browser=await chromium.connectOverCDP('http://127.0.0.1:'+forwardPort);const pages=browser.contexts().flatMap(c=>c.pages());const page=pages.find(p=>p.url()==='https://appassets.androidplatform.net/assets/index.html');if(page){await page.waitForFunction(()=>window.__futureSchoolAppStarted);return page;}
    }}catch{}await delay(250);
  }throw Error('Isolated WebView not available');
}
function dump(){
  let focus=run(['shell','dumpsys','window']);
  if(/^\s*mCurrentFocus=.*NotificationShade/m.test(focus)&&/^\s*mFocusedApp=.*cn\.mashangchengzhang\.studentmanager\.synctest\//m.test(focus)){
    run(['shell','cmd','statusbar','collapse']);focus=run(['shell','dumpsys','window']);
  }
  assert(/^\s*mCurrentFocus=.*cn\.mashangchengzhang\.studentmanager\.synctest\//m.test(focus),'Only inspect UI when the isolated test package itself has current focus');
  run(['shell','uiautomator','dump','/sdcard/fs-sync-test-dialog.xml']);
  return run(['shell','cat','/sdcard/fs-sync-test-dialog.xml']);
}
function nodes(xml){return [...xml.matchAll(/<node\b[^>]*>/g)].map(m=>Object.fromEntries([...m[0].matchAll(/([\w-]+)="([^"]*)"/g)].map(a=>[a[1],a[2]])));}
function tap(n){assert(n&&n.bounds);const b=n.bounds.match(/\[(\d+),(\d+)\]\[(\d+),(\d+)\]/);assert(b);run(['shell','input','tap',String(Math.round((+b[1]+ +b[3])/2)),String(Math.round((+b[2]+ +b[4])/2))]);}
(async()=>{
  reversePort=await freePort();forwardPort=await freePort();const deviceClock=Number(run(['shell','date','+%s']));assert(Number.isSafeInteger(deviceClock));
  if(remoteMode)server=require('./server/ops/start-remote-sync-fixture.cjs').start(process.env.SYNC_REMOTE_CONFIG,reversePort);
  else server=spawn(path.join(root,'server/.venv/Scripts/python.exe'),[path.join(root,'server/tests/serve_sync_pilot_fixture.py'),'--database',path.join(folder,'fixture.sqlite'),'--port',String(reversePort),'--clock-start',String(deviceClock)],{cwd:root,windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  server.on('error',e=>serverError=e);server.stdout.on('data',b=>log+=b);if(server.stderr)server.stderr.on('data',b=>log+=b);
  const base='http://127.0.0.1:'+reversePort;
  for(let i=0;;i++){try{if((await fetch(base+'/health/ready')).ok)break}catch{}if(i>(remoteMode?1800:150)||server.exitCode!==null||serverError)throw Error('Isolated fixture did not start');await delay(100);}
  async function api(route,method='GET',data,token){const r=await fetch(base+route,{method,headers:{...(data?{'Content-Type':'application/json'}:{}),...(token?{Authorization:'Bearer '+token}:{})},body:data?JSON.stringify(data):undefined});assert(r.ok,'USB fixture API '+route+' status '+r.status);return r.json();}
  const owner=(await api('/v1/auth/login','POST',{organization:'usb-fixture',login:'usb-owner',password:'Fictional USB-only passphrase 2026!'})).access_token;
  const health=await api('/health/ready');assert.equal(health.version,'0.5.0');assert.equal(health.archive_enabled,true);assert.equal(health.roster_review_only,false);
  const identity=await api('/fixture/identity');assert.equal(identity.synthetic_fixture,true);assert.equal(identity.fixture_kind,'sync-validation-v0.5');assert.equal(identity.real_database_opened,false);
  const classes=(await api('/v1/classes','GET',undefined,owner)).items;assert.equal(classes.length,2);const allowedClass=classes.find(c=>c.name==='同步联调一班');assert(allowedClass);
  const device=await api('/v1/admin/devices','POST',{name:'虚构USB专用平板',class_ids:[allowedClass.id]},owner);
  await api('/v1/admin/devices/'+device.id+'/sync-access','PUT',{enabled:true,allow_local_leaves:true},owner);
  const plan=await api('/v1/device/sync-plan','GET',undefined,device.device_token);assert.equal(plan.lessons.length,1);assert.equal(plan.classes.length,1);assert.equal(plan.students.length,2);assert(plan.students.every(s=>s.class_id===allowedClass.id));assert.equal(plan.lessons[0].ends_at-plan.lessons[0].starts_at,7200);assert(!JSON.stringify(plan).includes('ARCH-USB'));assert(!JSON.stringify(plan).includes('26020001'));
  assert(Math.abs(plan.server_time-Number(run(['shell','date','+%s'])))<120,'Device and fixture clocks need review; do not change device time');
  const teacher=(await api('/v1/auth/login','POST',{organization:'usb-fixture',login:'usb-teacher',password:'Fictional USB-only passphrase 2026!'})).access_token;
  assert.deepEqual((await api('/v1/classes','GET',undefined,teacher)).items.map(c=>c.id),[allowedClass.id]);assert.equal((await api('/v1/students','GET',undefined,teacher)).items.length,2);
  assert.equal((await fetch(base+'/v1/admin/archives',{headers:{Authorization:'Bearer '+teacher}})).status,403);
  run(['reverse','tcp:'+reversePort,'tcp:'+reversePort]);
  run(['shell','am','force-stop',pkg]);run(['shell','am','start','-W','-n',pkg+'/cn.mashangchengzhang.studentmanager.FutureSchoolLauncherActivity']);
  let page=await connect();testPage=page;
  await page.evaluate(session=>{if(state.students.length&&!state.settings.usbSyntheticFixture)throw Error('Refusing to overwrite nonfixture test records');closeModal();state=emptyState();state.settings.cameraPositionConfigured=true;state.settings.usbSyntheticFixture=true;const clock=t=>new Date(t*1000).toTimeString().slice(0,5);state.classes=[{id:11,name:'同步联调一班',courseName:'信息学奥赛提高班',teacher:'虚构USB老师',scheduleWeekday:(new Date().getDay()||7),scheduleStart:clock(session.starts_at),scheduleEnd:clock(session.ends_at),lessonDurationMinutes:120,scheduleIntervalWeeks:2,scheduleAnchorDate:localDate()}];state.students=[{id:1,name:'联调同名学生',studentNo:'26010001',className:'同步联调一班',teacher:'虚构USB老师',hours:20,createdAt:'2026-01-01'},{id:2,name:'联调同名学生',studentNo:'26010002',className:'同步联调一班',teacher:'虚构USB老师',hours:20,createdAt:'2026-01-01'},{id:3,name:'虚构长期不来',studentNo:'ARCH-USB',className:'',pausedClassName:'同步联调一班',longTermAbsent:true,teacher:'虚构USB老师',hours:23,createdAt:'2026-01-01'}];ensureEnrollmentHistory();lastGeneratedId=100;state.attendance=[{id:90,studentId:2,date:'2026-01-01',time:'10:00',classId:11,className:'同步联调一班',method:'姓名'}];state.transactions=[];isAdminAuthenticated=true;saveState();render();AndroidBridge.configureDeviceSync();},plan.lessons[0]);
  await delay(700);
  let ui=nodes(dump()),edits=ui.filter(n=>n.class==='android.widget.EditText');assert.equal(edits.length,2);
  tap(edits[0]);run(['shell','input','keyevent','KEYCODE_MOVE_END']);if(edits[0].text?.length)run(['shell','input','keyevent',...Array(Math.min(edits[0].text.length,240)).fill('67')]);run(['shell','input','text',base]);
  edits=nodes(dump()).filter(n=>n.class==='android.widget.EditText');tap(edits[1]);run(['shell','input','text',device.device_token]);
  run(['shell','input','keyevent','4']);await delay(250);
  ui=nodes(dump());edits=ui.filter(n=>n.class==='android.widget.EditText');assert.equal(edits.length,2,'Native configuration dialog must remain open after hiding keyboard');
  assert.equal(edits[0].text,base,'Native server address must match the temporary loopback fixture');
  assert((edits[1].text||'').length>0,'Device credential input must be populated');
  const verify=ui.find(n=>n.text==='验证连接'&&n.class==='android.widget.Button');assert(verify&&verify.enabled==='true');tap(verify);
  await page.waitForSelector('#activateDeviceSyncForm',{timeout:25000});assert.match(await page.locator('#modal').textContent(),/可匹配 2 名学生/);
  await page.locator('#syncMappingConfirmed').check();await page.locator('#activateDeviceSyncForm button[type=submit]').click();await page.waitForFunction(()=>state.deviceSync&&state.deviceSync.captureEnabled);
  assert.equal(await page.evaluate(()=>state.deviceSync.queue.length),0,'No historical replay');
  assert.equal(await page.evaluate(()=>Object.keys(state.deviceSync.students).length),2);assert.equal(await page.evaluate(()=>state.students.find(s=>s.id===3).hours),23);
  assert.equal(await page.evaluate(()=>{const next=new Date();next.setDate(next.getDate()+7);return regularLessonOnDate(state.classes[0],localDate(next))}),false,'Fortnightly off-week has no regular lesson');
  assert(!(await page.evaluate(()=>JSON.stringify(state))).includes(device.device_token));
  run(['reverse','--remove','tcp:'+reversePort]);
  const saved=await page.evaluate(()=>{closeModal();const r=recordStudentAttendance(1,'姓名');return{ok:r.ok,hours:state.students[0].hours,queue:state.deviceSync.queue.length}});assert.deepEqual(saved,{ok:true,hours:19,queue:1});
  await page.waitForFunction(()=>state.deviceSync.queue[0]&&state.deviceSync.queue[0].attempts>=1,{timeout:25000});
  const immutable=await page.evaluate(()=>state.deviceSync.queue[0].wire);
  assert.equal((await api('/v1/lessons/'+plan.lessons[0].id,'GET',undefined,owner)).arrived,0);
  run(['reverse','tcp:'+reversePort,'tcp:'+reversePort]);
  await page.evaluate(()=>{state.deviceSync.queue[0].nextAt=0;saveState();scheduleDeviceSync()});
  await page.waitForFunction(()=>state.deviceSync.queue[0]&&state.deviceSync.queue[0].attempts>=2,{timeout:25000});
  assert.equal((await api('/v1/lessons/'+plan.lessons[0].id,'GET',undefined,owner)).arrived,1,'The server committed despite the lost success receipt');
  assert.equal(await page.evaluate(()=>state.students[0].hours),19);
  // Restart only the isolated test package; both the encrypted credential and exact event survive.
  run(['shell','am','force-stop',pkg]);try{await browser.close()}catch{}browser=null;
  run(['forward','--remove','tcp:'+forwardPort]);
  run(['shell','am','start','-W','-n',pkg+'/cn.mashangchengzhang.studentmanager.FutureSchoolLauncherActivity']);page=await connect();testPage=page;
  assert.equal(await page.evaluate(()=>state.deviceSync.queue[0].wire),immutable);assert.equal(await page.evaluate(()=>state.students[0].hours),19);
  await page.evaluate(()=>{isAdminAuthenticated=true;currentView='settings';render();openDeviceSync()});await page.locator('#retryDeviceSync').click();
  await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});
  let students=(await api('/v1/students','GET',undefined,owner)).items;assert.equal(students.find(s=>s.external_ref==='26010001').remaining_units,1900);
  assert.equal((await api('/v1/lessons/'+plan.lessons[0].id,'GET',undefined,owner)).arrived,1);
  await page.evaluate(()=>{const r=saveStudentLeave(2,11,localDate(),'虚构缺席事假','本机备注老师');if(!r.ok)throw Error(r.error)});await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});
  const withLeave=await api('/v1/lessons/'+plan.lessons[0].id+'/workspace','GET',undefined,owner);assert.equal(withLeave.expected,2);assert.equal(withLeave.arrived,1);assert.equal(withLeave.leave_count,1);assert.equal(withLeave.rate_percent,50,'Approved leave stays in the attendance denominator');assert.equal(await page.evaluate(()=>state.students[1].hours),20);
  await page.evaluate(()=>{const r=cancelStudentLeave(studentLeaves().find(l=>l.studentId===2&&l.status==='approved').id,'本机备注老师','虚构撤假');if(!r.ok)throw Error(r.error)});await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});
  await page.evaluate(()=>{closeModal();const r=undoAttendanceRecord(state.attendance.find(a=>a.studentId===1).id);if(!r.ok)throw Error('Fixture revoke failed')});await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});
  assert.equal(await page.evaluate(()=>state.students[0].hours),20);students=(await api('/v1/students','GET',undefined,owner)).items;assert.equal(students.find(s=>s.external_ref==='26010001').remaining_units,2000);assert.equal((await api('/v1/lessons/'+plan.lessons[0].id,'GET',undefined,owner)).arrived,0);
  await page.evaluate(()=>{const r=saveStudentLeave(1,11,localDate(),'虚构USB事假','本机备注老师');if(!r.ok)throw Error(r.error)});await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});
  let lesson=await api('/v1/lessons/'+plan.lessons[0].id+'/workspace','GET',undefined,owner);assert.equal(lesson.leave_count,1);assert.equal(lesson.expected,2);assert.equal(lesson.rate_percent,0);assert.match(lesson.leaves[0].recorded_by,/平板代办/);
  await page.evaluate(()=>{const r=cancelStudentLeave(studentLeaves().find(l=>l.studentId===1&&l.status==='approved').id,'本机备注老师','虚构撤假');if(!r.ok)throw Error(r.error)});await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});assert.equal((await api('/v1/lessons/'+plan.lessons[0].id,'GET',undefined,owner)).leave_count,0);assert.equal(await page.evaluate(()=>state.students[0].hours),20);
  await api('/v1/admin/devices/'+device.id+'/sync-access','PUT',{enabled:false,allow_local_leaves:false},owner);
  await page.evaluate(()=>recordHoursAddition(2,1));await page.waitForFunction(()=>state.deviceSync.paused&&state.deviceSync.queue.some(e=>e.status==='blocked'),{timeout:25000});assert.equal(await page.evaluate(()=>state.students[1].hours),21);
  students=(await api('/v1/students','GET',undefined,owner)).items;assert.equal(students.find(s=>s.external_ref==='26010002').remaining_units,2000);
  await api('/v1/admin/devices/'+device.id+'/sync-access','PUT',{enabled:true,allow_local_leaves:true},owner);
  await page.evaluate(()=>{isAdminAuthenticated=true;currentView='settings';render();openDeviceSync()});await page.locator('#retryDeviceSync').click();await page.waitForFunction(()=>state.deviceSync.queue.length===0,{timeout:25000});
  students=(await api('/v1/students','GET',undefined,owner)).items;assert.equal(students.find(s=>s.external_ref==='26010002').remaining_units,2100);assert.equal(students.find(s=>s.external_ref==='26020001').remaining_units,3000);
  const archives=await api('/v1/admin/archives','GET',undefined,owner);assert.equal(archives.items.length,1);assert.equal(archives.items[0].remaining_units,2300);assert.equal((await api('/v1/reports/monthly?month='+new Date(deviceClock*1000).toISOString().slice(0,7),'GET',undefined,owner)).new_students,0);
  assert.equal((await api('/v1/reports/monthly?month='+new Date(deviceClock*1000).toISOString().slice(0,7),'GET',undefined,owner)).renewal_events,0,'Balance snapshots are not renewal records');
  assert.equal((await api('/v1/lessons/'+plan.lessons[0].id,'GET',undefined,owner)).expected,2,'Archive remains outside the lesson roster');
  await page.evaluate(()=>{isAdminAuthenticated=true;currentView='settings';render();openDeviceSync()});
  assert.equal(await page.locator('body').evaluate(n=>n.classList.contains('admin-mode')),true);
  assert.equal(await page.locator('#modal > p').isVisible(),true);assert.notEqual(await page.locator('#modal > p').evaluate(n=>getComputedStyle(n).color),'rgb(255, 255, 255)');
  await page.screenshot({path:path.join(root,'docs/screenshots/tablet-device-sync.png'),animations:'disabled'});
  assert(!log.includes(device.device_token));assert(!log.includes(owner));
  const reportDirectory=path.resolve(root,'../../outputs/sync-pilot-2026-10-27');fs.mkdirSync(reportDirectory,{recursive:true,mode:0o700});
  acceptanceReport=path.join(reportDirectory,(remoteMode?'remote-physical-':'physical-')+new Date().toISOString().replace(/[:.]/g,'-')+'.json');
  fs.writeFileSync(acceptanceReport,JSON.stringify({checked_at:new Date().toISOString(),device:serial,test_package:pkg,server_version:'0.5.0',local_synthetic_fixture:!remoteMode,remote_synthetic_fixture:remoteMode,mapped_students:2,archive_excluded:true,archive_remaining_units:2300,other_class_untouched:true,lesson_duration_minutes:120,fortnightly_off_week_verified:true,no_history_replay:true,offline_queue:true,committed_lost_receipt:true,restart_exact_retry:true,no_double_charge:true,revoke_refund:true,delegated_leave_cancel:true,leave_in_denominator_rate_percent:withLeave.rate_percent,grant_stop_and_resume:true,no_inferred_new_or_renewal:true,production_database_accessed:false,remote_service_modified:false,passwords_or_tokens_saved:false},null,2)+'\n',{flag:'wx',mode:0o600});
  console.log('Physical tablet isolated sync passed on current v0.5 extension: real native configuration/Keystore, exact mapping and archived exclusion, 120-minute fortnightly lesson/off-week, scoped teacher reads, no history replay, offline queue, lost receipt, restart/exact retry/no double charge, revoke/refund, delegated leave/cancel, grant stop/resume preserving balance, no inferred renewal. Production student database was never accessed.');
})().catch(async e=>{console.error(e.stack||e.message);if(testPage)try{console.error(JSON.stringify(await testPage.evaluate(()=>({toast:document.querySelector('#toast')?.textContent,configured:nativeSyncProfile().configured,hasPlan:!!deviceSyncPreview,bridge:typeof AndroidBridge.requestDeviceSync}))));}catch{}try{const diagnostics=nodes(dump()).filter(n=>n.class==='android.widget.TextView'&&/地址|验证|连接|保存|凭证/.test(n.text||''));console.error(JSON.stringify(diagnostics.map(n=>n.text.slice(0,220))));}catch{}process.exitCode=1}).finally(async()=>{
  if(browser)try{await browser.close()}catch{}
  if(reversePort)try{run(['reverse','--remove','tcp:'+reversePort])}catch{}
  if(forwardPort)try{run(['forward','--remove','tcp:'+forwardPort])}catch{}
  try{run(['shell','rm','-f','/sdcard/fs-sync-test-dialog.xml'])}catch{}
  if(server&&server.exitCode===null){
    if(remoteMode){server.stdin.end();await Promise.race([new Promise(r=>server.once('close',r)),delay(15000)]);}
    if(server.exitCode===null){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),delay(3000)]);}
  }
  if(remoteMode&&server){const closed=log.includes('SYNC_FIXTURE_CLOSED');if(acceptanceReport){const report=JSON.parse(fs.readFileSync(acceptanceReport));report.remote_fixture_closed=closed;fs.writeFileSync(acceptanceReport,JSON.stringify(report,null,2)+'\n',{mode:0o600});}if(!closed){process.exitCode=1;console.error('Remote synthetic cleanup was not confirmed; inspect before repeating.');}}
  const resolved=path.resolve(folder),prefix=path.resolve(os.tmpdir())+path.sep+'wxnotify-tablet-test-';if(!resolved.startsWith(prefix))throw Error('Unsafe temporary cleanup');fs.rmSync(resolved,{recursive:true,force:true});
});
