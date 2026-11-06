const {test}=require('node:test');const assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');globalThis.crypto??=webcrypto;
const snapshots=require('../management/tablet/snapshot-store.js');
function fixture(){
 const state={students:[{id:1,hours:10}],classes:[],attendance:[],transactions:[],
  settings:{studentLeaves:[],displaySize:1},managementSync:{enabled:true,queue:[]}};
 const disk={raw:JSON.stringify(state),getItem(){return this.raw;},setItem(k,v){this.raw=v;}};
 return{disk,state,store:snapshots.create({storage:disk,key:'fixture'})};
}
test('one durable CAS publishes same in-memory state and survives process restart',()=>{
 const f=fixture(),before=f.store.read(),next=JSON.parse(before);next.students[0].hours=9;next.managementSync.queue.push({eventRef:'one'});
 assert.equal(f.store.compareAndSwap(before,JSON.stringify(next)),true);assert.deepEqual(f.store.snapshot(),next);
 const restarted=snapshots.create({storage:f.disk,key:'fixture'});assert.deepEqual(restarted.snapshot(),next);
 assert.equal(f.store.compareAndSwap(before,JSON.stringify(f.state)),false);
});
test('full disk retains original memory and durable snapshot',()=>{
 const f=fixture(),before=f.store.read();f.disk.setItem=()=>{throw Error('full disk');};
 assert.throws(()=>f.store.compareAndSwap(before,JSON.stringify({...f.state,marker:'new'})));
 assert.equal(f.store.read(),before);assert.deepEqual(f.store.snapshot(),f.state);
});
test('unsaved UI draft blocks incoming async writes; no draft is silently discarded',()=>{
 const f=fixture(),before=f.store.read(),edit=f.store.beginEdit();edit.value.settings.displaySize=2;
 assert.throws(()=>f.store.read(),e=>e.code==='unsaved_changes');assert.equal(f.store.compareAndSwap(before,before),false);
 assert.equal(f.store.saveEdit(edit.token),true);assert.equal(f.store.snapshot().settings.displaySize,2);
 edit.value.settings.displaySize=99;assert.equal(f.store.snapshot().settings.displaySize,2); // retained UI ref cannot mutate committed state
 assert.equal(f.store.compareAndSwap(before,before),false);
});
test('ordinary UI save cannot overwrite canonical attendance, balances, classes or queue',()=>{
 for(const change of [s=>s.students[0].hours=99,s=>s.attendance.push({id:1}),s=>s.managementSync.queue.push({status:'pending'}),
  s=>s.settings.studentLeaves.push({id:1}),s=>s.classes.push({id:2}),s=>s.deviceSync={captureEnabled:true}]){
  const f=fixture(),raw=f.store.read(),edit=f.store.beginEdit();change(edit.value);
  assert.throws(()=>f.store.saveEdit(edit.token),e=>e.code==='canonical_fields_protected');assert.equal(f.disk.raw,raw);
  assert.equal(f.store.status().editing,true);f.store.cancelEdit(edit.token);assert.equal(f.store.read(),raw);
 }
});
test('external writer or replaced backup is detected without overwriting it',()=>{
 const f=fixture(),before=f.store.read();f.disk.raw=JSON.stringify({...f.state,external:'changed'});
 assert.throws(()=>f.store.read(),e=>e.code==='external_change');assert.equal(f.store.compareAndSwap(before,before),false);
 assert.equal(f.store.refresh().external,'changed');assert.equal(JSON.parse(f.store.read()).external,'changed');
});
test('renderer failure after durable save never falsely reports uncommitted or rolls back',()=>{
 const f=fixture(),store=snapshots.create({storage:f.disk,key:'fixture',onPublish:()=>{throw Error('render failure');}}),before=store.read();
 const next=JSON.stringify({...f.state,marker:'saved'});assert.equal(store.compareAndSwap(before,next),true);
 assert.equal(store.status().observerFailed,true);assert.equal(store.snapshot().marker,'saved');assert.equal(f.disk.raw,next);
});
test('discarding draft requires exact edit token; stale draft cannot be saved after cancel',()=>{
 const f=fixture(),edit=f.store.beginEdit();assert.throws(()=>f.store.cancelEdit(edit.token+1),e=>e.code==='edit_expired');
 assert.throws(()=>f.store.refresh(),e=>e.code==='unsaved_changes');f.store.cancelEdit(edit.token);
 assert.throws(()=>f.store.saveEdit(edit.token),e=>e.code==='edit_expired');assert.equal(f.store.status().editing,false);
});
