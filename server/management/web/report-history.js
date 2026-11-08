'use strict';
function reportTime(seconds){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(seconds*1000));}
function reportStatus(run){
 if(run.status==='complete')return{text:'生成成功',className:'badge submitted'};
 if(run.status==='failed')return{text:'生成失败',className:'badge rejected'};
 return{text:'正在生成',className:'badge late'};
}
async function generateReportSnapshot(kind,period){
 notify('正在生成报表快照，请稍候');
 return api('/v1/admin/report-runs',{kind,period_start:period,command_ref:crypto.randomUUID().replaceAll('-','')});
}
async function retryReportSnapshot(runId){
 notify('正在重试，并保留原失败版本');
 return api('/v1/admin/report-runs/'+encodeURIComponent(runId)+'/retry',{command_ref:crypto.randomUUID().replaceAll('-','')});
}
function reportHistoryPanel(kind,period,items,selectedId,view){
 const panel=node('section',undefined,'panel report-history'),heading=node('div',undefined,'report-history-heading'),copy=node('div');
 copy.append(node('h2','历史快照'),node('p','快照保存生成时的统计结果，后续补签、招新或续费不会改写旧版本。'));
 const generate=button('生成该周期新快照',async()=>{generate.disabled=true;try{const result=await generateReportSnapshot(kind,period);if(result.run.status==='failed'){notify('本次生成失败，失败版本已保留，可在列表中重试');await view(null);}else{notify('已生成版本 '+result.run.version);await view(result.run.id);}}finally{generate.disabled=false;}});
 generate.dataset.action='generate-report';heading.append(copy,generate);panel.append(heading,node('p','自动任务只处理已结束的上一周和上一月；手动生成当前周期时会标记为“进行中快照”。','weekly-note'));
 const live=button('查看当前实时统计',()=>view(null),'secondary');live.dataset.action='live-report';if(!selectedId)live.disabled=true;
 if(!items.length){const empty=node('div','该周期还没有历史快照。','report-history-empty');empty.append(live);panel.append(empty);return panel;}
 const list=node('div',undefined,'report-run-list');
 for(const run of items){
  const card=node('article',undefined,'report-run'+(run.id===selectedId?' selected':'')),top=node('div',undefined,'report-run-top'),title=node('div');
  title.append(node('h3','版本 '+run.version),node('p',reportTime(run.created_at)+' · '+(run.request_source==='automatic'?'自动生成':'手动生成')+(run.requested_by_name?' · '+run.requested_by_name:'')));
  const status=reportStatus(run),badges=node('div',undefined,'report-run-badges');badges.append(node('span',status.text,status.className));
  if(run.final_period===true)badges.append(node('span','已结束周期','badge'));
  if(run.final_period===false)badges.append(node('span','进行中快照','badge late'));
  top.append(title,badges);card.append(top);
  if(run.status==='failed')card.append(node('p','生成失败，此版本没有报表内容；重试会生成新版本，原失败记录继续保留。','error'));
  const actions=node('div',undefined,'report-run-actions');
  if(run.status==='complete'){const open=button(run.id===selectedId?'正在查看':'查看此版本',()=>view(run.id),run.id===selectedId?'':'secondary');open.disabled=run.id===selectedId;open.dataset.action='view-report-version';open.dataset.runId=run.id;actions.append(open);}
  if(run.status==='failed'){const retry=button('重试并生成新版本',async()=>{retry.disabled=true;try{const result=await retryReportSnapshot(run.id);if(result.run.status==='failed'){notify('重试仍未成功，新失败版本已保留');await view(null);}else{notify('重试成功，已生成版本 '+result.run.version);await view(result.run.id);}}finally{retry.disabled=false;}},'secondary');retry.dataset.action='retry-report';retry.dataset.runId=run.id;actions.append(retry);}
  if(run.status==='running')actions.append(node('span','请稍后刷新版本列表','muted'));
  card.append(actions);list.append(card);
 }
 panel.append(list,live);return panel;
}
