// Executes actual App entry functions, not a duplicate implementation.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');const {webcrypto}=require('node:crypto');
const repo=path.resolve(__dirname,'../..'),app=fs.readFileSync(path.join(repo,'app/src/main/assets/app.js'),'utf8');
function fn(from,to){const a=app.indexOf(from),b=app.indexOf(to,a+from.length);assert.ok(a>=0&&b>a);return app.slice(a,b);}
function fixture(active=true){
 const now=Date.parse('2026-11-01T14:00:00+08:00');
 const state={students:[{id:1,name:'演示学生',studentNo:'TEST',className:'演示班',hours:10}],classes:[{id:2,name:'演示班',teacher:'演示教师'}],attendance:[],transactions:[],settings:{studentLeaves:[]},
 managementSync:{protocol:1,enabled:active,mode:'server_authoritative',legacyMirrorDisabled:true,orgId:'org',deviceId:'device',generation:'generation',profileId:'profile',queue:[],receipts:{},
 students:{student:{localId:1,localClassId:2,name:'演示学生',studentNo:'TEST',className:'演示班',classId:'class',teacher:'演示教师',balanceUnits:1000,balanceRevision:0}},
 lessons:{lesson:{classId:'class',date:'2026-11-01',startsAt:now/1000,endsAt:now/1000+7200,state:'scheduled',studentIds:['student']}},facts:{lesson:{student:{expected:true,status:'missing',revision:0,chargeUnits:100}}}}};
 const disk={raw:JSON.stringify(state),getItem(){return this.raw;},setItem(k,v){this.raw=v;}},notes=[];
 const bridge={getCanonicalSyncProfile:()=>JSON.stringify({configured:true,orgId:'org',deviceId:'device',generation:'generation',profileId:'profile'})};
 class FixedDate extends Date{static now(){return now;}}
 const c=vm.createContext({console,crypto:webcrypto,TextEncoder,TextDecoder,atob,btoa,Date:FixedDate,localStorage:disk,AndroidBridge:bridge,notes,initial:state,
 setTimeout:()=>1,setInterval:()=>1,clearTimeout:()=>{},document:{hidden:false,addEventListener(){},getElementById(){return null;}}});
 vm.runInContext("globalThis.window=globalThis;window.addEventListener=()=>{};let state=initial;const STORAGE_KEY='fixture';let committedStateSnapshot=JSON.stringify(state);let nonce=0;function syncNonce(){return String(++nonce).padStart(32,'0');}function invalidatePageCache(){}function toast(s){notes.push(s);}function localDate(){return '2026-11-01';}function studentById(id){return state.students.find(s=>s.id===id);}const listPages={attendance:1};",c);
 for(const file of ['snapshot-store.js','checkin-queue.js','approval-receiver.js','runtime-bootstrap.js','face-profiles.js','sync-runtime.js','activation-commit.js'])vm.runInContext(fs.readFileSync(path.join(repo,'server/management/tablet',file),'utf8'),c);
 vm.runInContext(fs.readFileSync(path.join(repo,'app/src/main/assets/canonical-app.js'),'utf8'),c);
 vm.runInContext(fn('function persistCurrentState(){','function rollbackUnsavedState'),c);
 vm.runInContext(fn('function saveState(){','async function optimizeExistingFacePhotos'),c);
 vm.runInContext(fn("function recordStudentAttendance(studentId,method,lessonId=''){",'function recordHoursAddition'),c);
 return{c,disk,notes,bridge,run:s=>vm.runInContext(s,c),snapshot:()=>JSON.parse(disk.raw)};
}
test('actual App check-in entry uses canonical writer exactly once and ordinary save preserves queue',()=>{
 const f=fixture();assert.equal(f.run("recordStudentAttendance(1,'人脸')").ok,true);let s=f.snapshot();
 assert.equal(s.students[0].hours,9);assert.equal(s.attendance.length,1);assert.equal(s.managementSync.queue.length,1);
 assert.equal(f.run("state.settings.displaySize=2;saveState()"),true);s=f.snapshot();assert.equal(s.managementSync.queue.length,1);assert.equal(s.students[0].hours,9);
 assert.equal(f.run('JSON.stringify(state)===committedStateSnapshot'),true);assert.equal(s.settings.displaySize,2);
});
test('actual saveState cannot overwrite canonical hours and restores last synchronized state',()=>{
 const f=fixture();f.run("recordStudentAttendance(1,'手动')");const raw=f.disk.raw;
 assert.equal(f.run('state.students[0].hours=99;saveState()'),false);assert.equal(f.disk.raw,raw);
 assert.equal(f.run('state.students[0].hours'),9);assert.equal(f.run('JSON.stringify(state)===committedStateSnapshot'),true);
});
test('unsaved App edit blocks a background incoming CAS without losing the draft',()=>{
 const f=fixture(),raw=f.disk.raw;f.run('state.settings.note="draft"');
 assert.throws(()=>f.run('canonicalGuardedStore().read()'),e=>e.code==='unsaved_changes');
 assert.equal(f.disk.raw,raw);assert.equal(f.run('state.settings.note'),'draft');
});
test('missing native profile prevents check-in rather than silently using legacy deduction',()=>{
 const f=fixture(),raw=f.disk.raw;f.bridge.getCanonicalSyncProfile=()=>'{"configured":false}';
 assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,false);assert.equal(f.disk.raw,raw);
});
test('full disk leaves actual App state and durable balance unchanged',()=>{
 const f=fixture(),raw=f.disk.raw;f.disk.setItem=()=>{throw Error('full');};
 assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,false);assert.equal(f.disk.raw,raw);assert.equal(f.run('state.students[0].hours'),10);
});
test('mode gate is off for existing daily data; legacy persist path still works',()=>{
 const f=fixture(false);assert.equal(f.run('canonicalModeActive()'),false);assert.equal(f.run('state.settings.note="legacy";persistCurrentState()'),true);
 assert.equal(f.snapshot().settings.note,'legacy');assert.equal(f.snapshot().managementSync.queue.length,0);
});
test('App startup does not replay a local repair checkpoint into canonical data',()=>{
 const f=fixture();let recover=0;f.bridge.recoverLocalRepairCheckpoint=()=>{recover++;return f.disk.raw;};
 vm.runInContext('function emptyState(){return {settings:{legacyDefault:true}};}'+fn('function loadState(){','function classReminderCalendar'),f.c);
 assert.equal(f.run('loadState().managementSync.enabled'),true);assert.equal(recover,0);
 assert.equal(f.run('JSON.stringify(loadState())'),f.disk.raw);
});
test('empty roster keeps all three check-in entries away from local student management',()=>{
 const f=fixture(false);f.run('state.students=[];');
 vm.runInContext(fn('function openCheckin(method){','function openCheckinChoices'),f.c);
 for(const method of ['人脸','姓名','学号'])f.run(`openCheckin(${JSON.stringify(method)})`);
 assert.equal(f.notes.length,3);
 assert(f.notes.every(note=>note.includes('设备设置')));
});
test('canonical tablet cannot clear or restore an old backup to bypass canonical authority',()=>{
 const f=fixture(),raw=f.disk.raw;
 // Only the early guard executes; legacy backup machinery must not be invoked.
 const begin=app.indexOf('function requireSafetyBackup(reason){'),end=app.indexOf('\n}',begin)+2;
 assert.ok(begin>=0&&end>begin);vm.runInContext(app.slice(begin,end),f.c);
 assert.equal(f.run("requireSafetyBackup('恢复前备份')"),false);
 assert.equal(f.run("requireSafetyBackup('清空前备份')"),false);assert.equal(f.disk.raw,raw);
});
test('enrollment staging requires unlocked admin and transmits only stable baseline digest',async()=>{
 const f=fixture(false),calls=[];f.bridge.stageCanonicalSyncEnrollment=h=>calls.push(h);
 await f.run('stageCanonicalEnrollment()');assert.equal(calls.length,0);
 f.run('let isAdminAuthenticated=true;');const raw=f.disk.raw;
 await f.run('stageCanonicalEnrollment()');
 assert.equal(calls[0],require('node:crypto').createHash('sha256').update(raw).digest('hex'));
 assert.equal(f.disk.raw,raw);assert.equal(f.run('canonicalModeActive()'),false);
});
test('state changing during baseline hash refuses staging',async()=>{
 const f=fixture(false),calls=[];f.bridge.stageCanonicalSyncEnrollment=h=>calls.push(h);f.run('let isAdminAuthenticated=true;');
 const task=f.run('stageCanonicalEnrollment()');f.disk.raw=JSON.stringify({...f.snapshot(),newField:'changed'});await task;
 assert.equal(calls.length,0);assert.ok(f.notes.some(n=>n.includes('重新核对备份')));
});
test('current capture requires admin, passes exact saved bytes and acquisition time without changing state',async()=>{
 const f=fixture(false),calls=[];f.bridge.captureCanonicalSyncEnrollment=(...args)=>calls.push(args);
 await f.run('captureCanonicalEnrollment()');assert.equal(calls.length,0);
 f.run('let isAdminAuthenticated=true;');const raw=f.disk.raw;
 await f.run('captureCanonicalEnrollment()');
 assert.equal(calls.length,1);assert.equal(calls[0][0],raw);assert.equal(calls[0][1],require('node:crypto').createHash('sha256').update(raw).digest('hex'));
 assert.equal(calls[0][2],Date.parse('2026-11-01T14:00:00+08:00')/1000);assert.equal(f.disk.raw,raw);assert.equal(f.run('canonicalModeActive()'),false);
});
test('capture refuses both unsaved memory and a stale on-disk snapshot',async()=>{
 const f=fixture(false),calls=[];f.bridge.captureCanonicalSyncEnrollment=(...args)=>calls.push(args);f.run('let isAdminAuthenticated=true;');
 f.run('state.settings.note="unsaved"');await f.run('captureCanonicalEnrollment()');assert.equal(calls.length,0);
 f.run('delete state.settings.note');f.disk.raw=JSON.stringify({...f.snapshot(),externalChange:true});
 await f.run('captureCanonicalEnrollment()');await f.run('stageCanonicalEnrollment()');assert.equal(calls.length,0);
 assert(f.notes.some(n=>n.includes('重新采集')));
});
test('capture refuses a new save during asynchronous digest rather than label old bytes as current',async()=>{
 const f=fixture(false),calls=[];f.bridge.captureCanonicalSyncEnrollment=(...args)=>calls.push(args);f.run('let isAdminAuthenticated=true;');
 const task=f.run('captureCanonicalEnrollment()');f.run('state.settings.newSave=true;persistCurrentState()');await task;
 assert.equal(calls.length,0);assert.equal(f.snapshot().settings.newSave,true);
});
test('active mode cannot prepare another capture; result callback never renders native private fields',async()=>{
 const f=fixture(),calls=[];f.bridge.captureCanonicalSyncEnrollment=(...args)=>calls.push(args);f.run('let isAdminAuthenticated=true;');
 await f.run('captureCanonicalEnrollment()');assert.equal(calls.length,0);
 f.run('window.onCanonicalSyncNativeResult("capture",{ok:true,data:{payload:"SECRET-MUST-NOT-RENDER"}})');
 assert(!f.notes.some(n=>n.includes('SECRET-MUST-NOT-RENDER')));assert(f.notes.some(n=>n.includes('尚未上传')));
});

test('actual activation entry persists bootstrap before native commit and keeps check-ins blocked until acknowledgement',async()=>{
 const f=fixture(false),sha=s=>require('node:crypto').createHash('sha256').update(s).digest('hex'),raw=f.disk.raw,next=JSON.parse(raw),commits=[],prepared=[];
 next.managementSync.enabled=true;next.deviceSync={captureEnabled:false};const nextRaw=JSON.stringify(next);
 const p={configured:false,staged:true,orgId:'org',deviceId:'device',generation:'generation',profileId:'profile'};
 f.bridge.getCanonicalSyncProfile=()=>JSON.stringify(p);f.bridge.prepareCanonicalActivation=h=>prepared.push(h);
 f.bridge.getCanonicalActivationBootstrap=()=>JSON.stringify({format:'future-school-canonical-bootstrap-v1',org_id:'org',device_id:'device',generation:'generation',profile_id:'profile',baseline_sha256:sha(raw),bootstrap_sha256:sha(nextRaw),source_map_sha256:'b'.repeat(64),snapshot_base64:Buffer.from(nextRaw).toString('base64')});
 f.bridge.commitCanonicalActivation=h=>{assert.equal(f.disk.raw,nextRaw);commits.push(h);};
 f.run('let isAdminAuthenticated=true;');await f.run('startCanonicalActivation()');assert.deepEqual(prepared,[sha(raw)]);
 await f.run('finishCanonicalActivation()');assert.deepEqual(commits,[sha(nextRaw)]);assert.equal(f.run('canonicalModeActive()'),true);
 assert.equal(f.run("recordStudentAttendance(1,'manual')").ok,false);assert.equal(f.disk.raw,nextRaw);
 f.run("state.settings.note='pending edit';saveState()");assert.equal(f.disk.raw,nextRaw);
 p.configured=true;p.staged=false;assert.equal(f.run("recordStudentAttendance(1,'manual')").ok,true);assert.equal(f.snapshot().students[0].hours,9);
});

test('protocol 2 activation downloads the first snapshot without exposing a token to JavaScript',async()=>{
 const f=fixture(false),calls=[],starts=Date.parse('2026-11-01T14:00:00+08:00')/1000;
 const data={device:{id:'device',name:'签到平板',generation:'generation'},snapshot_at:starts+100,
  classes:[{id:'class',name:'演示班',course_name:'演示课程'}],lessons:[{id:'lesson',class_id:'class',starts_at:starts,ends_at:starts+7200,state:'scheduled',students:[{student_id:'student',name:'演示学生',external_ref:'TEST',expected:true,charge_units:100,status:'missing',attendance_revision:0,balance_units:1000,balance_revision:0}]}]};
 f.bridge.requestCanonicalSync=(id,profile,kind,payload)=>{calls.push({id,profile,kind,payload});f.run('window.onCanonicalSyncNativeResult('+JSON.stringify(id)+','+JSON.stringify(JSON.stringify({ok:true,status:200,data}))+')');};
 f.run('let isAdminAuthenticated=true;');await f.run('startRuntimeActivation()');
 assert.equal(calls.length,1);assert.equal(calls[0].kind,'bootstrap');assert.equal(calls[0].payload,'{}');
 assert(!calls[0].payload.includes('token'));const state=f.snapshot();assert.equal(state.managementSync.protocol,2);
 assert.equal(state.managementSync.enabled,true);assert.equal(state.students[0].hours,10);assert.equal(f.run('canonicalModeActive()'),true);
});

test('actual App safe rebind pauses check-in and never passes a token through the bridge',async()=>{
 const f=fixture(),calls=[],requests=[],starts=Date.parse('2026-11-01T14:00:00+08:00')/1000;
 f.run('state.managementSync.protocol=2;committedStateSnapshot=JSON.stringify(state);');f.disk.raw=f.run('committedStateSnapshot');
 f.run('let isAdminAuthenticated=true;');f.bridge.reissueRuntime=(...args)=>calls.push(args);
 await f.run('startRuntimeReissue()');assert.equal(calls.length,1);assert.equal(calls[0].length,0);
 const raw=f.disk.raw;assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,false);assert.equal(f.disk.raw,raw);
 f.bridge.getCanonicalSyncProfile=()=>JSON.stringify({configured:true,orgId:'org',deviceId:'device',generation:'generation-2',profileId:'profile-2'});
 const data={device:{id:'device',name:'签到平板',generation:'generation-2'},snapshot_at:starts+100,
  classes:[{id:'class',name:'演示班',course_name:'演示课程'}],lessons:[{id:'lesson',class_id:'class',starts_at:starts,ends_at:starts+7200,state:'scheduled',students:[{student_id:'student',name:'演示学生',external_ref:'TEST',expected:true,charge_units:100,status:'missing',attendance_revision:0,balance_units:1000,balance_revision:0}]}]};
 f.bridge.requestCanonicalSync=(id,profile,kind,payload)=>{requests.push({id,profile,kind,payload});f.run('window.onCanonicalSyncNativeResult('+JSON.stringify(id)+','+JSON.stringify(JSON.stringify({ok:true,status:200,data}))+')');};
 await f.run('finishRuntimeReissue()');const state=f.snapshot();assert.equal(state.managementSync.generation,'generation-2');assert.equal(state.managementSync.profileId,'profile-2');
 assert.equal(f.run('canonicalRebindPending'),false);assert.equal(state.managementSync.rebindPending,true);
 assert.equal(requests.length,0);
 await f.run('ManagementSyncRuntime.create({store:canonicalGuardedStore(),profile:canonicalProfile,request:canonicalNativeRequest}).tick()');
 assert.equal(f.snapshot().managementSync.rebindPending,undefined);
 assert.equal(requests[0].kind,'bootstrap');assert.equal(requests[0].profile,'profile-2');assert(!requests[0].payload.includes('token'));
});

test('actual App can rebind while retaining an offline check-in',async()=>{
 const f=fixture();f.run('state.managementSync.protocol=2;committedStateSnapshot=JSON.stringify(state);');f.disk.raw=f.run('committedStateSnapshot');
 f.run('let isAdminAuthenticated=true;');assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,true);const raw=f.disk.raw;let calls=0;f.bridge.reissueRuntime=()=>calls++;
 await f.run('startRuntimeReissue()');assert.equal(calls,1);assert.equal(f.disk.raw,raw);assert.equal(f.run('canonicalRebindPending'),true);
 f.run('window.onCanonicalSyncNativeResult("runtime-reissue",{ok:false,data:{cancelled:true}})');
 assert.equal(f.run('canonicalRebindPending'),false);assert.equal(f.snapshot().managementSync.queue[0].status,'pending');
});

test('actual settings UI exposes only tablet operations in server-authoritative mode',()=>{
 const f=fixture();f.run('function esc(value){return String(value??"");}function cameraPositionSummary(){return "横屏：顶部";}function deviceSyncSettingsMarkup(){return "<section data-test-device-sync>待上传与冲突核对</section>";}');
 vm.runInContext(fn('function renderSettings(){','function handleContentAction(event){'),f.c);
 const html=f.run('renderSettings()');assert.ok(html.includes('data-tablet-refresh'));assert.ok(html.includes('data-canonical-enrollment="reissue"'));assert.ok(html.includes('data-test-device-sync'));
 for(const forbidden of ['data-action="add-student"','id="exportExcel"','id="batchImport"','id="exportBackup"','id="openWebMirror"','id="openWechatNotices"'])assert.equal(html.includes(forbidden),false,forbidden);
});

test('actual kiosk UI reflects real pending and blocked queue counts',()=>{
 const f=fixture();f.run('state.managementSync.protocol=2;committedStateSnapshot=JSON.stringify(state);');f.disk.raw=f.run('committedStateSnapshot');
 f.run('function todayAttendance(){return state.attendance;}function esc(value){return String(value??"");}function uiIcon(){return "";}');
 const source=fs.readFileSync(path.join(repo,'app/src/main/assets/class-space.js'),'utf8'),begin=source.indexOf('function renderClassSpaceKiosk(){'),end=source.indexOf('function setClassSpaceDate',begin);
 assert.ok(begin>=0&&end>begin);vm.runInContext(source.slice(begin,end),f.c);
 assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,true);let html=f.run('renderClassSpaceKiosk()');
 assert.ok(html.includes('待上传 1 条'));assert.ok(html.includes('断网也不会丢失'));assert.equal(f.run('canonicalQueueStatus().pending'),1);assert.equal(f.run('canonicalQueueStatus().blocked'),0);
 assert.equal(f.run("ManagementCheckinQueue.block(canonicalGuardedStore(),'checkin:'+String(1).padStart(32,'0'),'lesson_unavailable').ok"),true);
 html=f.run('renderClassSpaceKiosk()');assert.ok(html.includes('待核对 1 条'));assert.ok(html.includes('原记录已保留'));assert.equal(f.run('canonicalQueueStatus().pending'),0);assert.equal(f.run('canonicalQueueStatus().blocked'),1);
});

test('actual App asks before ambiguous same-day lessons and shows the configured debit',()=>{
 const f=fixture();f.run("state.managementSync.protocol=2;state.managementSync.lessons['lesson-2']={...state.managementSync.lessons.lesson,startsAt:state.managementSync.lessons.lesson.startsAt+3600,endsAt:state.managementSync.lessons.lesson.endsAt+3600};state.managementSync.facts['lesson-2']={student:{expected:true,status:'missing',revision:0,chargeUnits:150}};committedStateSnapshot=JSON.stringify(state);");f.disk.raw=f.run('committedStateSnapshot');
 vm.runInContext(fn('function performCheckin(student,method,successSuffix=\'\',lessonId=\'\'){','function classroomForStudent'),f.c);
 f.run('let selectedLesson="",successCharge=0;function openCanonicalLessonChoices(){selectedLesson="required";}function closeModal(){}function render(){}function showCheckinSuccess(student,method,suffix,charge){successCharge=charge;}');
 f.run("performCheckin(studentById(1),'手动')");assert.equal(f.run('selectedLesson'),'required');assert.equal(f.snapshot().attendance.length,0);
 f.run("performCheckin(studentById(1),'手动','','lesson-2')");assert.equal(f.run('successCharge'),1.5);assert.equal(f.snapshot().students[0].hours,8.5);assert.equal(f.snapshot().transactions[0].amount,-1.5);
 f.run("performCheckin(studentById(1),'手动')");const state=f.snapshot();assert.equal(state.attendance.length,2);assert.equal(state.transactions.length,2);assert.equal(state.students[0].hours,7.5);
 assert.deepEqual(Array.from(state.attendance,row=>row.deviceLessonId),['lesson-2','lesson']);
});

function conflictFixture(){
 const f=fixture();f.run('state.managementSync.protocol=2;committedStateSnapshot=JSON.stringify(state);let isAdminAuthenticated=true;');f.disk.raw=f.run('committedStateSnapshot');
 assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,true);const eventRef=f.snapshot().managementSync.queue[0].eventRef;
 assert.equal(f.run(`ManagementCheckinQueue.block(canonicalGuardedStore(),${JSON.stringify(eventRef)},'attendance_conflict').ok`),true);
 return{...f,eventRef};
}
function webReceipt(f,status='arrived'){
 const entry=f.snapshot().managementSync.queue[0],p=entry.payload;
 return{protocol:2,org_id:'org',device_id:'device',generation:'generation',accepted:true,resolved:true,duplicate:false,
  event_id:'web-event-1',event_ref:entry.eventRef,student_id:p.student_id,lesson_id:p.lesson_id,status,apply_local_debit:false,
  attendance_revision:p.attendance_revision+1,balance_revision:p.balance_revision+1,balance_units:status==='leave'?p.previous_balance_units:p.remaining_units};
}

test('actual conflict action keeps pending resolution blocked and uploads the exact original wire without token',async()=>{
 const f=conflictFixture(),calls=[],button={disabled:false},raw=f.disk.raw;f.c.button=button;
 f.bridge.requestCanonicalSync=(id,profile,kind,payload)=>{calls.push({id,profile,kind,payload});f.run('window.onCanonicalSyncNativeResult('+JSON.stringify(id)+','+JSON.stringify(JSON.stringify({ok:false,status:409,data:{error:{code:'resolution_pending'}}}))+')');};
 await f.run(`resolveCanonicalConflict(${JSON.stringify(f.eventRef)},button)`);const state=f.snapshot();
 assert.equal(calls.length,1);assert.equal(calls[0].kind,'resolve');assert.equal(calls[0].payload,state.managementSync.queue[0].wire);
 assert.equal(calls[0].payload.includes('token'),false);assert.equal(calls[0].payload.includes('generation'),false);assert.equal(f.disk.raw,raw);
 assert.equal(state.managementSync.queue[0].status,'blocked');assert.equal(button.disabled,false);assert(f.notes.some(n=>n.includes('网页尚未完成')));
});

test('actual conflict action archives arrived and leave receipts without another local debit',async()=>{
 for(const status of ['arrived','leave']){
  const f=conflictFixture(),calls=[],button={disabled:false};f.c.button=button;f.run('function render(){}');
  f.bridge.requestCanonicalSync=(id,profile,kind,payload)=>{calls.push({kind,payload});const data=webReceipt(f,status);f.run('window.onCanonicalSyncNativeResult('+JSON.stringify(id)+','+JSON.stringify(JSON.stringify({ok:true,status:200,data}))+')');};
  await f.run(`resolveCanonicalConflict(${JSON.stringify(f.eventRef)},button)`);const state=f.snapshot();
  assert.equal(calls.length,1);assert.equal(calls[0].kind,'resolve');assert.equal(state.managementSync.queue[0].status,'resolved');
  assert.equal(state.attendance[0].syncResolution,'web_resolved');assert.equal(state.attendance[0].serverStatus,status);assert.equal(state.attendance[0].serverEventId,'web-event-1');
  assert.equal(state.students[0].hours,9);assert.equal(state.attendance.length,1);assert.equal(state.transactions.length,1);assert.equal(button.disabled,true);
 }
});

test('actual conflict action rejects an altered success receipt without clearing evidence',async()=>{
 const f=conflictFixture(),button={disabled:false},raw=f.disk.raw;f.c.button=button;
 f.bridge.requestCanonicalSync=(id,profile,kind,payload)=>{const data={...webReceipt(f),apply_local_debit:true};f.run('window.onCanonicalSyncNativeResult('+JSON.stringify(id)+','+JSON.stringify(JSON.stringify({ok:true,status:200,data}))+')');};
 await f.run(`resolveCanonicalConflict(${JSON.stringify(f.eventRef)},button)`);assert.equal(f.disk.raw,raw);assert.equal(button.disabled,false);
 assert.equal(f.snapshot().managementSync.queue[0].status,'blocked');assert(f.notes.some(n=>n.includes('原冲突和签到证据仍保留')));
});

test('native protocol 2 transport bypasses legacy generation only for fixed runtime routes',()=>{
 const source=fs.readFileSync(path.join(repo,'app/src/main/java/cn/mashangchengzhang/studentmanager/CanonicalSyncTransport.java'),'utf8');
 assert.ok(source.includes('boolean protocol2=bootstrap||faces||kind.equals("resolve")||(kind.equals("checkin")&&!body.has("generation"));'));
 assert.ok(source.includes('if(!protocol2&&!body.getString("generation").equals(p.getString("g")))'));
 assert.ok(source.includes('case "faces":route="/v1/tablet/face-profiles";if(body.length()!=0)throw new IOException("fields");break;'));
 assert.ok(source.includes('faces?16*1024*1024:bootstrap?2*1024*1024:65536'));
 assert.ok(source.includes('case "resolve":route="/v1/tablet/checkins/resolve";fields(body,"event_ref student_id lesson_id attendance_revision balance_revision previous_balance_units consumed_units occurred_at");break;'));
 assert.ok(source.includes('else{route="/v1/tablet/checkins";fields(body,"event_ref student_id lesson_id attendance_revision balance_revision previous_balance_units consumed_units occurred_at");}'));
});
