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
 const c=vm.createContext({console,crypto:webcrypto,TextEncoder,TextDecoder,atob,Date:FixedDate,localStorage:disk,AndroidBridge:bridge,notes,initial:state,
 setTimeout:()=>1,setInterval:()=>1,clearTimeout:()=>{},document:{hidden:false,addEventListener(){},getElementById(){return null;}}});
 vm.runInContext("globalThis.window=globalThis;window.addEventListener=()=>{};let state=initial;const STORAGE_KEY='fixture';let committedStateSnapshot=JSON.stringify(state);let nonce=0;function syncNonce(){return String(++nonce).padStart(32,'0');}function invalidatePageCache(){}function toast(s){notes.push(s);}function localDate(){return '2026-11-01';}function studentById(id){return state.students.find(s=>s.id===id);}const listPages={attendance:1};",c);
 for(const file of ['snapshot-store.js','checkin-queue.js','approval-receiver.js','runtime-bootstrap.js','sync-runtime.js','activation-commit.js'])vm.runInContext(fs.readFileSync(path.join(repo,'server/management/tablet',file),'utf8'),c);
 vm.runInContext(fs.readFileSync(path.join(repo,'app/src/main/assets/canonical-app.js'),'utf8'),c);
 vm.runInContext(fn('function persistCurrentState(){','function rollbackUnsavedState'),c);
 vm.runInContext(fn('function saveState(){','async function optimizeExistingFacePhotos'),c);
 vm.runInContext(fn('function recordStudentAttendance(studentId,method){','function recordHoursAddition'),c);
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
 vm.runInContext('function emptyState(){return {settings:{}};}'+fn('function loadState(){','function classReminderCalendar'),f.c);
 assert.equal(f.run('loadState().managementSync.enabled'),true);assert.equal(recover,0);
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
 assert.equal(f.run('canonicalRebindPending'),false);assert.equal(requests.length,1);assert.equal(requests[0].profile,'profile-2');assert(!requests[0].payload.includes('token'));
});

test('actual App refuses native rebind while an offline check-in awaits upload',async()=>{
 const f=fixture();f.run('state.managementSync.protocol=2;committedStateSnapshot=JSON.stringify(state);');f.disk.raw=f.run('committedStateSnapshot');
 f.run('let isAdminAuthenticated=true;');assert.equal(f.run("recordStudentAttendance(1,'手动')").ok,true);const raw=f.disk.raw;let calls=0;f.bridge.reissueRuntime=()=>calls++;
 await f.run('startRuntimeReissue()');assert.equal(calls,0);assert.equal(f.disk.raw,raw);assert.equal(f.run('canonicalRebindPending'),false);
 assert(f.notes.some(n=>n.includes('未上传')));assert.equal(f.snapshot().managementSync.queue[0].status,'pending');
});
