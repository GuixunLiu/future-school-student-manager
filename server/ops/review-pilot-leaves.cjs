'use strict';
// Private operator SSH reads one existing lesson; all comparison and report writes are local.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function read(f){if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('缺少固定核对材料');const st=fs.lstatSync(f.path);if(!st.isFile()||st.isSymbolicLink()||st.size>2097152)throw Error('核对输入不是普通文件');const raw=fs.readFileSync(f.path);if(sha(raw)!==f.sha256)throw Error('核对材料已变化');return raw;}
function load(dir){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'leave-review.private.json'),'utf8'));
 if(!path.isAbsolute(c.output_dir)||!path.isAbsolute(c.python))throw Error('缺少本机工作目录');
 for(const key of ['scoped_client','scoped_configuration','scoped_receipt','tablet_snapshot','exporter','reconciler'])read(c[key]);
 const original=require(c.scoped_client.path),sc=original.load(path.dirname(c.scoped_configuration.path));
 const receipt=original.validateResult(read(c.scoped_receipt).toString('utf8'),sc),plan=JSON.parse(read(sc.plan)),tablet=JSON.parse(read(c.tablet_snapshot));
 if(tablet.format!=='future-school-pilot-tablet-minimal-leave-snapshot'||tablet.schema_version!==1||tablet.class_id!==plan.server_class_id||tablet.identity_mapping_verified!==true||tablet.confirmed_plan_sha256!==sc.plan.sha256||tablet.lesson_date!==plan.starts_at.slice(0,10))throw Error('最新平板映射与已确认课次不同');
 if(!Number.isFinite(Date.parse(tablet.extracted_at))||Math.abs(Date.now()-Date.parse(tablet.extracted_at))>86400000)throw Error('平板核对快照超过一天，请重新备份核对');
 return {c,sc,receipt,plan,tablet};
}
function remoteScript(input){
 const {c,receipt,plan}=input,config={stage_directory:receipt.stage_directory,runtime_sha256:receipt.runtime_sha256,class_id:plan.server_class_id,lesson_id:receipt.lesson_id,student_ids:plan.members.map(m=>m.server_student_id).sort()};
 const source=`import base64,hashlib,json,sys\nsys.dont_write_bytecode=True\nraw=base64.b64decode('${read(c.exporter).toString('base64')}',validate=True)\nassert hashlib.sha256(raw).hexdigest()=='${c.exporter.sha256}'\nns={'__name__':'read_only_pilot_leaves'}\nexec(compile(raw,'<read-only-pilot-leaves>','exec'),ns)\nconfig=json.loads(base64.b64decode('${Buffer.from(JSON.stringify(config)).toString('base64')}'))\nprint(json.dumps(ns['export'](config),separators=(',',':')),flush=True)\n`;
 const encoded=Buffer.from(source).toString('base64');if(encoded.length>65536)throw Error('读取程序过大');return encoded+'\n';
}
function validateServer(snapshot,input,now=Date.now()){
 const {receipt,plan}=input,ids=plan.members.map(m=>m.server_student_id).sort();
 if(snapshot.format!=='future-school-pilot-server-leave-snapshot'||snapshot.schema_version!==1||snapshot.source_access!=='read_only'||snapshot.server_written!==false||snapshot.device_enabled!==false||snapshot.wechat_enabled!==false||snapshot.class_id!==plan.server_class_id||snapshot.lesson_id!==receipt.lesson_id||snapshot.identity_mapping_verified!==true||!Number.isFinite(Date.parse(snapshot.generated_at))||Math.abs(now-Date.parse(snapshot.generated_at))>300000||!Array.isArray(snapshot.student_ids)||snapshot.student_ids.join()!==ids.join()||!Array.isArray(snapshot.leaves)||!Array.isArray(snapshot.arrived_student_ids)||!/^[a-f0-9]{64}$/.test(snapshot.snapshot_sha256))throw Error('服务器只读快照与确认范围不符');
 return snapshot;
}
function processOutput(command,args,input){return new Promise((resolve,reject)=>{const p=spawn(command,args,{windowsHide:true,stdio:['pipe','pipe','inherit']});let text='';p.on('error',reject);p.stdout.on('data',b=>{text+=b.toString('utf8');if(Buffer.byteLength(text)>2097152){p.kill();reject(Error('核对输出超过限制'))}});p.on('close',code=>code===0?resolve(text):reject(Error('只读核对未完成，请保留错误，不修改或重部署服务')));p.stdin.on('error',reject);p.stdin.end(input);});}
async function run(dir,args){
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');const input=load(dir),{c,sc,receipt,plan,tablet}=input;
 if(args[0]==='--self-check'){remoteScript(input);console.log(JSON.stringify({launcher:'ok',read_only:true,network_connected:false,pinned_inputs:6}));return;}
 console.log('只读取已经部署的试点课次签到/请假状态，与刚备份的平板核对。不会安装、重启、写业务、启用同步或发送通知。');console.log('SSH提示处输入服务器密码，不保存密码，不使用教师凭证。');
 const boot=`env -u PYTHONPATH -u PYTHONHOME python3 -u -c 'import base64,sys; exec(compile(base64.b64decode(sys.stdin.buffer.readline(65537).strip(),validate=True),"<read-only-pilot-bootstrap>","exec"))'`;
 const text=await processOutput('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',sc.user+'@'+sc.host,boot],remoteScript(input));
 const server=validateServer(JSON.parse(text),input),lesson=receipt.lesson_id;
 const facts={format:'future-school-pilot-leave-review-input',schema_version:1,class_id:plan.server_class_id,lesson_id:lesson,device_id:null,student_ids:plan.members.map(m=>m.server_student_id).sort(),tablet:{...tablet,lesson_id:lesson,leaves:tablet.leaves.map(l=>({...l,lesson_id:lesson}))},server};
 fs.mkdirSync(c.output_dir,{recursive:true});if(fs.lstatSync(c.output_dir).isSymbolicLink())throw Error('核对目录不能为链接');const stamp=new Date().toISOString().replace(/[:.]/g,'-'),source=path.join(c.output_dir,'leave-facts-'+stamp+'.private.json'),output=path.join(c.output_dir,'leave-review-'+stamp+'.private.json');
 fs.writeFileSync(source,JSON.stringify(facts,null,2)+'\n',{flag:'wx',mode:0o600});
 await processOutput(c.python,[c.reconciler.path,'--input',source,'--output',output],'');
 const report=JSON.parse(fs.readFileSync(output,'utf8'));
 if(report.changes_applied!==false||report.activation_permitted!==false||report.events_generated!==false)throw Error('只读报告状态不符');
 console.log(report.leave_facts_match?'两端本课签到/请假事实核对一致；这不代表已接通同步。':'发现两端差异，已列入私有核对报告；未自动合并或覆盖。');console.log('应到 '+report.expected_students+' 人，平板/服务器签到 '+report.tablet_arrived+'/'+report.server_arrived+'，请假 '+report.tablet_approved_leave_students+'/'+report.server_approved_leave_students+'。');console.log('报告已保存：'+output);console.log('请把窗口结果告诉Codex。平板同步及微信仍关闭。');
}
module.exports={load,remoteScript,validateServer};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
