'use strict';
// Exercise the actual SSH stdin framing with a fictional installer and a large payload.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {frames,validateResult}=require('../ops/deploy-teacher-web.cjs');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'teacher-web-protocol-')),made=[];
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function pin(name,body){const p=path.join(tmp,name);fs.writeFileSync(p,body);made.push(p);return {path:p,sha256:sha(Buffer.from(body))};}
try{
 const dataset={format:'future-school-teacher-web-dataset',classes:[{}],source_records:2,source_sha256:'a'.repeat(64),fictional_padding:'x'.repeat(600000)};
 const installer=pin('installer.py',"def install(config,payload):\n    import base64,json\n    data=json.loads(base64.b64decode(payload['dataset']['base64']))\n    return {'fictional':True,'characters':len(data['fictional_padding']),'mode':config['makeup_mode']}\n");
 const file=pin('gateway.py','# fictional source\n'),data=pin('dataset.json',JSON.stringify(dataset));
 const c={installer,dataset:data,files:{'teacher_web/gateway.py':file},public_origin:'http://47.97.226.219:18083',expires_at:1794391683,makeup_mode:'request_pending_tablet'};
 const input=frames(c);assert(input.length>600000);
 const bootstrap='import base64,sys; exec(compile(base64.b64decode(sys.stdin.buffer.readline(262145).strip(),validate=True),"<fictional-bootstrap>","exec"))';
 const p=spawnSync(path.resolve(__dirname,'../.venv/Scripts/python.exe'),['-u','-c',bootstrap],{input,windowsHide:true,maxBuffer:100000,encoding:'utf8'});
 assert.equal(p.status,0,p.stderr);assert.deepEqual(JSON.parse(p.stdout),{fictional:true,characters:600000,mode:'request_pending_tablet'});
 const receipt={format:'future-school-teacher-web-installed',schema_version:1,public_url:c.public_origin,expires_at:c.expires_at,makeup_mode:c.makeup_mode,gateway_sha256:file.sha256,dataset_sha256:data.sha256,source_sha256:dataset.source_sha256,summary:{classes:1,source_records:2},accounts_preserved:true,old_roster_database_modified:false,tablet_modified:false,hours_deducted:false,wechat_enabled:false,device_sync_enabled:false,service_state:'ActiveState=active\nUnitFileState=enabled'};
 assert.equal(validateResult(receipt,c),receipt);assert.throws(()=>validateResult({...receipt,hours_deducted:true},c));
 fs.appendFileSync(data.path,' ');assert.throws(()=>frames(c));
 console.log('Teacher web deployment protocol passed: large framed stdin, Python bootstrap, pinned receipt and changed-input rejection. Fictional data only.');
}finally{for(const p of made)fs.unlinkSync(p);fs.rmdirSync(tmp);}
