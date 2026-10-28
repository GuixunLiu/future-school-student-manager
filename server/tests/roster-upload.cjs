'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict'),crypto=require('node:crypto'),{spawnSync}=require('node:child_process'),{EventEmitter}=require('node:events');
const uploader=require('../ops/upload-roster-review.cjs'),root=path.resolve(__dirname,'..'),base=path.resolve(os.tmpdir()),temp=fs.mkdtempSync(path.join(base,'roster-upload-test-'));
(async()=>{
 const files=['release.zip','bundle.json','server-review.json','stage.py'].map(name=>{const p=path.join(temp,name),bytes=Buffer.from('Fictional fixture '+name);fs.writeFileSync(p,bytes);return{name,path:p,sha256:crypto.createHash('sha256').update(bytes).digest('hex')}});
 const plan={version:'0.5.0',host:'example.invalid',user:'fixture-deploy',port:22,output_dir:temp,files,manifest_sha256:'a'.repeat(64),expected:{students:3,active:1,paused:2,classes:1,active_remaining_units:-150,paused_remaining_units:2300}},p=path.join(temp,'plan.json');fs.writeFileSync(p,JSON.stringify(plan));
 const loaded=uploader.loadPlan(p),script=uploader.makeWrapper(plan,loaded.payloads,{suffix:'aabbccdd'});
 const syntax=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read());print("ok")'],{input:script,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);
 assert(script.includes('O_EXCL'));assert(script.includes("exist_ok=False"));assert(!script.includes('sudo'));assert(!script.includes('password'));
 const result={review_service:'wxnotify-roster-review.service',loopback_port:18082,original_pilot_still_active:true,original_pilot_replaced:false,health:{version:'0.5.0',roster_review_only:true},migration:{integrity:'ok',bundle_sha256:files[1].sha256,...plan.expected}};
 let calls=0;const fakeSpawn=(command,args,opts)=>{calls++;assert.equal(command,'ssh');assert(args.includes('StrictHostKeyChecking=yes'));assert.equal(args.at(-1),'python3 -');assert.equal(opts.stdio[2],'inherit');const child=new EventEmitter();child.stdout=new EventEmitter();child.stdin=new EventEmitter();child.stdin.end=s=>{assert(s.includes('runpy.run_path'));queueMicrotask(()=>{child.stdout.emit('data',Buffer.from(JSON.stringify(result)));child.emit('close',0)})};return child};
 const receipt=await uploader.upload(p,{spawnImpl:fakeSpawn});assert.equal(calls,1);assert(fs.existsSync(receipt.path));assert.equal(receipt.paused,2);assert.throws(()=>uploader.validateResult(JSON.stringify({...result,migration:{...result.migration,paused:0}}),plan));
 if(process.platform==='win32'){
  const entry=path.join(temp,'虚构部署入口.cmd'),cmd=fs.readFileSync(path.join(root,'ops/windows-review-launcher.cmd'));
  assert([...cmd].every(b=>b<128),'CMD source must be ASCII');assert(!cmd.toString('ascii').replaceAll('\r\n','').includes('\n'),'CMD source must use CRLF');
  fs.writeFileSync(entry,cmd);fs.writeFileSync(path.join(temp,'review-plan.private.json'),JSON.stringify(plan));
  for(const name of ['windows-review-launcher.cjs','upload-roster-review.cjs'])fs.copyFileSync(path.join(root,'ops',name),path.join(temp,name));
  const check=spawnSync(process.env.ComSpec||'cmd.exe',['/d','/c',entry,'--self-check'],{encoding:'utf8',windowsHide:true,timeout:30000});
  assert.equal(check.status,0,check.stderr);assert.equal(check.stderr,'');const verified=JSON.parse(check.stdout.trim());assert.equal(verified.launcher,'ok');assert.equal(verified.network_connected,false);assert.equal(verified.files,4);assert.deepEqual(verified.expected,plan.expected);
 }
 fs.appendFileSync(files[1].path,'changed');assert.throws(()=>uploader.loadPlan(p),/checksum/);
 const def=spawnSync(process.execPath,[path.join(root,'ops/upload-roster-review.cjs')],{encoding:'utf8'});assert.equal(def.status,0);assert(def.stdout.includes('显式'));
 console.log('Roster uploader passed: checksum refusal before upload, Python wrapper syntax, exclusive private writes, one mocked strict-host-key SSH, no credentials in script/arguments, checked totals, safe default, actual Windows CMD/Chinese filename self-check with ASCII/CRLF entry. No real network.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>{if(!path.resolve(temp).startsWith(base+path.sep+'roster-upload-test-'))throw Error('Unsafe cleanup');fs.rmSync(temp,{recursive:true,force:true});});
