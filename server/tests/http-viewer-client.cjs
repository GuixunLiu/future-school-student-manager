'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {load,wrapper,validateResult}=require('../ops/deploy-http-viewer.cjs'),base=os.tmpdir(),dir=fs.mkdtempSync(path.join(base,'http-viewer-client-'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
try{
 const c={host:'8.8.8.8',user:'fictional-deploy',port:22,public_origin:'http://8.8.8.8:18083',owner_accepted_plain_http:true,expires_after_days:14,output_dir:dir};
 for(const [key,name] of [['installer','install-http-viewer.py'],['viewer','temporary-http-viewer.py']]){const file=path.join(__dirname,'../ops',name);c[key]={path:file,sha256:sha(fs.readFileSync(file))}}
 fs.writeFileSync(path.join(dir,'http-viewer-plan.private.json'),JSON.stringify(c));assert.equal(load(dir).owner_accepted_plain_http,true);
 const code=wrapper(c);assert(!code.includes('password'));assert(code.includes('mode=0o700,exist_ok=False'));
 const syntax=spawnSync(path.join(__dirname,'../.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read())'],{input:code,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);
 const r={format:'future-school-temporary-http-viewer-deployed',schema_version:1,public_url:c.public_origin,service:'wxnotify-http-viewer.service',viewer_port:18083,loopback_backend_port:18082,viewer_sha256:c.viewer.sha256,owner_accepted_plain_http:true,local_ready:true,existing_services_unchanged:true,runtime_source_unchanged:true,work_directory:'/opt/wxnotify/app/http-viewer-20261028090000-aabbccdd',business_writes_enabled:false,data_realtime:false,database_directly_opened:false,nginx_or_firewall_modified:false,passwords_saved:false,external_access_verified:false,expires_at:Math.floor(Date.now()/1000)+14*86400,service_status:'ActiveState=active\nSubState=running\nUnitFileState=enabled'};
 assert.equal(validateResult(JSON.stringify(r),c).local_ready,true);
 for(const change of [{business_writes_enabled:true},{nginx_or_firewall_modified:true},{external_access_verified:true},{public_url:'http://evil.example'},{expires_at:1}])assert.throws(()=>validateResult(JSON.stringify({...r,...change}),c));
 c.owner_accepted_plain_http=false;fs.writeFileSync(path.join(dir,'http-viewer-plan.private.json'),JSON.stringify(c));assert.throws(()=>load(dir));
 console.log('HTTP viewer client passed: explicit approval, fixed inputs, safe Python wrapper, independent new paths, bounded expiry, local/external result separation and refusal of changed scope. No network.');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
