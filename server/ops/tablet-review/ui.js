(function(){
  'use strict';
  const engine=globalThis.TabletMigrationReview,$=s=>document.querySelector(s),MAX_BYTES=128*1024*1024;
  let backup=null,snapshot=null,report=null,sourceMeta=null,snapshotMeta=null;
  const sequence={backup:0,snapshot:0},loading={backup:false,snapshot:false},loadErrors={backup:'',snapshot:''};
  const labels={not_compared:'未比较',new:'服务器未匹配',match:'字段一致',conflict:'存在差异'};
  function cell(row,value,tag='td'){const n=document.createElement(tag);n.textContent=String(value??'');row.append(n);return n;}
  function table(selector,records,render){const target=$(selector);target.replaceChildren();if(!records.length){const r=document.createElement('tr');cell(r,'暂无记录').colSpan=6;target.append(r);}else records.forEach(record=>{const row=document.createElement('tr');render(row,record);target.append(row);});}
  function hideResult(){report=null;$('#results').hidden=true;$('#downloadReport').disabled=true;for(const id of ['#resultTitle','#comparisonNote','#metrics','#issueSummary','#issueRows','#issueLimit','#studentRows','#classRows','#unreviewed'])$(id).replaceChildren();}
  function render(){
    hideResult();$('#error').hidden=true;
    if(loading.backup||loading.snapshot){$('#fileStatus').textContent='正在本机读取和核对…';return;}
    const failure=loadErrors.backup||loadErrors.snapshot;if(failure){$('#error').textContent=failure;$('#error').hidden=false;$('#fileStatus').textContent='未完成核对，未执行导入';return;}
    if(!backup){$('#fileStatus').textContent=snapshot?'服务器清单已读取，请选择平板备份':'尚未选择平板备份';return;}
    const current=engine.review(backup,snapshot);report={...current,generated_at:new Date().toISOString(),source:sourceMeta,...(snapshotMeta?{server_source:snapshotMeta}:{})};
    $('#results').hidden=false;$('#downloadReport').disabled=false;$('#fileStatus').textContent='已在本机完成核对，未执行导入';
    $('#resultTitle').textContent=current.issue_counts.error?'发现需要先处理的问题':current.issue_counts.warning?'结构检查完成，仍有人工核对项':'结构检查完成，等待人工确认';
    $('#comparisonNote').textContent=current.comparison_performed?'已与服务器机构 '+current.server_organization+' 的清单比较；请人工确认机构归属，不会自动覆盖差异。':'仅检查平板备份自身，尚未与服务器资料比较。';
    const stats=[['学生总数',current.summary.students],['在读 / 长期不来',current.summary.active+' / '+current.summary.paused],['班级数',current.summary.classes],['需处理 / 人工核对',current.issue_counts.error+' / '+current.issue_counts.warning]],metrics=$('#metrics');metrics.replaceChildren();stats.forEach(([label,value])=>{const card=document.createElement('div');card.className='metric';cell(card,label,'span');cell(card,value,'strong');metrics.append(card);});
    $('#issueSummary').textContent='需处理 '+current.issue_counts.error+' 项，人工核对 '+current.issue_counts.warning+' 项。请先看重复学号、班级关联及余额差异。';
    table('#issueRows',current.issues,(r,i)=>{const level=cell(r,({error:'需处理',warning:'人工核对',info:'说明'})[i.severity]);level.className='level '+i.severity;cell(r,i.entity);cell(r,i.message);});
    $('#issueLimit').textContent=current.issues_truncated?'问题超过2000项，报告保留总数但明细未完整显示；需要分批核对，不能视为完整验收。':'核对不会自动修改问题记录。';
    table('#studentRows',current.students.slice(0,200),(r,s)=>{const who=cell(r,s.name);cell(who,s.student_no,'small');cell(r,s.class_name||(s.status==='paused'?'长期不来 · 原班 '+s.paused_class_name:'未分班'));cell(r,s.remaining_units===null?'待核对':s.remaining_units/100);cell(r,s.status==='paused'?'长期不来':'在读');cell(r,labels[s.comparison]);});
    table('#classRows',current.classes.slice(0,200),(r,c)=>{const title=cell(r,c.name);cell(title,c.course_name||'待分类','small');cell(r,c.teacher_label||'待确认');cell(r,c.interval_weeks===2?'每两周一次':c.interval_weeks===1?'每周一次':'待核对');cell(r,(c.weekday?'周'+['','一','二','三','四','五','六','日'][c.weekday]:'课日待确认')+' '+(c.start_time||'未定')+'—'+(c.end_time||'未定'));cell(r,c.anchor_date||'—');});
    const unreviewed=$('#unreviewed');unreviewed.replaceChildren();current.unreviewed.forEach(text=>cell(unreviewed,text,'li'));
  }
  async function load(kind,input){
    const request=++sequence[kind],file=input.files[0];loading[kind]=Boolean(file);loadErrors[kind]='';hideResult();$('#error').hidden=true;
    if(kind==='backup'){backup=null;sourceMeta=null;}else{snapshot=null;snapshotMeta=null;}
    if(!file){render();return;}
    $('#fileStatus').textContent='正在本机读取和核对…';
    try{
      if(file.size>MAX_BYTES)throw Error('文件超过128MiB，请先在本机核对大小，不会上传该文件');
      const bytes=await file.arrayBuffer();if(request!==sequence[kind])return;
      const parsed=engine.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes).replace(/^\uFEFF/,''));
      const digest=globalThis.crypto?.subtle?Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join(''):null;if(request!==sequence[kind])return;
      const meta={bytes:file.size,sha256:digest};
      if(kind==='backup'){engine.review(parsed);backup=engine.projectBackup(parsed);sourceMeta=meta;}
      else{const checked=engine.validateSnapshot(parsed);snapshot={format:parsed.format,schema_version:1,complete:true,org_id:parsed.org_id,organization:parsed.organization,generated_at:typeof parsed.generated_at==='string'?parsed.generated_at.slice(0,40):undefined,classes:checked.classes.map(c=>({id:c.id,name:c.name,course_name:c.course_name,teachers:c.teachers.map(t=>({id:t.id,name:t.name}))})),students:checked.students.map(s=>({id:s.id,name:s.name,external_ref:s.external_ref,class_id:s.class_id,remaining_units:s.remaining_units,active:s.active}))};snapshotMeta=meta;}
      loading[kind]=false;render();
    }catch(error){if(request!==sequence[kind])return;hideResult();if(kind==='backup'){backup=null;sourceMeta=null;}else{snapshot=null;snapshotMeta=null;}input.value='';loading[kind]=false;loadErrors[kind]=error instanceof TypeError?'文件编码或格式不受支持，请使用原始JSON备份':error.message||'读取失败';render();}
  }
  $('#backupFile').onchange=e=>load('backup',e.target);$('#snapshotFile').onchange=e=>load('snapshot',e.target);
  $('#clearFiles').onclick=()=>{sequence.backup++;sequence.snapshot++;loading.backup=false;loading.snapshot=false;loadErrors.backup='';loadErrors.snapshot='';backup=null;snapshot=null;sourceMeta=null;snapshotMeta=null;$('#backupFile').value='';$('#snapshotFile').value='';$('#error').hidden=true;render();};
  $('#downloadReport').onclick=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='未来学校-迁移前核对报告.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
})();
