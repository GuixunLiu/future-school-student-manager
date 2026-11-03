/* Candidate orchestration, no automatic startup or network credentials.
 * request(kind, payload) MUST be the authenticated pinned native transport;
 * kind maps to a fixed native route. profile() returns only non-secret IDs.
 */
(function(root){
  'use strict';
  const queue=typeof module!=='undefined'&&module.exports?require('./checkin-queue.js'):root.ManagementCheckinQueue;
  const receiver=typeof module!=='undefined'&&module.exports?require('./approval-receiver.js'):root.ManagementApprovalReceiver;
  const classTeachers=typeof module!=='undefined'&&module.exports?require('./class-teacher-receiver.js'):root.ManagementClassTeacherReceiver;
  function create({store,profile,request,metadata=false,historical=false}){
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
        if(historical){
          const ready=await send('history-ready',{generation:initial.generation});
          if(ready?.status!==404){const r=ready?.data;if(!ready?.ok||r?.org_id!==initial.orgId||r?.device_id!==initial.deviceId||r?.generation!==initial.generation||r?.historical_protocol!==1)return{ok:false,phase:'history-ready',retry:true};}
        }
        if(metadata){
          const ready=await send('class-ready',{generation:initial.generation});
          if(ready?.status!==404){
            const r=ready?.data;if(!ready?.ok||r?.org_id!==initial.orgId||r?.device_id!==initial.deviceId||r?.generation!==initial.generation||r?.class_teacher_protocol!==1)return{ok:false,phase:'class-ready',retry:true};
            for(let n=0;n<10;n++){
              const next=await send('class-next',{generation:initial.generation}),v=next?.data;
              if(!next?.ok||v?.org_id!==initial.orgId||v?.device_id!==initial.deviceId||v?.generation!==initial.generation)return{ok:false,phase:'class-download',retry:true};
              if(v.item===null)break;
              const applied=await classTeachers.receive(guarded,v);if(!applied.ok){
                if(['mapping_changed','local_changes','storage_failed'].includes(applied.code)){
                  const e=JSON.parse(v.item.event_json);await send('class-receipt',{generation:initial.generation,class_id:e.class_id,revision:e.revision,sha256:v.item.sha256,receipt_token:v.item.receipt_token,status:'conflict'});
                }
                return{...applied,phase:'class-apply',retry:true};
              }
              const ack=await send('class-receipt',applied.receipt);
              if(!ack?.ok||ack.data?.class_id!==applied.receipt.class_id||ack.data?.revision!==applied.receipt.revision||ack.data?.status!=='applied')return{ok:false,phase:'class-receipt',retry:true};
              if(n===9)return{ok:true,phase:'class-download',more:true};
            }
          }
        }
        let received=0;
        for(let i=0;i<10;i++){
          const response=await send('next',{generation:initial.generation});
          if(!response?.ok){
            const paused=response?.status===409&&response?.data?.error?.code==='migration_in_progress';
            return{ok:false,phase:'download',code:paused?'migration_in_progress':'connection_failed',received,retry:true,...(paused?{paused:true}:{})};
          }
          const envelope=response.data;
          if(!envelope||envelope.org_id!==initial.orgId||envelope.device_id!==initial.deviceId||envelope.generation!==initial.generation)return{ok:false,code:'profile_changed',received};
          if(envelope.item===null)return{ok:true,uploaded:uploaded.uploaded,received};
          const result=await receiver.receive(guarded,envelope);
          if(!result.ok){
            if(['mapping_changed','local_changes','attendance_changed','balance_changed','storage_failed'].includes(result.code)){
              // These errors occur only after envelope/digest validation. A
              // conflict is distinct from success; do not advance the inbox.
              const event=JSON.parse(envelope.item.event_json);
              await send('receipt',{generation:initial.generation,event_id:event.event_id,
                receipt_token:envelope.item.receipt_token,sha256:envelope.item.sha256,status:'conflict',conflict_code:result.code});
            }
            return{...result,phase:'apply',received};
          }
          const ack=await send('receipt',result.receipt);
          if(ack?.status===409&&ack?.data?.error?.code==='migration_in_progress')return{ok:false,phase:'receipt',code:'migration_in_progress',received,retry:true,paused:true};
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
