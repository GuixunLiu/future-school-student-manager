'use strict';
const assert=require('node:assert/strict');
const {checkHealth,sshArguments,waitReady}=require('../ops/roster-review-browser.cjs');
const good={status:'ready',version:'0.5.0',mode:'roster_review',archive_enabled:true,roster_review_only:true,business_ready:false,wechat_sending_enabled:false,student_sync_enabled:false,parent_binding_enabled:false};
(async()=>{
 assert.equal(checkHealth(good),good);
 for(const changed of [{roster_review_only:false},{mode:'local_test'},{wechat_sending_enabled:true},{student_sync_enabled:true},{parent_binding_enabled:true},{version:'0.4.0'},{}]){
  if(Object.keys(changed).length)assert.throws(()=>checkHealth({...good,...changed}));else assert.throws(()=>checkHealth({}));
 }
 const args=sshArguments({host:'example.invalid',port:22,user:'fixture-deploy'});
 assert(args.includes('StrictHostKeyChecking=yes'));assert(args.includes('ExitOnForwardFailure=yes'));assert(args.includes('-N'));assert(args.includes('-T'));assert(args.includes('127.0.0.1:18082:127.0.0.1:18082'));assert.equal(args.at(-1),'fixture-deploy@example.invalid');assert(!args.join(' ').includes('password'));assert(!args.join(' ').includes('python'));
 let calls=0;assert.equal(await waitReady({read:async()=>{if(!calls++)throw Error('not ready');return good},sleep:async()=>{},now:()=>0}),good);assert.equal(calls,2);
 await assert.rejects(waitReady({read:async()=>good,failed:()=>Error('SSH failed'),now:()=>0}),/SSH failed/);
 await assert.rejects(waitReady({read:async()=>({...good,version:'other'}),now:()=>0}),/只读/);
 let clock=0;await assert.rejects(waitReady({read:async()=>{throw Error('offline')},sleep:async()=>{clock+=60000},now:()=>clock}),/超时/);
 console.log('Browser link passed: strict host keys, loopback-only forward, no remote command or password argument, readiness gate, wrong-mode refusal, SSH failure and bounded timeout. No network or browser opened.');
})().catch(error=>{console.error(error);process.exitCode=1});
