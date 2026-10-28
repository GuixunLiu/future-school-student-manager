'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {load,wrapper,validateResult}=require('../ops/patch-http-viewer.cjs'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'http-viewer-patch-client-'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
try{
 const c={host:'8.8.8.8',user:'fictional-deploy',port:22,public_origin:'http://8.8.8.8:18083',work_directory:'/opt/wxnotify/app/http-viewer-20261028100749-15dcb449',expected_old_sha256:'a'.repeat(64),expires_at:Math.floor(Date.now()/1000)+86400,output_dir:dir};
 for(const [key,name] of [['patcher','patch-http-viewer.py'],['viewer','temporary-http-viewer.py']]){const file=path.join(__dirname,'../ops',name);c[key]={path:file,sha256:sha(fs.readFileSync(file))}}
 const plan=path.join(dir,'http-viewer-patch.private.json');fs.writeFileSync(plan,JSON.stringify(c));assert.equal(load(dir).expires_at,c.expires_at);
 const code=wrapper(c);assert(!code.includes('password'));assert(!code.includes('mkdir'));
 const syntax=spawnSync(path.join(__dirname,'../.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read())'],{input:code,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);
 const r={format:'future-school-http-viewer-logout-fixed',schema_version:1,public_url:c.public_origin,old_sha256:c.expected_old_sha256,new_sha256:c.viewer.sha256,expires_at:c.expires_at,logout_without_json_reaches_backend:true,anonymous_logout_rejected:true,unit_config_changed:false,database_opened:false,teacher_credentials_used:false,expiry_changed:false,other_services_modified:false};
 assert.equal(validateResult(JSON.stringify(r),c).expires_at,c.expires_at);
 for(const change of [{expires_at:c.expires_at+1},{database_opened:true},{teacher_credentials_used:true},{expiry_changed:true},{new_sha256:'0'.repeat(64)}])assert.throws(()=>validateResult(JSON.stringify({...r,...change}),c));
 c.work_directory='/opt/other';fs.writeFileSync(plan,JSON.stringify(c));assert.throws(()=>load(dir));
 console.log('HTTP logout patch client passed: pinned code, scoped existing directory, unchanged expiry, syntax and receipt guards. No network or real credentials.');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
