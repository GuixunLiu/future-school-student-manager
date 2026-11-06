'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),{EventEmitter}=require('node:events');
const {load,rpcProcess}=require('../ops/run-canonical-cutover.cjs'),root=path.resolve(__dirname,'../..'),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function fixture(){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'fictional-cutover-client-')),now=Math.floor(Date.now()/1000),raw=JSON.stringify({fictional:true});
 const put=(name,value)=>{const p=path.join(dir,name),b=Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value));fs.writeFileSync(p,b);return{path:p,sha256:sha(b)};};
 const key=crypto.generateKeyPairSync('rsa',{modulusLength:3072}).publicKey.export({type:'spki',format:'der'});
 const cfg={format:'future-school-controlled-cutover-v1',organization:'weilai',deviceSerial:'HVA13XVR',webLogin:'liuguixun',sourcePins:Object.fromEntries(['server/ops/run-canonical-cutover.cjs','server/ops/canonical-cutover-broker.py'].map(n=>[n,sha(fs.readFileSync(path.join(root,n)))])),
  code:put('fictional-code.zip',Buffer.from('fictional code checksum only; real archive validated remotely before pause')),
  capture:put('fictional-capture.json',{format:'future-school-native-capture-v1',captured_at:now,bytes:Buffer.byteLength(raw),sha256:sha(raw),payload_base64:Buffer.from(raw).toString('base64')}),
  request:put('fictional-request.json',{protocol:1,nonce:'a'.repeat(32),public_key:key.toString('base64')}),reservedPlan:put('fictional-reserved.json',{students:[]}),outputDir:path.join(dir,'new-attempt')};
 const config=path.join(dir,'operator.json');fs.writeFileSync(config,JSON.stringify(cfg));return{dir,now,cfg,config,put,write(){fs.writeFileSync(config,JSON.stringify(cfg));}};
}
test('self check requires fresh original native bytes, actual device key, pinned source and outside-Git output',()=>{
 const f=fixture();try{assert.equal(load(f.config,f.now).capture.captured_at,f.now);assert.throws(()=>load(f.config,f.now+900));
  const old=f.cfg.capture;f.cfg.capture=f.put('wrong-capture.json',{format:'future-school-native-capture-v1',captured_at:f.now,bytes:1,sha256:'f'.repeat(64),payload_base64:'eA=='});f.write();assert.throws(()=>load(f.config,f.now));f.cfg.capture=old;
  f.cfg.outputDir=path.join(root,'private-should-not-be-created');f.write();assert.throws(()=>load(f.config,f.now));
 }finally{fs.rmSync(f.dir,{recursive:true,force:true});}
});
test('one strict SSH command carries only public loader; private values use ordered stdin frames',async()=>{
 let options,args;const frames=[],child=new EventEmitter();child.stdout=new EventEmitter();child.stdin=new EventEmitter();child.stdin.write=s=>{const v=JSON.parse(s);frames.push(v);if(v.action)setImmediate(()=>child.stdout.emit('data',Buffer.from(JSON.stringify({sequence:v.sequence,ok:true,data:{phase:v.action}})+'\n')));};child.stdin.end=()=>setImmediate(()=>child.emit('close',0));
 const rpc=rpcProcess(Buffer.from('print("fictional public broker")'),{spawnImpl:(name,a,o)=>{assert.equal(name,'ssh');args=a;options=o;return child;}});
 const result=await rpc.call('copy',{fictional_private_input:'SHOULD_NEVER_BE_IN_A_COMMAND_ARGUMENT'});assert.equal(result.phase,'copy');assert(args.includes('StrictHostKeyChecking=yes'));assert(args.includes('wxnotify-deploy@47.97.226.219'));assert(!args.join(' ').includes('SHOULD_NEVER'));assert.equal(options.stdio[2],'inherit');assert.equal(frames[1].sequence,1);rpc.end();
});
test('remote stop returns recovery state and never silently reissues a deployment action',async()=>{
 const child=new EventEmitter();child.stdout=new EventEmitter();child.stdin=new EventEmitter();let count=0;
 child.stdin.write=s=>{const v=JSON.parse(s);if(v.action){count++;setImmediate(()=>child.stdout.emit('data',Buffer.from(JSON.stringify({sequence:v.sequence,ok:false,recovery:'reconciliation_required',remote_directory:'/fictional-copy'})+'\n')));}};child.stdin.end=()=>setImmediate(()=>child.emit('close',1));
 const rpc=rpcProcess(Buffer.from('fictional'),{spawnImpl:()=>child});await assert.rejects(rpc.call('publish',{}),e=>e.recovery==='reconciliation_required');assert.equal(count,1);rpc.end();
});
