'use strict';
// Explicit user-run read-only SSH export followed by a local single-class comparison.
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const hash=b=>createHash('sha256').update(b).digest('hex');
function load(dir){
 const config=JSON.parse(fs.readFileSync(path.join(dir,'pilot-server-check.private.json'),'utf8'));
 for(const key of ['reader','helper','planner','validator','plan']){const f=config[key];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256)||fs.lstatSync(f.path).isSymbolicLink()||hash(fs.readFileSync(f.path))!==f.sha256)throw Error('核对输入已变化，请让Codex重新准备。');}
 const reader=require(config.reader.path);reader.validateOptions(config.connection);
 if(config.connection.database!=='/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/data/roster.sqlite'||config.connection.organization!=='weilai')throw Error('Unexpected reviewed database');
 if(path.resolve(path.dirname(config.reader.path),'export-roster-review.py')!==path.resolve(config.helper.path)||path.resolve(path.dirname(config.planner.path),'tablet-review/core.js')!==path.resolve(config.validator.path))throw Error('Unexpected helper paths');
 return config;
}
async function run(dir,args){
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');
 const c=load(dir);if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',pinned_inputs:5,read_only:true,network_connected:false}));return;}
 console.log('只读核对所选班级最新名册、课时和任课权限。出现SSH password提示时输入服务器密码，密码不会保存。');
 const reader=require(c.reader.path),result=await reader.readServerRoster(c.connection,{helperBytes:fs.readFileSync(c.helper.path)});
 const plan=JSON.parse(fs.readFileSync(c.plan.path)),snapshot=JSON.parse(fs.readFileSync(result.path));
 const check=require(c.planner.path).compareServer(plan,snapshot);check.server_snapshot_sha256=result.sha256;check.pilot_plan_sha256=c.plan.sha256;
 const file=path.join(c.connection.outputDir,'single-class-server-check-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(file,JSON.stringify(check,null,2)+'\n',{flag:'wx',mode:0o600});
 console.log(check.roster_matches?'所选班级服务器核对一致。':'发现差异，已保留核对记录，请交给Codex处理。');console.log('匹配人数：'+check.summary.matched_students+'；差异人数：'+check.summary.conflicting_students+'；额外在读人数：'+check.summary.extra_students+'。');console.log('回执已保存：'+file);console.log('本次没有启用同步或写入服务器。');
}
module.exports={load};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
