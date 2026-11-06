'use strict';
// Execute the real stdin framing/hold protocol locally with a fake installer: no SSH or service commands.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {wrapper}=require('../ops/deploy-scoped-pilot.cjs');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'scoped-bootstrap-')),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function file(name,bytes){const p=path.join(dir,name);fs.writeFileSync(p,bytes);return {path:p,sha256:sha(fs.readFileSync(p))};}
(async()=>{let child;
 try{
  const c={actor_login:'fictional-owner',files:{},stage_receipt:file('stage.json',JSON.stringify({work_directory:'/opt/wxnotify/app/single-class-offline-20261028121655-da40e468'})),standby_receipt:file('standby.json','{}'),plan:file('plan.json','{}')};
  c.installer=file('fake-installer.py','def install(config,files):\n return {"fictional_protocol":True}\ndef pause(config):\n return {"fictional_pause":True}\n');
  const python=path.join(__dirname,'../.venv/Scripts/python.exe');
  const bootstrap='import base64,sys,signal; signal.SIGHUP=getattr(signal,"SIGHUP",signal.SIGTERM); exec(compile(base64.b64decode(sys.stdin.buffer.readline(524289).strip(),validate=True),"<scoped-bootstrap>","exec"))';
  child=spawn(python,['-u','-c',bootstrap],{windowsHide:true,stdio:['pipe','pipe','pipe']});let errors='';child.stderr.on('data',b=>errors+=b);
  const ended=new Promise(resolve=>child.once('close',resolve));
  const first=new Promise((resolve,reject)=>{let raw='';const timer=setTimeout(()=>reject(Error('No protocol frame: '+errors)),5000);child.on('error',reject);child.stdout.on('data',b=>{raw+=b;const n=raw.indexOf('\n');if(n>=0){clearTimeout(timer);try{resolve(JSON.parse(raw.slice(0,n)))}catch(e){reject(e)}}});child.once('close',()=>{clearTimeout(timer);reject(Error('Protocol closed early: '+errors))});});
  child.stdin.write(wrapper(c));assert.equal((await first).fictional_protocol,true);assert.equal(child.exitCode,null);
  child.stdin.end();assert.equal(await ended,0);
  console.log('Scoped stdin protocol passed: bounded newline frame, result before EOF, connection held until EOF, clean exit. Fake installer only; no network or service operations.');
 }finally{if(child&&child.exitCode===null)child.kill();if(path.dirname(dir)!==path.resolve(os.tmpdir()))throw Error('Unexpected test cleanup path');fs.rmSync(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
