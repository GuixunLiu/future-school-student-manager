'use strict';
// One operator SSH connection; only a new offline directory and consistent private copies.
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const SOURCE='/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/data/roster.sqlite';
function load(dir){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'offline-stage.private.json'),'utf8'));
 if(net.isIP(c.host)!==4||!/^[a-z_][a-z0-9_-]{0,31}$/.test(c.user)||c.port!==22||c.source_database!==SOURCE||c.offline_only!==true||!path.isAbsolute(c.output_dir))throw Error('独立副本计划范围不符。');
 for(const key of ['helper','plan']){const f=c[key];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('缺少固定输入。');const st=fs.lstatSync(f.path);if(st.isSymbolicLink()||!st.isFile()||st.size>131072||sha(fs.readFileSync(f.path))!==f.sha256)throw Error('输入已变化，不能继续准备。');}
 const p=JSON.parse(fs.readFileSync(c.plan.path,'utf8'));if(p.format!=='future-school-owner-confirmed-pilot-lesson'||p.owner_confirmed!==true||p.plan_only!==true||p.ready_to_enable!==false||!Array.isArray(p.members)||p.members.length!==p.expected_students||!Number.isFinite(Date.parse(p.starts_at))||Date.parse(p.starts_at)<=Date.now())throw Error('已确认的实际课次不符或已过去，请重新核对。');
 return c;
}
function wrapper(c){
 const stamp=new Date().toISOString().replace(/[^0-9]/g,'').slice(0,14),work='/opt/wxnotify/app/single-class-offline-'+stamp+'-'+crypto.randomBytes(4).toString('hex');
 const p={work,source:c.source_database,helper:fs.readFileSync(c.helper.path).toString('base64'),helper_sha256:c.helper.sha256,plan:fs.readFileSync(c.plan.path).toString('base64'),plan_sha256:c.plan.sha256};
 return `import base64,hashlib,json,os,re,sys\nfrom pathlib import Path\nsys.dont_write_bytecode=True\nos.umask(0o077)\np=json.loads(base64.b64decode('${Buffer.from(JSON.stringify(p)).toString('base64')}'))\nassert p['source']=='${SOURCE}' and re.fullmatch(r'/opt/wxnotify/app/single-class-offline-[0-9]{14}-[a-f0-9]{8}',p['work'])\nhelper=base64.b64decode(p['helper'],validate=True);raw=base64.b64decode(p['plan'],validate=True)\nassert len(helper)<=131072 and len(raw)<=131072 and hashlib.sha256(helper).hexdigest()==p['helper_sha256'] and hashlib.sha256(raw).hexdigest()==p['plan_sha256']\nns={'__name__':'offline_pilot_stage'}\nexec(compile(helper,'<offline-pilot-stage>','exec'),ns)\ntry:\n result=ns['prepare'](Path(p['source']),Path(p['work']),json.loads(raw),p['plan_sha256'])\n result['work_directory']=p['work']\nexcept Exception as error:\n result={'format':'future-school-offline-pilot-stage-failed','schema_version':1,'stage_complete':False,'work_directory':p['work'],'source_write_requested':False,'service_started':False,'reason':str(error) if isinstance(error,ValueError) else 'Database or private path check failed; keep incomplete artifacts offline'}\nprint(json.dumps(result,separators=(',',':')))\n`;
}
function validateResult(text,c){
 const r=JSON.parse(text),p=JSON.parse(fs.readFileSync(c.plan.path,'utf8'));
 if(!/^\/opt\/wxnotify\/app\/single-class-offline-\d{14}-[a-f0-9]{8}$/.test(r.work_directory)||r.schema_version!==1)throw Error('副本回执路径不符。');
 if(r.format==='future-school-offline-pilot-stage-failed'){if(r.stage_complete!==false||r.source_write_requested!==false||r.service_started!==false||typeof r.reason!=='string'||r.reason.length>1000)throw Error('失败回执范围不符。');return r;}
 if(r.format!=='future-school-offline-pilot-stage'||r.stage_complete!==true||r.plan_sha256!==c.plan.sha256||r.selected_students!==p.expected_students||r.selected_remaining_units!==p.members.reduce((n,m)=>n+m.expected_remaining_units,0)||!Number.isInteger(r.users_preserved)||r.users_preserved<1||['source_opened_read_only','contains_private_data','candidate_sessions_cleared','candidate_devices_disabled','candidate_notifications_disabled'].some(k=>r[k]!==true)||['existing_services_modified','service_started','lesson_created','device_enabled','history_replayed','wechat_enabled'].some(k=>r[k]!==false)||['baseline_backup_sha256','candidate_sha256','preserved_data_sha256'].some(k=>!/^[a-f0-9]{64}$/.test(r[k])))throw Error('独立副本回执未通过核对。');return r;
}
async function run(dir,args){
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');const c=load(dir);
 if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',offline_only:true,pinned_inputs:2,network_connected:false}));return;}
 console.log('将只读核对已确认的试点班，创建新的私有备份及离线副本；账号、班级、余额和任课会保留。现有网页继续使用。');
 console.log('新副本暂不启动服务、不安排课次、不连接平板或发送微信。SSH提示处输入服务器密码，不保存密码。');
 const text=await new Promise((resolve,reject)=>{let chunks=[],bytes=0,failed=false;const child=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',c.user+'@'+c.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});const fail=e=>{if(!failed){failed=true;reject(e)}};child.on('error',fail);child.stdout.on('data',b=>{bytes+=b.length;if(bytes>65536){child.kill();fail(Error('回执过大。'))}else chunks.push(b)});child.on('close',code=>{if(failed)return;code===0?resolve(Buffer.concat(chunks).toString('utf8')):fail(Error('准备未完成，请保留提示。'))});child.stdin.on('error',fail);child.stdin.end(wrapper(c));});
 const r=validateResult(text,c);fs.mkdirSync(c.output_dir,{recursive:true,mode:0o700});if(fs.lstatSync(c.output_dir).isSymbolicLink())throw Error('回执目录不能为链接。');const target=path.join(c.output_dir,'offline-pilot-stage-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(target,JSON.stringify(r,null,2)+'\n',{flag:'wx',mode:0o600});
 if(r.stage_complete){console.log('离线副本准备成功：'+r.selected_students+'名试点学生，'+r.selected_remaining_units/100+'课时；现有网页未切换。');console.log('备份和候选库都在服务器私有目录，新副本尚未启用。');}else{console.log('准备未通过：'+r.reason);console.log('保留现场并返回聊天，不要重复导入或修改名单。');process.exitCode=1;}
 console.log('回执已保存：'+target);
}
module.exports={load,wrapper,validateResult};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
