'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {load,wrapper,validateResult}=require('../ops/stage-single-class-pilot.cjs'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'offline-pilot-client-'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
try{
 const p={format:'future-school-owner-confirmed-pilot-lesson',owner_confirmed:true,plan_only:true,ready_to_enable:false,starts_at:new Date(Date.now()+86400000).toISOString(),expected_students:2,members:[{expected_remaining_units:-150},{expected_remaining_units:300}]},plan=path.join(dir,'fictional-plan.private.json');fs.writeFileSync(plan,JSON.stringify(p));
 const helper=path.join(__dirname,'../ops/stage-single-class-pilot.py');
 const c={host:'8.8.8.8',user:'fictional-deploy',port:22,source_database:'/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/data/roster.sqlite',offline_only:true,output_dir:dir,helper:{path:helper,sha256:sha(fs.readFileSync(helper))},plan:{path:plan,sha256:sha(fs.readFileSync(plan))}};
 const config=path.join(dir,'offline-stage.private.json');fs.writeFileSync(config,JSON.stringify(c));assert.equal(load(dir).offline_only,true);
 const code=wrapper(c);assert(!code.includes('sudo'));assert(!code.includes('systemctl'));assert(!code.includes('password'));
 const syntax=spawnSync(path.join(__dirname,'../.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read())'],{input:code,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);
 const r={format:'future-school-offline-pilot-stage',schema_version:1,stage_complete:true,work_directory:'/opt/wxnotify/app/single-class-offline-20261028000000-aabbccdd',plan_sha256:c.plan.sha256,selected_students:2,selected_remaining_units:150,users_preserved:8,source_opened_read_only:true,contains_private_data:true,candidate_sessions_cleared:true,candidate_devices_disabled:true,candidate_notifications_disabled:true,existing_services_modified:false,service_started:false,lesson_created:false,device_enabled:false,history_replayed:false,wechat_enabled:false,baseline_backup_sha256:'a'.repeat(64),candidate_sha256:'b'.repeat(64),preserved_data_sha256:'c'.repeat(64)};
 assert.equal(validateResult(JSON.stringify(r),c).selected_students,2);
 for(const change of [{service_started:true},{lesson_created:true},{device_enabled:true},{selected_remaining_units:151},{work_directory:'/opt/existing'},{candidate_sessions_cleared:false}])assert.throws(()=>validateResult(JSON.stringify({...r,...change}),c));
 const failure={format:'future-school-offline-pilot-stage-failed',schema_version:1,stage_complete:false,work_directory:r.work_directory,source_write_requested:false,service_started:false,reason:'fictional stale balance'};assert.equal(validateResult(JSON.stringify(failure),c).stage_complete,false);
 c.source_database='/opt/existing.sqlite';fs.writeFileSync(config,JSON.stringify(c));assert.throws(()=>load(dir));
 console.log('Offline pilot client passed: fixed inputs and source, new directory only, Python wrapper syntax, no service startup, truthful success/failure receipts. No network.');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
