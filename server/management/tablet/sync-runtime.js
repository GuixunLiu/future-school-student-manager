/* Candidate orchestration, no automatic startup or network credentials.
 * request(kind, payload) MUST be the authenticated pinned native transport;
 * kind maps to a fixed native route. profile() returns only non-secret IDs.
 */
(function(root){
  'use strict';
  const queue=typeof module!=='undefined'&&module.exports?require('./checkin-queue.js'):root.ManagementCheckinQueue;
  const receiver=typeof module!=='undefined'&&module.exports?require('./approval-receiver.js'):root.ManagementApprovalReceiver;
  function create({store,profile,request}){
    let busy=false;
    function identity(){
      const s=store.snapshot().managementSync,p=profile();
      if(!s||!s.enabled||!p||p.orgId!==s.orgId||p.deviceId!==s.deviceId||p.generation!==s.generation||!p.profileId||p.profileId!==s.profileId){const e=new Error('profile_changed');e.code='profile_changed';throw e;}
      return{orgId:s.orgId,deviceId:s.deviceId,generation:s.generation,profileId:s.profileId};
    }
    async function tick(){
      if(busy)return{ok:true,busy:true};busy=true;
      try{
        const initial=identity();
        const check=()=>{if(JSON.stringify(identity())!==JSON.stringify(initial)){const e=new Error('profile_changed');e.code='profile_changed';throw e;}};
        const guarded={read(){check();return store.read();},compareAndSwap(a,b){check();return store.compareAndSwap(a,b);}};
        const send=async(kind,body)=>{check();const response=await request(kind,body);check();return response;};
        const uploaded=await queue.pump(guarded,body=>send('checkin',body));
        if(!uploaded.ok||uploaded.blocked||uploaded.more)return{...uploaded,phase:'upload'};
        let received=0;
        for(let i=0;i<10;i++){
          const response=await send('next',{generation:initial.generation});
          if(!response?.ok)return{ok:false,phase:'download',code:'connection_failed',received,retry:true};
          const envelope=response.data;
          if(!envelope||envelope.org_id!==initial.orgId||envelope.device_id!==initial.deviceId||envelope.generation!==initial.generation)return{ok:false,code:'profile_changed',received};
          if(envelope.item===null)return{ok:true,uploaded:uploaded.uploaded,received};
          const result=await receiver.receive(guarded,envelope);
          if(!result.ok){
            if(['local_changes','attendance_changed','balance_changed','storage_failed'].includes(result.code)){
              // These errors occur only after envelope/digest validation. A
              // conflict is distinct from success; do not advance the inbox.
              const event=JSON.parse(envelope.item.event_json);
              await send('receipt',{generation:initial.generation,event_id:event.event_id,
                receipt_token:envelope.item.receipt_token,sha256:envelope.item.sha256,status:'conflict',conflict_code:result.code});
            }
            return{...result,phase:'apply',received};
          }
          const ack=await send('receipt',result.receipt);
          if(!ack?.ok||ack.data?.event_id!==result.receipt.event_id||ack.data.status!=='applied')return{ok:false,phase:'receipt',code:'receipt_unconfirmed',received,retry:true};
          received++;
        }
        return{ok:true,uploaded:uploaded.uploaded,received,more:true};
      }catch(e){return{ok:false,code:e.code||'offline',retry:true};}
      finally{busy=false;}
    }
    return{tick};
  }
  const api={create};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementSyncRuntime=api;
})(globalThis);
