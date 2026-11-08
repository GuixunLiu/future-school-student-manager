'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {load,wrapper,validateResult}=require('../ops/deploy-pilot-standby.cjs'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'pilot-standby-client-'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
try{
 const old={format:'future-school-offline-pilot-stage',stage_complete:true,service_started:false,device_enabled:false,wechat_enabled:false,selected_students:16,work_directory:'/opt/wxnotify/app/single-class-offline-20261028121655-da40e468'};
 const receipt=path.join(dir,'fictional-stage.private.json');fs.writeFileSync(receipt,JSON.stringify(old));
 const c={host:'8.8.8.8',user:'fictional-deploy',port:22,standby_only:true,output_dir:dir,receipt:{path:receipt,sha256:sha(fs.readFileSync(receipt))}};
 for(const [key,name] of [['installer','install-pilot-standby.py'],['runtime','pilot-standby.py'],['helper','stage-single-class-pilot.py']]){const f=path.join(__dirname,'../ops',name);c[key]={path:f,sha256:sha(fs.readFileSync(f))};}
 const cfg=path.join(dir,'standby-deploy.private.json');fs.writeFileSync(cfg,JSON.stringify(c));assert.equal(load(dir).standby_only,true);
 for(const pause of [false,true]){const code=wrapper(c,pause);assert(!code.includes('password'));assert(!code.includes('sudo'));assert(code.includes(pause?"ns['pause']":"ns['install']"));const syntax=spawnSync(path.join(__dirname,'../.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read())'],{input:code,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);}
 const r={format:'future-school-pilot-standby-installed',schema_version:1,unit:'wxnotify-single-class-standby.service',stage_directory:old.work_directory,runtime_sha256:c.runtime.sha256,port:18084,loopback_only:true,standby_ready:true,selected_students:16,source_candidate_unchanged:true,existing_services_modified:false,business_writes_enabled:false,lesson_created:false,device_enabled:false,wechat_enabled:false,service_state:'ActiveState=active\nSubState=running\nUnitFileState=enabled'};
 assert.equal(validateResult(JSON.stringify(r),c).standby_ready,true);
 for(const change of [{business_writes_enabled:true},{port:18083},{loopback_only:false},{device_enabled:true},{stage_directory:'/opt/other'},{runtime_sha256:'0'.repeat(64)}])assert.throws(()=>validateResult(JSON.stringify({...r,...change}),c));
 const paused={format:'future-school-pilot-standby-paused',schema_version:1,unit:r.unit,stage_directory:old.work_directory,existing_services_modified:false,service_state:'ActiveState=inactive\nUnitFileState=disabled'};assert.equal(validateResult(JSON.stringify(paused),c,true).unit,r.unit);
 assert.throws(()=>validateResult(JSON.stringify({...paused,service_state:'ActiveState=active'}),c,true));
 c.standby_only=false;fs.writeFileSync(cfg,JSON.stringify(c));assert.throws(()=>load(dir));
 console.log('Pilot standby client passed: pinned actual-stage proof and programs, install/pause wrapper syntax, loopback-only read-only receipt guards. No network.');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
