'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),{EventEmitter}=require('node:events'),{spawnSync}=require('node:child_process');
const client=require('../ops/apply-teacher-access.cjs'),root=path.resolve(__dirname,'..'),base=path.resolve(os.tmpdir()),temp=fs.mkdtempSync(path.join(base,'teacher-access-client-'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
(async()=>{
 const plan={format:'future-school-confirmed-teacher-access',confirmed_by_owner:true,expected:{teachers:2,classes:2}},planPath=path.join(temp,'confirmed.json'),helper=path.join(temp,'helper.py');fs.writeFileSync(planPath,JSON.stringify(plan));fs.writeFileSync(helper,'# fictional helper\n');
 const config={host:'example.invalid',user:'fixture-deploy',port:22,work_directory:'/opt/wxnotify/app/roster-review-v0.5.0-aabbccdd-20261027065406-aabbccdd',output_dir:temp,plan_digest:'a'.repeat(64),expected:{teachers:2,new_accounts:1,classes:2},plan:{path:planPath,sha256:sha(fs.readFileSync(planPath))},helper:{path:helper,sha256:sha(fs.readFileSync(helper))}};
 fs.writeFileSync(path.join(temp,'teacher-access-config.private.json'),JSON.stringify(config));
 const loaded=client.load(temp),script=client.wrapper(config,loaded.files);
 const syntax=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read());print("ok")'],{input:script,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);assert(!script.includes('sudo'));assert(script.includes('O_EXCL'));assert(!script.includes('initial_password'));
 const receipt={plan_sha256:config.plan_digest,teachers:2,new_accounts:1,authorized_classes:2,owner_roles:['admin','teacher'],owner_password_changed:false,business_data_unchanged:true,roster_review_only:true,wechat_sending_enabled:false,backup_integrity:'ok',backup_sha256:'b'.repeat(64),original_pilot_still_active:true};
 let calls=0;const spawn=(command,args,settings)=>{calls++;assert.equal(command,'ssh');assert(args.includes('StrictHostKeyChecking=yes'));assert.equal(args.at(-1),'python3 -');assert.equal(settings.stdio[2],'inherit');const child=new EventEmitter();child.stdout=new EventEmitter();child.stdin=new EventEmitter();child.stdin.end=s=>{assert(s.includes('teacher-access.py'));queueMicrotask(()=>{child.stdout.emit('data',Buffer.from(JSON.stringify(receipt)));child.emit('close',0)})};return child};
 const r=await client.apply(temp,{spawnImpl:spawn});assert.equal(calls,1);assert(fs.existsSync(r.receipt_path));assert.throws(()=>client.result(JSON.stringify({...receipt,owner_password_changed:true}),config));assert.throws(()=>client.result(JSON.stringify({...receipt,new_accounts:2}),config));
 if(process.platform==='win32'){
  const payload=fs.readFileSync(path.join(root,'ops/apply-teacher-access.cmd'));assert([...payload].every(b=>b<128));assert(!payload.toString('ascii').replaceAll('\r\n','').includes('\n'));
  const entry=path.join(temp,'APPLY-TEACHER-ACCESS.cmd');fs.writeFileSync(entry,payload);fs.copyFileSync(path.join(root,'ops/apply-teacher-access.cjs'),path.join(temp,'apply-teacher-access.cjs'));
  const check=spawnSync(process.env.ComSpec||'cmd.exe',['/d','/c',entry,'--self-check'],{encoding:'utf8',windowsHide:true,timeout:30000});assert.equal(check.status,0,check.stderr);assert.equal(JSON.parse(check.stdout.trim()).network_connected,false);
 }
 fs.appendFileSync(planPath,'changed');assert.throws(()=>client.load(temp),/checksum/);
 console.log('Teacher access client passed: input hashes, Python wrapper syntax, exclusive private upload, one mocked strict-host-key SSH, checked result/no password reset, actual ASCII/CRLF Windows entry self-check, changed-plan refusal. No real network.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>{if(!path.resolve(temp).startsWith(base+path.sep+'teacher-access-client-'))throw Error('Unsafe cleanup');fs.rmSync(temp,{recursive:true,force:true});});
