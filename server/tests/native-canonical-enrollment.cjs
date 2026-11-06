'use strict';
// Purpose-built native test Activity only; never opens the daily student store.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {execFileSync,spawn}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),base=path.resolve(repo,'../../outputs/canonical-enrollment-2026-11-01'),tls=path.resolve(repo,'../../outputs/private-web-mirror-2026-10-29');
const adb=path.resolve(repo,'../android-toolchain/sdk/platform-tools/adb.exe'),java=path.resolve(repo,'../android-toolchain/jdk/jdk-17.0.20.1+1/bin/java.exe');
const pkg='cn.mashangchengzhang.studentmanager.synctest',daily='cn.mashangchengzhang.studentmanager',dir='/sdcard/Android/data/'+pkg+'/files/';
const cmd=args=>execFileSync(adb,['-s','HVA13XVR',...args],{encoding:'utf8',windowsHide:true,timeout:30000}).trim();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let stageBaseline='c'.repeat(64);
const metadata=()=>cmd(['shell','dumpsys','package',daily]).split(/\r?\n/).filter(s=>/versionCode=|versionName=|firstInstallTime=|lastUpdateTime=/.test(s)).map(s=>s.trim());
async function phase(name){
 const ref=crypto.randomUUID();cmd(['shell','am','force-stop',pkg]);
 cmd(['shell','am','start','-n',pkg+'/cn.mashangchengzhang.studentmanager.CanonicalEnrollmentProbeActivity','--es','phase',name,'--es','run_ref',ref,'--es','baseline',stageBaseline]);
 for(let i=0;i<80;i++){
  try{const r=JSON.parse(cmd(['shell','cat',dir+'canonical-probe-result.json']));if(r.run_ref===ref)return r;}catch(_){}
  await sleep(200);
 }throw Error('Native stage timed out');
}
(async()=>{
 fs.mkdirSync(base,{recursive:true});assert(fs.existsSync(path.join(tls,'server.crt'))&&fs.existsSync(path.join(tls,'server.key')),'Existing private local test TLS fixture required');
 for(const marker of ['wrong-baseline','revoke']){const p=path.join(base,marker);if(fs.existsSync(p))fs.unlinkSync(p);}
 // Some Android builds return exit 1 for pm path on a missing package.
 // list packages returns a successful empty inventory without masking errors.
 assert.equal(cmd(['shell','pm','list','packages',pkg]),'','Refuse to replace a preexisting test application');
 const before=metadata();let installed=false,server;
 try{
  cmd(['install',path.join(repo,'app/build/outputs/apk/debug/app-debug.apk')]);installed=true;
  assert.equal((await phase('capture-bad-digest')).response.ok,false);assert.equal((await phase('capture-stale')).response.ok,false);
  const captured=await phase('capture');assert.equal(captured.response.ok,true);assert.equal(captured.profile.configured,false);
  const meta=captured.response.data;assert.match(meta.file_name,/^canonical-capture-[a-f0-9]{32}\.private\.json$/);
  const container=JSON.parse(cmd(['shell','cat',dir+meta.file_name]));
  const raw=Buffer.from(container.payload_base64,'base64');assert.equal(crypto.createHash('sha256').update(raw).digest('hex'),meta.sha256);assert.equal(raw.length,meta.bytes);
  const source=JSON.parse(raw),mapping={protocol:1,org_id:'e'.repeat(32),device_id:'d'.repeat(32),generation:'a'.repeat(32),
    classes:[{server_id:'class',local_id:2,name:'Fictional Class',teacher_id:'teacher',teacher_name:'Fictional Teacher'}],
    students:[{server_id:'student',local_id:1,external_ref:'TEST001',name:'Fictional Student',class_id:'class',class_local_id:2}],lessons:[]};
  const manifest=require('../ops/build-canonical-manifest.cjs').buildNative(container,mapping,Number(cmd(['shell','date','+%s'])));
  assert.equal(manifest.body.baseline_sha256,meta.sha256);assert.equal(manifest.body.source.students[0].balance_units,1000);assert.equal(source.students.length,1);stageBaseline=meta.sha256;
  const prepared=await phase('prepare');assert.equal(prepared.response.ok,true);assert.equal(prepared.profile.configured,false);
  const req=JSON.parse(cmd(['shell','cat',dir+'canonical-sync-request.json']));assert.equal(req.protocol,1);
  const pub=path.join(base,'fictional-device-public.der');fs.writeFileSync(pub,Buffer.from(req.public_key,'base64'));
  const expires=Number(cmd(['shell','date','+%s']))+3600;
  const plan={protocol:1,mode:'canonical_enrollment_staging',org_id:'e'.repeat(32),device_id:'d'.repeat(32),generation:'a'.repeat(32),profile_id:'f'.repeat(32),nonce:req.nonce,baseline_sha256:stageBaseline,expires_at:expires,enabled:false,activation_required:true};
  fs.writeFileSync(path.join(base,'public-plan.json'),JSON.stringify(plan));
  const profile={e:'https://127.0.0.1:18445',t:'wxd_'+'A'.repeat(43),d:plan.device_id,p:fs.readFileSync(path.join(repo,'app/src/main/res/raw/web_mirror_server_spki.txt'),'utf8').trim(),o:plan.org_id,g:plan.generation,id:plan.profile_id,b:plan.baseline_sha256,n:req.nonce,x:expires};
  server=spawn(path.join(repo,'server/.venv/Scripts/python.exe'),[path.join(__dirname,'serve_canonical_enrollment_fixture.py'),base,tls],{cwd:repo,windowsHide:true,stdio:['pipe','pipe','inherit']});
  await new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(Error('TLS fixture stopped')));});
  cmd(['reverse','tcp:18445','tcp:18445']);
  function transfer(p){const encrypted=execFileSync(java,['-cp',path.join(base,'java'),'CanonicalEnvelope',pub,req.nonce],{input:JSON.stringify(p),encoding:'utf8',windowsHide:true});const file=path.join(base,'fictional-connection.enc.json');fs.writeFileSync(file,encrypted);cmd(['push',file,dir+'canonical-sync-connection.enc.json']);}
  transfer({...profile,b:'0'.repeat(64)});const wrong=await phase('stage');assert.equal(wrong.response.ok,false);assert.equal(wrong.profile.configured,false);assert.notEqual(wrong.profile.staged,true);
  transfer(profile);fs.writeFileSync(path.join(base,'wrong-baseline'),'');const stale=await phase('stage');assert.equal(stale.response.ok,false);assert.notEqual(stale.profile.staged,true);fs.unlinkSync(path.join(base,'wrong-baseline'));
  const accepted=await phase('stage');assert.equal(accepted.response.ok,true);assert.equal(accepted.response.data.activated,false);assert.equal(accepted.profile.configured,false);assert.equal(accepted.profile.staged,true);assert.equal(accepted.profile.baselineSha256,plan.baseline_sha256);
  // Restart proves staged credentials actually persisted in Android Keystore.
  const repeated=await phase('stage');assert.equal(repeated.response.ok,true);assert.equal(repeated.profile.staged,true);assert.equal(repeated.profile.configured,false);
  fs.writeFileSync(path.join(base,'revoke'),'');const revoked=await phase('stage');assert.equal(revoked.response.ok,false);assert.equal(revoked.profile.staged,true);assert.equal(revoked.profile.configured,false);fs.unlinkSync(path.join(base,'revoke'));
  assert.deepEqual(metadata(),before);
  const result={fictional_only:true,native_atomic_capture:true,stale_capture_rejected:true,wrong_capture_digest_rejected:true,capture_manifest_roundtrip:true,pinned_tls_native:true,hybrid_native_decryption:true,wrong_local_baseline_rejected:true,wrong_server_baseline_rejected:true,staged_keystore_survives_restart:true,revoked_plan_rejected:true,auto_activation:false,daily_app_metadata_unchanged:true};
  fs.writeFileSync(path.join(base,'native-enrollment-acceptance.json'),JSON.stringify(result,null,2));console.log('Native encrypted enrollment passed; staged only, daily App unchanged.');
 }finally{
  if(server){server.stdin.end();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);if(server.exitCode===null)server.kill();}
  try{cmd(['reverse','--remove','tcp:18445']);}catch(_){}
  if(installed){try{cmd(['uninstall',pkg]);cmd(['shell','am','start','-n',daily+'/.FutureSchoolLauncherActivity']);}catch(_){}}
 }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
