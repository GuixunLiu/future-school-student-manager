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
 lessons:{lesson:{classId:'class',date:'2026-11-01',startsAt:now/1000,studentIds:['student']}},facts:{lesson:{student:{expected:true,status:'missing',revision:0,chargeUnits:100}}}}};
 const disk={raw:JSON.stringify(state),getItem(){return this.raw;},setItem(k,v){this.raw=v;}},notes=[];
 const bridge={getCanonicalSyncProfile:()=>JSON.stringify({configured:true,orgId:'org',deviceId:'device',generation:'generation',profileId:'profile'})};
 class FixedDate extends Date{static now(){return now;}}
 const c=vm.createContext({console,crypto:webcrypto,TextEncoder,Date:FixedDate,localStorage:disk,AndroidBridge:bridge,notes,initial:state,
 setTimeout:()=>1,setInterval:()=>1,clearTimeout:()=>{},document:{hidden:false,addEventListener(){},getElementById(){return null;}}});
 vm.runInContext("globalThis.window=globalThis;window.addEventListener=()=>{};let state=initial;const STORAGE_KEY='fixture';let committedStateSnapshot=JSON.stringify(state);let nonce=0;function syncNonce(){return String(++nonce).padStart(32,'0');}function invalidatePageCache(){}function toast(s){notes.push(s);}function localDate(){return '2026-11-01';}function studentById(id){return state.students.find(s=>s.id===id);}const listPages={attendance:1};",c);
 for(const file of ['snapshot-store.js','checkin-queue.js','approval-receiver.js','sync-runtime.js'])vm.runInContext(fs.readFileSync(path.join(repo,'server/management/tablet',file),'utf8'),c);
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
