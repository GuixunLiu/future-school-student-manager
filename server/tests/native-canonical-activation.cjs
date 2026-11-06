'use strict';
// Fictional, isolated package only. Never opens or edits the daily student store.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),tool=path.resolve(repo,'../android-toolchain');
const adb=path.join(tool,'sdk/platform-tools/adb.exe'),java=path.join(tool,'jdk/jdk-17.0.20.1+1/bin/java.exe'),javac=path.join(tool,'jdk/jdk-17.0.20.1+1/bin/javac.exe');
const tls=path.resolve(repo,'../../outputs/private-web-mirror-2026-10-29'),base=path.resolve(repo,'../../outputs/canonical-activation-2026-11-02',crypto.randomUUID());
const pkg='cn.mashangchengzhang.studentmanager.synctest',daily='cn.mashangchengzhang.studentmanager',dir='/sdcard/Android/data/'+pkg+'/files/';
const cmd=args=>execFileSync(adb,['-s','HVA13XVR',...args],{encoding:'utf8',windowsHide:true,timeout:30000,maxBuffer:32*1024*1024}).trim();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const metadata=()=>cmd(['shell','dumpsys','package',daily]).split(/\r?\n/).filter(s=>/versionCode=|versionName=|firstInstallTime=|lastUpdateTime=/.test(s)).map(s=>s.trim());
let baseline='0'.repeat(64);
async function phase(name,value=baseline){
 const ref=crypto.randomUUID();cmd(['shell','am','force-stop',pkg]);cmd(['shell','am','start','-n',pkg+'/cn.mashangchengzhang.studentmanager.CanonicalEnrollmentProbeActivity','--es','phase',name,'--es','run_ref',ref,'--es','baseline',value]);
 for(let i=0;i<150;i++){try{const r=JSON.parse(cmd(['shell','cat',dir+'canonical-probe-result.json']));if(r.run_ref===ref)return r;}catch(_){}await sleep(200);}
 throw Error('Isolated native activation timed out: '+name);
}
(async()=>{
 fs.mkdirSync(base,{recursive:true});fs.mkdirSync(path.join(base,'java'));
 assert.equal(cmd(['shell','pm','list','packages',pkg]),'','Refuse to replace an existing test package');
 const before=metadata();let installed=false,server;
 try{
  execFileSync(javac,['-d',path.join(base,'java'),path.join(repo,'server/ops/CanonicalEnvelope.java')],{windowsHide:true});
  cmd(['install',path.join(repo,'app/build/outputs/apk/debug/app-debug.apk')]);installed=true;
  const captured=await phase('capture-large');assert.equal(captured.response.ok,true);assert(captured.response.data.bytes>2*1024*1024);
  const envelope=JSON.parse(cmd(['shell','cat',dir+captured.response.data.file_name]));const raw=Buffer.from(envelope.payload_base64,'base64').toString('utf8');baseline=sha(raw);assert.equal(baseline,envelope.sha256);
  assert.equal((await phase('prepare')).response.ok,true);const request=JSON.parse(cmd(['shell','cat',dir+'canonical-sync-request.json']));
  const pub=path.join(base,'fictional-public.der');fs.writeFileSync(pub,Buffer.from(request.public_key,'base64'));
  const now=Number(cmd(['shell','date','+%s'])),cutoff=(Math.floor((now+28800)/86400)+1)*86400-28800;
  const ids={org_id:'e'.repeat(32),device_id:'d'.repeat(32),generation:'a'.repeat(32),profile_id:'f'.repeat(32)};
  const plan={protocol:1,mode:'canonical_enrollment_staging',...ids,nonce:request.nonce,baseline_sha256:baseline,expires_at:now+900,enabled:false,activation_required:true};fs.writeFileSync(path.join(base,'public-plan.json'),JSON.stringify(plan));
  const source={classes:[{server_id:'c'.repeat(32),local_id:2,name:'Fictional Class',teacher_id:'9'.repeat(32),teacher_name:'Fictional Teacher'}],
   students:[{server_id:'8'.repeat(32),local_id:1,name:'Fictional Student',external_ref:'TEST001',class_id:'c'.repeat(32),class_local_id:2,balance_units:1000}],
   facts:[{lesson_id:'fictional-future',student_id:'8'.repeat(32),status:'missing',expected:true,charge_units:100,starts_at:cutoff+14*3600}],
   history_boundary:{org_id:ids.org_id,baseline_sha256:baseline,canonical_from:cutoff,boundary_sha256:'7'.repeat(64)}};
  const payload=require('../ops/build-canonical-bootstrap.cjs').build(raw,source,{...ids,baseline_sha256:baseline});
  const activation={protocol:1,mode:'canonical_activation',...ids,activation_id:'2'.repeat(32),nonce:'1'.repeat(32),baseline_sha256:baseline,bootstrap_sha256:payload.bootstrap_sha256,source_map_sha256:payload.source_map_sha256,offer_expires_at:now+900,active_until:now+3600,phase:'offered',old_mirror_writable:false};
  fs.writeFileSync(path.join(base,'activation-plan.json'),JSON.stringify(activation));
  server=spawn(path.join(repo,'server/.venv/Scripts/python.exe'),[path.join(__dirname,'serve_canonical_enrollment_fixture.py'),base,tls],{cwd:repo,windowsHide:true,stdio:['pipe','pipe','inherit']});
  await new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(Error('TLS fixture stopped')));});cmd(['reverse','tcp:18445','tcp:18445']);
  function transfer(value,bootstrap=false){
   const args=['-cp',path.join(base,'java'),'CanonicalEnvelope',...(bootstrap?['--bootstrap']:[]),pub,request.nonce];
   const encrypted=execFileSync(java,args,{input:JSON.stringify(value),encoding:'utf8',windowsHide:true,maxBuffer:32*1024*1024});
   const file=path.join(base,bootstrap?'fictional-bootstrap.enc.json':'fictional-connection.enc.json');fs.writeFileSync(file,encrypted);cmd(['push',file,dir+(bootstrap?'canonical-bootstrap.enc.json':'canonical-sync-connection.enc.json')]);
  }
  transfer({e:'https://127.0.0.1:18445',t:'wxd_'+'A'.repeat(43),d:ids.device_id,p:fs.readFileSync(path.join(repo,'app/src/main/res/raw/web_mirror_server_spki.txt'),'utf8').trim(),o:ids.org_id,g:ids.generation,id:ids.profile_id,b:baseline,n:request.nonce,x:plan.expires_at});
  assert.equal((await phase('stage')).response.ok,true);
  transfer({...payload,bootstrap_sha256:'0'.repeat(64)},true);assert.equal((await phase('activation-prepare')).response.ok,false);
  transfer(payload,true);assert.equal((await phase('activation-prepare','0'.repeat(64))).response.ok,false);
  const prepared=await phase('activation-prepare');assert.equal(prepared.response.ok,true);assert.equal(prepared.snapshot_sha256,payload.bootstrap_sha256);assert(prepared.snapshot_bytes>2*1024*1024);assert.equal(prepared.profile.configured,false);
  fs.writeFileSync(path.join(base,'commit-lost'),'');const lost=await phase('activation-commit');assert.equal(lost.response.ok,false);assert.equal(lost.profile.activationPending,true);assert.equal(lost.profile.configured,false);assert(fs.existsSync(path.join(base,'activation-active')));
  const blocked=await phase('request-next');assert.equal(blocked.response.ok,false);assert.equal(blocked.profile.activationPending,true);assert(!fs.existsSync(path.join(base,'next-requested')));
  fs.writeFileSync(path.join(base,'pause'),'');const paused=await phase('activation-commit');assert.equal(paused.response.ok,false);assert.equal(paused.profile.activationPending,true);
  fs.unlinkSync(path.join(base,'pause'));fs.unlinkSync(path.join(base,'commit-lost'));
  const committed=await phase('activation-commit');assert.equal(committed.response.ok,true);assert.equal(committed.response.data.activated,true);assert.equal(committed.profile.configured,true);assert.equal(committed.profile.staged,undefined);
  const next=await phase('request-next');assert.equal(next.response.ok,true);assert.equal(next.response.data.item,null);assert(fs.existsSync(path.join(base,'next-requested')));
  assert.equal((await phase('activation-commit')).response.ok,true);assert.equal(fs.readFileSync(path.join(base,'activation-active'),'utf8'),'1');
  fs.writeFileSync(path.join(base,'revoke'),'');assert.equal((await phase('request-next')).response.ok,false);assert.deepEqual(metadata(),before);
  const result={fictional_only:true,large_snapshot_bytes:prepared.snapshot_bytes,pinned_tls:true,rsa_aes_bootstrap:true,wrong_bootstrap_rejected:true,wrong_local_baseline_rejected:true,encrypted_atomic_bootstrap:true,pending_profile_survives_restart:true,pending_blocks_requests:true,lost_commit_retry:true,pause_preserves_pending:true,ack_before_writes:true,revocation_rejected:true,daily_app_metadata_unchanged:true};
  fs.writeFileSync(path.join(base,'native-activation-acceptance.json'),JSON.stringify(result,null,2));console.log('Native activation passed: >2MiB encrypted bootstrap, restart, lost reply, pause and acknowledgement; daily App unchanged.');console.log('Receipt: '+path.join(base,'native-activation-acceptance.json'));
 }finally{
  if(server){server.stdin.end();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);if(server.exitCode===null)server.kill();}
  try{cmd(['reverse','--remove','tcp:18445']);}catch(_){}
  if(installed){cmd(['uninstall',pkg]);cmd(['shell','am','start','-n',daily+'/.FutureSchoolLauncherActivity']);}
 }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
