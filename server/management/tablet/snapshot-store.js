/* Candidate storage authority shared by check-in queue and approval receiver.
 * Owns the committed snapshot; UI gets clones and explicit editable drafts.
 * It deliberately does not call the legacy saveState/mirror/capture hooks.
 * Integration must make this the ONLY writer while canonical mode is active.
 */
(function(root){
  'use strict';
  const fail=code=>{const error=new Error(code);error.code=code;throw error;};
  const parse=raw=>{const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))fail('invalid_snapshot');return value;};
  const clone=value=>JSON.parse(JSON.stringify(value));
  const protectedView=s=>JSON.stringify({students:s.students,classes:s.classes,attendance:s.attendance,
    transactions:s.transactions,managementSync:s.managementSync,deviceSync:s.deviceSync,studentLeaves:s.settings?.studentLeaves});
  function create({storage,key,onPublish=()=>{}}){
    if(!storage||typeof storage.getItem!=='function'||typeof storage.setItem!=='function'||typeof key!=='string'||!key)fail('invalid_storage');
    let raw=storage.getItem(key),current=parse(raw),draft=null,editSequence=0,observerFailed=false;
    const clean=()=>{if(draft)fail('unsaved_changes');if(storage.getItem(key)!==raw)fail('external_change');};
    function publish(nextRaw){
      // Parse/allocate BEFORE durable commit. After setItem succeeds, assigning
      // local bindings cannot fail. Observer/UI failure never undoes the write.
      const next=parse(nextRaw);storage.setItem(key,nextRaw);raw=nextRaw;current=next;
      try{onPublish(clone(current),raw);observerFailed=false;}catch(_){observerFailed=true;}
    }
    return {
      read(){clean();return raw;},
      compareAndSwap(before,after){
        if(draft||before!==raw||storage.getItem(key)!==before)return false;
        publish(after);return true;
      },
      snapshot(){return clone(current);},
      beginEdit(){clean();draft={token:++editSequence,value:clone(current)};return{token:draft.token,value:draft.value};},
      saveEdit(token){
        if(!draft||draft.token!==token)fail('edit_expired');
        if(storage.getItem(key)!==raw)fail('external_change');
        if(current.managementSync?.enabled===true&&protectedView(draft.value)!==protectedView(current))fail('canonical_fields_protected');
        const next=JSON.stringify(draft.value);publish(next);draft=null;return true;
      },
      cancelEdit(token){if(!draft||draft.token!==token)fail('edit_expired');draft=null;},
      refresh(){if(draft)fail('unsaved_changes');const next=storage.getItem(key);const parsed=parse(next);raw=next;current=parsed;return clone(current);},
      status(){return{editing:!!draft,observerFailed};}
    };
  }
  const api={create};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementSnapshotStore=api;
})(globalThis);
